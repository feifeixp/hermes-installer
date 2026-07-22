# Setup and account guide

This guide explains account sign-in, provider setup, and recovery paths in
Hermes WebUI. There is no first-run blocking wizard: the workspace always opens
directly.

If an AI assistant is helping with install, reinstall, bootstrap, provider
setup, or first-run support, read
[`docs/onboarding-agent-checklist.md`](onboarding-agent-checklist.md) before
running commands or inspecting logs.

The short version: run the bootstrap, open the WebUI, then start a chat. The
main workspace is available immediately. Sign in from the account avatar when
you want to use Neowow Coding Plan; after the explicit sign-in succeeds, the
app prepares the plan in the background. If you are using a local model server
from Docker, pay special attention to the Base URL section below.

## Before you start

Hermes WebUI is only the browser interface. The actual agent runtime, memory,
skills, config, cron jobs, and provider credentials belong to Hermes Agent.

The bootstrap supports Linux, macOS, and WSL2. Native Windows is not supported
by the bootstrap yet. A community native Windows setup is being tracked in
[#1952](https://github.com/nesquena/hermes-webui/issues/1952), including:

- [Native Windows guide](https://github.com/markwang2658/hermes-windows-native-guide)
- [Native Windows setup scripts](https://github.com/markwang2658/hermes-windows-native)

For Windows users who want the supported path today, use WSL2 and see
[Windows / WSL auto-start](wsl-autostart.md).

## Install path choices

| Path | Use it when | Notes |
|---|---|---|
| Local bootstrap | You run WebUI directly on Linux, macOS, or WSL2 | Best for a personal server, Mac mini, VPS, or homelab host. |
| Docker single-container | You want the simplest container setup | Recommended first Docker path. WebUI runs the agent in-process. |
| Docker two-container | You already run the agent gateway separately | More isolated, but tools launched from WebUI run in the WebUI container. |
| Docker three-container | You want agent gateway plus dashboard plus WebUI | Same caveats as two-container, plus the dashboard service. |
| Native Windows community path | You are intentionally testing unsupported native Windows | Community-maintained for now, not the official bootstrap path. |

If a Docker install gets confusing, start again with the single-container setup.
It avoids most UID/GID, source-volume, and tool-location surprises. See
[Docker setup guide](docker.md) for the full container reference.

## Testing setup safely

Do not delete `~/.hermes` just to test account or provider setup. That directory can hold
your real Hermes config, credentials, memory, skills, profiles, sessions, and
cron state.

For a clean local trial, use an isolated Hermes home and WebUI state directory:

```bash
mkdir -p ~/hermes-onboarding-test
HERMES_HOME=~/hermes-onboarding-test/.hermes \
HERMES_WEBUI_STATE_DIR=~/hermes-onboarding-test/webui \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

Then open `http://127.0.0.1:8789`.

For an assistant-led trial run, follow the safety rules, evidence commands, and
pass/fail criteria in
[`docs/onboarding-agent-checklist.md`](onboarding-agent-checklist.md).

If your repo has a `.env` file, remember that the bootstrap loads it. Remove or
adjust any `HERMES_HOME`, `HERMES_WEBUI_STATE_DIR`, or `HERMES_WEBUI_PORT`
entries there before using the isolated command above.

## Neodomain sign-in

Neodomain account sign-in is available in both desktop and managed online
deployments. Desktop OAuth returns only to the running Hermes server on
`localhost` or `127.0.0.1`; other callback targets are rejected. Managed online
deployments continue to use their configured Neodomain authentication mode.

After OAuth completes, the app explicitly activates the Coding Plan and refreshes
the plan/model data in the background. The main workspace stays available;
failure is shown as a retryable notification instead of a blocking setup page.

## What the app checks

The account and provider surfaces report the runtime state WebUI can see:

- Hermes Agent importability: whether WebUI can import and run `AIAgent`.
- Provider status: whether `config.yaml` and credential state are enough for a
  chat request.
- Password status: whether WebUI password protection is enabled.
- Config paths: the active `config.yaml` and `.env` locations for this profile.

If the agent check fails, use [Troubleshooting](troubleshooting.md), especially
the `AIAgent not available` section. If provider setup is incomplete, configure
it in Settings or run `hermes model` in the same machine environment that will
run WebUI.

## Configuring a provider

The setup step groups providers by how much information they usually need.

| Group | Examples | What you usually enter |
|---|---|---|
| Easy start | OpenRouter, Anthropic, OpenAI | API key and model. |
| Open / self-hosted | Ollama, LM Studio, custom OpenAI-compatible | Base URL, model, optional API key. |
| Specialized | Gemini, DeepSeek, Xiaomi MiMo, Z.AI / GLM, NVIDIA NIM, Mistral, xAI | Provider API key and default model. |

For API-key providers, Settings writes the key to the active Hermes `.env` file
and writes the default model/provider to `config.yaml`.

For local providers, the API key field can be blank when the server is keyless.
Most LM Studio, Ollama, vLLM, llama-server, and TabbyAPI installs run this way.
Use the provider connection check in Settings to verify the Base URL and
populate the model list before saving.

Advanced provider flows such as Nous Portal and GitHub Copilot are still
terminal-first. If Settings points you back to `hermes model`, use that CLI
flow first, then refresh WebUI.

## Base URL rules for local model servers

For self-hosted providers, the Base URL should point to the OpenAI-compatible
API root. Common examples:

| Server | Typical Base URL |
|---|---|
| LM Studio on the same non-Docker host | `http://127.0.0.1:1234/v1` |
| Ollama on the same non-Docker host | `http://127.0.0.1:11434/v1` |
| LM Studio from Docker Desktop | `http://host.docker.internal:1234/v1` |
| Ollama from Docker Desktop | `http://host.docker.internal:11434/v1` |
| Local server on another LAN machine | `http://<lan-ip>:<port>/v1` |

Inside Docker, `localhost` means the WebUI container itself, not your Mac,
Windows host, or another machine on your LAN. If LM Studio or Ollama is running
outside the container, use `host.docker.internal` on Docker Desktop or the
server's LAN IP address.

The provider connection check probes `<base-url>/models` before saving. A
successful probe fills the model dropdown. A failed check reports DNS failure,
connection refused, timeout, HTTP error, or an unexpected response shape.

## Workspace step

The workspace is the filesystem location Hermes should use for new sessions.
It can be a source checkout, a project directory, or a general workspace folder.

In Docker, the default browsable path is `/workspace`, which maps to the host
directory mounted by the compose file. If the workspace appears empty, check the
Docker UID/GID and mount guidance in [Docker setup guide](docker.md).

## Password step

Password protection is optional for localhost-only installs. Enable it if you
expose WebUI outside `127.0.0.1`, behind a reverse proxy, or on a LAN.

The password is stored through the normal WebUI settings path and hashed
server-side. You can change it later from Settings.

## What gets written

The wizard uses the same files and APIs as the normal app:

- Active Hermes `config.yaml`: provider, default model, and Base URL when
  relevant.
- Active Hermes `.env`: provider API keys when you entered one.
- WebUI `settings.json`: onboarding completion, workspace, password state, and
  other WebUI preferences.

State normally lives outside the repository. By default:

- Hermes Agent state: Windows `%LOCALAPPDATA%\hermes`; POSIX `~/.hermes`
- WebUI state: `$HERMES_HOME/webui` (Windows default `%LOCALAPPDATA%\hermes\webui`, POSIX default `~/.hermes/webui`)

Override these with `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` when you need an
isolated test install.

## When to file an issue

File an issue when the diagnostics point to WebUI rather than local
configuration. Include:

The in-app **Report issue** action first shows a redacted preview and lets the
user choose which log categories to include. Nothing is uploaded until the user
gives explicit confirmation. If the upload endpoint is temporarily unavailable,
the bundle is stored as a private pending report for a later retry.

1. Install path: local bootstrap, Docker single-container, Docker
   two-container, Docker three-container, WSL2, or community native Windows.
2. Output from `/health`, or the startup banner if the server never starts.
3. The provider selected in onboarding and the Base URL shape, with secrets
   redacted.
4. For Docker provider problems, the result of probing from inside the
   container, for example:

```bash
docker exec hermes-webui sh -c 'curl -sS -w "\nHTTP %{http_code}\n" http://host.docker.internal:1234/v1/models | head -50'
```

5. Any account or provider error text and relevant logs.

Never paste API keys, OAuth tokens, or full `.env` contents into an issue.
