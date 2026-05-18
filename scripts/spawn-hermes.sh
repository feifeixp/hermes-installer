#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# spawn-hermes.sh — manually spawn one Hermes ECS instance.
#
# This mirrors what the broker (dashboard /api/me/instance/start) does
# automatically. Useful for:
#   • First-time wiring verification (before broker is deployed)
#   • Ops debugging (reproduce a user's spawn out-of-band)
#   • Provisioning a fixed-domain instance (e.g. for a demo)
#
# Reads config from (first found wins):
#   1. command-line env vars
#   2. ./scripts/.hermes-broker.env   (repo-local, git-ignored)
#   3. ~/.hermes-broker.env           (user-level)
# See .hermes-broker.env.example for the full var list.
#
# Usage:
#   ./scripts/spawn-hermes.sh <user-id> [region]
#
# Example:
#   ./scripts/spawn-hermes.sh test-001 cn-hongkong
#   ./scripts/spawn-hermes.sh 31046663
#
# Result:
#   • An ECS instance named hermes-<user-id> running in your VSwitch
#   • A Cloudflare DNS A record chat-<user-id>.neowow.studio → ECS IP
#   • Reachable at https://chat-<user-id>.neowow.studio after ~5 min
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config sourcing ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for cfg in "${SCRIPT_DIR}/.hermes-broker.env" "${HOME}/.hermes-broker.env"; do
  if [ -f "$cfg" ]; then
    echo "→ loading config from $cfg"
    # shellcheck disable=SC1090
    set -a; . "$cfg"; set +a
    break
  fi
done

# ── Args + defaults ─────────────────────────────────────────────────────────
USER_ID="${1:?usage: $0 <user-id> [region]}"
REGION="${2:-${ALIYUN_REGION:-cn-hongkong}}"

# Defaults — can be overridden via env / config file
: "${ALIYUN_PROFILE:=hermes-broker}"
: "${ALIYUN_INSTANCE_TYPE:=ecs.t6-c1m2.large}"
: "${ALIYUN_IMAGE_ID:=ubuntu_22_04_x64_20G_alibase_}"
: "${ALIYUN_BANDWIDTH_MBPS:=5}"
: "${ALIYUN_DISK_GB:=40}"
: "${ACME_EMAIL:=admin@neowow.studio}"
: "${HERMES_WEBUI_IMAGE:=zlxj-registry.ap-southeast-1.cr.aliyuncs.com/hermes/hermes:latest}"
: "${OSS_ENDPOINT:=oss-cn-hangzhou-internal.aliyuncs.com}"
: "${OSS_STATE_BUCKET:=neowow-hermes-state}"
: "${ACR_PULL_REGISTRY:=}"   # if empty, inferred from image URL below
: "${ACR_PULL_USERNAME:=}"
: "${ACR_PULL_PASSWORD:=}"
: "${OSS_ACCESS_KEY_ID:=}"
: "${OSS_ACCESS_KEY_SECRET:=}"

# Infer ACR registry from image URL if not set explicitly
if [ -z "$ACR_PULL_REGISTRY" ]; then
  ACR_PULL_REGISTRY="${HERMES_WEBUI_IMAGE%%/*}"
fi

# ── Required vars check ─────────────────────────────────────────────────────
require() {
  local name=$1
  if [ -z "${!name:-}" ]; then
    echo "❌ Missing required: $name"
    echo "   Set in $SCRIPT_DIR/.hermes-broker.env (copy from .hermes-broker.env.example)"
    exit 1
  fi
}
require ALIYUN_VSWITCH_ID
require ALIYUN_SECURITY_GROUP_ID
require CLOUDFLARE_API_TOKEN
require CLOUDFLARE_ZONE_ID

# Tool checks
command -v aliyun >/dev/null 2>&1 || { echo "❌ install aliyun-cli first: https://help.aliyun.com/document_detail/121541.html"; exit 1; }
command -v jq     >/dev/null 2>&1 || { echo "❌ install jq first: brew install jq / apt install jq"; exit 1; }

DOMAIN="chat-${USER_ID}.neowow.studio"

# Resolve ALIYUN_IMAGE_ID — if it ends in `_` or looks like a family
# prefix (no .vhd, no date stamp), query DescribeImages for the latest
# matching image. Aliyun expects an exact image ID at RunInstances time
# (no SSM-style family resolution), so we have to ask. Cache the
# resolved ID for the rest of the script.
if [[ "$ALIYUN_IMAGE_ID" == *_ ]] || [[ "$ALIYUN_IMAGE_ID" != *.vhd ]]; then
  echo "→ Looking up latest image matching '${ALIYUN_IMAGE_ID}*' in $REGION..."
  RESOLVED=$(aliyun ecs DescribeImages \
    --profile "$ALIYUN_PROFILE" --region "$REGION" \
    --RegionId "$REGION" \
    --OSType linux --Architecture x86_64 \
    --ImageOwnerAlias system --PageSize 50 2>/dev/null \
    | jq -r --arg p "${ALIYUN_IMAGE_ID}" '
        [.Images.Image[]
          | select(.ImageId | startswith($p))
          | select(.ImageId | contains("with_") | not)
          | select(.Size <= 20)
          | {ImageId, CreationTime}]
        | sort_by(.CreationTime) | reverse | .[0].ImageId // empty')
  if [ -z "$RESOLVED" ]; then
    echo "❌ No image matching '${ALIYUN_IMAGE_ID}*' in $REGION."
    echo "   Browse available: aliyun ecs DescribeImages --profile $ALIYUN_PROFILE --region $REGION --ImageOwnerAlias system --PageSize 50 | jq '.Images.Image[] | .ImageId' | head"
    exit 1
  fi
  ALIYUN_IMAGE_ID="$RESOLVED"
  echo "→ Resolved: $ALIYUN_IMAGE_ID"
fi

echo ""
echo "──────────────────────────────────────────────────────────────"
echo "  Spawning Hermes instance"
echo "──────────────────────────────────────────────────────────────"
echo "  user:     $USER_ID"
echo "  domain:   $DOMAIN"
echo "  region:   $REGION"
echo "  image:    $ALIYUN_IMAGE_ID"
echo "  container:$HERMES_WEBUI_IMAGE"
echo "  type:     $ALIYUN_INSTANCE_TYPE"
echo "──────────────────────────────────────────────────────────────"
echo ""

# ── 1. Render cloud-init ────────────────────────────────────────────────────
echo "→ Rendering cloud-init.yaml from template..."
TMP=$(mktemp)
TEMPLATE_URL="${HERMES_TEMPLATE_BASE:-https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker}/cloud-init.yaml.template"
curl -fsSL "$TEMPLATE_URL" \
  | sed \
    -e "s|%USER_ID%|${USER_ID}|g" \
    -e "s|%DOMAIN%|${DOMAIN}|g" \
    -e "s|%ACME_EMAIL%|${ACME_EMAIL}|g" \
    -e "s|%IMAGE%|${HERMES_WEBUI_IMAGE}|g" \
    -e "s|%ACR_PULL_REGISTRY%|${ACR_PULL_REGISTRY}|g" \
    -e "s|%ACR_PULL_USERNAME%|${ACR_PULL_USERNAME}|g" \
    -e "s|%ACR_PULL_PASSWORD%|${ACR_PULL_PASSWORD}|g" \
    -e "s|%OSS_ACCESS_KEY_ID%|${OSS_ACCESS_KEY_ID}|g" \
    -e "s|%OSS_ACCESS_KEY_SECRET%|${OSS_ACCESS_KEY_SECRET}|g" \
    -e "s|%OSS_ENDPOINT%|${OSS_ENDPOINT}|g" \
    -e "s|%OSS_BUCKET%|${OSS_STATE_BUCKET}|g" \
    > "$TMP"

# Sanity check — any leftover placeholders mean a typo above
if grep -nE '%[A-Z_]+%' "$TMP"; then
  echo "❌ cloud-init has unresolved placeholders (above). Fix the sed lines."
  rm -f "$TMP"
  exit 1
fi

# Base64-encode for UserData (Aliyun's API wants base64)
if base64 --help 2>&1 | grep -q '\-w'; then
  USER_DATA_B64=$(base64 -w0 < "$TMP")        # GNU base64 (Linux)
else
  USER_DATA_B64=$(base64 < "$TMP" | tr -d '\n')  # BSD base64 (macOS)
fi
rm -f "$TMP"

# ── 2. RunInstances ─────────────────────────────────────────────────────────
echo "→ Calling RunInstances..."
RUN_OUT=$(aliyun ecs RunInstances \
  --profile "$ALIYUN_PROFILE" \
  --region  "$REGION" \
  --RegionId "$REGION" \
  --ImageId "$ALIYUN_IMAGE_ID" \
  --InstanceType "$ALIYUN_INSTANCE_TYPE" \
  --SecurityGroupId "$ALIYUN_SECURITY_GROUP_ID" \
  --VSwitchId "$ALIYUN_VSWITCH_ID" \
  --InstanceName "hermes-${USER_ID}" \
  --HostName "hermes-${USER_ID}" \
  --Amount 1 --MinAmount 1 \
  --InstanceChargeType PostPaid \
  --InternetChargeType PayByTraffic \
  --InternetMaxBandwidthOut "$ALIYUN_BANDWIDTH_MBPS" \
  --SystemDisk.Category cloud_essd \
  --SystemDisk.Size "$ALIYUN_DISK_GB" \
  --UserData "$USER_DATA_B64" \
  --Tag.1.Key  hermes-userid  \
  --Tag.1.Value "$USER_ID" \
  --Tag.2.Key  hermes-version \
  --Tag.2.Value phase-2-manual)

INSTANCE_ID=$(echo "$RUN_OUT" | jq -r '.InstanceIdSets.InstanceIdSet[0]')
if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "null" ]; then
  echo "❌ RunInstances returned no instance ID. Raw response:"
  echo "$RUN_OUT" | jq .
  exit 1
fi
echo "→ Instance: $INSTANCE_ID"

# ── 3. Wait for public IP ───────────────────────────────────────────────────
echo "→ Waiting for public IP..."
PUBLIC_IP=""
for i in {1..30}; do
  PUBLIC_IP=$(aliyun ecs DescribeInstances \
    --profile "$ALIYUN_PROFILE" --region "$REGION" \
    --RegionId "$REGION" \
    --InstanceIds "[\"$INSTANCE_ID\"]" 2>/dev/null \
    | jq -r '.Instances.Instance[0].PublicIpAddress.IpAddress[0] // empty')
  if [ -n "$PUBLIC_IP" ]; then break; fi
  sleep 3
done
if [ -z "$PUBLIC_IP" ]; then
  echo "❌ No public IP after 90s. Instance $INSTANCE_ID may be stuck."
  echo "   Check Aliyun console + manually delete to avoid charges."
  exit 1
fi
echo "→ Public IP: $PUBLIC_IP"

# ── 4. Write Cloudflare DNS A record ────────────────────────────────────────
echo "→ Writing Cloudflare DNS A record..."
DNS_RESP=$(curl -fsS -X POST \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"A\",\"name\":\"chat-${USER_ID}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":300,\"proxied\":false}")
if [ "$(echo "$DNS_RESP" | jq -r '.success')" != "true" ]; then
  # Maybe the record exists — try update instead
  echo "→ Create failed; trying update..."
  EXIST=$(curl -fsS \
    "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?name=chat-${USER_ID}.neowow.studio" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")
  REC_ID=$(echo "$EXIST" | jq -r '.result[0].id // empty')
  if [ -n "$REC_ID" ]; then
    curl -fsS -X PATCH \
      "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${REC_ID}" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"content\":\"${PUBLIC_IP}\",\"proxied\":false}" > /dev/null
    echo "→ DNS updated"
  else
    echo "❌ DNS create+update both failed:"
    echo "$DNS_RESP" | jq .
    exit 1
  fi
else
  echo "→ DNS A record created"
fi

# ── Done ────────────────────────────────────────────────────────────────────
cat <<EOF

✓ Spawn submitted.

  Instance ID : $INSTANCE_ID
  Public IP   : $PUBLIC_IP
  URL         : https://${DOMAIN}

  cloud-init will take ~4-5 min to finish.

  Watch progress:
    ssh root@${PUBLIC_IP}
    journalctl -u cloud-init --no-pager -f
    cd /opt/hermes-docker && sudo docker compose ps

  Quick check (run after 5 min):
    curl -I https://${DOMAIN}

  When done testing, clean up:
    ./scripts/delete-hermes.sh $USER_ID $REGION $INSTANCE_ID

EOF
