#!/usr/bin/env bash
# entrypoint-with-sync.sh — replaces `bash webui/start.sh` as the
# container's CMD. Wraps the WebUI startup with OSS sync lifecycle:
#
#   1. Before WebUI starts → OSS pull (restore user state)
#   2. While WebUI runs   → background loop pushes every N seconds
#   3. On SIGTERM         → kill background loop, final push, kill
#                            WebUI gracefully, exit
#
# Why a wrapper instead of putting this in webui/start.sh?
#   • start.sh is desktop-shared code (used by local Hermes app too).
#     Adding cloud-only sync there would be a weird coupling.
#   • Signal handling requires the wrapper to be PID 1 (or have a
#     proper init like tini). A bash wrapper IS PID 1 in our container.
#   • If sync is disabled (OSS_SYNC_ENABLED != 1), the wrapper is a
#     thin pass-through with no extra processes.
#
# Boot path under this wrapper:
#   PID 1: this script (bash)
#     ├─→ oss-sync-container.sh pull   (foreground, ~5-30s)
#     ├─→ start.sh as child            (PID 2+)
#     │     └─→ bootstrap.py → server.py
#     └─→ background sync loop         (PID 3+)
#           └─→ sleep N; oss-sync-container.sh push; repeat
#
# On `docker stop` (SIGTERM):
#   • trap fires → kill background loop → kill -TERM webui (PID 2)
#   • wait webui (graceful shutdown ~5s; webui flushes state DB)
#   • oss-sync-container.sh push --final
#   • exit 0 (clean container stop)
# ==============================================================================

set -uo pipefail
# Note: deliberately NOT set -e — we want failed OSS calls (transient
# network blip) to log a warning, not kill the container.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYNC_SCRIPT="${REPO_ROOT}/docker/oss-sync-container.sh"
PUSH_INTERVAL="${OSS_SYNC_INTERVAL_SECS:-300}"

# State for the trap handler
WEBUI_PID=""
SYNC_PID=""

log() {
    echo "$(date -Iseconds) [entrypoint] $*" >&2
}

# ── Cleanup on SIGTERM/SIGINT ───────────────────────────────────────────────
cleanup() {
    log "shutdown signal received; stopping sync + webui..."

    # 1. Stop the periodic-push background loop FIRST so it doesn't
    #    race with the final push. `kill 0` would whack our own group;
    #    target the loop PID specifically.
    if [[ -n "${SYNC_PID}" ]] && kill -0 "${SYNC_PID}" 2>/dev/null; then
        kill -TERM "${SYNC_PID}" 2>/dev/null || true
        wait "${SYNC_PID}" 2>/dev/null || true
        log "background sync loop stopped"
    fi

    # 2. SIGTERM the webui. Server.py installs its own SIGTERM handler
    #    that flushes pending DB writes + closes SSE connections cleanly
    #    (#1558 startup recovery uses this). Give it up to 25s.
    if [[ -n "${WEBUI_PID}" ]] && kill -0 "${WEBUI_PID}" 2>/dev/null; then
        log "sending SIGTERM to webui (PID ${WEBUI_PID})..."
        kill -TERM "${WEBUI_PID}" 2>/dev/null || true
        # Wait, but with a timeout — if webui hangs we still want to
        # do the OSS push and exit cleanly. Bash doesn't have a clean
        # `wait --timeout`, so use a polling loop.
        for _ in $(seq 1 25); do
            kill -0 "${WEBUI_PID}" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "${WEBUI_PID}" 2>/dev/null; then
            log "webui didn't exit in 25s, sending SIGKILL"
            kill -KILL "${WEBUI_PID}" 2>/dev/null || true
        fi
        wait "${WEBUI_PID}" 2>/dev/null || true
        log "webui stopped"
    fi

    # 3. Final OSS push. Even if webui crashed, sync the state we have.
    if [[ "${OSS_SYNC_ENABLED:-0}" == "1" ]]; then
        log "final OSS push..."
        bash "${SYNC_SCRIPT}" push --final \
            || log "final push FAILED — some recent changes may not be in OSS"
    fi

    log "shutdown complete"
    exit 0
}
trap cleanup TERM INT

# ── Pre-flight OSS verify + pull ────────────────────────────────────────────
if [[ "${OSS_SYNC_ENABLED:-0}" == "1" ]]; then
    log "OSS sync enabled (bucket=${OSS_BUCKET:-?} userId=${OSS_USER_ID:-?})"

    # Verify auth before we proceed. If creds are wrong, we want to
    # know NOW (clear error in `docker logs`), not 5 minutes later
    # when the first periodic push silently fails.
    if ! bash "${SYNC_SCRIPT}" verify; then
        log "ERROR: OSS verify failed. Starting WebUI anyway (data won't be"
        log "       backed up — fix OSS creds in compose and restart)."
    else
        # Pull existing state. Soft-fail: if pull errors, we still start
        # WebUI with whatever's in the volume (could be fresh / empty).
        log "pulling existing state from OSS..."
        if ! bash "${SYNC_SCRIPT}" pull; then
            log "WARN: OSS pull failed; starting with local volume state"
        fi
    fi
else
    log "OSS sync disabled (OSS_SYNC_ENABLED=${OSS_SYNC_ENABLED:-0})"
fi

# ── Launch WebUI ────────────────────────────────────────────────────────────
log "starting webui via webui/start.sh..."
bash "${REPO_ROOT}/webui/start.sh" "$@" &
WEBUI_PID=$!
log "webui PID=${WEBUI_PID}"

# ── Background push loop ────────────────────────────────────────────────────
if [[ "${OSS_SYNC_ENABLED:-0}" == "1" ]]; then
    (
        # Stagger first push to avoid hammering OSS right at boot — let
        # the WebUI settle, then start the cadence.
        sleep 30
        while true; do
            sleep "${PUSH_INTERVAL}"
            # Re-check WebUI still alive before pushing — if it died,
            # the trap will fire on the next signal; meanwhile keep
            # the volume backed up.
            bash "${SYNC_SCRIPT}" push \
                || log "periodic push failed (will retry in ${PUSH_INTERVAL}s)"
        done
    ) &
    SYNC_PID=$!
    log "background sync loop PID=${SYNC_PID} (interval=${PUSH_INTERVAL}s)"
fi

# ── Wait for webui to finish ────────────────────────────────────────────────
# The `wait` returns when:
#   a) webui exits normally (clean shutdown via /api/admin/shutdown)
#   b) SIGTERM arrives and the trap fires (exits via cleanup)
#   c) webui crashes (non-zero exit code → we propagate it)
wait "${WEBUI_PID}"
EXIT_CODE=$?

# Reaching here means webui exited without an external signal (case a or c).
# Stop background sync + final push.
if [[ -n "${SYNC_PID}" ]] && kill -0 "${SYNC_PID}" 2>/dev/null; then
    kill -TERM "${SYNC_PID}" 2>/dev/null || true
    wait "${SYNC_PID}" 2>/dev/null || true
fi
if [[ "${OSS_SYNC_ENABLED:-0}" == "1" ]]; then
    log "webui exited (code=${EXIT_CODE}); final push..."
    bash "${SYNC_SCRIPT}" push --final || true
fi

log "exiting (code=${EXIT_CODE})"
exit "${EXIT_CODE}"
