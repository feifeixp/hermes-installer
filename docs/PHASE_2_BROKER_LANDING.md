# Phase 2 Broker Landing — `chat.neowow.studio` migration

> **What this is**: the operational handover doc for turning
> `chat.neowow.studio` from a multi-tenant chat surface into a **broker
> landing** that auto-spawns or wakes the caller's per-user instance
> and redirects them.
>
> **Status**: code is in. **DNS + Caddy + deploy steps below are
> manual** — we don't have automation for them yet.

---

## Why we're doing this

Phase 1 had `chat.neowow.studio` serve a shared Hermes Agent on one
Tencent Cloud box, with the `HERMES_INSTANCE_OWNER_USERID` gate
deciding multi-tenant vs single-tenant. That model is now retired:

- All users should get a private ECS instance (per
  `PHASE_2_DESIGN.md`).
- `chat.neowow.studio` becomes a **router** — not a chat surface.
- `chat-<userId>.neowow.studio` is the actual chat hostname (one per
  user, lives until idle-stopped, persisted via OSS sync).

---

## End-state architecture

```
                         ┌─────────────────────────────────┐
                         │  app.neowow.studio              │
                         │  ───────────────────────────    │
                         │  Cloudflare Worker (dashboard)  │
                         │  • OAuth start / callback       │
                         │  • Coding Plan API              │
                         │  • Broker API (/api/me/instance)│
                         └────────────┬────────────────────┘
                                      │
                                      │ same worker, different host
                                      ▼
                         ┌─────────────────────────────────┐
                         │  chat.neowow.studio             │
                         │  ───────────────────────────    │
                         │  middleware.ts rewrites to      │
                         │  /chat-landing page (SPA)       │
                         │  • Reads neoToken cookie        │
                         │  • POSTs /api/me/instance/start │
                         │  • Polls /status                │
                         │  • Redirects → chat-<id>.…      │
                         └────────────┬────────────────────┘
                                      │
                                      │ window.location.replace
                                      ▼
                ┌─────────────────────────────────────────────┐
                │  chat-<userId>.neowow.studio                │
                │  ─────────────────────────────────────────  │
                │  • DNS A record points at user's ECS IP     │
                │  • Caddy on the ECS handles TLS (LE)        │
                │  • Caddy proxies → hermes-webui:7891        │
                │  • The actual chat surface                  │
                └─────────────────────────────────────────────┘
```

## Code drops (already committed)

| File | Purpose |
|------|---------|
| `dashboard/src/middleware.ts` | Detect `Host: chat.neowow.studio` → rewrite to `/chat-landing` |
| `dashboard/src/app/chat-landing/page.tsx` | The broker SPA: cookie check → call `/api/me/instance/start` → poll `/status` → redirect |

No new API code needed — the `/api/me/instance/{start,status,stop,heartbeat}`
endpoints already ship (Phase 2 M1–M3 commits `111a25d`, `7543963`,
`8b75cd4`).

---

## Deployment steps (manual, one-time)

Do these in order. Each step is reversible (rollback notes at the
bottom).

### Step 1 — Add `chat.neowow.studio` as a custom domain on the dashboard Worker

The dashboard is deployed to Cloudflare via OpenNext (`npm run
deploy:cf`). To make `chat.neowow.studio` hit the same worker:

1. Cloudflare dashboard → **Workers & Pages** → `neowow-studio-dashboard`
2. **Settings** → **Domains & Routes** → **Add custom domain**
3. Enter `chat.neowow.studio` and click **Add Domain**.
4. CF auto-issues a TLS cert (~30s). Hold here until the status row
   shows "Active" with a green check.

> **Why "custom domain" and not "Worker Route"?** Custom domains
> auto-provision TLS and are routed through Cloudflare's edge; routes
> require you to manage the cert yourself. Custom domain is simpler.

### Step 2 — Change `chat.neowow.studio` DNS to point at the Worker

Before this step, `chat.neowow.studio` is an A record pointing at the
Tencent Cloud box's IP. We're moving it to the Worker.

1. Cloudflare DNS for `neowow.studio` zone
2. Find the existing `chat` record (A type, points at Tencent IP).
3. **Edit** → change Type to **CNAME**, Target to
   `neowow-studio-dashboard.<your-account>.workers.dev` (the worker
   subdomain).
4. Keep proxy toggle **orange (proxied)** — CF needs to terminate TLS.
5. Save.

DNS propagation: usually <1 min via Cloudflare. Verify:

```bash
dig +short chat.neowow.studio
# Should return the worker's IPs (104.21.x.x or similar), NOT Tencent.

curl -sI https://chat.neowow.studio/ | head -5
# Should show: server: cloudflare
```

### Step 3 — Add `chat-<owner-userId>.neowow.studio` so the existing Tencent box keeps working under the new hostname

Pick the owner's 19-digit userId (from `/api/me/whoami` on the
dashboard). Replace `<USER_ID>` below.

1. Cloudflare DNS → **Add record**:
   - Type: **A**
   - Name: `chat-<USER_ID>`
   - IPv4: (Tencent Cloud box's public IP — same as `chat`'s old value)
   - Proxy status: **DNS only** (grey cloud) — Caddy on the Tencent
     box handles TLS itself; CF proxying would conflict with LE
     HTTP-01 challenges.
2. Save.

### Step 4 — Update the Tencent box's Caddyfile to serve the new hostname

SSH to the Tencent Cloud box:

```bash
ssh <tencent-user>@<tencent-ip>

# Backup
sudo cp /opt/hermes-docker/Caddyfile /opt/hermes-docker/Caddyfile.bak.$(date +%s)

# Replace the old %DOMAIN% (= chat.neowow.studio) with chat-<USER_ID>
sudo sed -i 's/^chat\.neowow\.studio /chat-<USER_ID>.neowow.studio /' /opt/hermes-docker/Caddyfile

# Verify
grep '\.neowow\.studio' /opt/hermes-docker/Caddyfile  # should show new hostname

# Reload Caddy — picks up new domain, requests a fresh LE cert (~30s)
cd /opt/hermes-docker
sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# Watch the cert dance
sudo docker compose logs -f caddy --tail=50 | grep -iE "cert|tls|acme"
```

You should see ACME HTTP-01 challenge succeed within ~30s, after
which `https://chat-<USER_ID>.neowow.studio/` will work.

### Step 5 — Pre-seed the broker's TableStore row for the owner

The owner already has a working Tencent instance, but no
`inst_<userId>` row in TableStore — the broker doesn't know about it.
Add it manually so the broker treats the Tencent box as the owner's
existing instance:

> **TODO** — write a small admin endpoint or run an `aliyun-cli`
> command here. Until that's built, the first time the owner hits
> `chat.neowow.studio` after this migration, the broker will try to
> spawn a NEW instance (Aliyun ECS, once that provider exists). For
> now you can either:
>   - Skip this step and accept the owner gets a fresh Aliyun spawn
>     later (the Tencent box stays alive for fallback).
>   - Add a `inst_<userId>` row manually via TableStore console with
>     fields matching `lib/instance-store.ts:InstanceRow`.

### Step 6 — Deploy the dashboard with new middleware + landing page

```bash
cd /Users/ff/aliyun-supa/dashboard
npm run deploy:cf
```

After deploy, smoke test:

```bash
# 1. New user, no cookie — should serve the landing HTML (not 302)
curl -sI https://chat.neowow.studio/ | head -3
# Expect: 200 OK + content-type: text/html

# 2. With a stale neoToken cookie — same response (landing handles auth in JS)
curl -sI -H 'Cookie: neoToken=invalid' https://chat.neowow.studio/ | head -3
# Expect: 200 OK

# 3. The /chat-landing path direct hit (would normally be invisible
#    behind the rewrite, but reachable directly from app.neowow.studio)
curl -sI https://app.neowow.studio/chat-landing | head -3
# Expect: 200 OK (the page works from either host)
```

### Step 7 — Visual / E2E verification

In a real browser:

1. **Logged-out**: open Incognito → visit `https://chat.neowow.studio`
   → should see the landing card → JS redirects to
   `app.neowow.studio/api/oauth/start` → after login, returns with
   `#neo_session=` fragment → broker call → redirect.
2. **Logged-in (owner)**: visit `https://chat.neowow.studio` →
   landing card flashes briefly → redirect to
   `chat-<USER_ID>.neowow.studio` → chat works as before.

---

## Rollback

If anything's wrong, **revert DNS in Step 2** — change the `chat`
record back to A → Tencent IP. Cloudflare custom domain (Step 1) and
the new `chat-<USER_ID>` record (Step 3) can stay; they're harmless
when unused. The Caddyfile change (Step 4) is harmless too — Caddy
will serve both hostnames if both DNS records resolve to it (but only
`chat-<USER_ID>` will have a working TLS cert).

To fully revert: also reverse Step 4 (the sed in reverse) and reload
Caddy.

---

## Known gaps / next steps

1. **Aliyun provider not yet implemented** — broker will fall back to
   the existing Tencent HK provider. Cost-wise that's fine for the
   short term; users get HK instances. Once `lib/cloud/aliyun.ts` is
   in, set `HERMES_CLOUD_PROVIDER=aliyun` env var and redeploy.

2. **State migration of the existing Tencent box** — owner's
   `~/.hermes/` lives on Tencent disk. When we eventually destroy
   that box, anything not synced to OSS will be lost. The OSS sync
   script (`oss-sync.sh`) runs on shutdown for Phase 2 instances; the
   existing Tencent box doesn't have it. Action: run `oss-sync.sh`
   manually inside the Tencent container before decommission, or copy
   the OSS sync logic into the existing Caddy/WebUI compose.

3. **Custom domain auto-provisioning** — currently the operator adds
   `chat.neowow.studio` to the worker UI by hand. Adding per-user
   `chat-<userId>.neowow.studio` domains is API-driven (Cloudflare
   API + the broker's DNS upsert already exists in
   `lib/cloudflare-dns.ts`), but if we ever want a single Cloudflare
   Worker to serve PER-USER chat hostnames too (e.g. for SSR landing
   on chat-XXX), we'd need to either programmatically add each as a
   custom domain (no API for that) or use a wildcard route
   (`*.neowow.studio/*`). For now per-user chats are served by their
   own Caddy on the ECS, so no Worker routing needed there.

4. **Subscription gate UX**: the `/chat-landing` page shows a
   subscription paywall when broker returns 402. Make sure
   `/account` has a clear "Subscribe" button that lands them on
   neowow.studio's recharge flow. Currently `/account` shows the
   Coding Plan / Points cards — fine for now but might need a
   dedicated "Phase 2 plans" tab once we have Per-Instance pricing.
