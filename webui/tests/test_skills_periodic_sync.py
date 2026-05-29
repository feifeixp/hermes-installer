"""Periodic skill sync — pull subscriptions while the WebUI runs.

The startup sync only runs once. A user can subscribe to a skill in the
browser market (app.neowow.studio) AFTER the container booted; there's no
push from the dashboard. `sync_skills_if_token` is the guarded unit the
periodic loop calls every few minutes: it syncs only when credentials are
saved (so tokenless desktop installs stay quiet) and delegates the actual
pull to `sync_all_skills`.
"""
from pathlib import Path


def test_skips_sync_when_no_token():
    """No saved token → returns None and never calls the sync fn."""
    from api.skills import sync_skills_if_token

    calls = []
    result = sync_skills_if_token(
        read_state=lambda: {},
        sync=lambda: calls.append(1) or {"added": []},
    )
    assert result is None
    assert calls == []


def test_blank_token_is_treated_as_no_token():
    """Whitespace-only token → skip (same as missing)."""
    from api.skills import sync_skills_if_token

    calls = []
    result = sync_skills_if_token(
        read_state=lambda: {"token": "   "},
        sync=lambda: calls.append(1) or {},
    )
    assert result is None
    assert calls == []


def test_runs_sync_when_token_present():
    """A saved token → calls sync once and returns its summary verbatim."""
    from api.skills import sync_skills_if_token

    summary = {"added": [{"id": "skill-x"}], "updated": [], "removed": []}
    calls = []

    def fake_sync():
        calls.append(1)
        return summary

    result = sync_skills_if_token(
        read_state=lambda: {"token": "deploy-tok"},
        sync=fake_sync,
    )
    assert result is summary
    assert calls == [1]


def test_periodic_sync_loop_wired_into_server():
    """The WebUI must start a periodic-skill-sync daemon thread so browser
    subscriptions reach a running instance without a container rebuild."""
    server_src = (Path(__file__).resolve().parent.parent / "server.py").read_text("utf-8")
    assert "periodic-skill-sync" in server_src
    assert "sync_skills_if_token" in server_src
