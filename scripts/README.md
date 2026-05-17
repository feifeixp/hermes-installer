# scripts/

Manual ops for Hermes per-user ECS instances. Mirrors what
`dashboard /api/me/instance/*` does programmatically, but accessible
via shell — useful for first-time verification, debugging, and
ad-hoc demos.

## Files

| File | What it does |
|---|---|
| `.hermes-broker.env.example` | Config template — copy to `.hermes-broker.env` and fill in |
| `spawn-hermes.sh`            | Create + boot a Hermes instance, write Cloudflare DNS |
| `stop-hermes.sh`             | Power off but keep storage + IP (resumable) |
| `delete-hermes.sh`           | Permanently destroy instance + remove DNS |
| `snapshot-neowow-patches.sh` | (unrelated) snapshot of patches against upstream Hermes |

## Setup (one-time)

```bash
# 1. Install dependencies
brew install aliyun-cli jq        # macOS
# or:
sudo apt install jq && curl -L https://aliyuncli.alicdn.com/aliyun-cli-linux-latest-amd64.tgz | tar xz && sudo mv aliyun /usr/local/bin/

# 2. Configure aliyun-cli with your RAM 子账号 AK
aliyun configure --profile hermes-broker --mode AK
#   Access Key Id     : LTAI5tXXXXXXXXXX
#   Access Key Secret : ...
#   Default Region    : cn-hongkong
#   Default Output    : json

# 3. Copy config template + fill in
cp scripts/.hermes-broker.env.example scripts/.hermes-broker.env
$EDITOR scripts/.hermes-broker.env
#   Fill: ALIYUN_VSWITCH_ID, ALIYUN_SECURITY_GROUP_ID,
#         ACR_PULL_USERNAME, ACR_PULL_PASSWORD,
#         CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID
# Note: file is in .gitignore — don't commit your filled-in copy.

# 4. Test
./scripts/spawn-hermes.sh test-001
```

## Where to put the config file

The scripts search **in this order** (first hit wins):

```
1. ENV vars on the command line
   e.g. `ALIYUN_VSWITCH_ID=vsw-xxx ./scripts/spawn-hermes.sh test-001`

2. scripts/.hermes-broker.env         ← repo-local, git-ignored
   Best for: testing, demo, ops scripts

3. ~/.hermes-broker.env               ← user-level
   Best for: personal dev box, single-user laptop
```

## Quickstart — full lifecycle

```bash
# Spawn
./scripts/spawn-hermes.sh test-001
# → Instance: i-bp1xxxx
# → Public IP: 47.123.45.67
# → URL: https://chat-test-001.neowow.studio
# wait ~5 min for cloud-init to finish

# Verify
curl -I https://chat-test-001.neowow.studio
# expect HTTP/2 200 (or 302 to login)

# SSH in for debugging
ssh root@47.123.45.67
journalctl -u cloud-init --no-pager | tail -100
cd /opt/hermes-docker && sudo docker compose ps

# Pause (keep storage)
./scripts/stop-hermes.sh i-bp1xxxx

# Permanently delete
./scripts/delete-hermes.sh test-001
# (looks up instance by hermes-userid tag if you don't supply the ID)
```

## When to use the scripts vs the broker

| Scenario | Use |
|---|---|
| Smoke-test new region / new AK / new VSwitch | scripts |
| Reproduce a user's broken spawn for debugging | scripts |
| Spawn the master `chat.neowow.studio` demo instance | scripts (one-time, then leave it) |
| Real per-user spawn (production) | dashboard broker — set `HERMES_CLOUD_PROVIDER=aliyun` + the same env vars |

The broker reads the same env vars (different names — `ALIYUN_AK_ID`,
`ALIYUN_AK_SECRET`, `ALIYUN_VSWITCH_ID`, etc.) from Cloudflare Pages
secrets. See dashboard/src/lib/cloud/aliyun.ts for the full list.

## Troubleshooting

**`No public IP after 90s`**
- Region quota exhausted? Aliyun ECS console → check region quota
- VSwitch full? Each VSwitch has a host count limit (typically 256)
- Try a different region (`./scripts/spawn-hermes.sh test-001 cn-shanghai`)

**`docker login` fails inside cloud-init**
- ACR_PULL_USERNAME / PASSWORD wrong — re-issue token via ACR console
- ACR_PULL_REGISTRY mismatch (`-vpc.` vs public) — must match the region your ECS is in:
  - ECS region = ACR region → use `-vpc.` (faster, free)
  - ECS region ≠ ACR region → public endpoint only

**`Connection refused` after 5 min**
- cloud-init still running. SSH in: `journalctl -u cloud-init -f`
- Docker pull stuck → check ACR connectivity from inside ECS
- Caddy can't get LE cert → DNS not yet propagated, wait 2 more min

**Forgot to delete a test instance**
- It's still billing! Find + delete:
  ```bash
  aliyun ecs DescribeInstances --profile hermes-broker --region cn-hongkong \
    | jq '.Instances.Instance[] | {InstanceId, Status, InstanceName}'
  ./scripts/delete-hermes.sh <user-id> cn-hongkong i-bp1xxx
  ```
