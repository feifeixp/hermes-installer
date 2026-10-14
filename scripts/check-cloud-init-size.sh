#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# check-cloud-init-size.sh — guard against the cloud-init UserData blowing past
# Aliyun's limit.
#
# Aliyun ECS RunInstances accepts UserData as a Base64 string capped at 16 KB
# (16384 bytes) — i.e. ~12 KB of raw payload. When the rendered cloud-config
# exceeds this, RunInstances rejects the spawn and the broker returns HTTP 502
# ("Could not prepare cloud-init"). This happened once when cumulative feature
# growth pushed the inline template over the cap (fixed by moving the bulk into
# docker/provision.sh, fetched at boot).
#
# This script renders docker/cloud-init.yaml.template with representative
# secret values, mimics the broker's comment/blank stripping, Base64-encodes,
# and fails if the result approaches the cap. Run it in CI and locally before
# touching the template.
#
# Usage:  ./scripts/check-cloud-init-size.sh
# Exit:   0 = within budget, 1 = over budget (or template missing)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${1:-${SCRIPT_DIR}/../docker/cloud-init.yaml.template}"

# Aliyun's hard cap on the Base64-encoded UserData string.
ALIYUN_B64_CAP=16384
# Keep the rendered scaffolding under HALF the cap. The other half is reserved
# headroom for injected secrets: the original 502 incident was an already-large
# inline template (~11 KB base64) plus a single ~4 KB oversized secret tipping
# it past the cap. With the bulk moved into docker/provision.sh the template
# renders to ~3-4 KB base64, so even a multi-KB secret comfortably fits.
BUDGET=8192

if [ ! -f "$TEMPLATE" ]; then
  echo "❌ template not found: $TEMPLATE" >&2
  exit 1
fi

# Representative substitutions. Secret lengths are realistic-to-generous so the
# guard reflects a real spawn, not an empty render. HEARTBEAT_TOKEN is sized as
# a typical signed JWT (~280 chars).
HEARTBEAT='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiIzMTA0NjY2MyIsInNjb3BlIjoiaW5zdGFuY2UtaGVhcnRiZWF0IiwiaWF0IjoxNzE2OTQwMDAwLCJleHAiOjE3MTcwMjY0MDAsImlzcyI6ImFwcC5uZW93b3cuc3R1ZGlvIn0.aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789abcdefghijklmn'

RENDERED="$(sed \
  -e "s|%USER_ID%|31046663|g" \
  -e "s|%DOMAIN%|chat-31046663.neowow.studio|g" \
  -e "s|%ACME_EMAIL%|admin@neowow.studio|g" \
  -e "s|%IMAGE%|zlxj-registry.ap-southeast-1.cr.aliyuncs.com/hermes/hermes:latest|g" \
  -e "s|%ACR_PULL_REGISTRY%|zlxj-registry-vpc.cn-shanghai.cr.aliyuncs.com|g" \
  -e "s|%ACR_PULL_USERNAME%|hermes-pull-readonly|g" \
  -e "s|%ACR_PULL_PASSWORD%|Hk39sLpQ2vXmZ7wB4nR8tY1cF6dG0jA5eU3iO9kT2bN|g" \
  -e "s|%OSS_ACCESS_KEY_ID%|LTAI5tABCDEFGHJKLMNPQRST|g" \
  -e "s|%OSS_ACCESS_KEY_SECRET%|aBcDeFgHiJkLmNoPqRsTuVwXyZ012345|g" \
  -e "s|%OSS_ENDPOINT%|oss-cn-hangzhou-internal.aliyuncs.com|g" \
  -e "s|%OSS_BUCKET%|neowow-hermes-state|g" \
  -e "s|%CLOUDFLARE_API_TOKEN%|v1.0-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-|g" \
  -e "s|%HEARTBEAT_TOKEN%|${HEARTBEAT}|g" \
  "$TEMPLATE")"

# Mimic the broker: drop blank lines and comment-only lines before encoding.
STRIPPED="$(printf '%s\n' "$RENDERED" | awk 'NF==0{next} /^[[:space:]]*#/{next} {print}')"

RAW_BYTES=$(printf '%s' "$STRIPPED" | wc -c | tr -d ' ')
B64_BYTES=$(printf '%s' "$STRIPPED" | base64 | tr -d '\n' | wc -c | tr -d ' ')

echo "cloud-init UserData size:"
echo "  raw (comment-stripped) : ${RAW_BYTES} bytes"
echo "  base64-encoded         : ${B64_BYTES} bytes"
echo "  budget                 : ${BUDGET} bytes  (Aliyun cap ${ALIYUN_B64_CAP})"

if [ "$B64_BYTES" -gt "$ALIYUN_B64_CAP" ]; then
  echo "❌ OVER ALIYUN CAP by $((B64_BYTES - ALIYUN_B64_CAP)) bytes — RunInstances will reject this (HTTP 502)." >&2
  echo "   Move bulk into docker/provision.sh (fetched at boot) instead of inlining it." >&2
  exit 1
fi
if [ "$B64_BYTES" -gt "$BUDGET" ]; then
  echo "❌ over budget by $((B64_BYTES - BUDGET)) bytes (within the hard cap, but too close)." >&2
  echo "   Trim the template or move config into docker/provision.sh." >&2
  exit 1
fi

echo "✓ within budget ($((BUDGET - B64_BYTES)) bytes to spare)."
