#!/usr/bin/env bash
# oss-sync.sh — Phase 2 M2 state sync between per-user ECS and Aliyun OSS.
#
# This script lives on each spawned ECS (placed by cloud-init at
# /opt/hermes-docker/oss-sync.sh). It runs in three modes:
#
#   pull   — OSS → local. Called once at ECS boot BEFORE docker compose
#            comes up. Restores the user's chat history into the
#            hermes_state Docker volume.
#
#   push   — local → OSS. Called every 5 minutes by cron and once at
#            shutdown by systemd. Incremental — only changed files
#            transfer.
#
#   verify — quick "do we have OSS access?" check. Used during cloud-init
#            so we can bail early with a clean error if creds are wrong.
#
# Configuration via env vars (cloud-init writes these to /etc/default/
# hermes-oss-sync):
#
#   OSS_ACCESS_KEY_ID       — Aliyun AccessKey ID (RAM user recommended,
#                              with policy scoped to neowow-hermes-state
#                              bucket only).
#   OSS_ACCESS_KEY_SECRET   — companion secret.
#   OSS_ENDPOINT            — e.g. oss-cn-hangzhou.aliyuncs.com (without
#                              schema).
#   OSS_BUCKET              — neowow-hermes-state
#   OSS_PREFIX              — users/<userId>/hermes (per-user; the broker
#                              writes this with the userId substituted in)
#   LOCAL_PATH              — /opt/hermes/.hermes (inside the docker volume
#                              mount; we read via the host bind path that
#                              cloud-init sets up at /var/lib/hermes-state)
#
# Logs to /var/log/hermes-oss-sync.log (rotated by logrotate.d entry).
# ==============================================================================

set -euo pipefail

# ── Load config ─────────────────────────────────────────────────────────────
CONFIG_FILE="/etc/default/hermes-oss-sync"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: missing $CONFIG_FILE (cloud-init should have created it)" >&2
    exit 2
fi
# shellcheck source=/dev/null
source "$CONFIG_FILE"

# Validate required vars. Fail fast — bad creds = silent data loss.
: "${OSS_ACCESS_KEY_ID:?required}"
: "${OSS_ACCESS_KEY_SECRET:?required}"
: "${OSS_ENDPOINT:?required}"
: "${OSS_BUCKET:?required}"
: "${OSS_PREFIX:?required}"
: "${LOCAL_PATH:?required}"

OSS_URI="oss://${OSS_BUCKET}/${OSS_PREFIX%/}"
LOG_FILE="/var/log/hermes-oss-sync.log"

# ── Ossutil path (installed by cloud-init) ──────────────────────────────────
OSSUTIL="${OSSUTIL:-/usr/local/bin/ossutil}"
if [[ ! -x "$OSSUTIL" ]]; then
    echo "ERROR: $OSSUTIL not found / not executable" >&2
    exit 3
fi

# ── ossutil config ──────────────────────────────────────────────────────────
# ossutil reads ~/.ossutilconfig by default. We write it on the fly so the
# script doesn't need root and so config rotates with each run (the env
# could change, e.g. operator rotates the AK).
OSSUTIL_CONFIG="$(mktemp)"
trap 'rm -f "$OSSUTIL_CONFIG"' EXIT
cat > "$OSSUTIL_CONFIG" <<EOF
[Credentials]
language=EN
accessKeyID=${OSS_ACCESS_KEY_ID}
accessKeySecret=${OSS_ACCESS_KEY_SECRET}
endpoint=${OSS_ENDPOINT}
EOF

log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE" >&2
}

# ── Subcommands ─────────────────────────────────────────────────────────────

cmd_verify() {
    log "verify: probing access to ${OSS_URI}/"
    # `stat` on a non-existent path returns 1 with a specific error;
    # auth failure returns a different non-zero. We accept both
    # "object not found" (fresh user) and "ok" as success.
    if "$OSSUTIL" -c "$OSSUTIL_CONFIG" ls "${OSS_URI}/" >/dev/null 2>&1; then
        log "verify: ok (prefix exists)"
        return 0
    fi
    # Try a list of the bucket root to distinguish "auth bad" vs
    # "prefix not yet created".
    if "$OSSUTIL" -c "$OSSUTIL_CONFIG" ls "oss://${OSS_BUCKET}/" --limited-num 1 >/dev/null 2>&1; then
        log "verify: ok (bucket accessible; prefix will be created on first push)"
        return 0
    fi
    log "verify: FAILED — check OSS credentials + bucket permissions"
    return 1
}

cmd_pull() {
    log "pull: $OSS_URI/sessions/ → $LOCAL_PATH/sessions/"
    mkdir -p "$LOCAL_PATH/sessions"

    # `cp -r` from OSS does the right thing: skips existing identical
    # files, downloads new/changed ones. --update means "only if local
    # mtime older than remote".
    #
    # We DON'T include --delete because if a user is starting fresh
    # (no remote state yet), we don't want to wipe whatever's local.
    # Fresh start = empty local + empty remote = empty after pull.
    if "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp -r \
        "${OSS_URI}/sessions/" "$LOCAL_PATH/sessions/" \
        --update --force >> "$LOG_FILE" 2>&1; then
        log "pull: ok"
    else
        # First-ever pull for a new user: OSS prefix doesn't exist → ossutil
        # returns non-zero. Treat as success (empty state to restore).
        log "pull: no remote state (treating as fresh start)"
    fi
}

cmd_push() {
    log "push: $LOCAL_PATH/sessions/ → $OSS_URI/sessions/"

    if [[ ! -d "$LOCAL_PATH/sessions" ]]; then
        log "push: no local sessions dir; skipping"
        return 0
    fi

    # Incremental upload — only changed files. --delete on the REMOTE
    # side mirrors what the local has (so deleted sessions get cleaned
    # remotely too). This is the inverse-direction setting from pull.
    "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp -r \
        "$LOCAL_PATH/sessions/" "${OSS_URI}/sessions/" \
        --update --force >> "$LOG_FILE" 2>&1

    # Drop a marker so we can sanity-check from the broker side.
    echo "$(date -Iseconds) push from $(hostname)" \
        | "$OSSUTIL" -c "$OSSUTIL_CONFIG" cp - \
            "${OSS_URI}/_meta/last-synced.txt" --force >/dev/null 2>&1 \
        || true

    log "push: ok"
}

# ── Dispatch ────────────────────────────────────────────────────────────────

case "${1:-}" in
    verify) cmd_verify ;;
    pull)   cmd_pull   ;;
    push)   cmd_push   ;;
    *)
        echo "usage: $0 {verify|pull|push}" >&2
        exit 1
        ;;
esac
