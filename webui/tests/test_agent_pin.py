"""Lock the hermes-agent bundle to a PINNED, verified tag (not floating main).

Floating to NousResearch/hermes-agent main HEAD meant every build pulled
bleeding-edge code that could break our neowow-coding-plan patch. We pin to a
tag whose patch-apply was verified. This test prevents an accidental revert to
floating.
"""

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_bundle_source():
    spec = importlib.util.spec_from_file_location("bundle_source", _ROOT / "bundle_source.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_agent_pinned_to_verified_tag():
    bs = _load_bundle_source()
    assert getattr(bs, "PINNED_REF", "") == "v2026.4.23"


def test_clone_command_pins_the_ref():
    src = (_ROOT / "bundle_source.py").read_text(encoding="utf-8")
    # The clone must pass --branch with the pinned ref (not a bare clone of main).
    assert "--branch" in src and "PINNED_REF" in src, \
        "bundle_source must clone --branch PINNED_REF, not float to main"
