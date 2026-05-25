"""Hermes Web UI — API modules.

Identity constants are imported from the top-level _meta.py when running
from source. Inside a PyInstaller bundle _meta is bundled alongside main.py,
so the import path is the same. If _meta is not available (e.g. very early
boot before sys.path is set up), we fall back to inline literals so the
webui never crashes due to missing metadata.
"""

try:
    import sys as _sys
    from pathlib import Path as _Path
    # In a PyInstaller bundle sys._MEIPASS points to the bundle root;
    # in source mode we resolve relative to this file's grandparent.
    _root = getattr(_sys, "_MEIPASS", None) or str(_Path(__file__).parent.parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from _meta import (  # type: ignore[import]
        APP_NAME, APP_FULL_NAME, BUNDLE_ID,
        COMPANY, COPYRIGHT, CONTACT, HOMEPAGE, VERSION,
    )
except Exception:
    # Fallback literals — keep in sync with _meta.py
    APP_NAME     = "Hermes"
    APP_FULL_NAME = "Hermes Installer"
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
