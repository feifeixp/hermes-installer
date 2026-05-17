#!/usr/bin/env bash
# delete-hermes.sh — permanently destroy an instance + free its DNS A record.
#
# WARNING: storage is destroyed. Make sure OSS state-sync has pushed
# the latest snapshot first (or you don't care about losing session
# history). Cloud-init has a shutdown hook that runs oss-sync push
# during graceful shutdown, but it only fires for `StopInstance` —
# `DeleteInstance --Force=true` skips it.
#
# Usage: ./scripts/delete-hermes.sh <user-id> [region] [instance-id]
#
# If <instance-id> is omitted, looks up the instance by hermes-userid
# tag — useful when you only remember the user, not the i-* ID.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for cfg in "${SCRIPT_DIR}/.hermes-broker.env" "${HOME}/.hermes-broker.env"; do
  [ -f "$cfg" ] && { set -a; . "$cfg"; set +a; break; }
done

USER_ID="${1:?usage: $0 <user-id> [region] [instance-id]}"
REGION="${2:-${ALIYUN_REGION:-cn-hongkong}}"
INSTANCE_ID="${3:-}"
: "${ALIYUN_PROFILE:=hermes-broker}"
: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN required}"
: "${CLOUDFLARE_ZONE_ID:?CLOUDFLARE_ZONE_ID required}"

# Resolve instance ID by tag if not supplied
if [ -z "$INSTANCE_ID" ]; then
  echo "→ Looking up instance by tag hermes-userid=$USER_ID..."
  INSTANCE_ID=$(aliyun ecs DescribeInstances \
    --profile "$ALIYUN_PROFILE" --region "$REGION" \
    --RegionId "$REGION" \
    --Tag.1.Key hermes-userid --Tag.1.Value "$USER_ID" \
    | jq -r '.Instances.Instance[0].InstanceId // empty')
  if [ -z "$INSTANCE_ID" ]; then
    echo "❌ No instance found for user $USER_ID in region $REGION."
    echo "   Pass the instance ID explicitly: $0 $USER_ID $REGION i-bp1xxx"
    exit 1
  fi
  echo "→ Found: $INSTANCE_ID"
fi

# Confirm destructive op
echo ""
echo "⚠️  About to DELETE instance $INSTANCE_ID (user $USER_ID) + its DNS."
read -r -p "    Type DELETE to confirm: " CONFIRM
[ "$CONFIRM" = "DELETE" ] || { echo "Aborted."; exit 1; }

# 1. Delete ECS
echo "→ Deleting ECS..."
aliyun ecs DeleteInstance \
  --profile "$ALIYUN_PROFILE" --region "$REGION" \
  --InstanceId "$INSTANCE_ID" \
  --Force true > /dev/null

# 2. Delete DNS A record
echo "→ Deleting CF DNS record..."
EXIST=$(curl -fsS \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records?name=chat-${USER_ID}.neowow.studio" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")
REC_ID=$(echo "$EXIST" | jq -r '.result[0].id // empty')
if [ -n "$REC_ID" ]; then
  curl -fsS -X DELETE \
    "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${REC_ID}" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" > /dev/null
  echo "→ DNS record removed"
else
  echo "→ No DNS record found (was it already removed?)"
fi

echo "✓ Instance + DNS cleaned up."
