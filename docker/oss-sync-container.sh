#!/usr/bin/env bash
# oss-sync-container.sh — in-container OSS state sync.
#
# Pairs with docker/entrypoint-with-sync.sh. Unlike the older
# docker/oss-sync.sh (which runs on the HOST and reaches into a
# bind-mounted hermes_state volume), this script runs INSIDE the
# webui container itself — no host-side dependency, works with named
# Docker volumes, deploys to Aliyun ECS + Tencent Cloud + bare Docker
# all the same way.
#
# Why two scripts (host vs container):
#   • Host script (legacy): designed for Phase 2 cloud-init on a
#     dedicated VM where systemd manages container lifecycle and
#     cron runs as root. Bigger blast radius (touches /var/log,
#     /etc/default, etc.) but matches the "VM owns the box" model.
#   • Container script (this one): for shared deployments
#     (chat.neowow.studio Phase 1) where the box might be Tencent
#     today, Aliyun tomorrow. Container is the unit of state, sync
#     lives inside it. Anyone running `docker compose up` gets sync
#     for free without learning cron/systemd.
#
# Modes:
#   pull   — OSS → /opt/hermes/.hermes/.  Called once at container
#            start by entrypoint before WebUI starts.
#   push   — /opt/hermes/.hermes/ → OSS.  Called periodically + at
#            container stop. Incremental, only changed files.
#   verify — quick auth check.  Called by entrypoint to fail loudly
#            on misconfigured AK/SK.
#
# Configuration (env vars; entrypoint reads compose env):
#   OSS_SYNC_ENABLED         "1" to enable, anything else = no-op
#   OSS_ACCESS_KEY_ID        Aliyun AK ID (RAM user recommended)
#   OSS_ACCESS_KEY_SECRET    companion secret
#   OSS_ENDPOINT             e.g. oss-cn-hangzhou.aliyuncs.com (no schema)
#   OSS_BUCKET               e.g. neowow-hermes-state
#   OSS_USER_ID              the user's Neodomain userId — used as
#                             prefix segment to isolate users in shared
#                             bucket. Without this we won't know WHOSE
#                             state to sync.
#
# Sync rules (what's in / out, see PHASE_2_DESIGN.md):
#   IN  — sessions/, webui/settings.json, webui/skills/,
#         webui/workspaces.json, config.yaml, workspace/
#   OUT — webui/neowow.json (JWT, per-device), webui/gateway.json
#         (per-instance), webui/hermes_session.json (local login),
#         webui/.login_attempts.json, .env (API keys, sensitive),
#         hermes-agent/ (rebuilt from image), __pycache__, *.pid,
#         *.lock, *.sock.
# ==============================================================================

set -euo pipefail

LOCAL_PATH="${LOCAL_PATH:-/opt/hermes/.hermes}"
WORKSPACE_PATH="${WORKSPACE_PATH:-/opt/hermes/workspace}"
OSSUTIL="${OSSUTIL:-/usr/local/bin/ossutil}"
LOG_PREFIX="[oss-sync]"

log() {
    # stderr so it shows in `docker logs` alongside webui output without
    # corrupting the structured JSON request log on stdout.
    echo "$(date -Iseconds) ${LOG_PREFIX} $*" >&2
}

# Soft-disable when not configured — graceful no-op. The container should
# always start successfully even without OSS creds (development, BYO
# state, single-tenant deploys).
if [[ "${OSS_SYNC_ENABLED:-0}" != "1" ]]; then
    log "OSS_SYNC_ENABLED=${OSS_SYNC_ENABLED:-0}; sync disabled (no-op)"
    exit 0
fi

# Required vars when sync IS enabled. Fail-fast — operator misconfig
# silently producing "no backup" is the worst outcome we can imagine.
if [[ -z "${OSS_ACCESS_KEY_ID:-}" ]] \
   || [[ -z "${OSS_ACCESS_KEY_SECRET:-}" ]] \
   || [[ -z "${OSS_USER_ID:-}" ]] \
   || [[ -z "${OSS_BUCKET:-}" ]] \
   || [[ -z "${OSS_ENDPOINT:-}" ]]; then
    log "ERROR: OSS_SYNC_ENABLED=1 but missing OSS_ACCESS_KEY_ID / "
    log "       OSS_ACCESS_KEY_SECRET / OSS_USER_ID / OSS_BUCKET / OSS_ENDPOINT."
    log "       Refusing to silently skip — exit non-zero."
    exit 2
fi

if [[ ! -x "$OSSUTIL" ]]; then
    log "ERROR: ossutil binary not found at $OSSUTIL"
    exit 3
fi

OSS_PREFIX="${OSS_PREFIX:-users/${OSS_USER_ID}/hermes}"
OSS_URI="oss://${OSS_BUCKET}/${OSS_PREFIX%/}"

# Write ossutil credentials to a tmpfile every run — supports operator
# rotating AK/SK between container restarts without rebuilding the
# image. Cleaned up via trap.
OSSUTIL_CONFIG="$(mktemp)"
chmod 600 "$OSSUTIL_CONFIG"  # don't leak creds via /tmp world-readable
trap 'rm -f "$OSSUTIL_CONFIG"' EXIT
cat > "$OSSUTIL_CONFIG" <<EOF
[Credentials]
language=EN
accessKeyID=${OSS_ACCESS_KEY_ID}
accessKeySecret=${OSS_ACCESS_KEY_SECRET}
endpoint=${OSS_ENDPOINT}
EOF

# ── Sync path definitions ───────────────────────────────────────────────────
# Paths to sync, expressed as (local_subpath, oss_subpath, kind) triples.
# `kind`:
#   • file  — single file
#   • dir   — recursive directory
SYNC_PATHS=(
    # sessions — most important. Per-session JSON files.
    "${LOCAL_PATH}/sessions|sessions|dir"
    # webui state worth preserving across boxes
    "${LOCAL_PATH}/webui/settings.json|webui/settings.json|file"
    "${LOCAL_PATH}/webui/workspaces.json|webui/workspaces.json|file"
    "${LOCAL_PATH}/webui/skills|webui/skills|dir"
    # provider config (model/base_url) — rare changes but worth it
    "${LOCAL_PATH}/config.yaml|config.yaml|file"
    # user-generated workspace files (code, screenshots, etc.)
    "${WORKSPACE_PATH}|workspace|dir"
)

# ossutil's --include / --exclude patterns. Applied to ALL dir syncs.
# Order matters — first match wins.
COMMON_EXCLUDES=(
    --exclude "__pycache__/*"
    --exclude "*.pyc"
    --exclude "*.pid"
    --exclude "*.lock"
    --exclude "*.sock"
    --exclude ".DS_Store"
    --exclude "Thumbs.db"
    # Sensitive things that should NEVER leave the box
    --exclude ".env"
    --exclude "*.key"
    --exclude "*.pem"
)

# ── Subcommands ─────────────────────────────────────────────────────────────

cmd_verify() {
    log "verify: probing ${OSS_URI}/ for AK/SK validity"
    if "$OSSUTIL" -c "$OSSUTIL_CONFIG" ls "${OSS_URI}/" --limited-num 1 >/dev/null 2>&1; then
        log "verify: ok (prefix exists or accessible)"
        return 0
    fi
    # Distinguish "auth failed" from "empty prefix" — fresh users have
    # no objects under their prefix yet. Try the bucket root.
    if "$OSSUTIL" -c "$OSSUTIL_CONFIG" ls "oss://${OSS_BUCKET}/" --limited-num 1 >/dev/null 2>&1; then
        log "verify: ok (bucket reachable, prefix will be created on first push)"
        return 0
    fi
    log "verify: FAILED — bucket/AK/SK not accepted by OSS. Double-check:"
    log "  • OSS_ACCESS_KEY_ID matches a RAM user with oss:PutObject/GetObject perms"
    log "  • OSS_ACCESS_KEY_SECRET is the secret for THAT user"
    log "  • OSS_ENDPOINT region matches OSS_BUCKET's region"
    return 1
}

cmd_pull() {
    log "pull: ${OSS_URI} → ${LOCAL_PATH} + ${WORKSPACE_PATH}"

    # Ensure local target dirs exist so ossutil doesn't error on first
    # pull. mkdir -p is idempotent.
    mkdir -p "$LOCAL_PATH/sessions" "$LOCAL_PATH/webui/skills" "$WORKSPACE_PATH"

    local failed=0
    for triple in "${SYNC_PATHS[@]}"; do
        IFS='|' read -r local_path oss_subpath kind <<< "$triple"
        local oss_path="${OSS_URI}/${oss_subpath}"

        case "$kind" in
            file)
                # Single file: cp without -r. --update only downloads
                # when local is older than remote.
                if "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp "$oss_path" "$local_path" \
                       --update --force >/dev/null 2>&1; then
                    log "pull: ok  $local_path  ←  $oss_path"
                else
                    # Not present in OSS yet — fresh user. Not an error.
                    log "pull: ---  $oss_path  (not in OSS yet; fresh state)"
                fi
                ;;
            dir)
                # Recursive: trailing slash on BOTH sides + -r.
                mkdir -p "$local_path"
                if "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp -r \
                       "$oss_path/" "$local_path/" \
                       --update --force \
                       "${COMMON_EXCLUDES[@]}" >/dev/null 2>&1; then
                    log "pull: ok  $local_path/  ←  $oss_path/"
                else
                    log "pull: ---  $oss_path/  (not in OSS yet; fresh state)"
                fi
                ;;
        esac
    done

    if (( failed > 0 )); then
        log "pull: ${failed} path(s) failed (treating as soft errors; container still starts)"
    fi
    log "pull: done"
}

cmd_push() {
    log "push: ${LOCAL_PATH} + ${WORKSPACE_PATH} → ${OSS_URI}"
    local final="${1:-}"   # `--final` for graceful-shutdown final push

    local pushed=0
    local skipped=0

    for triple in "${SYNC_PATHS[@]}"; do
        IFS='|' read -r local_path oss_subpath kind <<< "$triple"
        local oss_path="${OSS_URI}/${oss_subpath}"

        case "$kind" in
            file)
                if [[ ! -f "$local_path" ]]; then
                    skipped=$((skipped+1))
                    continue
                fi
                if "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp "$local_path" "$oss_path" \
                       --update --force >/dev/null 2>&1; then
                    pushed=$((pushed+1))
                fi
                ;;
            dir)
                if [[ ! -d "$local_path" ]]; then
                    skipped=$((skipped+1))
                    continue
                fi
                # `--update` uploads only when local mtime > remote.
                # We deliberately don't pass `--delete` — a transient
                # local glitch (e.g. fs mount issue) should NOT wipe
                # the cloud backup. Pruning happens lazily via lifecycle
                # rules on the bucket (TODO: add lifecycle rule docs).
                if "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp -r \
                       "$local_path/" "$oss_path/" \
                       --update --force \
                       "${COMMON_EXCLUDES[@]}" >/dev/null 2>&1; then
                    pushed=$((pushed+1))
                fi
                ;;
        esac
    done

    # Marker so we can sanity-check from the dashboard side ("when did
    # this user last sync?"). Best-effort.
    local marker
    marker=$(printf '{"hostname":"%s","at":"%s","final":"%s","pushed":%d,"skipped":%d}\n' \
        "$(hostname)" "$(date -Iseconds)" "$final" "$pushed" "$skipped")
    echo "$marker" | "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp - \
        "${OSS_URI}/_meta/last-synced.json" --force >/dev/null 2>&1 || true

    log "push: ${pushed} synced, ${skipped} skipped (no local) ${final:+(final)}"
}

# ── Dispatch ────────────────────────────────────────────────────────────────

case "${1:-}" in
    verify) cmd_verify ;;
    pull)   cmd_pull   ;;
    push)   shift; cmd_push "${1:-}" ;;
    *)
        echo "usage: $0 {verify|pull|push [--final]}" >&2
        exit 1
        ;;
esac
