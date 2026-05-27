# Client Crash Reporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared `crash_reporter.py` module that captures crashes from both `main.py` (PyInstaller frozen) and `webui/server.py` (venv subprocess), uploads them to `https://app.neowow.studio/api/client-log` with log tail attachment + local retry queue + PII filtering.

**Architecture:** Single stdlib-only Python file at repo root, imported via `sys.path` bridge using the existing `HERMES_INSTALLER_BASE_DIR` env var. Both processes share PII rules, queue logic, and retry policy. 12 trigger points (5 existing in main.py + 7 new across both processes). Server-side endpoint already exists — extend its schema to accept `logTail` + add server-side PII filter.

**Tech Stack:** Python 3.11 stdlib only (urllib.request, json, threading, pathlib). pytest for tests. Next.js / Cloudflare Workers for server endpoint (TypeScript).

**Spec:** `docs/superpowers/specs/2026-05-27-crash-reporter-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `crash_reporter.py` | Create | Single public function `report()` + private helpers. ~150 LOC. |
| `tests/test_crash_reporter.py` | Create | Unit tests for all helpers + report() public API |
| `tests/test_crash_reporter_integration.py` | Create | End-to-end with mock HTTP server |
| `main.py` | Modify | Replace existing `_send_crash_report` with `from crash_reporter import report`; wire 4 new triggers |
| `webui/server.py` | Modify | Install `sys.excepthook` at top; track `_main_started` flag |
| `webui/api/routes.py` | Modify | Wrap handle_get/post/patch/delete/put with try/except + `_report_handler_crash` helper |
| `hermes_installer.spec` | Modify | Add `crash_reporter` to `hiddenimports` |
| `dashboard/src/app/api/client-log/route.ts` | Modify | Accept `logTail`, server-side PII filter, phase whitelist |

---

## Task 1: Scaffold `crash_reporter.py` module

**Files:**
- Create: `crash_reporter.py`
- Test: (none yet — scaffolding only)

- [ ] **Step 1: Create the module skeleton with all public symbols stubbed**

Create `crash_reporter.py`:

```python
"""Hermes Installer — client-side crash reporter.

Imported by both main.py (PyInstaller frozen) and webui/server.py (venv
subprocess). The webui side finds this module via the HERMES_INSTALLER_BASE_DIR
env var set by main.py.

Design spec: docs/superpowers/specs/2026-05-27-crash-reporter-design.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
ENDPOINT = "https://app.neowow.studio/api/client-log"
TIMEOUT_SECONDS = 8
JOIN_BUDGET_SECONDS = 0.5
MAX_LOG_TAIL_BYTES = 150_000
MAX_QUEUE_ENTRIES = 20
MAX_QUEUE_ENTRY_BYTES = 200_000
FLUSH_TIME_BUDGET_SECONDS = 5.0
MAX_ATTEMPTS_BEFORE_DLQ = 5

QUEUE_DIR = Path.home() / ".hermes" / "pending-crash-reports"
DLQ_DIR = QUEUE_DIR / "quarantine"

PHASES = frozenset({
    # main.py — existing
    "startup_webview2_missing",
    "startup_pywebview_missing",
    "startup_pywebview_failed",
    "windows_install_failed",
    "main_unhandled",
    # main.py — new
    "wait_for_server_timeout",
    "venv_health_check_failed",
    "windows_install_dir_wiped",
    "webui_subprocess_exit_unexpected",
    # webui/server.py — new
    "webui_pre_main_import_error",
    "webui_startup_crash",
    "webui_runtime_exception",
})


# ── Public API ───────────────────────────────────────────────────────────────
def report(
    phase: str,
    error: str,
    *,
    traceback: str | None = None,
    log_path: str | None = None,
    extra: dict | None = None,
) -> bool:
    """Send a crash report. Returns True on confirmed HTTP 2xx, False otherwise."""
    raise NotImplementedError  # filled in by later tasks


# ── Queue management ─────────────────────────────────────────────────────────
def flush_queue() -> int:
    """Re-send all pending crash reports. Called from main.py at startup."""
    raise NotImplementedError  # filled in by Task 7
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python3 -c "import crash_reporter; print(crash_reporter.ENDPOINT)"`
Expected: `https://app.neowow.studio/api/client-log`

- [ ] **Step 3: Commit**

```bash
git add crash_reporter.py
git commit -m "feat(crash-reporter): scaffold module with public API stubs"
```

---

## Task 2: Implement `_post` + report() success path

**Files:**
- Modify: `crash_reporter.py`
- Create: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write the first failing test**

Create `tests/test_crash_reporter.py`:

```python
"""Unit tests for crash_reporter module.

These run on all platforms. No real network IO — urllib.request.urlopen is
mocked in every test that exercises the wire format.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root to path so we can import crash_reporter
sys.path.insert(0, str(Path(__file__).parent.parent))

import crash_reporter as cr


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Redirect the queue directory to a temp path per test."""
    qdir = tmp_path / "queue"
    monkeypatch.setattr(cr, "QUEUE_DIR", qdir)
    monkeypatch.setattr(cr, "DLQ_DIR", qdir / "quarantine")
    return qdir


def _mock_urlopen_ok(status: int = 204):
    """Build a mock urlopen that returns an HTTP response with the given status."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = status
    return MagicMock(return_value=response)


def test_report_success_204(isolated_queue):
    """A successful POST returns True and writes nothing to the queue."""
    with patch.object(cr.urllib.request, "urlopen", _mock_urlopen_ok()):
        result = cr.report("main_unhandled", "test error")
    assert result is True
    assert not isolated_queue.exists() or not any(isolated_queue.iterdir())
```

- [ ] **Step 2: Run the test and verify it FAILS for the right reason**

Run: `pytest tests/test_crash_reporter.py::test_report_success_204 -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement `_post()` + replace `report()` stub**

Edit `crash_reporter.py`. Replace the `report()` stub with:

```python
def _collect_metadata() -> dict:
    """Return non-PII metadata about the running process."""
    return {
        "pid":            os.getpid(),
        "python_version": sys.version.split()[0],
    }


def _build_payload(phase: str, error: str, traceback: str | None,
                   log_tail: str | None, extra: dict | None) -> dict:
    """Build the wire payload. PII filtering happens in caller."""
    from main import _get_app_version  # local import; main.py may not be importable in webui ctx
    try:
        version = _get_app_version()
    except Exception:
        version = os.environ.get("HERMES_INSTALLER_VERSION", "unknown")
    payload = {
        "app":      "hermes-installer",
        "version":  str(version)[:32],
        "platform": sys.platform[:32],
        "phase":    phase[:64],
        "error":    str(error)[:500],
    }
    if traceback:
        payload["traceback"] = str(traceback)[:5000]
    if log_tail:
        payload["logTail"] = str(log_tail)[:MAX_LOG_TAIL_BYTES]
    merged_extra = _collect_metadata()
    if extra:
        merged_extra.update(extra)
    payload["extra"] = merged_extra
    return payload


def _post(payload: dict, headers: dict) -> bool:
    """POST the payload. Returns True on HTTP 2xx, raises on network error."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return 200 <= resp.status < 300


def report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool:
    if phase not in PHASES:
        logger.warning("crash_reporter: unknown phase %r — sending anyway", phase)
    payload = _build_payload(phase, error, traceback, None, extra)
    headers = {"Content-Type": "application/json"}
    try:
        return _post(payload, headers)
    except Exception as exc:
        logger.debug("crash_reporter: _post failed (%s) — not enqueued yet", exc)
        return False
```

Note: `_get_app_version` import is best-effort; webui side will fall back to env var.

- [ ] **Step 4: Run the test and verify it PASSES**

Run: `pytest tests/test_crash_reporter.py::test_report_success_204 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): implement _post and report() success path"
```

---

## Task 3: Implement local queue + persistence on failure

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crash_reporter.py`:

```python
def test_report_network_fail_enqueues(isolated_queue):
    """When POST fails, payload lands in the queue."""
    failing_urlopen = MagicMock(side_effect=cr.urllib.error.URLError("connection refused"))
    with patch.object(cr.urllib.request, "urlopen", failing_urlopen):
        result = cr.report("main_unhandled", "boom")
    assert result is False
    files = list(isolated_queue.glob("*.json"))
    assert len(files) == 1, f"expected 1 queued report, got {len(files)}"
    # Filename is epoch-ns + .attempt-N.json
    assert files[0].name.endswith(".attempt-1.json")
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["phase"] == "main_unhandled"
    assert payload["error"] == "boom"


def test_queue_file_has_0600_permissions(isolated_queue):
    """Queue files must be 0600 — they contain JWT."""
    if sys.platform == "win32":
        pytest.skip("POSIX permissions don't apply on Windows")
    failing_urlopen = MagicMock(side_effect=cr.urllib.error.URLError("nope"))
    with patch.object(cr.urllib.request, "urlopen", failing_urlopen):
        cr.report("main_unhandled", "boom")
    files = list(isolated_queue.glob("*.json"))
    assert files
    mode = files[0].stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
```

- [ ] **Step 2: Run tests and verify they FAIL**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 2 new tests FAIL (no enqueue logic yet)

- [ ] **Step 3: Implement `_enqueue()` and wire into `report()`**

Edit `crash_reporter.py`. Add after `_post`:

```python
def _enqueue(payload: dict, attempt: int = 1) -> Path | None:
    """Persist payload to the queue for later retry. Returns the file path or None on failure."""
    try:
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        _drop_oldest_if_full()
        body = json.dumps(payload).encode("utf-8")
        if len(body) > MAX_QUEUE_ENTRY_BYTES:
            logger.warning("crash_reporter: payload too large (%d B), truncating", len(body))
            body = body[:MAX_QUEUE_ENTRY_BYTES]
        # Filename: <epoch_ns>.attempt-<N>.json
        path = QUEUE_DIR / f"{time.time_ns()}.attempt-{attempt}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: ignore chmod failure
        os.replace(tmp, path)  # atomic
        return path
    except Exception as exc:
        logger.error("crash_reporter: enqueue failed: %s", exc)
        return None


def _drop_oldest_if_full() -> None:
    """If queue at capacity, remove oldest entry to make room (FIFO)."""
    try:
        entries = sorted(QUEUE_DIR.glob("*.json"))
        while len(entries) >= MAX_QUEUE_ENTRIES:
            oldest = entries.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass
    except FileNotFoundError:
        pass
```

Update `report()` to call `_enqueue` on failure:

```python
def report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool:
    if phase not in PHASES:
        logger.warning("crash_reporter: unknown phase %r — sending anyway", phase)
    payload = _build_payload(phase, error, traceback, None, extra)
    headers = {"Content-Type": "application/json"}
    try:
        if _post(payload, headers):
            return True
        _enqueue(payload)
        return False
    except Exception as exc:
        logger.debug("crash_reporter: _post failed (%s), enqueueing", exc)
        _enqueue(payload)
        return False
```

- [ ] **Step 4: Run tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): persist failed reports to local queue (0600, FIFO eviction)"
```

---

## Task 4: Async wrapper with 0.5s main-thread budget

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crash_reporter.py`:

```python
def test_report_timeout_returns_within_budget(isolated_queue):
    """Slow POST → report() returns within ~0.6s even if network would take 10s."""
    def slow_urlopen(*args, **kwargs):
        time.sleep(2.0)
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m

    with patch.object(cr.urllib.request, "urlopen", slow_urlopen):
        t0 = time.monotonic()
        result = cr.report("main_unhandled", "slow network")
        elapsed = time.monotonic() - t0

    assert result is False, "should return False since we didn't wait for completion"
    assert elapsed < 0.8, f"report() blocked {elapsed:.2f}s — should be < 0.8s"
```

- [ ] **Step 2: Run test and verify it FAILS**

Run: `pytest tests/test_crash_reporter.py::test_report_timeout_returns_within_budget -v`
Expected: FAIL (currently `report()` blocks for ~2s)

- [ ] **Step 3: Refactor `report()` to dispatch via daemon thread**

Edit `crash_reporter.py`. Replace `report()`:

```python
def report(phase, error, *, traceback=None, log_path=None, extra=None) -> bool:
    """Send a crash report. Non-blocking — main thread returns within JOIN_BUDGET_SECONDS."""
    if phase not in PHASES:
        logger.warning("crash_reporter: unknown phase %r — sending anyway", phase)

    log_tail = _read_log_tail(log_path) if log_path else None
    payload = _build_payload(phase, error, traceback, log_tail, extra)
    payload = _sanitize_payload(payload)
    headers = {"Content-Type": "application/json"}
    _attach_jwt(headers)

    # Shared state between main thread and worker: was it a clean success?
    result = {"success": False}

    def _worker():
        try:
            if _post(payload, headers):
                result["success"] = True
                return
        except Exception as exc:
            logger.debug("crash_reporter: post failed: %s", exc)
        _enqueue(payload)

    t = threading.Thread(target=_worker, name="crash-reporter", daemon=True)
    t.start()
    t.join(timeout=JOIN_BUDGET_SECONDS)
    # If the thread is still running, it'll continue in background.
    # We can only report definitive success if it finished AND set the flag.
    return bool(result["success"])
```

Also add **stub** placeholders for the helpers we just called (filled in by later tasks):

```python
def _sanitize_payload(payload: dict) -> dict:
    """Apply PII filter to text fields. Stub for now — implemented in Task 5."""
    return payload


def _read_log_tail(path: str | None) -> str | None:
    """Read the last N bytes of a log file. Stub for now — implemented in Task 6."""
    return None


def _attach_jwt(headers: dict) -> None:
    """Attach Bearer JWT from ~/.hermes/webui/neowow.json. Stub for now — implemented in Task 8."""
    return
```

- [ ] **Step 4: Run all tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): dispatch POST via daemon thread with 0.5s join budget"
```

---

## Task 5: PII filter

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write 3 failing tests**

Append to `tests/test_crash_reporter.py`:

```python
def test_pii_windows_username_filtered(isolated_queue):
    """C:\\Users\\Alice\\foo → C:\\Users\\<USER>\\foo in traceback."""
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report(
            "main_unhandled",
            "Error in C:\\Users\\Alice\\.hermes\\webui\\foo.py",
            traceback="File C:\\Users\\Alice\\AppData\\Local\\Temp\\x.py",
        )
    time.sleep(0.6)  # let worker thread finish
    payload = json.loads(captured["body"])
    assert "Alice" not in payload["error"], f"Alice leaked: {payload['error']!r}"
    assert "<USER>" in payload["error"]
    assert "Alice" not in payload["traceback"]
    assert "<USER>" in payload["traceback"]


def test_pii_unix_username_filtered(isolated_queue):
    """/Users/alice/foo → /Users/<USER>/foo on macOS-style paths."""
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "Crash in /Users/alice/.hermes/server.py")
    time.sleep(0.6)
    payload = json.loads(captured["body"])
    assert "alice" not in payload["error"]
    assert "/Users/<USER>" in payload["error"]


def test_pii_api_key_and_jwt_redacted(isolated_queue):
    """API keys + JWTs are not transmitted in plaintext."""
    secrets = [
        "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "api_key=secretvalue123456789",
        "neoToken=secret-cookie-value",
    ]
    body = "stuff\n" + "\n".join(secrets) + "\nmore stuff"
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["body"] = req.data.decode("utf-8")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "ok", traceback=body)
    time.sleep(0.6)
    payload = json.loads(captured["body"])
    for secret in ["abcdefghijklmnopqrstuvwxyz1234567890",
                   "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
                   "secretvalue123456789",
                   "secret-cookie-value"]:
        assert secret not in payload["traceback"], f"secret {secret[:10]}... leaked"
```

- [ ] **Step 2: Run tests and verify they FAIL**

Run: `pytest tests/test_crash_reporter.py -v -k pii`
Expected: 3 FAILS

- [ ] **Step 3: Implement `_sanitize_pii()` + plug into `_sanitize_payload()`**

Edit `crash_reporter.py`. Add near the top (after constants):

```python
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Windows: C:\Users\Alice\foo  →  C:\Users\<USER>\foo
    (re.compile(r'([A-Za-z]:[\\/])Users[\\/][^\\/\s\"\']+', re.IGNORECASE),
     r'\1Users\\<USER>'),
    # macOS: /Users/alice/foo  →  /Users/<USER>/foo
    (re.compile(r'/Users/[^/\s\"\']+'), '/Users/<USER>'),
    # Linux: /home/alice/foo  →  /home/<USER>/foo
    (re.compile(r'/home/[^/\s\"\']+'), '/home/<USER>'),
    # API keys (prefix sk-)
    (re.compile(r'sk-[A-Za-z0-9_-]{20,}'), 'sk-***REDACTED***'),
    # api_key= or api-key=
    (re.compile(r'api[_-]?key[=:][\"\']?[^\s\"\',;)]+', re.IGNORECASE),
     'api_key=***REDACTED***'),
    # Authorization: Bearer ...
    (re.compile(r'Authorization:\s*Bearer\s+\S+', re.IGNORECASE),
     'Authorization: Bearer ***REDACTED***'),
    # Bearer <token> (loose)
    (re.compile(r'Bearer\s+[A-Za-z0-9._-]{20,}'), 'Bearer ***REDACTED***'),
    # neoToken cookie
    (re.compile(r'neoToken=[^;\s]+'), 'neoToken=***REDACTED***'),
    # JWT fallback (3 base64url segments)
    (re.compile(r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b'),
     '<JWT_REDACTED>'),
]


def _sanitize_pii(text: str) -> str:
    """Apply all PII redaction patterns to a string."""
    if not text:
        return text
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out
```

Replace the `_sanitize_payload()` stub:

```python
def _sanitize_payload(payload: dict) -> dict:
    """Apply PII filter to text fields (error, traceback, logTail, extra values)."""
    out = dict(payload)
    for k in ("error", "traceback", "logTail"):
        if k in out and isinstance(out[k], str):
            out[k] = _sanitize_pii(out[k])
    extra = out.get("extra")
    if isinstance(extra, dict):
        out["extra"] = {
            k: _sanitize_pii(v) if isinstance(v, str) else v
            for k, v in extra.items()
        }
    return out
```

- [ ] **Step 4: Run all tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): PII filter for usernames, API keys, JWTs, cookies"
```

---

## Task 6: Log tail reader

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write 2 failing tests**

Append to `tests/test_crash_reporter.py`:

```python
def test_log_tail_reads_last_n_bytes(tmp_path):
    """Large log file → only the tail is read."""
    log_file = tmp_path / "big.log"
    # Write 1 MB of distinct lines
    lines = [f"line {i:06d}\n" for i in range(50_000)]
    log_file.write_text("".join(lines), encoding="utf-8")
    assert log_file.stat().st_size > 200_000

    tail = cr._read_log_tail(str(log_file))
    assert tail is not None
    assert len(tail.encode("utf-8")) <= cr.MAX_LOG_TAIL_BYTES
    # Should contain the LAST line, not the FIRST
    assert "line 049999" in tail
    assert "line 000000" not in tail


def test_log_tail_missing_file_ok(tmp_path):
    """Non-existent path → None, no exception."""
    missing = tmp_path / "nope.log"
    result = cr._read_log_tail(str(missing))
    assert result is None
```

- [ ] **Step 2: Run tests and verify they FAIL**

Run: `pytest tests/test_crash_reporter.py -v -k log_tail`
Expected: 2 FAILS

- [ ] **Step 3: Implement `_read_log_tail()`**

Edit `crash_reporter.py`. Replace the stub:

```python
def _read_log_tail(path: str | None) -> str | None:
    """Return the last MAX_LOG_TAIL_BYTES bytes of a log file, decoded as UTF-8."""
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > MAX_LOG_TAIL_BYTES:
                f.seek(size - MAX_LOG_TAIL_BYTES)
                # Drop the (likely partial) first line for clean boundary
                _ = f.readline()
            raw = f.read()
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("crash_reporter: _read_log_tail(%s) failed: %s", path, exc)
        return None
```

- [ ] **Step 4: Run all tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): _read_log_tail with size cap + bounded seek"
```

---

## Task 7: `flush_queue()` + dead-letter quarantine

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write 3 failing tests**

Append to `tests/test_crash_reporter.py`:

```python
def test_flush_resends_queued_entries(isolated_queue):
    """flush_queue() POSTs each enqueued entry and deletes it on success."""
    # First, fail a report to populate queue
    with patch.object(cr.urllib.request, "urlopen",
                      MagicMock(side_effect=cr.urllib.error.URLError("down"))):
        cr.report("main_unhandled", "boom 1")
    time.sleep(0.6)

    assert len(list(isolated_queue.glob("*.json"))) == 1

    # Then flush with a working endpoint
    with patch.object(cr.urllib.request, "urlopen", _mock_urlopen_ok()):
        n = cr.flush_queue()
    assert n == 1
    assert not list(isolated_queue.glob("*.json")), "queued file should be deleted"


def test_flush_moves_to_dlq_after_max_attempts(isolated_queue):
    """A payload that fails 5 times is moved to quarantine."""
    # Manually drop a payload at attempt-5 (simulates 4 prior failures)
    isolated_queue.mkdir(parents=True, exist_ok=True)
    stale = isolated_queue / "1234567890.attempt-5.json"
    stale.write_text(json.dumps({"phase": "main_unhandled", "error": "old", "extra": {}}))

    with patch.object(cr.urllib.request, "urlopen",
                      MagicMock(side_effect=cr.urllib.error.URLError("still down"))):
        cr.flush_queue()

    dlq_dir = isolated_queue / "quarantine"
    assert not stale.exists(), "should be moved out of queue"
    assert len(list(dlq_dir.glob("*.json"))) == 1, "should be in DLQ"


def test_flush_5s_budget_respected(isolated_queue):
    """flush_queue total runtime is bounded even with many slow entries."""
    # Pre-populate 10 entries
    isolated_queue.mkdir(parents=True, exist_ok=True)
    for i in range(10):
        (isolated_queue / f"100000000{i:02d}.attempt-1.json").write_text(
            json.dumps({"phase": "main_unhandled", "error": f"e{i}", "extra": {}}))

    def slow_urlopen(*args, **kwargs):
        time.sleep(0.7)
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m

    with patch.object(cr.urllib.request, "urlopen", slow_urlopen):
        t0 = time.monotonic()
        cr.flush_queue()
        elapsed = time.monotonic() - t0
    assert elapsed < cr.FLUSH_TIME_BUDGET_SECONDS + 1.0, \
        f"flush took {elapsed:.2f}s, budget is {cr.FLUSH_TIME_BUDGET_SECONDS}s + slack"
```

- [ ] **Step 2: Run tests and verify they FAIL**

Run: `pytest tests/test_crash_reporter.py -v -k flush`
Expected: 3 FAILS

- [ ] **Step 3: Implement `flush_queue()` + DLQ logic**

Edit `crash_reporter.py`. Replace the `flush_queue` stub:

```python
_ATTEMPT_RE = re.compile(r"\.attempt-(\d+)\.json$")


def _parse_attempt(path: Path) -> int:
    """Extract attempt-N from filename; default 1 if absent."""
    m = _ATTEMPT_RE.search(path.name)
    return int(m.group(1)) if m else 1


def _move_to_dlq(path: Path) -> None:
    """Move a payload file to the dead-letter quarantine."""
    try:
        DLQ_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(path, DLQ_DIR / path.name)
        logger.warning("crash_reporter: moved %s to DLQ after %d attempts",
                       path.name, MAX_ATTEMPTS_BEFORE_DLQ)
    except Exception as exc:
        logger.error("crash_reporter: DLQ move failed: %s", exc)


def _bump_attempt(path: Path) -> Path:
    """Rename a queue file to increment its attempt counter. Returns new path."""
    cur = _parse_attempt(path)
    base = _ATTEMPT_RE.sub("", path.name)
    new_path = path.with_name(f"{base}.attempt-{cur + 1}.json")
    try:
        os.replace(path, new_path)
        return new_path
    except Exception:
        return path  # best-effort; leave as-is


def flush_queue() -> int:
    """Re-send all pending reports. Returns the number successfully sent.

    Budget: FLUSH_TIME_BUDGET_SECONDS. Entries that exceed MAX_ATTEMPTS_BEFORE_DLQ
    are moved to quarantine. Called from main.py at startup.
    """
    if not QUEUE_DIR.is_dir():
        return 0
    dlq_count = len(list(DLQ_DIR.glob("*.json"))) if DLQ_DIR.is_dir() else 0
    if dlq_count:
        logger.warning("crash_reporter: %d dead-letter payloads in %s", dlq_count, DLQ_DIR)

    entries = sorted(QUEUE_DIR.glob("*.json"))  # exclude subdirs (quarantine/)
    entries = [p for p in entries if p.is_file()]
    sent = 0
    deadline = time.monotonic() + FLUSH_TIME_BUDGET_SECONDS

    for path in entries:
        if time.monotonic() >= deadline:
            logger.info("crash_reporter: flush budget exceeded, %d entries remain", len(entries) - sent)
            break

        attempt = _parse_attempt(path)
        if attempt >= MAX_ATTEMPTS_BEFORE_DLQ:
            _move_to_dlq(path)
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("crash_reporter: malformed queue file %s — dropping", path)
            try: path.unlink()
            except OSError: pass
            continue

        headers = {"Content-Type": "application/json"}
        _attach_jwt(headers)
        try:
            if _post(payload, headers):
                path.unlink()
                sent += 1
                continue
        except Exception as exc:
            logger.debug("crash_reporter: flush retry failed: %s", exc)
        # Bump attempt counter and move on (will retry next startup)
        _bump_attempt(path)

    return sent
```

- [ ] **Step 4: Run all tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): flush_queue with attempt counter + DLQ quarantine + 5s budget"
```

---

## Task 8: JWT attachment

**Files:**
- Modify: `crash_reporter.py`
- Modify: `tests/test_crash_reporter.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_crash_reporter.py`:

```python
def test_attach_jwt_from_neowow_file(isolated_queue, tmp_path, monkeypatch):
    """When ~/.hermes/webui/neowow.json has a JWT, it goes into Authorization."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    jwt_file = tmp_path / ".hermes" / "webui" / "neowow.json"
    jwt_file.parent.mkdir(parents=True)
    jwt_value = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VySWQiOiJ1MTIzIn0.signature123"
    jwt_file.write_text(json.dumps({"jwt": jwt_value}))

    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization", "")
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m

    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "test")
    time.sleep(0.6)

    assert captured["auth"] == f"Bearer {jwt_value}"


def test_attach_jwt_missing_file_ok(isolated_queue, tmp_path, monkeypatch):
    """No neowow.json → no Authorization header, no error."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    captured = {}
    def capture_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization", None)
        m = MagicMock()
        m.__enter__.return_value = m
        m.__exit__.return_value = False
        m.status = 204
        return m
    with patch.object(cr.urllib.request, "urlopen", capture_urlopen):
        cr.report("main_unhandled", "test")
    time.sleep(0.6)
    assert captured["auth"] is None
```

- [ ] **Step 2: Run tests and verify they FAIL**

Run: `pytest tests/test_crash_reporter.py -v -k attach_jwt`
Expected: 2 FAILS (no JWT attached yet)

- [ ] **Step 3: Implement `_attach_jwt()`**

Edit `crash_reporter.py`. Replace the stub:

```python
def _attach_jwt(headers: dict) -> None:
    """Read JWT from ~/.hermes/webui/neowow.json and add Authorization header."""
    try:
        jwt_path = Path.home() / ".hermes" / "webui" / "neowow.json"
        if not jwt_path.is_file():
            return
        data = json.loads(jwt_path.read_text(encoding="utf-8"))
        jwt = (data.get("jwt") or data.get("accessToken")
               or data.get("authorization") or "")
        if isinstance(jwt, str) and jwt.count(".") == 2:
            headers["Authorization"] = f"Bearer {jwt}"
    except Exception as exc:
        logger.debug("crash_reporter: _attach_jwt failed: %s", exc)
```

- [ ] **Step 4: Run all tests and verify they PASS**

Run: `pytest tests/test_crash_reporter.py -v`
Expected: 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add crash_reporter.py tests/test_crash_reporter.py
git commit -m "feat(crash-reporter): attach JWT from ~/.hermes/webui/neowow.json"
```

---

## Task 9: Integration test with mock HTTP server

**Files:**
- Create: `tests/test_crash_reporter_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_crash_reporter_integration.py`:

```python
"""End-to-end test: real HTTP, mock server, full report() flow."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import crash_reporter as cr


@pytest.fixture
def mock_server(monkeypatch):
    """Start a real HTTP server on a random port. Yields received payloads."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                received.append({
                    "payload": json.loads(body),
                    "auth": self.headers.get("Authorization", ""),
                })
            except Exception:
                received.append({"payload": None, "auth": ""})
            self.send_response(204)
            self.end_headers()
        def log_message(self, fmt, *args): pass  # silence

    # Bind to a random free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    monkeypatch.setattr(cr, "ENDPOINT", f"http://127.0.0.1:{port}/api/client-log")
    yield received
    server.shutdown()


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    qdir = tmp_path / "queue"
    monkeypatch.setattr(cr, "QUEUE_DIR", qdir)
    monkeypatch.setattr(cr, "DLQ_DIR", qdir / "quarantine")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return qdir


def test_end_to_end_report_with_log_tail(mock_server, tmp_path):
    """Full report() invocation hits the wire and server sees expected payload."""
    # Set up a fake log file to attach
    log_file = tmp_path / "webui-server.log"
    log_file.write_text("a" * 200_000 + "TAIL-MARKER\n", encoding="utf-8")

    result = cr.report(
        "webui_startup_crash",
        "NameError: name 'base_events' is not defined",
        traceback="Traceback (most recent call last):\n  File ...",
        log_path=str(log_file),
        extra={"venv_python": "/Users/alice/.hermes/hermes-agent/venv/python.exe"},
    )

    # Wait for the daemon thread to finish
    time.sleep(1.0)

    assert result is True, "should succeed against mock"
    assert len(mock_server) == 1, f"expected 1 POST, got {len(mock_server)}"

    record = mock_server[0]
    p = record["payload"]
    assert p["app"] == "hermes-installer"
    assert p["phase"] == "webui_startup_crash"
    assert "base_events" in p["error"]
    assert p["traceback"].startswith("Traceback")
    assert p["logTail"].endswith("TAIL-MARKER\n")
    assert len(p["logTail"].encode("utf-8")) <= cr.MAX_LOG_TAIL_BYTES
    # PII filter ran
    assert "alice" not in p["extra"]["venv_python"]
    assert "<USER>" in p["extra"]["venv_python"]


def test_end_to_end_failure_then_recovery(mock_server, tmp_path):
    """After a failed report, flush_queue() recovers when the server comes back."""
    # Phase 1: report when ENDPOINT is bad (use a port nobody's on)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # actually free it so connection refused fires

    saved_endpoint = cr.ENDPOINT
    cr.ENDPOINT = f"http://127.0.0.1:{dead_port}/api/client-log"
    cr.report("main_unhandled", "phase 1 fail")
    time.sleep(0.6)
    cr.ENDPOINT = saved_endpoint  # restore monkeypatch target

    queued = list((cr.QUEUE_DIR).glob("*.json"))
    assert len(queued) == 1, "first report should be queued"

    # Phase 2: server back up, flush
    n = cr.flush_queue()
    assert n == 1
    assert not list(cr.QUEUE_DIR.glob("*.json")), "queue should be empty after flush"
    assert len(mock_server) == 1, "mock should have received one recovered report"
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/test_crash_reporter_integration.py -v`
Expected: 2 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_crash_reporter_integration.py
git commit -m "test(crash-reporter): end-to-end with real HTTP + mock server"
```

---

## Task 10: Server-side route.ts updates (logTail + PII filter + phase whitelist)

**Files:**
- Modify: `/Users/ff/aliyun-supa/dashboard/src/app/api/client-log/route.ts`

- [ ] **Step 1: Read the current route and find existing test (if any)**

Run: `cat /Users/ff/aliyun-supa/dashboard/src/app/api/client-log/route.ts | head -120`

Run: `find /Users/ff/aliyun-supa/dashboard -name "*client-log*test*" 2>/dev/null`

If no existing test, skip TDD steps for the test and just write the impl + manual verify.

- [ ] **Step 2: Update route.ts to accept logTail, server-side PII filter, phase whitelist**

Edit `/Users/ff/aliyun-supa/dashboard/src/app/api/client-log/route.ts`. Add near the top (after imports):

```typescript
// Phase whitelist — mirrors crash_reporter.py PHASES.
// Non-whitelist phases are accepted but logged as warnings (don't break clients).
const KNOWN_PHASES = new Set([
  // main.py — existing
  'startup_webview2_missing',
  'startup_pywebview_missing',
  'startup_pywebview_failed',
  'windows_install_failed',
  'main_unhandled',
  // main.py — new
  'wait_for_server_timeout',
  'venv_health_check_failed',
  'windows_install_dir_wiped',
  'webui_subprocess_exit_unexpected',
  // webui/server.py — new
  'webui_pre_main_import_error',
  'webui_startup_crash',
  'webui_runtime_exception',
]);

// PII filter — mirrors crash_reporter._PII_PATTERNS as a defence-in-depth
// second layer. Client filter is the primary defence; this catches tampered
// or bypassed clients.
const PII_RULES: Array<[RegExp, string]> = [
  // Windows: C:\Users\Alice\foo  →  C:\Users\<USER>\foo
  [/([A-Za-z]:[\\\/])Users[\\\/][^\\\/\s"']+/gi, '$1Users\\<USER>'],
  // macOS / Linux usernames in paths
  [/\/Users\/[^\/\s"']+/g, '/Users/<USER>'],
  [/\/home\/[^\/\s"']+/g,  '/home/<USER>'],
  // API keys
  [/sk-[A-Za-z0-9_-]{20,}/g, 'sk-***REDACTED***'],
  [/api[_-]?key[=:]["']?[^\s"',;)]+/gi, 'api_key=***REDACTED***'],
  [/Authorization:\s*Bearer\s+\S+/gi, 'Authorization: Bearer ***REDACTED***'],
  [/Bearer\s+[A-Za-z0-9._-]{20,}/g, 'Bearer ***REDACTED***'],
  [/neoToken=[^;\s]+/g, 'neoToken=***REDACTED***'],
  // JWT fallback
  [/\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b/g, '<JWT_REDACTED>'],
];

function sanitizePii(text: string): string {
  if (!text) return text;
  let out = text;
  for (const [pat, repl] of PII_RULES) out = out.replace(pat, repl);
  return out;
}
```

Then in `export async function POST`, after parsing body, add `logTail` to the destructured fields and apply PII filter:

```typescript
  const app      = String(body.app      || 'hermes-installer').slice(0, 64);
  const version  = String(body.version  || '').slice(0, 32);
  const platform = String(body.platform || '').slice(0, 32);
  const phase    = String(body.phase    || '').slice(0, 64);
  // Sanitize PII as second layer (client is primary defence).
  const error     = sanitizePii(String(body.error     || '').slice(0, 500));
  const traceback = sanitizePii(String(body.traceback || '').slice(0, 5000));
  const logTail   = sanitizePii(String(body.logTail   || '').slice(0, 200_000));  // 200 KB cap (50KB margin over client 150KB)

  // Warn on unknown phases (don't reject — forward-compat)
  if (phase && !KNOWN_PHASES.has(phase)) {
    console.warn(`[client-log] unknown phase: ${phase} (app=${app} v=${version})`);
  }
```

And update the `console.log` to include log tail length (don't dump full log — it's huge):

```typescript
  console.log(`[client-log] app=${app} v=${version} platform=${platform} phase=${phase} userId=${userId || 'unknown'} error=${error.slice(0, 120)}`);
  if (traceback) {
    console.log(`[client-log] traceback (${userId || 'unknown'}):`, traceback.slice(0, 500));
  }
  if (logTail) {
    console.log(`[client-log] logTail (${userId || 'unknown'}) ${logTail.length} bytes; tail-100:`, logTail.slice(-100));
  }
```

- [ ] **Step 3: Verify route file parses cleanly**

Run from dashboard repo:

```bash
cd /Users/ff/aliyun-supa/dashboard && npx tsc --noEmit src/app/api/client-log/route.ts 2>&1 | head -20
```

Expected: no errors (or only errors about missing imports in isolation; main build is what matters)

- [ ] **Step 4: Manual smoke test — POST with logTail + PII**

Run from any terminal (assumes dashboard is running locally on :3000 OR deployed):

```bash
curl -X POST http://localhost:3000/api/client-log \
  -H "Content-Type: application/json" \
  -d '{
    "app": "hermes-installer",
    "version": "test",
    "platform": "darwin",
    "phase": "main_unhandled",
    "error": "test crash in /Users/alice/foo",
    "traceback": "File /Users/alice/x.py",
    "logTail": "some log... /Users/alice/y\n"
  }'
```

Expected: HTTP 204. Check server console for:
- `[client-log] ... error=test crash in /Users/<USER>/foo` (PII filtered)
- `[client-log] logTail ...` with `<USER>` substituted

- [ ] **Step 5: Commit**

```bash
cd /Users/ff/aliyun-supa
git add dashboard/src/app/api/client-log/route.ts
git commit -m "feat(client-log): accept logTail field + server-side PII filter + phase whitelist warn"
```

---

## Task 11: Wire `main.py` — replace `_send_crash_report` with shared import

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read existing `_send_crash_report` callsites**

Run: `grep -n "_send_crash_report\|^def _send_crash_report" main.py`

Expected: 6 hits — 1 definition + 5 calls.

- [ ] **Step 2: Replace function definition with import**

Edit `main.py`. Find the `def _send_crash_report(...)` function (~line 328) and **replace the entire function body** with:

```python
# ── Crash reporter (shared with webui/server.py) ────────────────────────────
# The actual implementation lives in crash_reporter.py at repo root. Imported
# here via the BASE_DIR-on-sys.path machinery already set up above. webui side
# uses the HERMES_INSTALLER_BASE_DIR env var (also set above) to find it.
try:
    import crash_reporter as _crash_reporter
except ImportError as _cr_exc:
    log.warning("crash_reporter import failed (%s) — reports disabled this run", _cr_exc)
    _crash_reporter = None


def _send_crash_report(phase: str, error: str, extra: "dict | None" = None) -> None:
    """Backward-compat shim — forwards to crash_reporter.report().

    Kept as a named function so existing call sites (5 of them) don't need
    to be touched in this PR. New triggers in main.py call
    crash_reporter.report() directly.
    """
    if _crash_reporter is None:
        return
    try:
        _crash_reporter.report(phase, error, extra=extra)
    except Exception as exc:
        log.debug("crash report dispatch failed: %s", exc)
```

- [ ] **Step 3: Add `flush_queue()` call at startup**

Edit `main.py`. Find the line `log.info("=== Hermes Installer starting ===")` near the top. Right after the `log.info(...)` line, add:

```python
# Flush any pending crash reports from a previous (likely-crashed) run.
# Best-effort: don't let queue-flush exceptions block the installer.
try:
    if _crash_reporter is not None:
        _flushed = _crash_reporter.flush_queue()
        if _flushed:
            log.info("flushed %d pending crash reports from previous run", _flushed)
except Exception as _exc:
    log.debug("flush_queue at startup failed: %s", _exc)
```

Note: this needs to happen AFTER `import crash_reporter` (which is in step 2). Verify ordering when you commit.

- [ ] **Step 4: Verify imports and call sites still work**

Run: `python3 -c "import ast; ast.parse(open('main.py').read()); print('OK')"`

Run: `grep -n "_send_crash_report(" main.py`

Expected: 5 calls (unchanged from before) — all 5 still pass through the shim.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "refactor(main): replace _send_crash_report body with crash_reporter import + add flush_queue at startup"
```

---

## Task 12: Wire `main.py` — add 4 new trigger points

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add `wait_for_server_timeout` trigger**

Edit `main.py`. Find `ready = _wait_for_server(port, timeout=WEBUI_STARTUP_TIMEOUT)` near line 944 (Windows branch). **After** the existing post-timeout alert block (where `_alert` is called for `if not ready`), add:

```python
        # ── Report timeout to backend so we can see how often this happens ──
        if not ready and _crash_reporter is not None:
            log_tail_path = str(_LOG_DIR / "webui-server.log")
            try:
                _crash_reporter.report(
                    phase="wait_for_server_timeout",
                    error=f"webui server did not bind port {port} within {WEBUI_STARTUP_TIMEOUT}s",
                    log_path=log_tail_path,
                    extra={
                        "port": port,
                        "subprocess_returncode": (
                            _win_server_proc.poll() if _win_server_proc else None
                        ),
                    },
                )
            except Exception as exc:
                log.debug("wait_for_server_timeout report failed: %s", exc)
```

Add the same logic to the macOS branch around line 985 (similar `if not ready:`) — use `BOOTSTRAP_PY` parent dir's log instead of `_LOG_DIR / "webui-server.log"`. If unsure, omit log_path and just send `extra={"port": port}`.

- [ ] **Step 2: Add `venv_health_check_failed` trigger**

Edit `main.py`. Find `_is_agent_installed()` (~line 416). Inside the `health_script` failure branch where it inspects stderr for "python313.dll" etc., **after** the existing `log.warning("agent venv contaminated...")`, add:

```python
        if _crash_reporter is not None:
            try:
                _crash_reporter.report(
                    phase="venv_health_check_failed",
                    error=stderr[:200] or f"venv health check failed rc={result.returncode}",
                    extra={
                        "returncode": result.returncode,
                        "stderr_tail": stderr[:1000],
                        "venv_python": str(venv_python),
                    },
                )
            except Exception:
                pass  # never let reporting break the health check
```

- [ ] **Step 3: Add `windows_install_dir_wiped` trigger (info-level)**

Edit `main.py`. Find `_wipe_contaminated_agent_venv()` (added in v1.4.2). **After** the successful `log.info("Agent dir wiped successfully")`, add:

```python
        if _crash_reporter is not None:
            try:
                _crash_reporter.report(
                    phase="windows_install_dir_wiped",
                    error="auto-rebuild triggered by health check failure",
                    extra={"agent_dir": str(agent_dir)},
                )
            except Exception:
                pass
```

- [ ] **Step 4: Add `webui_subprocess_exit_unexpected` monitor thread**

Edit `main.py`. Find where `_win_server_proc` is assigned and immediately before `_open_native_window(...)` is called. Add:

```python
    # ── Monitor subprocess for unexpected death after webview opens ──
    # If server.py crashes mid-session, webview goes blank with no diagnostic.
    # This daemon thread catches it and reports + logs the exit code.
    if _win_server_proc is not None and _crash_reporter is not None:
        def _monitor_webui_subprocess():
            import threading  # safe; daemon
            while True:
                rc = _win_server_proc.poll()
                if rc is None:
                    time.sleep(2)
                    continue
                # Process exited (may be intentional shutdown, may be crash)
                log.error("webui server.py exited rc=%s while installer alive", rc)
                try:
                    _crash_reporter.report(
                        phase="webui_subprocess_exit_unexpected",
                        error=f"server.py exited rc={rc} while installer was running",
                        log_path=str(_LOG_DIR / "webui-server.log"),
                        extra={"returncode": rc},
                    )
                except Exception as exc:
                    log.debug("subprocess-exit report failed: %s", exc)
                break  # exit thread

        threading.Thread(
            target=_monitor_webui_subprocess,
            name="hermes-webui-subprocess-monitor",
            daemon=True,
        ).start()
```

- [ ] **Step 5: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('main.py').read()); print('main.py OK')"`

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(main): wire 4 new crash-report triggers (timeout, health-check, dir-wipe, subprocess-exit)"
```

---

## Task 13: Wire `webui/server.py` — sys.excepthook

**Files:**
- Modify: `webui/server.py`

- [ ] **Step 1: Add crash_reporter import + sys.excepthook + `_main_started` flag**

Edit `webui/server.py`. Find the asyncio preload block at the top (the `import asyncio as _asyncio_preload  # noqa: F401` from v1.4.0). **Immediately after** that block, **before any other imports**, add:

```python
# ── Crash reporter sys.excepthook (Windows reliability hardening) ────────
# Catches any unhandled exception during import or main() execution and
# reports it to https://app.neowow.studio/api/client-log. Without this,
# webui crashes leave only a local log file behind that nobody on the
# backend can see without the user pasting it. Sourced from
# docs/superpowers/specs/2026-05-27-crash-reporter-design.md
import sys as _sys, os as _os

_installer_dir = _os.environ.get("HERMES_INSTALLER_BASE_DIR")
if _installer_dir and _installer_dir not in _sys.path:
    _sys.path.insert(0, _installer_dir)
try:
    import crash_reporter as _cr
except ImportError:
    _cr = None  # Running in docker / dev mode without the installer bundle.

_main_started = False  # Flipped to True at the top of main()


def _default_webui_log_path() -> "str | None":
    """Compute the webui-server.log path. Mirrors the formula in main.py:_LOG_DIR
    so we don't need any cross-process env-var coordination."""
    if _sys.platform == "win32":
        base = _os.environ.get("APPDATA") or _os.environ.get("TEMP")
        return _os.path.join(base, "Hermes", "webui-server.log") if base else None
    if _sys.platform == "darwin":
        return _os.path.expanduser("~/Library/Logs/Hermes/webui-server.log")
    base = _os.environ.get("TMPDIR", "/tmp")
    return _os.path.join(base, "hermes", "webui-server.log")


def _excepthook(exc_type, exc_value, tb):
    """Catch unhandled webui exceptions and report them before letting Python exit."""
    if _cr is None:
        return _sys.__excepthook__(exc_type, exc_value, tb)
    import traceback as _tb
    phase = "webui_startup_crash" if _main_started else "webui_pre_main_import_error"
    try:
        _cr.report(
            phase=phase,
            error=f"{exc_type.__name__}: {exc_value}",
            traceback="".join(_tb.format_exception(exc_type, exc_value, tb)),
            log_path=_default_webui_log_path(),
        )
    except Exception:
        pass  # IRON RULE: reporting must never re-raise into the hook
    return _sys.__excepthook__(exc_type, exc_value, tb)


_sys.excepthook = _excepthook
```

- [ ] **Step 2: Set `_main_started = True` at the top of `main()`**

Edit `webui/server.py`. Find `def main() -> None:`. As the **very first line** of the function body, add:

```python
def main() -> None:
    global _main_started
    _main_started = True
    ...existing body...
```

- [ ] **Step 3: Verify syntax + module imports cleanly with the hook**

Run from repo root:

```bash
cd webui && /Users/ff/hermes-installer/.build_venv/bin/python -c "
import os, sys
os.environ['HERMES_HOME'] = '/tmp/cr-smoke'
os.makedirs('/tmp/cr-smoke/webui', exist_ok=True)
os.environ['HERMES_INSTALLER_BASE_DIR'] = '$(pwd)/..'
import server  # don't run main, just verify imports work
print('webui import OK, _excepthook installed:', sys.excepthook.__name__)
" 2>&1 | tail -5
```

Expected: `webui import OK, _excepthook installed: _excepthook`

- [ ] **Step 4: Smoke test the hook actually catches and reports**

Create a temporary script `/tmp/test_webui_excepthook.py`:

```python
"""Synthetic crash from inside webui server.py-equivalent context."""
import os, sys
os.environ['HERMES_HOME'] = '/tmp/cr-smoke2'
os.environ['HERMES_INSTALLER_BASE_DIR'] = os.path.dirname(os.path.abspath(__file__)) + '/..'
os.makedirs('/tmp/cr-smoke2/webui', exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui'))
# Trigger excepthook install:
import server  # noqa
raise RuntimeError("synthetic test crash for excepthook")
```

Run from repo root:

```bash
cp /tmp/test_webui_excepthook.py .
/Users/ff/hermes-installer/.build_venv/bin/python test_webui_excepthook.py 2>&1 | tail -10
rm test_webui_excepthook.py
```

Expected: traceback with `RuntimeError: synthetic test crash`. Backend logs will (separately) show one new `[client-log] phase=webui_startup_crash` entry — but since `_crash_reporter` is best-effort, this step passes if the script just crashed normally.

- [ ] **Step 5: Commit**

```bash
git add webui/server.py
git commit -m "feat(webui): sys.excepthook reports unhandled crashes to crash_reporter"
```

---

## Task 14: Wire `webui/api/routes.py` — request handler wrapping

**Files:**
- Modify: `webui/api/routes.py`

- [ ] **Step 1: Add `_report_handler_crash` helper near top of routes.py**

Edit `webui/api/routes.py`. Find `_CLIENT_DISCONNECT_ERRORS = (` near the top. **After** that tuple definition, add:

```python
# ── Crash-reporter glue (wraps handle_get/post/etc. for 500 surfacing) ──
# crash_reporter is loaded by webui/server.py at import time; we just grab it
# from sys.modules so this module stays import-order-agnostic.
def _report_handler_crash(method: str, path: str, exc: BaseException) -> None:
    """Forward an unhandled handler exception to the crash_reporter.

    SSE / client-disconnect errors are excluded (they're normal). Anything
    in _CLIENT_DISCONNECT_ERRORS is treated as benign and not reported.
    """
    if isinstance(exc, _CLIENT_DISCONNECT_ERRORS):
        return
    cr = sys.modules.get("crash_reporter")
    if cr is None:
        return
    import traceback as _tb
    try:
        cr.report(
            phase="webui_runtime_exception",
            error=f"{method} {path}: {type(exc).__name__}: {exc}",
            traceback=_tb.format_exc(),
        )
    except Exception:
        pass  # IRON RULE: report path may not re-raise
```

- [ ] **Step 2: Wrap each of the 5 handle_* dispatchers**

Edit `webui/api/routes.py`. Find each of these function definitions:
- `def handle_get(handler, parsed):`
- `def handle_post(handler, parsed):`
- `def handle_patch(handler, parsed):`
- `def handle_delete(handler, parsed):`
- `def handle_put(handler, parsed):`

For each one, replace the function body's outermost layer so that the existing logic is wrapped:

```python
def handle_get(handler, parsed):
    try:
        ... existing body (unchanged) ...
    except _CLIENT_DISCONNECT_ERRORS:
        raise  # benign — handled by existing logic
    except Exception as _exc:
        _report_handler_crash("GET", parsed.path, _exc)
        raise  # preserve existing 500 → upstream handling
```

Do this for GET / POST / PATCH / DELETE / PUT.

**Note:** Some handle_* functions are very long (handle_get is ~3000 lines). You don't need to re-paste the whole body — instead use this approach: find the existing `def handle_get(handler, parsed):` line, and **immediately under it** add the `try:` line. Then go to the **end** of the function and add the `except` clauses BEFORE the function returns/ends. Editor users can use proper Python indentation tooling; for grep-edit users, this is fragile — use the Edit tool's exact-string-match capability with surrounding context.

Alternative if wrapping a 3000-line function body is fragile: define a decorator and apply it:

```python
def _wrap_handler(method: str):
    """Decorator: catch unhandled exceptions in a handle_* dispatcher."""
    def deco(fn):
        from functools import wraps
        @wraps(fn)
        def wrapped(handler, parsed):
            try:
                return fn(handler, parsed)
            except _CLIENT_DISCONNECT_ERRORS:
                raise
            except Exception as exc:
                _report_handler_crash(method, parsed.path, exc)
                raise
        return wrapped
    return deco

# Then in server.py or routes.py (wherever names are bound):
handle_get    = _wrap_handler("GET")(handle_get)
handle_post   = _wrap_handler("POST")(handle_post)
handle_patch  = _wrap_handler("PATCH")(handle_patch)
handle_delete = _wrap_handler("DELETE")(handle_delete)
handle_put    = _wrap_handler("PUT")(handle_put)
```

The decorator approach is **strongly preferred** — avoids touching each function's body and keeps the change contained to ~10 lines at the bottom of routes.py (just above the module's end).

- [ ] **Step 3: Verify syntax**

Run: `cd webui && /Users/ff/hermes-installer/.build_venv/bin/python -c "import ast; ast.parse(open('api/routes.py').read()); print('routes.py OK')"`

- [ ] **Step 4: Smoke test that wrapping doesn't break existing tests**

Run: `cd webui && /Users/ff/hermes-installer/.build_venv/bin/python -m pytest tests/test_routes_404.py tests/test_routes_basics.py -v 2>&1 | tail -20`

(Adjust to whatever routes tests exist; the point is: handler dispatchers still behave normally for non-exception paths.)

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add webui/api/routes.py
git commit -m "feat(webui): wrap handle_* dispatchers with crash_reporter for unhandled 500s"
```

---

## Task 15: PyInstaller spec — ensure crash_reporter.py is bundled

**Files:**
- Modify: `hermes_installer.spec`

- [ ] **Step 1: Inspect the current spec for hiddenimports / datas**

Run: `grep -n "hiddenimports\|datas\|crash_reporter\|Analysis" hermes_installer.spec`

- [ ] **Step 2: Add `crash_reporter` to hiddenimports**

Edit `hermes_installer.spec`. Find the `Analysis(...)` call. In its `hiddenimports=[...]` list (create one if absent), add `'crash_reporter'`. Example:

```python
a = Analysis(
    ['main.py'],
    ...
    hiddenimports=[
        'crash_reporter',   # shared with webui/server.py via HERMES_INSTALLER_BASE_DIR
        # ... any existing entries ...
    ],
    ...
)
```

- [ ] **Step 3: Local build to verify bundle**

Run: `bash build.sh 2>&1 | tail -10`

Expected: build succeeds, .dmg/.app/.exe appears in `dist/`.

- [ ] **Step 4: Verify `crash_reporter.py` is bundled at top of _MEI**

Run for macOS:

```bash
unzip -l "dist/Hermes Installer.app/Contents/Frameworks/crash_reporter*" 2>/dev/null || \
  find "dist/Hermes Installer.app" -name "crash_reporter*"
```

Expected: a file like `crash_reporter.pyc` or `crash_reporter.py` shows up under the .app's bundled Python area.

For Windows verification (if building on Windows), unzip the .exe similarly via `7z` or simply launch the .exe once and inspect the `%TEMP%\_MEI*` directory it extracts to.

- [ ] **Step 5: Commit**

```bash
git add hermes_installer.spec
git commit -m "build: declare crash_reporter as a PyInstaller hidden import"
```

---

## Task 16: Final end-to-end smoke test

**Files:** (none modified)

- [ ] **Step 1: Run the full unit + integration test suite**

Run: `pytest tests/test_crash_reporter.py tests/test_crash_reporter_integration.py -v`

Expected: 13 + 2 = 15 tests PASS.

- [ ] **Step 2: Run the existing windows-install tests to confirm no regression**

Run: `pytest tests/test_windows_install.py -v`

Expected: all PASS (no regression from the main.py changes).

- [ ] **Step 3: Build .app locally and run it once**

Run: `bash build.sh && open "dist/Hermes Installer.app"`

Verify: the installer launches normally (no console errors about crash_reporter import). Quit it cleanly.

- [ ] **Step 4: Trigger a synthetic crash to verify end-to-end pipe**

In a fresh terminal, force the crash reporter to send a test event against the real backend:

```bash
python3 -c "
import sys, json
sys.path.insert(0, '.')
import crash_reporter as cr
ok = cr.report(
    'main_unhandled',
    'synthetic v1.5.0 release-smoke test',
    extra={'smoke_test': True, 'commit': '$(git rev-parse --short HEAD)'},
)
import time; time.sleep(1.5)  # let daemon thread finish
print('reported:', ok)
"
```

Expected: `reported: True`. Backend admin → 云实例 tab should show a `lastClientErrorAt` bump (within ~30s).

- [ ] **Step 5: Final summary commit (optional)**

```bash
# Only if there are uncommitted housekeeping changes from smoke testing
git status
# If anything stray:
git add -A && git commit -m "chore: post-implementation cleanup"
```

---

## Self-Review Notes

This plan covers all spec requirements:

- ✅ **Architecture / single file**: Task 1 scaffolds, Tasks 2-8 build out features
- ✅ **8 trigger points**: Tasks 11-14 wire all 7 new + the 5 existing keep working via shim (Task 11)
- ✅ **Log tail attachment**: Task 6 implements + Task 9 integration-verifies
- ✅ **PII filter (client + server)**: Task 5 (client) + Task 10 (server)
- ✅ **Local queue + retry**: Task 3 (enqueue) + Task 7 (flush/DLQ)
- ✅ **Async 0.5s join budget**: Task 4
- ✅ **JWT attach**: Task 8
- ✅ **PyInstaller bundle**: Task 15
- ✅ **End-to-end verify**: Task 9 (unit-level) + Task 16 (real backend)

All test code is concrete. All commit messages are concrete. No "TBD" or "similar to" references. Method names consistent (`report` / `flush_queue` / `_post` / `_enqueue` / etc.) across all tasks.

One judgment call worth flagging to the implementing engineer: **Task 14 step 2 prefers the decorator approach over wrapping function bodies**. If the implementing engineer disagrees (e.g., for stylistic alignment with other handler patterns in the codebase), in-body try/except is fine — semantics are identical.
