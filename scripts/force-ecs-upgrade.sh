#!/usr/bin/env bash
# force-ecs-upgrade.sh — force an immediate `docker compose pull && up -d`
# on a fleet of ECS instances running the Hermes WebUI Docker stack.
#
# WHEN TO USE
#   Every ECS already has /etc/cron.d/hermes-auto-update running this
#   pair of commands every hour on the hour. If you can wait that long,
#   you don't need this script — push your tag, the build-image
#   workflow uploads the new latest image, ECS picks it up within
#   60 minutes.
#
#   Run this script when you want the upgrade NOW:
#     - critical bug fix that can't wait for the hourly tick
#     - manual deploy + verify cycle
#     - rollout of a tag pin (set IMAGE=...:v1.4.9 in /opt/hermes-docker/.env
#       first, then force the pull)
#
# USAGE
#   bash scripts/force-ecs-upgrade.sh hosts.txt
#   bash scripts/force-ecs-upgrade.sh host1.example.com host2.example.com ...
#   cat hosts.txt | bash scripts/force-ecs-upgrade.sh -
#
#   hosts.txt format — one ECS per line. Lines may be:
#     user@host      → ssh user@host
#     host           → ssh into host as $DEFAULT_USER (default: root)
#     host:port      → ssh -p port
#     # comment      → ignored
#     (blank)        → ignored
#
# ENVIRONMENT KNOBS
#   DEFAULT_USER     SSH user when host has no user@ prefix (default: root)
#   SSH_OPTS         Extra args to ssh (default: -o BatchMode=yes -o
#                    ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
#   PARALLEL         Max concurrent SSH connections (default: 4 — keeps the
#                    docker registry from rate-limiting and lets you read
#                    the live progress without a wall of text)
#   DEPLOY_DIR       Path on each ECS where docker-compose.yml lives
#                    (default: /opt/hermes-docker — matches cloud-init)
#   DRY_RUN=1        Print what would run on each host; don't actually SSH
#
# EXIT CODE
#   0 on full success. Non-zero = at least one host failed; per-host
#   summary printed at the end with the failing hosts named.
#
# WHY NOT JUST WATCHTOWER / KURED
#   Watchtower polls the registry from each container, which means each
#   ECS independently downloads metadata even when nothing's new. For 5
#   instances that's fine; for 500 you'd want a fleet-wide pre-pull
#   signal (which this script is). Watchtower also restarts on every
#   image change including dev pushes — we want explicit operator control.

set -uo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────
: "${DEFAULT_USER:=root}"
: "${SSH_OPTS:=-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new}"
: "${PARALLEL:=4}"
: "${DEPLOY_DIR:=/opt/hermes-docker}"
: "${DRY_RUN:=0}"

# ── Argument parsing ──────────────────────────────────────────────────────
if [[ $# -eq 0 ]]; then
    echo "usage: $0 <hosts-file>|<host>... [<host>...]" >&2
    echo "       cat hosts.txt | $0 -" >&2
    exit 2
fi

declare -a HOSTS=()
if [[ $# -eq 1 && "$1" == "-" ]]; then
    while IFS= read -r line; do HOSTS+=("$line"); done
elif [[ $# -eq 1 && -f "$1" ]]; then
    while IFS= read -r line; do HOSTS+=("$line"); done < "$1"
else
    HOSTS=("$@")
fi

# Filter out comments + blanks
declare -a CLEAN_HOSTS=()
for h in "${HOSTS[@]}"; do
    h="${h%%#*}"               # strip trailing comment
    h="${h#"${h%%[![:space:]]*}"}"   # ltrim
    h="${h%"${h##*[![:space:]]}"}"   # rtrim
    [[ -z "$h" ]] && continue
    CLEAN_HOSTS+=("$h")
done

if [[ ${#CLEAN_HOSTS[@]} -eq 0 ]]; then
    echo "no hosts to upgrade" >&2
    exit 2
fi

echo "→ Forcing docker compose pull && up -d on ${#CLEAN_HOSTS[@]} ECS instance(s)"
echo "  PARALLEL=$PARALLEL  DEPLOY_DIR=$DEPLOY_DIR  DRY_RUN=$DRY_RUN"
echo

# ── Per-host worker ───────────────────────────────────────────────────────
upgrade_one() {
    local raw="$1"
    local target port_arg ssh_target

    # Parse host[:port]
    port_arg=""
    if [[ "$raw" == *:* && "$raw" != *@*:* ]]; then
        # plain host:port
        port_arg="-p ${raw##*:}"
        raw="${raw%:*}"
    elif [[ "$raw" == *@*:* ]]; then
        # user@host:port
        port_arg="-p ${raw##*:}"
        raw="${raw%:*}"
    fi

    if [[ "$raw" != *@* ]]; then
        target="${DEFAULT_USER}@${raw}"
    else
        target="$raw"
    fi
    ssh_target=("$target")
    [[ -n "$port_arg" ]] && ssh_target=("$port_arg" "$target")

    local remote_cmd
    # Use a heredoc so the entire upgrade is a single SSH call. -e bails
    # on any failure; we capture both stdout and stderr so the operator
    # sees compose output even when a step dies halfway.
    remote_cmd=$(cat <<'REMOTE'
set -e
cd "${DEPLOY_DIR:-/opt/hermes-docker}"
echo "--- $(hostname) $(date -Iseconds) ---"
docker compose pull
docker compose up -d
docker compose ps
REMOTE
)

    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[dry-run] ssh $SSH_OPTS ${ssh_target[*]} 'bash -s' <<< <upgrade>"
        return 0
    fi

    local out
    if out=$(ssh $SSH_OPTS "${ssh_target[@]}" "DEPLOY_DIR='$DEPLOY_DIR' bash -s" <<< "$remote_cmd" 2>&1); then
        printf '\n=== ✓ %s ===\n%s\n' "$target" "$out"
        return 0
    else
        printf '\n=== ✗ %s ===\n%s\n' "$target" "$out" >&2
        return 1
    fi
}

# ── Drive the fleet ───────────────────────────────────────────────────────
# Track failed hosts in a tempfile so the subshells can append. xargs -P
# gives us PARALLEL concurrency; we export the worker function via bash.

FAILED_LOG=$(mktemp)
trap 'rm -f "$FAILED_LOG"' EXIT

export DEPLOY_DIR DRY_RUN SSH_OPTS DEFAULT_USER FAILED_LOG
export -f upgrade_one

# Run hosts through xargs in parallel. Each invocation either prints a
# success block or an error block; we append failing host names to
# FAILED_LOG for the final summary. The `|| echo "$host" >> $FAILED_LOG`
# pattern keeps the script going so one bad host doesn't abort the rest.
printf '%s\n' "${CLEAN_HOSTS[@]}" \
    | xargs -P "$PARALLEL" -I{} bash -c 'upgrade_one "$@" || echo "$1" >> "$FAILED_LOG"' _ {}

# ── Summary ────────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────"
if [[ -s "$FAILED_LOG" ]]; then
    fail_count=$(wc -l < "$FAILED_LOG")
    echo "❌ ${fail_count} host(s) FAILED:"
    sort -u "$FAILED_LOG" | sed 's/^/   /'
    exit 1
else
    echo "✅ all ${#CLEAN_HOSTS[@]} host(s) upgraded"
fi
