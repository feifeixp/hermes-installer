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
# Auto-elevate when possible. Two scenarios:
#   1. Running from a saved file path → simple `exec sudo bash "$0" "$@"`.
#   2. Piped from `curl ... | bash` → $0 is the shell name "bash", not
#      a file we can re-exec. Re-fetch the script from a known URL and
#      pipe THAT through sudo bash. Idiomatic for curl-pipe installers.
#
# The re-fetch URL defaults to upstream main, but can be overridden via
# HERMES_BOOTSTRAP_URL when running a fork or pinning to a tag.
HERMES_BOOTSTRAP_URL="${HERMES_BOOTSTRAP_URL:-https://raw.githubusercontent.com/feifeixp/hermes-installer/main/deploy/cloud/bootstrap-cloud.sh}"

if [[ $EUID -ne 0 ]]; then
    if [[ -f "$0" ]]; then
        echo "→ Re-running with sudo (script path: $0)..."
        exec sudo -E bash "$0" "$@"
    fi
    # curl-pipe path: re-fetch + re-pipe through sudo bash.
    if ! command -v curl >/dev/null 2>&1; then
        cat >&2 <<EOF
ERROR: This script needs root privileges and curl is not available
to re-fetch itself for sudo. Either:

  • Run as root from the start:
      sudo -i
      curl -fsSL ${HERMES_BOOTSTRAP_URL} | bash -s -- ${DOMAIN}

  • Or download manually then sudo:
      wget ${HERMES_BOOTSTRAP_URL}
      sudo bash bootstrap-cloud.sh ${DOMAIN}
EOF
        exit 1
    fi
    echo "→ Re-running with sudo (re-fetching from ${HERMES_BOOTSTRAP_URL})..."
    # `bash -c "<script>" PROGNAME ARGS...` — PROGNAME is consumed as
    # \$0 inside the new bash, ARGS become \$1..\$N. We pass "bootstrap-
    # cloud" as the friendly progname so error messages don't say "bash".
    SCRIPT_BODY="$(curl -fsSL "$HERMES_BOOTSTRAP_URL")" || {
        echo "ERROR: Could not re-fetch ${HERMES_BOOTSTRAP_URL}" >&2
        exit 1
    }
    exec sudo -E bash -c "$SCRIPT_BODY" bootstrap-cloud "$@"
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
#
# Caddy install is the #1 fragile step on China cloud servers:
#   • Cloudsmith: SSL handshake often fails (common from CN networks)
#   • api.github.com: rate-limited or blocked, hangs without timeout
#   • github.com release downloads: slow / TCP RST
#
# Strategy: hard `--max-time` on every curl so we never hang. Try
# 4 sources in order, take whoever responds first:
#   1. Cloudsmith (official; works in EU/US)
#   2. github.com direct release (works in US/most of world)
#   3. ghproxy.com mirror (China-friendly GitHub proxy)
#   4. mirror.ghproxy.com (alt China-friendly mirror)
# Pin to a known version so we don't depend on api.github.com.
CADDY_VER_PIN="2.8.4"

# Helper: install Caddy from a tar.gz URL. Returns 0 on success.
_try_caddy_url() {
    local url="$1"
    local arch="$2"
    rm -f /tmp/caddy.tar.gz
    if ! curl -fsSL --max-time 60 --retry 1 "$url" -o /tmp/caddy.tar.gz 2>/dev/null; then
        return 1
    fi
    if ! tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin/ caddy 2>/dev/null; then
        rm -f /tmp/caddy.tar.gz
        return 1
    fi
    chmod +x /usr/local/bin/caddy
    rm -f /tmp/caddy.tar.gz
    /usr/local/bin/caddy version >/dev/null 2>&1
}

if ! command -v caddy &>/dev/null; then
    echo "→ Installing Caddy..."
    CADDY_OK=false

    # ── Try 1: Cloudsmith apt repo (official) ─────────────────────────
    # Remove any stale keyring file FIRST so gpg --dearmor doesn't prompt
    # to overwrite (which hangs the script).
    rm -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    if curl -fsSL --max-time 15 \
        https://dl.cloudsmith.io/public/caddy/stable/gpg.key 2>/dev/null \
        | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null; then

        if curl -fsSL --max-time 15 \
            https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
            > /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null; then

            if apt-get update -qq 2>/dev/null && apt-get install -y -qq caddy 2>/dev/null; then
                CADDY_OK=true
                echo "  → Caddy installed via Cloudsmith"
            fi
        fi
    fi

    # ── Try 2/3/4: tar.gz binaries from various hosts ─────────────────
    if [[ "$CADDY_OK" != "true" ]]; then
        ARCH=$(uname -m)
        [[ "$ARCH" == "x86_64" ]] && ARCH="amd64"
        [[ "$ARCH" == "aarch64" ]] && ARCH="arm64"

        # GitHub release path stays the same for all mirrors; only the
        # host changes. We pin CADDY_VER_PIN so we don't have to query
        # api.github.com (which is rate-limited from CN).
        REL_PATH="caddyserver/caddy/releases/download/v${CADDY_VER_PIN}/caddy_${CADDY_VER_PIN}_linux_${ARCH}.tar.gz"

        for HOST_PREFIX in \
            "https://github.com/" \
            "https://ghproxy.com/https://github.com/" \
            "https://mirror.ghproxy.com/https://github.com/" \
        ; do
            URL="${HOST_PREFIX}${REL_PATH}"
            echo "  → Trying: $URL"
            if _try_caddy_url "$URL" "$ARCH"; then
                CADDY_OK=true
                echo "  → Caddy installed from $HOST_PREFIX"
                break
            fi
        done

        # systemd unit (only needed for binary install — apt path bundles it)
        if [[ "$CADDY_OK" == "true" ]]; then
            groupadd --system caddy 2>/dev/null || true
            useradd --system --gid caddy --create-home \
                --home-dir /var/lib/caddy --shell /usr/sbin/nologin \
                --comment "Caddy web server" caddy 2>/dev/null || true

            # Try to fetch the official caddy.service unit. If we can't,
            # write a minimal one inline — this should never block install.
            if ! curl -fsSL --max-time 15 \
                "https://ghproxy.com/https://raw.githubusercontent.com/caddyserver/dist/master/init/caddy.service" \
                -o /etc/systemd/system/caddy.service 2>/dev/null; then
                cat > /etc/systemd/system/caddy.service <<'CADDY_UNIT'
[Unit]
Description=Caddy
After=network.target

[Service]
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
CADDY_UNIT
            fi
            mkdir -p /etc/caddy
            systemctl daemon-reload
            systemctl enable caddy 2>/dev/null || true
        fi
    fi

    if [[ "$CADDY_OK" != "true" ]]; then
        cat <<'EOF'
ERROR: All Caddy install methods failed. Manual install:

  ssh into this server and run:

    curl -fsSL --max-time 60 \
      "https://ghproxy.com/https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_linux_amd64.tar.gz" \
      -o /tmp/caddy.tar.gz
    sudo tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin/ caddy
    sudo chmod +x /usr/local/bin/caddy

  Then re-run this script.
EOF
        exit 1
    fi
    echo "  ✓ Caddy installed: $(caddy version | head -1)"
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
