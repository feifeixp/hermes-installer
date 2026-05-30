"""Regression: docker/patch_hermes_agent.py seeds 16 default personalities
into hermes-agent's DEFAULT_CONFIG["personalities"].

Covers:
- the bundled data module has 16 non-empty string personas
- _render_personalities_block produces a valid Python dict-body literal
- _patch_config_py replaces the empty `"personalities": {}` anchor, the
  result is syntactically valid Python, and re-running is idempotent
- a missing anchor / missing roster degrades to a no-op (never crashes)
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

# Load the patch script + data module by path (they live under docker/,
# not on the normal import path).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCKER = _REPO_ROOT / "docker"


def _load(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def patch_mod():
    return _load("patch_hermes_agent_under_test", _DOCKER / "patch_hermes_agent.py")


@pytest.fixture(scope="module")
def data_mod():
    return _load("default_personalities_under_test", _DOCKER / "default_personalities.py")


def test_data_module_has_16_personas(data_mod):
    p = data_mod.DEFAULT_PERSONALITIES
    assert isinstance(p, dict)
    assert len(p) == 16
    assert all(isinstance(k, str) and k for k in p)
    assert all(isinstance(v, str) and v for v in p.values())
    # Spot-check a couple of the documented pinyin keys.
    assert "jianglan" in p
    assert "kemin" in p


def test_render_block_is_valid_python(patch_mod):
    block = patch_mod._render_personalities_block({"a": "one", "b": "two \"quoted\""})
    # Wrap into a tiny dict literal and parse it.
    snippet = "X = {\n" + block + "\n}\n"
    tree = ast.parse(snippet)
    # Evaluate the literal to confirm round-trip.
    ns: dict = {}
    exec(compile(tree, "<test>", "exec"), ns)
    assert ns["X"]["personalities"] == {"a": "one", "b": 'two "quoted"'}


def _fake_config(tmp_path: Path) -> Path:
    """Minimal config.py shell with the upstream empty-personalities anchor."""
    agent = tmp_path / "agent"
    (agent / "hermes_cli").mkdir(parents=True)
    cfg = agent / "hermes_cli" / "config.py"
    cfg.write_text(
        "DEFAULT_CONFIG = {\n"
        '    "model": "x",\n'
        '    "personalities": {},\n'
        '    "security": {},\n'
        "}\n",
        encoding="utf-8",
    )
    return agent


def test_patch_config_injects_and_is_idempotent(patch_mod, tmp_path):
    agent = _fake_config(tmp_path)

    changed1 = patch_mod._patch_config_py(agent)
    assert changed1 is True

    cfg_text = (agent / "hermes_cli" / "config.py").read_text(encoding="utf-8")
    # Still valid Python.
    ast.parse(cfg_text)
    # The empty anchor is gone; personas are present.
    assert '"personalities": {},' not in cfg_text
    assert '"jianglan":' in cfg_text
    assert '"kemin":' in cfg_text

    # Evaluate DEFAULT_CONFIG to confirm 16 personalities landed.
    ns: dict = {}
    exec(compile(cfg_text, "<cfg>", "exec"), ns)
    assert len(ns["DEFAULT_CONFIG"]["personalities"]) == 16

    # Re-run = no-op.
    changed2 = patch_mod._patch_config_py(agent)
    assert changed2 is False


def test_patch_config_noop_when_anchor_absent(patch_mod, tmp_path):
    agent = tmp_path / "agent"
    (agent / "hermes_cli").mkdir(parents=True)
    cfg = agent / "hermes_cli" / "config.py"
    # No `"personalities": {}` anchor at all.
    cfg.write_text('DEFAULT_CONFIG = {\n    "model": "x",\n}\n', encoding="utf-8")
    changed = patch_mod._patch_config_py(agent)
    assert changed is False
    # File unchanged.
    assert cfg.read_text(encoding="utf-8") == 'DEFAULT_CONFIG = {\n    "model": "x",\n}\n'


def test_patch_config_missing_file_is_noop(patch_mod, tmp_path):
    agent = tmp_path / "agent"
    (agent / "hermes_cli").mkdir(parents=True)
    # No config.py.
    assert patch_mod._patch_config_py(agent) is False
