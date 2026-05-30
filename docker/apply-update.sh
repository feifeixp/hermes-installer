#!/usr/bin/env bash
# Cloud graceful-update apply-watcher.
#
# /etc/cron.d/hermes-apply-update runs this every 2 minutes. It recreates the
# hermes-webui container to a newer (already-pulled) image ONLY when it's safe:
#   • the user clicked「立即更新」  → control/apply-requested exists, OR
#   • the instance is idle         → control/activity.json says not-busy and
#                                     quiet for >= IDLE_SECS.
# The hourly cron now only `docker compose pull`s (stages the image); this
# script decides WHEN to actually `up -d`. Decision mirrors
# webui/api/self_update.py::should_apply() — keep them in sync.
#
# Control dir (bind-mounted into the container at /opt/hermes/control):
#   control/activity.json     container→host  {"ts":<unix>,"busy":<bool>}
#   control/apply-requested   container→host  touch — user clicked立即更新
#   control/update-available  host→container  {"image":...} — staged, drives banner
set -uo pipefail

DEPLOY_DIR=/opt/hermes-docker
CONTROL_DIR="$DEPLOY_DIR/control"
LOG=/var/log/hermes-update.log
IDLE_SECS=600
WEBUI=hermes-webui

log() { echo "[$(date -Iseconds)] apply-watcher: $*" >> "$LOG"; }

cd "$DEPLOY_DIR" 2>/dev/null || exit 0
mkdir -p "$CONTROL_DIR" 2>/dev/null || true

# Image id of the running container vs the locally-pulled image for the service.
running_img=$(docker inspect -f '{{.Image}}' "$WEBUI" 2>/dev/null || true)
latest_img=$(docker compose images -q "$WEBUI" 2>/dev/null | head -1)
if [ -z "${latest_img:-}" ]; then
  ref=$(docker compose config --images 2>/dev/null | grep -i webui | head -1)
  [ -n "${ref:-}" ] && latest_img=$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null || true)
fi

# No staged update (unknown ids, or running == latest) → clear banner, done.
if [ -z "${running_img:-}" ] || [ -z "${latest_img:-}" ] || [ "$running_img" = "$latest_img" ]; then
  rm -f "$CONTROL_DIR/update-available" 2>/dev/null || true
  exit 0
fi

# A newer image is staged → tell the WebUI to show the「立即更新」banner.
printf '{"image":"%s"}\n' "${latest_img#sha256:}" > "$CONTROL_DIR/update-available" 2>/dev/null || true

apply=0
if [ -f "$CONTROL_DIR/apply-requested" ]; then
  apply=1
  log "user requested immediate update"
elif [ -f "$CONTROL_DIR/activity.json" ]; then
  busy=$(grep -oiE '"busy"[[:space:]]*:[[:space:]]*(true|false)' "$CONTROL_DIR/activity.json" | grep -oiE 'true|false' | head -1)
  ts=$(grep -oE '"ts"[[:space:]]*:[[:space:]]*[0-9]+' "$CONTROL_DIR/activity.json" | grep -oE '[0-9]+' | head -1)
  busy=${busy:-true}; ts=${ts:-0}
  now=$(date +%s)
  if [ "$busy" = "false" ] && [ $((now - ts)) -ge "$IDLE_SECS" ]; then
    apply=1
    log "idle $((now - ts))s >= ${IDLE_SECS}s — auto-updating"
  fi
fi
# Missing activity.json → conservative: do NOT auto-apply (matches should_apply).

[ "$apply" -eq 1 ] || exit 0

log "applying update: ${running_img} -> ${latest_img}"
if docker compose pull >> "$LOG" 2>&1 && docker compose up -d >> "$LOG" 2>&1; then
  rm -f "$CONTROL_DIR/apply-requested" "$CONTROL_DIR/update-available" 2>/dev/null || true
  log "update applied OK"
else
  log "update FAILED — will retry next tick"
fi
