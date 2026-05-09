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
#
# We can't auto-elevate via `exec sudo bash "$0" "$@"` reliably:
# when the script is piped from curl (the canonical install path
# documented in CLOUD_DEPLOY.md), `$0` is the string "bash" (the
# shell name, not a file path), so the re-exec becomes `sudo bash
# bash chat.neowow.studio` which errors with "cannot execute binary
# file". Instead we tell the user how to retry — explicit and
# debuggable.
if [[ $EUID -ne 0 ]]; then
    cat >&2 <<EOF
ERROR: This script needs root privileges.

If you piped it from curl, put sudo BEFORE the pipe:

  curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/deploy/cloud/bootstrap-cloud.sh \\
    | sudo bash -s -- $DOMAIN

Or download first, then run:

  curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/deploy/cloud/bootstrap-cloud.sh -o bootstrap-cloud.sh
  sudo bash bootstrap-cloud.sh $DOMAIN

Or run it directly as root:

  sudo -i
  curl -fsSL https://... | bash -s -- $DOMAIN

EOF
    exit 1
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

# ── 5. Install systemd unit ──────────────────────────────────────────
# We DON'T pre-run start.sh manually. The unit file invokes it on
# first start; bootstrap.py inside detects missing hermes-agent and
# installs it (clone + venv + uv pip install). This way the install
# runs as the `hermes` user with the right systemd environment, and
# we get a single observable progress source (journalctl) instead of
# split between stdout and the systemd journal.
echo "→ Installing systemd unit..."
cp "$INSTALL_DIR/deploy/cloud/hermes-webui.service" /etc/systemd/system/hermes-webui.service
systemctl daemon-reload
systemctl enable hermes-webui

# ── 6. Install Caddyfile ─────────────────────────────────────────────
echo "→ Writing /etc/caddy/Caddyfile (substituting domain $DOMAIN)..."
sed "s|chat.neowow.studio|$DOMAIN|g" "$INSTALL_DIR/deploy/cloud/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl reload caddy

# ── 7. Start services ────────────────────────────────────────────────
echo "→ Starting hermes-webui (FIRST start triggers install — 5-10 min)..."
systemctl start hermes-webui

# ── 8. Health check ──────────────────────────────────────────────────
# First-boot install includes git clone of hermes-agent (~50 MB), uv
# venv creation, and `uv pip install -e .[all]` (~700 MB of Python
# wheels). On a clean ecs.t6 this realistically takes 5-10 minutes.
# We poll for up to 15 min and emit progress so the user doesn't think
# we hung.
echo "→ Waiting for WebUI to become healthy (up to 15 min)..."
HEALTH_OK=false
for i in {1..90}; do
    if curl -fsS http://127.0.0.1:7891/health >/dev/null 2>&1; then
        HEALTH_OK=true
        echo "  ✓ WebUI is up!"
        break
    fi
    # Every 30 s, peek at the journal so the user sees something
    # is actually happening (vs. silent hang).
    if (( i % 3 == 0 )); then
        echo "  ... still installing (${i}0 s elapsed); recent log:"
        journalctl -u hermes-webui --no-pager -n 1 --since "5 sec ago" 2>/dev/null \
            | sed 's/^/      /' || true
    fi
    sleep 10
done

if [[ "$HEALTH_OK" != "true" ]]; then
    cat <<EOF
  ✗ WebUI did not become healthy after 15 minutes.

  Diagnostic commands:
    sudo systemctl status hermes-webui
    sudo journalctl -u hermes-webui --no-pager -n 200

  Common causes:
    • Slow disk / network — bump ECS spec or wait longer; install may still complete
    • Out of memory — the agent venv install needs ~1 GB peak. Check free -m
    • git clone failed — check ECS can reach github.com
    • uv install failed — check ECS can reach pypi.org
EOF
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
