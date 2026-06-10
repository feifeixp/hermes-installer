# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Neowow Studio.
Produces:
  macOS   → dist/Neowow Studio.app  (+ .dmg via build.sh)
  Windows → dist/Hermes Installer.exe  (single file, via build.bat)

All identity / version / copyright constants come from _meta.py so this
file never needs to be touched for a version bump or legal-text change.
"""

import os
import sys
from pathlib import Path

# Import metadata from single source of truth
from _meta import (
    APP_NAME, APP_FULL_NAME, EXE_NAME,
    BUNDLE_ID, VERSION, VERSION_TUPLE,
    MACOS_INFO_PLIST,
    windows_version_info_text,
)

block_cipher = None
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# ── Windows: write version_info file before EXE step ──────────────────────
# PyInstaller reads this file to embed company/copyright/version into the
# EXE's VS_VERSION_INFO resource block (visible in file Properties on Windows).
_WIN_VERSION_FILE = Path("dist_version_info.txt")
if IS_WIN:
    _WIN_VERSION_FILE.write_text(windows_version_info_text(), encoding="utf-8")

# ── Hidden imports ─────────────────────────────────────────────────────────
HIDDEN_IMPORTS = [
    # crash_reporter is imported by both main.py and webui/server.py via
    # HERMES_INSTALLER_BASE_DIR. PyInstaller's static analyzer usually picks
    # this up from main.py, but list it explicitly as insurance so the module
    # is guaranteed to be in the bundle for both the frozen exe and the
    # webui venv subprocess that resolves it from the bundled source tree.
    "crash_reporter",
]

if IS_WIN:
    HIDDEN_IMPORTS += [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",          # pythonnet (Windows WebView2 bridge)
    ]

# ── Analysis ───────────────────────────────────────────────────────────────

# Build selective webui file list — exclude tests, docs, Docker, and heavy
# markdown docs that are never read at runtime.
_webui_root = Path("webui")
_webui_datas: list[tuple[str, str]] = []
for _f in _webui_root.rglob("*"):
    if _f.is_file():
        _parts = _f.parts
        # Skip non-runtime directories anywhere in path
        if any(p in ("tests", "docs", ".github", "__pycache__") for p in _parts):
            continue
        # Skip heavy markdown docs and Docker files
        _name = _f.name
        if _name in (
            "ARCHITECTURE.md", "BUGS.md", "CHANGELOG.md", "CONTRIBUTING.md",
            "CONTRIBUTORS.md", "DESIGN.md", "HERMES.md", "ROADMAP.md",
            "SPRINTS.md", "TESTING.md",
            "Dockerfile", "docker-compose.yml", "docker-compose.two-container.yml",
            "docker-compose.three-container.yml", "docker_init.bash",
            ".dockerignore",
        ):
            continue
        # Keep: bootstrap.py, server.py, api/*.py, static/*, requirements.txt, etc.
        _dest = str(_f.parent)
        _webui_datas.append((str(_f), _dest))

a = Analysis(
    ["main.py"],                    # only main.py is the entry point now
    pathex=["."],
    binaries=(
        # tools/uv: macOS/Linux uv binary (CI's "Bundle uv binary" step copies
        # it here). Collected as a BINARY — NOT a data file — so that on macOS
        # PyInstaller signs it inside-out and relocates it into
        # Contents/Frameworks. A Mach-O collected via `datas` lands in
        # Contents/Resources, where `codesign --deep` skips it (signature would
        # go into xattrs), and Apple notarization then rejects the bundle with
        # "code object is not signed at all". See PyInstaller building/osx.py.
        ([("tools/uv", "tools")] if (not IS_WIN) and Path("tools/uv").exists() else [])
    ),
    datas=(
        _webui_datas
        # Bundle zip is optional: present → offline install; absent → git clone at runtime
        + ([("hermes_agent_bundle.zip", ".")] if Path("hermes_agent_bundle.zip").exists() else [])
        # uv.exe: Windows-only install tool, bundled so users don't need internet for uv itself
        + ([("tools/uv.exe", "tools")] if IS_WIN and Path("tools/uv.exe").exists() else [])
        # NOTE: macOS/Linux `tools/uv` is collected as a BINARY (see `binaries=`
        # above), not here, so PyInstaller signs it for notarization.
        # patch_hermes_agent.py: injects the neowow-coding-plan ProviderConfig
        # into hermes_cli/auth.py + providers.py after pip install. Without
        # this file in the bundle, _windows_install_agent's Step 3.5 logs
        # "patch script not found ... skipping" and chat dispatch later
        # fails with "Unknown provider 'neowow-coding-plan'".
        + ([("docker/patch_hermes_agent.py", "docker")] if Path("docker/patch_hermes_agent.py").exists() else [])
        # _meta.py: ship as a data file (not just inside the PYZ archive) so
        # webui/api/__init__.py can load it via importlib in the venv
        # subprocess, which has no access to the frozen exe's PYZ.
        # Otherwise webui falls back to the literals block and reports
        # VERSION="" in the UI.
        + ([("_meta.py", ".")] if Path("_meta.py").exists() else [])
        # Preset persona SOUL.md files (16 bundled identities). The 智能体灵魂
        # panel's「从预设人格选择」picker reads these at runtime from
        # docker/assets/personas/SOUL/ (webui/api/personas_presets.py). They
        # MUST be shipped or the picker shows「没有可用的预设人格」in the
        # packaged app — previously only worked in a dev checkout. glob() on a
        # missing dir yields [], so this is self-guarding.
        + [
            (str(_p), "docker/assets/personas/SOUL")
            for _p in sorted(Path("docker/assets/personas/SOUL").glob("*.SOUL.md"))
        ]
    ),
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest", "_pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ───────────────────────────────────────────────────────────────────
# Windows → onefile (single .exe, no _internal folder)
# macOS   → onedir  (needed for .app bundle structure)
if IS_WIN:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        exclude_binaries=False,
        name=EXE_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,      # UPX can trigger antivirus false-positives
        console=True,   # console window = server keep-alive on Windows
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="icon.ico" if Path("icon.ico").exists() else None,
        # Embed company / copyright / version into EXE resource block
        version=str(_WIN_VERSION_FILE) if _WIN_VERSION_FILE.exists() else None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=EXE_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=IS_MAC,
        target_arch=None,
        # Let PyInstaller code-sign during the build when CI provides a
        # Developer ID identity. PyInstaller signs every collected Mach-O
        # (incl. Python.framework) inside-out with hardened runtime — the
        # ONLY reliable way to pass Apple notarization. `codesign --deep`
        # and `find -exec codesign` both corrupt the framework (it's
        # referenced via several symlinks) → "signature invalid". Empty
        # env → None → unsigned build (unchanged fallback).
        codesign_identity=(os.environ.get("HERMES_CODESIGN_IDENTITY") or None),
        entitlements_file=(os.environ.get("HERMES_ENTITLEMENTS") or None),
        icon="icon.icns" if (IS_MAC and Path("icon.icns").exists()) else None,
    )

# ── COLLECT (onedir — macOS only) ─────────────────────────────────────────
if IS_MAC:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=EXE_NAME,
    )

# ── BUNDLE (.app) — macOS only ─────────────────────────────────────────────
if IS_MAC:
    app_bundle = BUNDLE(
        coll,
        name=f"{APP_FULL_NAME}.app",
        icon="icon.icns" if Path("icon.icns").exists() else None,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist=MACOS_INFO_PLIST,
    )
