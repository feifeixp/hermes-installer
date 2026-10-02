#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# provision.sh — heavy lifting for a Phase 2 per-user Hermes ECS instance.
#
# This used to live inline in docker/cloud-init.yaml.template (write_files +
# runcmd). It was moved out because the fully-rendered cloud-config grew past
# Aliyun's UserData limit: RunInstances accepts UserData as a Base64 string
# capped at 16 KB (~12 KB raw). Cumulative feature growth plus an unusually
# long injected secret tipped it over → "Could not prepare cloud-init" / 502.
#
# cloud-init now writes only the per-spawn secrets to /etc/hermes/spawn.env and
# curls + runs this script. Keeping the bulk here (fetched from main at boot,
# same pattern as oss-sync.sh / apply-update.sh) keeps UserData tiny no matter
# how much provisioning logic accumulates.
#
# Contract:
#   • /etc/hermes/spawn.env exists and defines the vars sourced below.
#   • Runs as root, once, from cloud-init's runcmd (first boot only). Subsequent
#     boots skip this — Docker (systemctl enable) + `restart: unless-stopped`
#     containers + the installed cron/systemd units carry ongoing behavior.
#
# Boot timeline (first run): ~4-5 min RunInstances → "serving traffic".
# Subsequent boots (stop/start): ~30 s — everything's already on disk.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

echo "=== hermes provision.sh starting $(date -Iseconds) ==="

# ── Load per-spawn secrets/config injected by cloud-init ─────────────────────
SPAWN_ENV="${SPAWN_ENV:-/etc/hermes/spawn.env}"
if [ ! -f "$SPAWN_ENV" ]; then
  echo "FATAL: $SPAWN_ENV not found — cloud-init should have written it." >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; . "$SPAWN_ENV"; set +a

# Defaults for optional/empty vars so `set -u` below doesn't trip on them.
: "${USER_ID:?spawn.env missing USER_ID}"
: "${DOMAIN:?spawn.env missing DOMAIN}"
: "${IMAGE:?spawn.env missing IMAGE}"
: "${ACME_EMAIL:=admin@neowow.studio}"
: "${ACR_PULL_REGISTRY:=}"
: "${ACR_PULL_USERNAME:=}"
: "${ACR_PULL_PASSWORD:=}"
: "${OSS_ACCESS_KEY_ID:=}"
: "${OSS_ACCESS_KEY_SECRET:=}"
: "${OSS_ENDPOINT:=}"
: "${OSS_BUCKET:=}"
: "${CLOUDFLARE_API_TOKEN:=}"
: "${HEARTBEAT_TOKEN:=}"

mkdir -p /opt/hermes-docker /etc/hermes

# ═════════════════════════════════════════════════════════════════════════════
# 1. Config files (formerly cloud-init write_files)
# ═════════════════════════════════════════════════════════════════════════════

# ── /opt/hermes-docker/docker-compose.yml ───────────────────────────────────
# Per-user changes vs Phase 1: HERMES_INSTANCE_OWNER_USERID (so WebUI rejects
# other users' JWTs) + this instance's subdomain in the Caddyfile.
# NOTE: \${CLOUDFLARE_API_TOKEN} stays literal — compose interpolates it from
# the .env file (written below). $IMAGE / $USER_ID / $HEARTBEAT_TOKEN are
# expanded here from spawn.env.
cat > /opt/hermes-docker/docker-compose.yml <<EOF
services:
  caddy:
    # slothcroissant/caddy-cloudflaredns is stock Caddy + the
    # github.com/caddy-dns/cloudflare plugin already compiled in. Needed
    # because LE HTTP-01/ALPN-01 challenges fail when *.neowow.studio's
    # wildcard CNAME (proxied through cf-proxy worker) intercepts validator
    # traffic → 503. DNS-01 sidesteps the HTTP path entirely.
    image: slothcroissant/caddy-cloudflaredns:latest
    container_name: hermes-caddy
    restart: unless-stopped
    environment:
      CLOUDFLARE_API_TOKEN: "\${CLOUDFLARE_API_TOKEN}"
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks: [hermes]
    depends_on:
      hermes-webui:
        condition: service_healthy

  hermes-webui:
    image: $IMAGE
    container_name: hermes-webui
    restart: unless-stopped
    environment:
      HERMES_WEBUI_AUTH_MODE: neodomain
      HERMES_WEBUI_HOST: 0.0.0.0
      HERMES_WEBUI_PORT: 7891
      HERMES_WEBUI_FOREGROUND: "1"
      HERMES_WEBUI_STATE_DIR: /opt/hermes/.hermes/webui
      # Per-instance owner. WebUI auth.py enforces JWT userId == this value.
      HERMES_INSTANCE_OWNER_USERID: "$USER_ID"
      # Phase β: lock onboarding to the Neowow Coding Plan card only, so
      # cloud instances always bill through our /api/me/chat proxy.
      HERMES_NEOWOW_ONLY: "1"
      # Server-side heartbeat token — lets the Hermes background thread ping
      # /api/me/instance/server-heartbeat while tasks run, preventing the
      # idle-sweep from stopping the instance when the browser is closed.
      NEOWOW_HEARTBEAT_TOKEN: "$HEARTBEAT_TOKEN"
      # Fast PyPI mirror — uv reads UV_INDEX_URL; Aliyun mirror is ~10× faster
      # from HK/Shanghai. UV_CACHE_DIR points at a named volume so wheels
      # survive container recreation.
      UV_INDEX_URL: "https://mirrors.aliyun.com/pypi/simple/"
      UV_EXTRA_INDEX_URL: "https://pypi.org/simple/"
      UV_CACHE_DIR: /uv_cache
    # Bind-mount (NOT named volume) so host-side oss-sync.sh reads/writes the
    # same bytes the WebUI sees. /var/lib/hermes-state is created in runcmd
    # before this container starts. hermes_venv / hermes_uv_cache are named
    # volumes that persist across hourly \`docker compose up -d\`.
    volumes:
      - /var/lib/hermes-state:/opt/hermes/.hermes
      - hermes_venv:/app/venv
      - hermes_uv_cache:/uv_cache
      - /opt/hermes-docker/control:/opt/hermes/control
    expose: ["7891"]
    networks: [hermes]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:7891/health"]
      interval: 10s
      timeout: 3s
      start_period: 30s
      retries: 5

volumes:
  caddy_data:      {}
  caddy_config:    {}
  hermes_venv:     {}
  hermes_uv_cache: {}

networks:
  hermes:
    driver: bridge
EOF
chmod 0644 /opt/hermes-docker/docker-compose.yml

# ── /opt/hermes-docker/Caddyfile ─────────────────────────────────────────────
# DNS-01 challenge via Cloudflare. {env.CLOUDFLARE_API_TOKEN} pulls from the
# compose env (interpolated from .env). Caddy {…} placeholders carry no $ so
# they stay literal in this unquoted heredoc; $ACME_EMAIL / $DOMAIN expand.
cat > /opt/hermes-docker/Caddyfile <<EOF
{
    email $ACME_EMAIL
}

$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
        resolvers 1.1.1.1
    }
    reverse_proxy hermes-webui:7891 {
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host  {host}
        flush_interval -1
    }
    request_body {
        max_size 50MB
    }
    log {
        output stdout
        format console
    }
}
EOF
chmod 0644 /opt/hermes-docker/Caddyfile

# ── /opt/hermes-docker/.env — compose interpolation source ───────────────────
# docker-compose.yml uses ${CLOUDFLARE_API_TOKEN}; compose reads this adjacent
# file. printf (not heredoc) so a token with shell metacharacters is written
# verbatim.
printf 'CLOUDFLARE_API_TOKEN=%s\n' "$CLOUDFLARE_API_TOKEN" > /opt/hermes-docker/.env
chmod 0600 /opt/hermes-docker/.env

# ── Hourly cron — STAGE new images only (pull). Recreate is done by the
# ── apply-watcher below, only when safe, so a new image never hard-restarts
# ── an active user mid-session.
cat > /etc/cron.d/hermes-auto-update <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

0 * * * * root cd /opt/hermes-docker && echo "=== $(date -Iseconds) pull ===" >> /var/log/hermes-update.log && /usr/bin/docker compose pull >> /var/log/hermes-update.log 2>&1
EOF
chmod 0644 /etc/cron.d/hermes-auto-update

# ── Apply-watcher cron — every 2 min, recreate to a staged image ONLY when
# ── safe. See /opt/hermes-docker/apply-update.sh (curl'd in runcmd).
cat > /etc/cron.d/hermes-apply-update <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

*/2 * * * * root /opt/hermes-docker/apply-update.sh >> /var/log/hermes-update.log 2>&1
EOF
chmod 0644 /etc/cron.d/hermes-apply-update

# ── M2: OSS state sync configuration. Read by oss-sync.sh. 0600 so other
# ── users on the VM (if any) can't read the creds.
cat > /etc/default/hermes-oss-sync <<EOF
OSS_ACCESS_KEY_ID="$OSS_ACCESS_KEY_ID"
OSS_ACCESS_KEY_SECRET="$OSS_ACCESS_KEY_SECRET"
OSS_ENDPOINT="$OSS_ENDPOINT"
OSS_BUCKET="$OSS_BUCKET"
OSS_PREFIX="users/$USER_ID/hermes"
LOCAL_PATH="/var/lib/hermes-state"
OSSUTIL="/usr/local/bin/ossutil"
EOF
chmod 0600 /etc/default/hermes-oss-sync

# ── M2: 5-minute push cron. Failures tolerated (next tick retries) so the
# ── sync never takes the instance down.
cat > /etc/cron.d/hermes-oss-sync <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

*/5 * * * * root /opt/hermes-docker/oss-sync.sh push > /dev/null 2>&1 || true
EOF
chmod 0644 /etc/cron.d/hermes-oss-sync

# ── M2: shutdown hook — push state one last time before halt. ExecStop runs
# ── synchronously during shutdown (60s budget; a typical sessions DB rsync is
# ── < 2s, so this is just paranoia).
cat > /etc/systemd/system/hermes-oss-shutdown.service <<'EOF'
[Unit]
Description=Push Hermes state to OSS before shutdown
DefaultDependencies=no
Before=shutdown.target poweroff.target halt.target reboot.target
RequiresMountsFor=/var/lib/hermes-state

[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/bin/true
ExecStop=/opt/hermes-docker/oss-sync.sh push
TimeoutStopSec=60s

[Install]
WantedBy=shutdown.target
EOF
chmod 0644 /etc/systemd/system/hermes-oss-shutdown.service

# ═════════════════════════════════════════════════════════════════════════════
# 2. Provisioning steps (formerly cloud-init runcmd)
# ═════════════════════════════════════════════════════════════════════════════

# ── Install Docker via APT (Aliyun's mirror). We avoid `curl get.docker.com |
# ── sh` because Aliyun's 云安全中心 flags the curl-pipe-shell pattern as a worm
# ── signature, triggering a security alert on every spawn. APT costs ~30 s more
# ── but eliminates the false-positive.
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc && echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://mirrors.aliyun.com/docker-ce/linux/ubuntu jammy stable' > /etc/apt/sources.list.d/docker.list

_docker_ok=false
for _try in 1 2 3; do
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && _docker_ok=true && break
  echo "Docker install attempt ${_try}/3 failed, waiting 15s..."
  sleep 15
done
$_docker_ok || { echo 'FATAL: Docker CE install failed after 3 attempts'; exit 1; }

systemctl enable --now docker
docker --version || { echo 'FATAL: docker binary not found after install'; exit 1; }

# ── M2: ossutil (single Go binary, ~20 MB, hosted on Aliyun CDN) + oss-sync.sh.
curl -fsSL --retry 3 https://gosspublic.alicdn.com/ossutil/v2/2.1.1/ossutil-2.1.1-linux-amd64.zip -o /tmp/ossutil.zip && cd /tmp && unzip -o ossutil.zip && cp -f ossutil-2.1.1-linux-amd64/ossutil /usr/local/bin/ossutil && chmod +x /usr/local/bin/ossutil
curl -fsSL --retry 3 https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/oss-sync.sh -o /opt/hermes-docker/oss-sync.sh && chmod +x /opt/hermes-docker/oss-sync.sh

# ── M2: probe OSS access (fail-soft) + pull existing state. We proceed even on
# ── verify failure — better to run without remote backup than to fail the spawn.
/opt/hermes-docker/oss-sync.sh verify || echo 'WARNING: OSS not accessible; sessions WILL NOT persist across destroy'
mkdir -p /var/lib/hermes-state
/opt/hermes-docker/oss-sync.sh pull

# ── Docker login to private ACR — only when ACR_PULL_* are set. Reads the
# ── password from spawn.env, so no sed/printf %% escaping footgun anymore;
# ── --password-stdin keeps it out of the process list / cloud-init log.
if [ -n "$ACR_PULL_REGISTRY" ] && [ -n "$ACR_PULL_USERNAME" ]; then
  printf '%s' "$ACR_PULL_PASSWORD" | docker login "$ACR_PULL_REGISTRY" -u "$ACR_PULL_USERNAME" --password-stdin
fi

# ── Pull images upfront (faster + clearer errors than letting compose do it).
docker pull "$IMAGE"
docker pull slothcroissant/caddy-cloudflaredns:latest

# ── Seed /var/lib/hermes-state from the image, only when empty.
# The image bakes hermes-agent into /opt/hermes/.hermes, but the bind mount
# (unlike a named volume) does NOT copy image contents in — it hides them. So
# we seed once. The `-z "$(ls -A …)"` guard keeps it idempotent: if oss-sync
# already pulled state, we don't clobber it. --user root so cp can write into
# the root-owned bind-mount target; the trailing chown re-owns to the detected
# hermes uid/gid (image uses 1500/999, not the typical 1000).
if [ -z "$(ls -A /var/lib/hermes-state 2>/dev/null)" ]; then
  echo 'Seeding /var/lib/hermes-state from image baseline...'
  docker run --rm --user root -v /var/lib/hermes-state:/dst "$IMAGE" bash -c 'UID_H=$(id -u hermes); GID_H=$(id -g hermes); cp -ar /opt/hermes/.hermes/. /dst/ && chown -R ${UID_H}:${GID_H} /dst && echo "chowned to ${UID_H}:${GID_H}"'
  echo 'Seed complete.'
else
  echo 'hermes-state already populated, skipping image seed.'
fi

# ── Graceful-update apply-watcher script + control dir. The control dir must
# ── exist and be writable by the container's hermes user (1500:999) BEFORE
# ── compose up, since it's bind-mounted into the WebUI.
curl -fsSL --retry 3 https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/apply-update.sh -o /opt/hermes-docker/apply-update.sh && chmod +x /opt/hermes-docker/apply-update.sh
mkdir -p /opt/hermes-docker/control && chown 1500:999 /opt/hermes-docker/control 2>/dev/null || chmod 0777 /opt/hermes-docker/control

# ── Bring the stack up. compose waits for hermes-webui health before Caddy.
cd /opt/hermes-docker && docker compose up -d

# ── Activate cron units + the shutdown hook.
systemctl reload cron
systemctl daemon-reload
systemctl enable hermes-oss-shutdown.service
systemctl start hermes-oss-shutdown.service

# ── Hygiene: secrets are now baked into the derived config files (.env,
# ── hermes-oss-sync, the container env). Shred the aggregate spawn.env so the
# ── full secret set doesn't linger in one place. runcmd runs once, so nothing
# ── re-reads it.
shred -u "$SPAWN_ENV" 2>/dev/null || rm -f "$SPAWN_ENV"

echo "=== hermes provision.sh done $(date -Iseconds) — instance ready ($DOMAIN) ==="
