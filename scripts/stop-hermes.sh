#!/usr/bin/env bash
# stop-hermes.sh — power off an instance but KEEP storage + public IP.
# Can be re-started later via Aliyun console or `aliyun ecs StartInstance`.
#
# Use this for: paused user, billing optimization for inactive users.
# (To permanently destroy + free storage, use delete-hermes.sh instead.)
#
# Usage: ./scripts/stop-hermes.sh <instance-id> [region]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for cfg in "${SCRIPT_DIR}/.hermes-broker.env" "${HOME}/.hermes-broker.env"; do
  [ -f "$cfg" ] && { set -a; . "$cfg"; set +a; break; }
done

INSTANCE_ID="${1:?usage: $0 <instance-id> [region]}"
REGION="${2:-${ALIYUN_REGION:-cn-hongkong}}"
: "${ALIYUN_PROFILE:=hermes-broker}"

echo "→ Stopping $INSTANCE_ID in $REGION (keep storage)..."
aliyun ecs StopInstance \
  --profile "$ALIYUN_PROFILE" --region "$REGION" \
  --InstanceId "$INSTANCE_ID" \
  --StoppedMode KeepCharging \
  --ForceStop false > /dev/null

echo "✓ Stop requested. Instance state goes Running → Stopping → Stopped (~30s)."
echo "  Storage retained — restart with:"
echo "    aliyun ecs StartInstance --profile $ALIYUN_PROFILE --region $REGION --InstanceId $INSTANCE_ID"
