# Agent instructions for Hermes WebUI

This file is the shared entry point for AI assistants working in this
repository. Keep it project-specific and safe to publish. Do not put personal
machine setup, private network details, credentials, tokens, or local-only
workflow notes here.

## Read first

Before making changes, read:

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/CONTRACTS.md`
4. `CHANGELOG.md`

For architecture, testing, or setup work, also read the matching reference:

- `ARCHITECTURE.md` for design constraints and current module layout
- `TESTING.md` for local verification commands and manual test guidance
- `docs/onboarding.md` for first-run onboarding behavior
- `docs/troubleshooting.md` for diagnostic flows
- `docs/rfcs/README.md` for larger RFCs and state/durability contracts

For UI or UX work, read `docs/UIUX-GUIDE.md` and `DESIGN.md` before
changing layout, interaction flow, themes, chat rendering, or composer chrome.

## Onboarding and reinstall support

If the task involves install, reinstall, bootstrap, first-run onboarding,
provider setup, local model server setup, Docker onboarding, WSL onboarding, or
support for a failed first run, read `docs/onboarding-agent-checklist.md`
before running commands or inspecting logs.

Follow that checklist's safety rules:

- use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for trials unless the
  human explicitly asks to use real state
- do not delete or overwrite a real `~/.hermes` directory without explicit
  approval
- do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes
- collect non-secret status and log evidence before recommending a fix

## Contribution style

- Keep one logical change per PR; split unrelated refactors or cleanup.
- Read `docs/CONTRACTS.md` and the linked contract/RFC for the touched
  subsystem before editing.
- Prefer the existing Python + vanilla JavaScript structure. Do not add
  dependencies, build tools, frameworks, or long-lived processes without clear
  justification and a rollback story.
- Update docs when changing setup, onboarding, runtime behavior, architecture,
  testing guidance, or user-facing workflows.
- Update `CHANGELOG.md` for user-visible behavior, setup, workflow, or
  documentation changes that should be release-note ready.
- For UI or UX changes, include before/after evidence and test relevant
  desktop, narrow, and mobile states.
- For behavior changes, add or update automated tests where practical and list
  the manual verification performed.
- For runtime, streaming, recovery, replay, compression, or sidebar metadata
  changes, name the state layer being mutated and prove the relevant invariant.

## Local state and secrets

Hermes WebUI can read and write real agent state, sessions, workspaces,
credentials, and cron data. Treat local validation as potentially destructive
unless you have confirmed the active state directories.

Prefer isolated trial state for experiments:

```bash
HERMES_HOME=/tmp/hermes-webui-agent-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-agent-state \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

Do not include private machine instructions in this tracked file. Use a
git-ignored local note for personal workflow details.

## Neowow distribution overrides — preserve on upstream merge

This repository is a Neowow-distribution build of Hermes WebUI. Some files
have been intentionally diverged from upstream and **must not be overwritten**
when pulling upstream changes, rebasing, or applying refactors. When you see
a diff that "looks like a revert to upstream" against any of the paths below,
stop and confirm with the human first.

**Branded assets (web favicons, app icons, manifest art).** Custom Neowow
branding lives in:

- `webui/static/favicon.ico`
- `webui/static/favicon.svg`
- `webui/static/favicon-32.png`
- `webui/static/favicon-192.png`
- `webui/static/favicon-512.png`
- `webui/static/favicon-512.svg`

Treat these as user-owned artifacts. Generating a new icon set from a
template or "syncing favicons with upstream" will silently wipe the
customization. If `index.html` references additional icon paths (e.g.
`apple-touch-icon`), point those at one of the files above rather than
re-introducing the upstream filename.

**Appearance defaults.** `_SETTINGS_DEFAULTS` in `webui/api/config.py`
ships `theme="system"` + `skin="sienna"` (upstream default is
`theme="dark"` + `skin="default"`). The inline boot script in
`webui/static/index.html` and the `_SETTINGS_SKIN_VALUES` allowlist are
aligned with these. Keep them in sync if you touch either side.

**Default UI locale.** `_SETTINGS_DEFAULTS["language"]` is `"zh"` (upstream
ships `"en"`). The audience is primarily zh-CN.

**Bot identity / messaging copy.** Bot name comes from
`HERMES_WEBUI_BOT_NAME` env, defaulting to `"Hermes"`. Don't hardcode the
brand name into strings; check the env var.
