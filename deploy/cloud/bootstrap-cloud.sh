#!/usr/bin/env bash
# bootstrap-cloud.sh — install Hermes WebUI on a fresh Linux server.
#
# Usage (as root, on Debian/Ubuntu):
#
#   curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/deploy/cloud/bootstrap-cloud.sh | bash -s -- chat.neowow.studio
#
# Or, after cloning hermes-installer manually:
#
#   sudo bash deploy/cloud/bootstrap-cloud.sh chat.neowow.studio
#
# What it does:
#   1. Creates an unprivileged `hermes` user under /opt/hermes
#   2. Clones hermes-installer to /opt/hermes/hermes-installer (idempotent)
#   3. Runs webui/start.sh once to install Hermes Agent + venv
#   4. Installs the systemd unit (deploy/cloud/hermes-webui.service)
#   5. Installs Caddy + writes /etc/caddy/Caddyfile from the template
#   6. Enables + starts both services
#
# Idempotent: re-running won't break an existing install. Use
# `--force-reinstall` to wipe and start fresh (rare).

set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    echo "usage: $0 <domain>   e.g. chat.neowow.studio"
    exit 1
fi

# Must run as root (writes to /etc/, /opt/, creates user).
if [[ $EUID -ne 0 ]]; then
    echo "Re-running with sudo..."
    exec sudo -E bash "$0" "$@"
fi

# ── 1. Detect distro ─────────────────────────────────────────────────
if ! command -v apt-get &>/dev/null; then
    echo "This script assumes Debian/Ubuntu (apt). For other distros, install"
    echo "the deps manually and copy the systemd unit + Caddyfile by hand."
    exit 1
fi

echo "→ Updating apt index..."
apt-get update -qq
apt-get install -y -qq curl ca-certificates git debian-keyring debian-archive-keyring apt-transport-https

# ── 2. Install Caddy (if not present) ────────────────────────────────
if ! command -v caddy &>/dev/null; then
    echo "→ Installing Caddy..."
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
else
    echo "→ Caddy already installed: $(caddy version | head -1)"
fi

# ── 3. Create hermes user ────────────────────────────────────────────
if ! id hermes &>/dev/null; then
    echo "→ Creating hermes user..."
    useradd -r -m -d /opt/hermes -s /bin/bash hermes
fi

# ── 4. Clone or update hermes-installer ──────────────────────────────
INSTALL_DIR="/opt/hermes/hermes-installer"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "→ Updating existing hermes-installer checkout..."
    sudo -u hermes git -C "$INSTALL_DIR" fetch origin
    sudo -u hermes git -C "$INSTALL_DIR" reset --hard origin/main
else
    echo "→ Cloning hermes-installer to $INSTALL_DIR..."
    sudo -u hermes git clone https://github.com/feifeixp/hermes-installer.git "$INSTALL_DIR"
fi

# ── 5. Install Python deps + Hermes Agent ────────────────────────────
echo "→ Running webui/start.sh once to install Hermes Agent + venv (this may take 5-10 minutes)..."
echo "  (subsequent restarts will reuse the venv)"
sudo -u hermes bash -c "cd $INSTALL_DIR && bash webui/start.sh --foreground &"
START_PID=$!
sleep 60
if ! kill -0 $START_PID 2>/dev/null; then
    echo "  start.sh exited early — check $INSTALL_DIR for errors"
    exit 1
fi
# Stop the foreground server; systemd will take over.
kill $START_PID 2>/dev/null || true
wait $START_PID 2>/dev/null || true

# ── 6. Install systemd unit ──────────────────────────────────────────
echo "→ Installing systemd unit..."
cp "$INSTALL_DIR/deploy/cloud/hermes-webui.service" /etc/systemd/system/hermes-webui.service
systemctl daemon-reload
systemctl enable hermes-webui

# ── 7. Install Caddyfile ─────────────────────────────────────────────
echo "→ Writing /etc/caddy/Caddyfile (substituting domain $DOMAIN)..."
sed "s|chat.neowow.studio|$DOMAIN|g" "$INSTALL_DIR/deploy/cloud/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl reload caddy

# ── 8. Start services ────────────────────────────────────────────────
echo "→ Starting hermes-webui..."
systemctl start hermes-webui

# ── 9. Health check ──────────────────────────────────────────────────
echo "→ Waiting for WebUI to become healthy (up to 60 s)..."
for i in {1..30}; do
    if curl -fsS http://127.0.0.1:7891/health >/dev/null 2>&1; then
        echo "  ✓ WebUI is up!"
        break
    fi
    sleep 2
done

if ! curl -fsS http://127.0.0.1:7891/health >/dev/null 2>&1; then
    echo "  ✗ WebUI did not become healthy. Check: journalctl -u hermes-webui -e"
    exit 1
fi

echo
echo "════════════════════════════════════════════════════════════════════"
echo "  ✓ Cloud Hermes WebUI deployment complete"
echo "════════════════════════════════════════════════════════════════════"
echo
echo "  Domain:          https://$DOMAIN"
echo "  Status:          systemctl status hermes-webui"
echo "  Logs:            journalctl -u hermes-webui -f"
echo "  Restart:         systemctl restart hermes-webui"
echo "  Update code:     cd $INSTALL_DIR && git pull && systemctl restart hermes-webui"
echo
echo "  Auth mode:       Neodomain OAuth (via app.neowow.studio cookie)"
echo
echo "  Next steps:"
echo "    1. Make sure DNS for $DOMAIN points at this server"
echo "    2. Open https://$DOMAIN in a browser"
echo "    3. You'll be redirected to app.neowow.studio for login"
echo "    4. After login, you land back at https://$DOMAIN authenticated"
echo
