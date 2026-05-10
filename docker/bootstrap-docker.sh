#!/usr/bin/env bash
# bootstrap-docker.sh — deploy chat.neowow.studio via Docker Compose.
# ==============================================================================
# Replaces the old bare-metal bootstrap-cloud.sh (deleted alongside this
# commit). Significantly simpler because the heavy lifting (installing
# Hermes Agent + Python venv + ~700 MB of wheels) is baked into the
# Docker image, not done at deploy time.
#
# Usage (as root or with sudo):
#
#   curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/bootstrap-docker.sh \
#     | sudo bash -s -- chat.example.com [acme-email@example.com]
#
# Or after cloning the repo:
#
#   sudo bash docker/bootstrap-docker.sh chat.example.com
#
# What it does:
#   1. Installs Docker + docker-compose-plugin if missing
#   2. Picks the closest registry (Aliyun ACR for CN, ghcr.io for elsewhere)
#   3. Writes /opt/hermes-docker/{docker-compose.yml, Caddyfile} with your domain
#   4. Pulls images and starts the stack
#   5. Waits for both services to become healthy
#
# Idempotent: re-running on the same machine pulls latest images and
# recreates containers. State volumes (sessions, certs) persist.
# ==============================================================================

set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-admin@${DOMAIN}}"

if [[ -z "$DOMAIN" ]]; then
    cat >&2 <<'EOF'
usage: bootstrap-docker.sh <domain> [acme-email]

  domain      e.g. chat.neowow.studio. DNS A record must point at this server.
  acme-email  optional; for LetsEncrypt account registration. Defaults to
              admin@<domain>.

example:
  curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/bootstrap-docker.sh \\
    | sudo bash -s -- chat.example.com you@example.com
EOF
    exit 1
fi

# ── Auto-elevate ──────────────────────────────────────────────────────────────
HERMES_BOOTSTRAP_URL="${HERMES_BOOTSTRAP_URL:-https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/bootstrap-docker.sh}"
if [[ $EUID -ne 0 ]]; then
    if [[ -f "$0" ]]; then
        echo "→ Re-running with sudo (script: $0)..."
        exec sudo -E bash "$0" "$@"
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "ERROR: curl not found. Install curl first or run as root." >&2
        exit 1
    fi
    echo "→ Re-running with sudo (re-fetching ${HERMES_BOOTSTRAP_URL})..."
    SCRIPT_BODY="$(curl -fsSL "$HERMES_BOOTSTRAP_URL")" || {
        echo "ERROR: Could not re-fetch ${HERMES_BOOTSTRAP_URL}" >&2
        exit 1
    }
    exec sudo -E bash -c "$SCRIPT_BODY" bootstrap-docker "$@"
fi

# ── Install Docker ────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "→ Installing Docker via get.docker.com..."
    # The official one-liner. Works on Debian / Ubuntu / CentOS / Fedora /
    # Raspbian. From CN networks it can be slow but doesn't usually fail
    # outright (Docker's CDN has good China presence).
    if ! curl -fsSL --max-time 60 https://get.docker.com -o /tmp/get-docker.sh; then
        cat >&2 <<'EOF'
ERROR: Failed to download Docker installer.

Manual install:
  apt update && apt install -y docker.io docker-compose-plugin
Or visit https://docs.docker.com/engine/install/ for distro-specific steps.

Then re-run this script.
EOF
        exit 1
    fi
    sh /tmp/get-docker.sh
    rm -f /tmp/get-docker.sh
    systemctl enable --now docker
else
    echo "→ Docker already installed: $(docker --version)"
fi

# ── Verify docker compose v2 plugin ───────────────────────────────────────────
if ! docker compose version >/dev/null 2>&1; then
    echo "→ Installing docker compose plugin..."
    apt-get update -qq && apt-get install -y -qq docker-compose-plugin
fi

# ── Pick registry ─────────────────────────────────────────────────────────────
# Aliyun ACR is fastest from China; ghcr.io is fastest internationally.
# Detect via a TCP connectivity test to ghcr.io with a tight timeout —
# CN networks usually fail this fast.
DEFAULT_REGISTRY="ghcr.io/feifeixp"
if [[ -n "${HERMES_REGISTRY:-}" ]]; then
    REGISTRY="$HERMES_REGISTRY"
    echo "→ Using registry from \$HERMES_REGISTRY: $REGISTRY"
elif curl -fsS --max-time 5 -o /dev/null https://ghcr.io 2>/dev/null; then
    REGISTRY="$DEFAULT_REGISTRY"
    echo "→ Using ghcr.io (international)"
else
    REGISTRY="registry.cn-shanghai.aliyuncs.com/neowow"
    echo "→ ghcr.io unreachable, falling back to Aliyun ACR (China)"
fi

IMAGE_TAG="${HERMES_IMAGE_TAG:-latest}"
HERMES_WEBUI_IMAGE="${REGISTRY}/hermes-webui:${IMAGE_TAG}"

# ── Set up deployment directory ───────────────────────────────────────────────
DEPLOY_DIR="/opt/hermes-docker"
mkdir -p "$DEPLOY_DIR"
cd "$DEPLOY_DIR"

# Fetch templates from the repo. Could git-clone but two file fetches
# is leaner.
TEMPLATE_BASE="${HERMES_TEMPLATE_BASE:-https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker}"

echo "→ Writing docker-compose.yml..."
curl -fsSL --max-time 15 "${TEMPLATE_BASE}/docker-compose.yml.template" -o docker-compose.yml
echo "→ Writing Caddyfile..."
curl -fsSL --max-time 15 "${TEMPLATE_BASE}/Caddyfile.template" -o Caddyfile

# Substitute placeholders
sed -i "s|%DOMAIN%|${DOMAIN}|g" Caddyfile
sed -i "s|%ACME_EMAIL%|${ACME_EMAIL}|g" Caddyfile

# Stamp the resolved image into the compose file as an override env file —
# cleaner than sed-editing docker-compose.yml directly.
cat > .env <<EOF
# Generated by bootstrap-docker.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
HERMES_WEBUI_IMAGE=${HERMES_WEBUI_IMAGE}
EOF

# ── Pull and start ────────────────────────────────────────────────────────────
echo "→ Pulling images (${HERMES_WEBUI_IMAGE})..."
echo "  This is the only big network step — typically 1-3 min from CN, 30 sec from US."
docker compose pull

echo "→ Starting services..."
docker compose up -d --remove-orphans

# ── Health check ──────────────────────────────────────────────────────────────
echo "→ Waiting for services to become healthy..."
for i in {1..60}; do
    if [[ "$(docker compose ps --format json hermes-webui 2>/dev/null | grep -c '"Health":"healthy"')" -ge 1 ]] \
       || curl -fsS --max-time 3 http://127.0.0.1:80/ -H "Host: ${DOMAIN}" -o /dev/null 2>&1; then
        echo "  ✓ hermes-webui is healthy"
        break
    fi
    if (( i % 6 == 0 )); then
        echo "  ... still waiting (${i}0 s); recent logs:"
        docker compose logs --tail=3 hermes-webui 2>&1 | sed 's/^/      /' | tail -5
    fi
    sleep 5
done

if ! docker compose ps --format json hermes-webui 2>/dev/null | grep -q '"State":"running"'; then
    cat <<EOF
✗ hermes-webui failed to start.

Diagnostic commands:
  cd ${DEPLOY_DIR}
  docker compose ps
  docker compose logs hermes-webui --tail 100
  docker compose logs caddy --tail 50
EOF
    exit 1
fi

# ── Done ──────────────────────────────────────────────────────────────────────
cat <<EOF

════════════════════════════════════════════════════════════════════
  ✓ Cloud Hermes WebUI deployment complete (Docker Compose)
════════════════════════════════════════════════════════════════════

  Domain:          https://${DOMAIN}
  Compose dir:     ${DEPLOY_DIR}
  Image:           ${HERMES_WEBUI_IMAGE}

  Quick checks:
    docker compose ps                    # service state
    docker compose logs -f hermes-webui  # follow WebUI logs
    docker compose logs -f caddy         # follow Caddy / TLS logs

  Maintenance:
    docker compose pull && \\
      docker compose up -d               # update to latest image

  Hard-reset state (DESTRUCTIVE — clears sessions/certs):
    docker compose down -v

  Auth mode: Neodomain OAuth via .neowow.studio cookie.

  Next:
    1. Confirm DNS for ${DOMAIN} points at this server's public IP
    2. Confirm cloud security group / firewall opens 80, 443 inbound
    3. Open https://${DOMAIN} in a browser to test the OAuth flow
EOF
