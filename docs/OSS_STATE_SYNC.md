# OSS state sync — survive server replacement

> **What this is**: every chat.neowow.studio deploy backs the user's
> sessions / settings / skills / workspace to Aliyun OSS on a 5-minute
> cadence (and on graceful shutdown). New container starts pull the
> latest backup before WebUI comes up.
>
> **What problem it solves**: today the Tencent Cloud chat box's data
> lives in a Docker named volume. Replace the box → volume's gone.
> Even on the same box, image upgrades + `docker compose down -v` =
> data loss. OSS sync makes the storage durable independent of the
> compute.

---

## Lifecycle

```
┌──────────────────────────────────────────────────────────────────────┐
│  Container START                                                       │
│  ─────────────                                                         │
│  entrypoint-with-sync.sh runs:                                         │
│    1. ossutil verify         (fail fast on bad AK/SK)                  │
│    2. ossutil pull           (OSS → /opt/hermes/.hermes/)              │
│    3. exec start.sh           (WebUI boots with restored state)        │
│    4. spawn background loop  (periodic push every 5 min)               │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │  WebUI running, agents working,
                              │  every ${OSS_SYNC_INTERVAL_SECS}:
                              ▼
                  ┌────────────────────────────┐
                  │  ossutil cp -r --update    │
                  │  local → OSS (incremental) │
                  └────────────────────────────┘
                              │
                              │  docker compose stop / SIGTERM
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Container STOP                                                        │
│  ─────────────                                                         │
│  trap handler:                                                         │
│    1. kill background loop                                             │
│    2. kill -TERM webui  (server.py flushes state.db, closes SSEs)      │
│    3. final ossutil push  (catches the last in-flight writes)          │
│    4. exit 0                                                           │
└──────────────────────────────────────────────────────────────────────┘
```

## What gets synced

✅ **Included** (lives in OSS):

| Local path | OSS path |
|---|---|
| `/opt/hermes/.hermes/sessions/` | `users/<id>/hermes/sessions/` |
| `/opt/hermes/.hermes/webui/settings.json` | `users/<id>/hermes/webui/settings.json` |
| `/opt/hermes/.hermes/webui/workspaces.json` | `users/<id>/hermes/webui/workspaces.json` |
| `/opt/hermes/.hermes/webui/skills/` | `users/<id>/hermes/webui/skills/` |
| `/opt/hermes/.hermes/config.yaml` | `users/<id>/hermes/config.yaml` |
| `/opt/hermes/workspace/` | `users/<id>/hermes/workspace/` |

❌ **Excluded** (each box / device has its own):

| Path | Why excluded |
|---|---|
| `webui/neowow.json` | Contains the local JWT — per-device, not portable. |
| `webui/gateway.json` | Per-instance gateway URL (chat-<id>.neowow.studio). |
| `webui/hermes_session.json` | Local password-mode session cookie. |
| `webui/.login_attempts.json` | Rate-limit state, scope-local. |
| `.env` | API keys, intentionally sensitive — never in OSS. |
| `hermes-agent/` | Reinstalled from image on every container start. |
| `__pycache__/`, `*.pyc`, `*.pid`, `*.lock`, `*.sock` | Caches / fd handles. |

## Setup

### 1. Create an OSS bucket

Aliyun console → 对象存储 OSS → 创建 Bucket:

| Setting | Value |
|---|---|
| 名称 | `neowow-hermes-state` (or your own) |
| 地域 | match your ECS region (e.g. `华东 1 杭州`) |
| 存储类型 | 标准存储 |
| 读写权限 | **私有** ⚠️ NEVER make it public |
| 版本控制 | 推荐开启（point-in-time 恢复） |
| 服务端加密 | 推荐 `OSS 完全托管` |

### 2. Create a scoped RAM user for the AK/SK

Don't use your account-level AccessKey. Create a RAM sub-user with
ONLY the permissions this sync needs.

Aliyun console → RAM 访问控制 → 用户 → 创建用户:
- 用户名: `hermes-state-sync`
- 访问方式: ☑ OpenAPI 调用访问

Attach this policy (replace `<your-bucket>`):

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:GetObject",
        "oss:PutObject",
        "oss:DeleteObject",
        "oss:ListObjects"
      ],
      "Resource": [
        "acs:oss:*:*:<your-bucket>",
        "acs:oss:*:*:<your-bucket>/*"
      ]
    }
  ]
}
```

Copy the AK ID + Secret immediately — they're shown only once.

### 3. Drop your `.env` at `/opt/hermes-docker/.env`

Copy `docker/env.oss-sync.example` and fill in:

```bash
ssh <your-server>
sudo nano /opt/hermes-docker/.env
```

Set:
```
OSS_SYNC_ENABLED=1
OSS_ACCESS_KEY_ID=<RAM user AK>
OSS_ACCESS_KEY_SECRET=<RAM user secret>
OSS_BUCKET=neowow-hermes-state
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_USER_ID=<your 19-digit Neodomain userId>
OSS_SYNC_INTERVAL_SECS=300
```

> ⚠️ chmod 600 the .env: `sudo chmod 600 /opt/hermes-docker/.env`.
> docker compose doesn't care about perms but other shell users on
> the box shouldn't read your AK.

### 4. Pull the new image + recreate

```bash
cd /opt/hermes-docker
sudo docker compose pull
sudo docker compose stop hermes-webui
sudo docker compose rm -f hermes-webui
sudo docker compose up -d hermes-webui
sleep 5
sudo docker compose logs -f hermes-webui --tail=30
```

You should see:
```
[entrypoint] OSS sync enabled (bucket=neowow-hermes-state userId=...)
[oss-sync] verify: ok (bucket reachable, prefix will be created on first push)
[entrypoint] pulling existing state from OSS...
[oss-sync] pull: ...
[entrypoint] starting webui via webui/start.sh...
```

After ~30 seconds:
```
[entrypoint] background sync loop PID=... (interval=300s)
```

After 5 minutes of activity:
```
[oss-sync] push: N synced, 0 skipped
```

## Verifying it works

### A. Check OSS has the state

```bash
# From inside the container
sudo docker compose exec hermes-webui bash -c '
  ossutil --config /tmp/.cfg ls "oss://${OSS_BUCKET}/users/${OSS_USER_ID}/hermes/" 2>&1 | head
' 2>/dev/null
```

Or in Aliyun OSS console — browse to `<bucket>/users/<id>/hermes/`,
you should see `sessions/`, `webui/`, `config.yaml`, `_meta/last-synced.json`.

The `_meta/last-synced.json` updates every push — that's your
"is sync alive?" canary:
```json
{"hostname":"chat-xxx","at":"2026-05-16T01:30:00+00:00","final":"","pushed":3,"skipped":2}
```

### B. End-to-end recovery test

Most direct way to know sync actually works:

```bash
# 1. Note current state — get a session ID from the UI or:
sudo docker compose exec hermes-webui ls /opt/hermes/.hermes/sessions/ | head

# 2. Trigger a final push
sudo docker compose stop hermes-webui   # SIGTERM → final push runs

# 3. Verify in OSS console that sessions/ has your file

# 4. WIPE the volume to simulate machine loss
sudo docker compose rm -f hermes-webui
sudo docker volume rm hermes-docker_hermes_state  # or whatever your project named it
# (Caddy stays up, just hermes data wiped)

# 5. Recreate — pull should restore everything
sudo docker compose up -d hermes-webui
sleep 30
sudo docker compose exec hermes-webui ls /opt/hermes/.hermes/sessions/ | head

# Should see the same files. If yes → sync works end-to-end.
```

### C. Watching the cadence

```bash
sudo docker compose logs -f hermes-webui 2>&1 | grep -E 'oss-sync|entrypoint'
```

## Operational tips

### Cost

OSS PUT requests cost ~¥0.01 per 10k requests. With our ~30 files
synced every 5 minutes = 30 × 12 = **360 PUT/hour** × 24 = **8.6k/day**.
Per user that's ~¥0.01/day for the PUT cost alone, plus storage
(¥0.12 per GB-month for standard storage).

For 1000 users with ~5 MB each: 5 GB × ¥0.12 = **~¥0.6/month storage** +
8.6M PUTs/month × ¥0.01/10k = **~¥8.6/month PUTs**. Total **~¥10/month
for 1000 users**, before egress.

To slash cost:
- Bump `OSS_SYNC_INTERVAL_SECS` to 900 or 1800 (15-30 min) → ⅓ - ⅙ the PUTs
- Use `OSS 低频访问` storage class instead of standard
- Add lifecycle rule: transition to IA after 30 days, delete after 90

### Bucket lifecycle (recommended)

Aliyun OSS console → 你的 bucket → 基础设置 → 生命周期管理:

```yaml
- name: transition-old-sessions-to-IA
  prefix: ""
  rules:
    - days_after_creation: 30
      target: IA
- name: delete-meta-markers-monthly
  prefix: "users/*/hermes/_meta/"
  rules:
    - days_after_modification: 30
      action: delete
```

### Cross-region failover

The bucket is single-region by default. For DR, enable cross-region
replication (CRR) in the bucket settings — typically Hangzhou →
Shanghai. The container pulls from the primary; OSS auto-fails to the
replica if the primary region is down.

### What if OSS is unreachable at startup?

- `entrypoint verify` fails → loud log warning, BUT container still
  starts. WebUI works on whatever's in the local volume (could be
  empty for a fresh container).
- Periodic push will keep retrying every interval. Once OSS comes
  back, the next push catches up automatically.
- Final push on SIGTERM does best-effort — if OSS is still down, the
  changes are lost.

We don't BLOCK boot on OSS being up because that would cascade-fail
when Aliyun has a regional incident. Better to keep chat available
on stale-but-local state than refuse to serve at all.

### Sensitive data — what NOT to put in workspaces

`/opt/hermes/workspace/` IS synced. That means files your agent
creates / edits there flow to OSS. If a user pastes their own .env
into a workspace and the agent reads it, that .env now lives in your
OSS bucket. UI doesn't warn about this.

Mitigations:
- Bucket is private + scoped IAM → only your RAM sync user can read.
- Server-side encryption ON → OSS encrypts at rest.
- Document in user-facing docs that workspaces are backed up.
- (Future) Add a workspace-level `.no-sync` marker file that excludes
  that subtree.

## Failure modes (and what we do)

| Mode | Behavior |
|---|---|
| Bad AK/SK | `verify` fails at boot → loud error, container starts anyway with empty state. Operator fixes `.env` + restarts. |
| OSS region down | `pull` fails → container starts with local state. Periodic push retries. |
| Network slow during push | Push takes longer; if it exceeds the interval, next push waits for previous to finish. |
| Local state corrupted | Pull is `--update` (only downloads newer), so it WON'T overwrite a healthy local file with stale OSS. Operator can manually trigger via `docker exec ... ossutil cp -r ...`. |
| Two containers writing concurrently | Last-writer-wins on a per-file basis. Acceptable since the broker model is one container per userId at a time. |
| Disk full on OSS bucket | Push errors. Operator sees in logs, adds storage quota. No data loss locally — just no backup until fixed. |
| User accidentally deletes from OSS | Versioning (recommended setting) lets you restore. Without versioning, gone. |

## Disabling

```bash
# Edit .env
sudo sed -i 's/^OSS_SYNC_ENABLED=.*/OSS_SYNC_ENABLED=0/' /opt/hermes-docker/.env
sudo docker compose up -d hermes-webui
```

Container restarts, sync is a clean no-op, data stays in the local
volume only (back to "your old behavior").

## Next: bidirectional with local Hermes Desktop

The same OSS bucket can sync to a local `~/.hermes/` install. WIP —
will land as `hermes-cli sync push/pull <userId>` once the cloud side
is stable. Approach:

- Local desktop reads the same RAM user's AK/SK from `~/.aliyun-cli`
  or env.
- `hermes-cli sync pull` does the same `ossutil cp -r --update` from
  the same prefix into `~/.hermes/`.
- `hermes-cli sync push` is the reverse.
- A `last-synced.json` per device lets us detect "your desktop is
  ahead of cloud by 2 hours" and prompt before overwriting.

For now: only cloud-side sync is wired up.
