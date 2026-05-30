"""Cloud graceful-update — container-side control channel + apply decision.

The hardened webui container can't recreate itself; the host apply-watcher
(apply-update.sh) does the `docker compose up -d`. These are the container-side
pure/filesystem helpers it shares with the watcher: write activity, record the
user's「立即更新」request, read the host's update-available signal, and the
pure `should_apply` decision (the watcher mirrors it in shell).
"""
import json
import time
from pathlib import Path

_WEBUI = Path(__file__).resolve().parent.parent


# ── Wiring (source-grep, repo convention) ────────────────────────────────────

def test_routes_registered():
    src = (_WEBUI / "api" / "routes.py").read_text("utf-8")
    assert "/api/neowow/apply-update" in src
    assert "/api/neowow/update-available" in src


def test_server_starts_activity_writer_and_hooks_log():
    src = (_WEBUI / "server.py").read_text("utf-8")
    assert "self-update-activity" in src      # daemon thread
    assert "note_activity" in src             # request log hook


# ── should_apply (pure decision) ──────────────────────────────────────────────

def test_apply_when_user_requested():
    from api.self_update import should_apply
    # Even if busy, an explicit user click wins.
    assert should_apply(now=1000, activity={"ts": 999, "busy": True}, apply_requested=True) is True


def test_apply_when_no_activity_signal():
    from api.self_update import should_apply
    assert should_apply(now=1000, activity=None, apply_requested=False) is True


def test_no_apply_when_busy():
    from api.self_update import should_apply
    assert should_apply(now=10_000, activity={"ts": 0, "busy": True}, apply_requested=False) is False


def test_no_apply_when_recently_active():
    from api.self_update import should_apply
    # 60s of quiet, threshold 600 → still considered active.
    assert should_apply(now=1060, activity={"ts": 1000, "busy": False},
                        apply_requested=False, idle_secs=600) is False


def test_apply_when_idle_long_enough():
    from api.self_update import should_apply
    assert should_apply(now=1700, activity={"ts": 1000, "busy": False},
                        apply_requested=False, idle_secs=600) is True


# ── activity write/read round-trip ────────────────────────────────────────────

def test_activity_round_trip(tmp_path):
    from api.self_update import write_activity, read_activity
    write_activity(1234.0, True, control=tmp_path)
    got = read_activity(control=tmp_path)
    assert got == {"ts": 1234, "busy": True}


def test_read_activity_missing_is_none(tmp_path):
    from api.self_update import read_activity
    assert read_activity(control=tmp_path) is None


# ── apply request ─────────────────────────────────────────────────────────────

def test_request_apply_writes_flag(tmp_path):
    from api.self_update import request_apply
    res = request_apply(control=tmp_path)
    assert res["ok"] is True
    assert (tmp_path / "apply-requested").exists()


# ── activity tracking (request hook) ──────────────────────────────────────────

def test_note_activity_updates_timestamp():
    from api.self_update import note_activity, last_activity_ts
    note_activity("/api/chat/send", now=5000)
    assert last_activity_ts() == 5000


def test_health_and_poll_paths_are_ignored():
    from api.self_update import note_activity, last_activity_ts
    note_activity("/api/chat/send", now=5000)
    note_activity("/health", now=9999)
    note_activity("/api/neowow/update-available?x=1", now=9999)
    assert last_activity_ts() == 5000   # ignored paths didn't bump it


# ── update-available (host → container) ───────────────────────────────────────

def test_read_update_available(tmp_path):
    from api.self_update import read_update_available
    (tmp_path / "update-available").write_text(json.dumps({"image": "abc123"}), encoding="utf-8")
    assert read_update_available(control=tmp_path) == {"image": "abc123"}


def test_read_update_available_missing_is_none(tmp_path):
    from api.self_update import read_update_available
    assert read_update_available(control=tmp_path) is None
