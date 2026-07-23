# Coding Plan provider integration — what to know before touching

This document captures the architecture + failure modes of the
**Neowow Coding Plan** provider registration so future engineers
don't re-walk the Phase β.10 → ζ debugging path.

## TL;DR — the three-layer contract

A working Coding Plan chat dispatch requires THREE things to agree
on the same provider id:

| Layer | File | What it stores |
|-------|------|----------------|
| UI label | `webui/api/onboarding.py:_NEOWOW_CODING_PLAN_PROVIDER_ID` | `"neowow-coding-plan"` — what the wizard / settings panel show |
| Runtime config | `webui/api/onboarding.py:_NEOWOW_RUNTIME_PROVIDER` | `"neowow-coding-plan"` — what gets written to `config.yaml`'s `model.provider` field |
| Agent registry | `hermes_cli/auth.py:PROVIDER_REGISTRY` (injected by `docker/patch_hermes_agent.py`) | A `ProviderConfig(id="neowow-coding-plan", ...)` entry with `inference_base_url`, `api_key_env_vars`, etc. |

If any one of the three drifts from the other two, chat fails at
dispatch time with **"Unknown provider 'xxx'"** or **"no API key was
found"** — even though the WebUI looks healthy and the container's
`/health` endpoint returns ok.

## How we got here — the false starts

| Phase | What it tried | Why it failed |
|-------|---------------|---------------|
| β.10 | `provider: neowow-coding-plan` directly to `config.yaml` | hermes_cli auto-derives env var from provider name → looked for `NEOWOW-CODING-PLAN_API_KEY` (with hyphens) which is unfriendly and not what we write. PROVIDER_REGISTRY had no matching entry → "Unknown provider" |
| β.14 | Switched to `provider: openai` thinking it's the openai-compat fall-through | `PROVIDER_REGISTRY['openai']` exists as a KEY but maps to **`{}`** (an empty placeholder). `dict.get('openai', {})` returns the empty dict; agent treats this as "registered but no real config" → "no API key was found" no matter what env var is set |
| β.16 | Switched to `provider: custom` | `'custom'` isn't in PROVIDER_REGISTRY at all. Same "Unknown provider" failure |
| ζ | Registered `neowow-coding-plan` as a REAL `ProviderConfig` entry in the agent | Works because all three layers now agree on the same id |

**Key insight**: `dict.get(name, {})` silently "succeeds" for both
"key missing" AND "key present but empty". The agent CLI's registered-
but-empty placeholder entries (`openai`, possibly others) made our
"is it registered?" check return false positives. Always use
`name in registry` AND `bool(registry[name])` checks.

## How to add a new Coding-Plan-style provider

Recipe — 5 steps.

### 1. Pick the canonical id

Use kebab-case: `coding-plan-foo`. This single string must be the
SAME everywhere — WebUI UI label, config.yaml value, PROVIDER_REGISTRY
key. Don't try to be clever with one-id-for-UI / another-for-runtime;
that's what caused β.10 → ζ.

### 2. Add the agent registration patch

Edit `docker/patch_hermes_agent.py`:

- New `_AUTH_PY_MARKER` + `_AUTH_PY_INJECT` block with the
  `ProviderConfig(id="coding-plan-foo", ...)` entry. Mirror the
  existing `neowow-coding-plan` block. Set `api_key_env_vars` as
  a tuple of (`CODING_PLAN_FOO_API_KEY`, ...fallbacks).
- New `_PROVIDERS_PY_MARKER` + `_PROVIDERS_PY_INJECT` for the
  `HermesOverlay` entry (transport=`openai_chat` if it's OpenAI-
  compat).
- Update the `_patch_*` functions to do the new injections.

### 3. Add the runtime constants in WebUI

In `webui/api/onboarding.py`:

```python
_CODING_PLAN_FOO_PROVIDER_ID = "coding-plan-foo"          # UI label
# (same string used as the canonical name everywhere)
```

And in `_SUPPORTED_PROVIDER_SETUPS`:

```python
_CODING_PLAN_FOO_PROVIDER_ID: {
    "label":        "Coding Plan Foo (推荐 · ...)",
    "env_var":      "CODING_PLAN_FOO_API_KEY",            # canonical
    "default_model": "...",
    "default_base_url": "https://...",
    "requires_base_url": False,
    "models":       [...],
    "category":     "easy_start",
    "quick":        True,
    "key_optional": True,
}
```

### 4. Verify with the safety gate

`_agent_recognizes_provider(provider_id)` in `onboarding.py` checks
`PROVIDER_REGISTRY` for the entry at write-time. If you forgot to
update the patch script, `apply_onboarding_setup` raises with a
clear "provider not registered" message instead of silently writing
a broken config.yaml. Run the wizard once to verify the gate fires.

**Scoping note**: The write-time gate enforces only against the
`_NEOWOW_RUNTIME_PROVIDER` name used by the explicit
`POST /api/neowow/activate-provider` flow. Automatic onboarding is limited to
repairing already-complete legacy installs; it does not silently activate a
new account.
It does NOT block other curated providers like `openrouter` or
`custom`, which hermes_cli handles via special-case logic in
`resolve_provider()` rather than `PROVIDER_REGISTRY` (they're
intentionally excluded from the dict — see the comment around
`auth.py:453`). Apply the gate uniformly and you'll reject those
known-good aggregators. The runtime `startup_check.py` is the
broader safety net — it logs ERROR for ANY config.yaml provider
that isn't dispatchable, regardless of source.

The first-run status endpoint reads the Coding Plan catalog from the static
fallback or last-good disk cache so opening the wizard never waits on the
network. Explicit provider activation and the live model endpoint refresh from
`ga.neodomain.cn`; the current fallback catalog includes `kimi-k3`.

Managed builds keep the workspace blocked until both login and runtime
readiness are confirmed. `/api/chat/start` and `/api/chat` also reject requests
without a Neowow JWT, so bypassing the browser gate cannot start the Agent.
Existing logged-in installs that are not yet ready must use the explicit
`POST /api/neowow/activate-provider` action; normal boot never activates the
provider silently.

Saving or clearing the JWT and completing provider activation invalidate both
the configured-model cache and the live-model cache. The browser then forces a
fresh `/api/models/live` request, preventing the logged-out
`deepseek-v4-flash` fallback from hiding Kimi, Gemini, or other models included
in the user's current plan.

### 5. Verify CI

`docker/patch_hermes_agent.py` now:
- Verifies file-content markers are present AFTER patching (catches
  partial / failed sed-like inject).
- Imports `hermes_cli.auth` and asserts `provider_id in
  PROVIDER_REGISTRY` (catches a patched file that's syntactically
  invalid → can't be imported).
- Exits **non-zero** if either check fails, so the Dockerfile
  `RUN python3 patch_hermes_agent.py` step aborts the image build.
  No more shipping a broken image that boots "healthy" but every
  chat returns "Unknown provider".

## Operational tips

### How to tell which layer is broken

When a user reports "chat returns Unknown provider 'X'":

1. **Check the agent registry**:
   ```bash
   docker exec hermes-webui /opt/hermes/.hermes/hermes-agent/venv/bin/python3 -c "
   from hermes_cli.auth import PROVIDER_REGISTRY
   print('X' in PROVIDER_REGISTRY)
   if 'X' in PROVIDER_REGISTRY:
       e = PROVIDER_REGISTRY['X']
       print('has inference_base_url:', hasattr(e, 'inference_base_url'))
   "
   ```
   - `False` → patch didn't run / install missed it. Re-run
     `docker/patch_hermes_agent.py`.
   - `True` but `has inference_base_url: False` → entry is a placeholder
     `{}`. Patch the inject content.

2. **Check config.yaml**:
   ```bash
   docker exec hermes-webui grep -A2 "^model:" /opt/hermes/.hermes/config.yaml
   ```
   `provider: X` — confirm X matches the registry id from step 1.

3. **Check .env**:
   ```bash
   docker exec hermes-webui grep "^X_API_KEY\|^OPENAI_API_KEY" /opt/hermes/.hermes/.env
   ```
   The agent reads `api_key_env_vars` in order; first non-empty match wins.

### How to manually patch a running container (production hotfix)

If you can't rebuild the image (CI broken / Aliyun ACR auth failing):

```bash
# Inside the host shell:
docker exec hermes-webui sh -c '
  curl -fsSL https://raw.githubusercontent.com/feifeixp/hermes-installer/main/docker/patch_hermes_agent.py \
    -o /tmp/patch.py
  /opt/hermes/.hermes/hermes-agent/venv/bin/python3 /tmp/patch.py \
    --agent-dir /opt/hermes/.hermes/hermes-agent
'
docker compose stop hermes-webui
docker compose rm -f hermes-webui
docker compose up -d hermes-webui
```

Container fully recreated so `hermes_cli.auth` re-imports the patched
file. **`docker compose restart` is not sufficient** — the agent CLI
caches imports per-process, and restart sometimes reuses the same PID.

### Detecting drift in production

The webui's `api/startup_check.py` runs `run_startup_checks()` on boot
and logs ERROR-level messages if:
- `config.yaml` has a `model.provider` that's not in PROVIDER_REGISTRY
- `HERMES_NEOWOW_ONLY=1` but no Coding Plan API key in env

Watch `docker logs hermes-webui` after every deploy. The user-facing
WebUI keeps working (we don't crash on config mismatch — Settings UI
needs to be reachable so users can re-onboard), but the loud log
makes it obvious to ops.

## Why we don't use a fork of hermes-agent

Considered: maintain a `feifeixp/hermes-agent` fork with our providers
baked in. Pros: clean, semantic. Cons: every upstream commit requires
rebase or merge; CI complexity; potential for the local mod to break
silently on rebase.

The patch-after-install approach (`docker/patch_hermes_agent.py`) is
**idempotent + self-verifying**:
- Re-running is a no-op once registered
- Verification fails the build if upstream renamed `PROVIDER_REGISTRY`
- We can pull fresh upstream commits at any time; patch re-applies
- Less moving infrastructure (no separate fork repo)

If/when upstream accepts a multi-tenant Coding Plan provider as a
first-class entry, we can remove the patch + simplify.
