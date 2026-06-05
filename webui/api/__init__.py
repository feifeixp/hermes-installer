"""Hermes Web UI — API modules.

Identity constants are imported from the top-level _meta.py when running
from source. Inside a PyInstaller bundle _meta is bundled alongside main.py,
so the import path is the same. If _meta is not available (e.g. very early
boot before sys.path is set up), we fall back to inline literals so the
webui never crashes due to missing metadata.
"""

try:
    import sys as _sys
    import importlib.util as _ilu
    from pathlib import Path as _Path
    # Locate _meta.py without polluting sys.path.
    #
    # Previously we did `sys.path.insert(0, <grandparent>)` so a plain
    # `from _meta import ...` would resolve. That was fine when running
    # from source (grandparent = repo root, contains _meta.py and nothing
    # dangerous) and inside the frozen exe itself (grandparent = _MEIPASS,
    # contains _meta.py and frozen-Python files which the frozen Python
    # owns anyway). But when the FROZEN exe spawned `webui/server.py` as a
    # SUBPROCESS under a venv Python of a different version (e.g. uv-managed
    # 3.11), the grandparent computed to `_MEI<rand>/` — which is the
    # PyInstaller extraction dir packed with cp313 stdlib .pyd files
    # (unicodedata.pyd, _socket.pyd, …). Inserting it at sys.path[0] made
    # the venv Python's `import unicodedata` resolve to the cp313 .pyd
    # ahead of its own DLLs dir, triggering "Module use of python313.dll
    # conflicts with this version of Python" and a cascading
    # `LookupError: unknown encoding: idna` inside socket.gethostbyaddr.
    #
    # Load _meta.py by explicit path via importlib instead. This finds
    # the same file in all three scenarios (source, frozen-exe, frozen-exe
    # subprocess) without ever touching sys.path.
    _meta = None
    for _cand in (
        getattr(_sys, "_MEIPASS", None),
        str(_Path(__file__).parent.parent.parent),
    ):
        if not _cand:
            continue
        _path = _Path(_cand) / "_meta.py"
        if _path.is_file():
            _spec = _ilu.spec_from_file_location("_meta", str(_path))
            if _spec and _spec.loader:
                _meta = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_meta)
                break
    if _meta is None:
        raise ImportError("_meta.py not found relative to webui/api/__init__.py")
    APP_NAME     = _meta.APP_NAME
    APP_FULL_NAME = _meta.APP_FULL_NAME
    BUNDLE_ID    = _meta.BUNDLE_ID
    COMPANY      = _meta.COMPANY
    COPYRIGHT    = _meta.COPYRIGHT
    CONTACT      = _meta.CONTACT
    HOMEPAGE     = _meta.HOMEPAGE
    VERSION      = _meta.VERSION
except Exception:
    # Fallback literals — keep in sync with _meta.py
    APP_NAME     = "NeoMuse"
    APP_FULL_NAME = "NeoMuse"
    BUNDLE_ID    = "cn.neodomain.hermes"
    COMPANY      = "Neodomain Inc."
    COPYRIGHT    = "Copyright © 2024-2026 Neodomain Inc."
    CONTACT      = "contact@neodomain.cn"
    HOMEPAGE     = "https://neowow.studio"
    VERSION      = ""

__all__ = [
    "APP_NAME", "APP_FULL_NAME", "BUNDLE_ID",
    "COMPANY", "COPYRIGHT", "CONTACT", "HOMEPAGE", "VERSION",
]
