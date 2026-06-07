"""Tests for the local-Gemma (Ollama) onboarding helper."""


def test_pick_gemma_model_by_ram():
    from api.local_gemma import pick_gemma_model
    GiB = 1024 ** 3
    assert pick_gemma_model(15 * GiB) == "gemma4:e2b"   # < 16 → smaller
    assert pick_gemma_model(16 * GiB) == "gemma4:e4b"   # ≥ 16 → bigger
    assert pick_gemma_model(32 * GiB) == "gemma4:e4b"
    assert pick_gemma_model(0) == "gemma4:e2b"           # unknown/0 → safest


def test_local_llm_available(monkeypatch):
    from api import local_gemma
    monkeypatch.delenv("HERMES_NEOWOW_ONLY", raising=False)
    monkeypatch.setattr(local_gemma.sys, "platform", "darwin")
    assert local_gemma.local_llm_available() is True
    monkeypatch.setattr(local_gemma.sys, "platform", "linux")
    assert local_gemma.local_llm_available() is True
    # Windows excluded in v1.
    monkeypatch.setattr(local_gemma.sys, "platform", "win32")
    assert local_gemma.local_llm_available() is False
    # Cloud (neowow_only) hard-excluded even on a unix platform.
    monkeypatch.setattr(local_gemma.sys, "platform", "darwin")
    monkeypatch.setenv("HERMES_NEOWOW_ONLY", "1")
    assert local_gemma.local_llm_available() is False


def _job():
    return {"state": "running", "model": "gemma4:e2b", "percent": 0, "log": [], "error": None}


def test_install_happy_path_pull_then_configure():
    from api.local_gemma import _run_install
    calls = {}
    deps = {
        "ollama_installed": lambda: True,
        "pull":      lambda model, on_progress=None: calls.__setitem__("pull", model),
        "configure": lambda model: calls.__setitem__("configure", model),
    }
    job = _job()
    _run_install(job, "gemma4:e2b", deps)
    assert job["state"] == "done"
    assert calls["pull"] == "gemma4:e2b"
    assert calls["configure"] == "gemma4:e2b"


def test_install_not_installed_needs_manual():
    from api.local_gemma import _run_install
    calls = {"pull": False}
    deps = {
        "ollama_installed": lambda: False,
        "pull":      lambda model, on_progress=None: calls.__setitem__("pull", True),
        "configure": lambda model: None,
    }
    job = _job()
    _run_install(job, "gemma4:e2b", deps)
    assert job["state"] == "need_manual_install"
    assert calls["pull"] is False        # never pull when Ollama absent


def test_install_pull_failure_sets_error():
    from api.local_gemma import _run_install
    def boom(model, on_progress=None):
        raise RuntimeError("network down")
    deps = {
        "ollama_installed": lambda: True,
        "pull":      boom,
        "configure": lambda model: None,
    }
    job = _job()
    _run_install(job, "gemma4:e2b", deps)
    assert job["state"] == "error"
    assert "network down" in (job["error"] or "")
