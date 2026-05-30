#!/usr/bin/env bash
# One-time migration: retrofit an EXISTING cloud ECS onto the graceful-update
# setup (hourly pull-only + apply-watcher + control channel). New instances get
# this from cloud-init; this script upgrades the fleet created before the
# feature shipped.
#
# Run as root on the instance (via SSH or Aliyun RunCommand):
#   curl -fsSL <raw>/docker/migrate-fleet-graceful-update.sh | sudo bash
#
# IDEMPOTENT + SAFE:
#   • Edits the EXISTING docker-compose.yml IN PLACE (only inserts the control
#     mount). It never replaces the compose from a template — cloud-init hosts
#     bind /var/lib/hermes-state while template hosts use a named volume, and
#     swapping that would orphan user state.
#   • Backs up the compose and validates with `docker compose config` before
#     applying; rolls back on any failure.
set -uo pipefail

DEPLOY_DIR=/opt/hermes-docker
TEMPLATE_BASE="${HERMES_TEMPLATE_BASE:-https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker}"
COMPOSE="$DEPLOY_DIR/docker-compose.yml"
LOG=/var/log/hermes-update.log

say() { echo "[migrate] $*"; }
die() { echo "[migrate] FATAL: $*" >&2; exit 1; }

[ -d "$DEPLOY_DIR" ] || die "$DEPLOY_DIR not found — not a Hermes docker host?"
[ -f "$COMPOSE" ]   || die "$COMPOSE not found"
cd "$DEPLOY_DIR"

# ── 1. apply-watcher script + control dir ────────────────────────────────────
say "installing apply-update.sh + control dir..."
curl -fsSL --max-time 20 "${TEMPLATE_BASE}/apply-update.sh" -o "$DEPLOY_DIR/apply-update.sh.new" \
  && [ -s "$DEPLOY_DIR/apply-update.sh.new" ] \
  || die "failed to download apply-update.sh"
mv -f "$DEPLOY_DIR/apply-update.sh.new" "$DEPLOY_DIR/apply-update.sh"
chmod 0755 "$DEPLOY_DIR/apply-update.sh"
mkdir -p "$DEPLOY_DIR/control"
chown 1500:999 "$DEPLOY_DIR/control" 2>/dev/null || chmod 0777 "$DEPLOY_DIR/control"

# ── 2. Add the control bind mount to the EXISTING compose (additive) ─────────
if grep -q '/opt/hermes/control' "$COMPOSE"; then
  say "control mount already present in compose — skipping edit"
else
  say "adding control bind mount to docker-compose.yml..."
  cp -f "$COMPOSE" "$COMPOSE.bak.$(date +%s)"
  # Insert the control mount immediately after the webui state-volume line
  # (matching that line's indentation). The `:/opt/hermes/.hermes` mount is
  # unique to the webui service in both compose variants.
  awk '
    !done && $0 ~ /:\/opt\/hermes\/\.hermes([[:space:]]|$)/ {
      print
      match($0, /^[[:space:]]*-[[:space:]]*/)
      indent = substr($0, 1, RLENGTH)
      print indent "/opt/hermes-docker/control:/opt/hermes/control"
      done = 1
      next
    }
    { print }
  ' "$COMPOSE" > "$COMPOSE.tmp"
  if ! grep -q '/opt/hermes/control' "$COMPOSE.tmp"; then
    rm -f "$COMPOSE.tmp"; die "could not locate the webui volumes anchor — aborting (compose untouched)"
  fi
  mv -f "$COMPOSE.tmp" "$COMPOSE"
  # Validate; roll back if the edit produced an invalid compose.
  if ! docker compose config -q >/dev/null 2>&1; then
    latest_bak=$(ls -t "$COMPOSE".bak.* 2>/dev/null | head -1)
    [ -n "$latest_bak" ] && cp -f "$latest_bak" "$COMPOSE"
    die "compose validation failed after edit — rolled back"
  fi
fi

# ── 3. Crons: hourly pull-only + */2 apply-watcher ───────────────────────────
say "rewriting cron (pull-only + apply-watcher)..."
cat > /etc/cron.d/hermes-auto-update <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

0 * * * * root cd /opt/hermes-docker && echo "=== $(date -Iseconds) pull ===" >> /var/log/hermes-update.log && /usr/bin/docker compose pull >> /var/log/hermes-update.log 2>&1
EOF
chmod 0644 /etc/cron.d/hermes-auto-update
cat > /etc/cron.d/hermes-apply-update <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

*/2 * * * * root /opt/hermes-docker/apply-update.sh >> /var/log/hermes-update.log 2>&1
EOF
chmod 0644 /etc/cron.d/hermes-apply-update
systemctl reload cron 2>/dev/null || systemctl restart cron 2>/dev/null || true
touch "$LOG"; chmod 0644 "$LOG"

# ── 4. One-time apply: pull latest + recreate with the new compose ───────────
# This is the LAST blind restart — it lands the control mount + the latest
# WebUI image. After this, updates are graceful (idle-auto / user-confirmed).
say "pulling latest + recreating once (final blind restart)..."
docker compose pull  >> "$LOG" 2>&1 || true
docker compose up -d >> "$LOG" 2>&1 || die "docker compose up -d failed — check $LOG"

say "done — graceful-update is now active on this instance."
