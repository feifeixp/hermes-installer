"""Regression: the 16 default personalities are seeded into config.yaml's
``agent.personalities`` at WebUI startup.

This replaces the previous DEFAULT_CONFIG-patch approach, which was wrong
twice over (it wrote the top-level ``personalities`` key that no reader
uses, and DEFAULT_CONFIG only ever seeds a brand-new config so existing
instances never received the roster). Seeding now lives in
``webui/server.py:_seed_default_personalities()`` and merges the roster
from ``webui/api/default_personalities.py`` into the active config.yaml's
``agent.personalities`` — adding only missing keys, persisting to disk,
and being safe to run on every boot.

Covers:
- the roster module ships exactly 16 non-empty string personas
- the seed injects all 16 into an existing config without clobbering the
  user's own personas or other ``agent`` keys, and is idempotent
- the seed creates the ``agent`` / ``personalities`` containers when they
  are missing
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def _restore_config_cache():
    """``_seed_default_personalities`` calls ``reload_config()``, which mutates
    the module-global config cache to point at this test's temp config. Reload
    it again at teardown — after ``monkeypatch`` has restored
    ``HERMES_CONFIG_PATH`` — so we never leak temp state into later tests."""
    yield
    try:
        from api.config import reload_config

        reload_config()
    except Exception:
        pass


def test_roster_has_16_personas():
    from api.default_personalities import DEFAULT_PERSONALITIES as roster

    assert isinstance(roster, dict)
    assert len(roster) == 16
    assert all(isinstance(k, str) and k for k in roster)
    assert all(isinstance(v, str) and v for v in roster.values())
    # Spot-check a couple of the documented pinyin keys.
    assert "jianglan" in roster
    assert "kemin" in roster


def test_seed_injects_and_is_idempotent(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "agent:\n  max_turns: 50\n  personalities:\n    mycustom: 'keep me'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(cfg))

    import server

    server._seed_default_personalities()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    personas = data["agent"]["personalities"]
    # Other agent keys are preserved.
    assert data["agent"]["max_turns"] == 50
    # The user's own persona is never clobbered.
    assert personas["mycustom"] == "keep me"
    # All 16 defaults landed alongside it.
    assert len([k for k in personas if k != "mycustom"]) == 16
    assert "jianglan" in personas and len(personas["jianglan"]) > 0

    # Re-running writes nothing new (idempotent).
    before = cfg.read_text(encoding="utf-8")
    server._seed_default_personalities()
    assert cfg.read_text(encoding="utf-8") == before


def test_seed_creates_missing_agent_block(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    # No `agent` block at all.
    cfg.write_text("model: x\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(cfg))

    import server

    server._seed_default_personalities()

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert len(data["agent"]["personalities"]) == 16
