# Optional Laya decision assistant

Open **Settings → Laya decision assistant** to configure a separately running
[Laya](https://github.com/NandhaKishorM/laya) service. Chinese and English copy is
available; other locales currently use English for this panel. No PyTorch or
Laya dependency is added to the WebUI process.

## Start a service

For a local trial, use a separate Python environment (Python 3.10+):

```sh
python3 -m venv .venv-laya
.venv-laya/bin/python -m pip install 'laya[serve]==0.3.20'
LAYA_HOST=127.0.0.1 LAYA_PORT=8082 LAYA_DEVICE=cpu \
  LAYA_MODELS=multilingual LAYA_THREADS=4 .venv-laya/bin/laya-serve
```

Initial startup downloads the checkpoint. Let it finish before testing. Adjust
thread count and device to your hardware. The settings panel does not install,
start, or supervise the service. For production, pin the model artifact and run
the service under your normal process manager. A remote service needs HTTPS;
configure its bearer authentication with `LAYA_API_KEY` and enter the same key
in the panel. Localhost always means the machine running the WebUI server.

## Configure and test

1. Enter the service base URL (default `http://127.0.0.1:8082`) and checkpoint.
   Use **Multilingual** for Chinese requests. A service must have the selected
   checkpoint available.
2. **Test connection** makes a real inference request with a fixed greeting, so
   it checks authentication and the response contract as well as connectivity.
3. **Classify request** sends only the entered sample. Both test buttons use the
   current unsaved form values and never invoke agent tools or save settings.
4. Select a mode and **Save settings**. Changes apply to subsequent WebUI chat
   turns, across all profiles in this server instance.

| Mode | Effect |
| --- | --- |
| Off (default) | No inference request during chat. |
| Observe only | Classify in a background thread; do not wait or alter the agent prompt. |
| Assist routing | Wait up to the configured deadline, then add a fixed workflow suggestion only when the result meets the threshold. |

The six workflow labels are text/explanation, research, coding, media
generation, canvas editing, and other/unclear. The default suggestion threshold
is 0.90; the default deadline is 500 ms. Probability is not a guarantee of
accuracy. Start with observation and evaluate actual product requests before
using suggestions. This is an advisory classifier, not a model selector or an
automatic tool dispatcher.

## Runtime and state boundaries

The hook runs immediately before the existing WebUI `AIAgent.run_conversation`
call. Only a per-turn system-message supplement is added. The selected model,
available tools, permissions, user text, persisted transcript, and session
identity are unchanged. CLI and messaging gateway turns are outside this hook.

Low probability, `other`, attachments, empty/overlong requests, cancellation,
timeouts, malformed responses, and service failures use the original flow.
At most two service calls run concurrently; saturation also falls back. A
wall-clock deadline bounds chat waiting, including DNS delays. A timed-out
transport thread can continue until it exits, but cannot later change the turn.

Inference receives the current text (maximum 6,000 characters) plus up to four
recent user/assistant text turns (each limited to 1,000 characters), with the
existing credential redactor applied. Tool messages and attachments are not
sent. Redaction does not make conversation text anonymous: choose an endpoint
you trust with that content.

Settings live in `HERMES_WEBUI_STATE_DIR/laya-settings.json` (the configured
WebUI state directory). Writes are atomic, with owner-only file permissions on
POSIX. API keys are stored in this local file and never returned to the browser.
Leaving the field empty preserves a saved key; the checkbox explicitly clears
it. Changing the endpoint clears the old key unless a new key is entered.
HTTP redirects and environment proxies are disabled for service requests.

The recent-results list contains at most 20 instance-wide entries, including
mode, time, label, probability, latency, and status. It contains no message text
or session IDs and disappears on restart. A result above threshold means it is
eligible as a suggestion, not that the agent followed it. Test-button results
are separate and do not enter this list.

The settings API uses the WebUI's existing authentication and CSRF checks:
`GET /api/laya`, `POST /api/laya`, and `POST /api/laya/test`.

## Rollback and verification

Save **Off** to stop new chat inference requests immediately; in-flight requests
finish under their previous configuration. The Laya process can then be stopped
independently. No conversation migration or model reconfiguration is needed.

Automated tests are in `tests/test_laya.py` and cover settings, secret handling,
the HTTP inference contract, response validation, observation, timeout fallback,
and the limited streaming hook. For a UI change, verify saving/reloading,
connection failure, unsaved test values, sample classification, navigation,
unsaved-change guards, and desktop/mobile layouts using isolated Hermes state.
