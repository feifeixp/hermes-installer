"""Unit tests for macOS offline-install pure helpers in main.py.

main.py lives at the repo root and imports pywebview at module load, which
isn't available in the test venv. So we load ONLY the two pure helper
functions by exec'ing main.py's source in a stubbed module namespace —
no import side effects, no pywebview needed.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_MAIN = Path(__file__).resolve().parent.parent.parent / "main.py"


def _load_pure_helpers():
    """Return a namespace containing only the named pure helper functions
    from main.py, without executing its module-level side effects."""
    src = _MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_uv_pip_install_args", "_agent_venv_python"}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert len(funcs) == len(wanted), f"missing helpers: {wanted - {f.name for f in funcs}}"
    module = ast.Module(body=funcs, type_ignores=[])
    ns: dict = {"Path": Path, "sys": sys}
    exec(compile(module, str(_MAIN), "exec"), ns)
    return ns


def test_uv_pip_install_args_uses_cn_mirror_first():
    ns = _load_pure_helpers()
    args = ns["_uv_pip_install_args"]("/agent", "/agent/venv/bin/python")
    assert "-e" in args and "/agent" in args
    i = args.index("--index-url")
    assert args[i + 1] == "https://mirrors.aliyun.com/pypi/simple/"
    assert "https://pypi.org/simple/" in args
    assert "first-index" in args


def test_agent_venv_python_posix_layout():
    ns = _load_pure_helpers()
    p = ns["_agent_venv_python"](Path("/home/x/.hermes/hermes-agent"), is_windows=False)
    assert p == Path("/home/x/.hermes/hermes-agent/venv/bin/python")


def test_agent_venv_python_windows_layout():
    ns = _load_pure_helpers()
    p = ns["_agent_venv_python"](Path("C:/u/.hermes/hermes-agent"), is_windows=True)
    assert p == Path("C:/u/.hermes/hermes-agent/venv/Scripts/python.exe")
