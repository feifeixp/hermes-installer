# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Hermes Agent Installer.
Produces:
  macOS   → dist/Hermes Installer.app  (+ .dmg via build.sh)
  Windows → dist/Hermes Installer.exe  (single file, via build.bat)
"""

import sys
from pathlib import Path

block_cipher = None
IS_MAC     = sys.platform == "darwin"
IS_WIN     = sys.platform == "win32"

# ── Hidden imports ─────────────────────────────────────────────────────────
HIDDEN_IMPORTS = [
    # uvicorn
    "uvicorn", "uvicorn.main", "uvicorn.config", "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.off", "uvicorn.lifespan.on",
    # fastapi / starlette
    "fastapi", "fastapi.responses", "fastapi.staticfiles",
    "fastapi.middleware", "fastapi.middleware.cors",
    "starlette", "starlette.responses", "starlette.routing", "starlette.middleware",
    # async
    "anyio", "anyio._backends._asyncio",
    # http
    "aiohttp", "aiohttp.connector", "aiohttp.client",
    "h11", "httptools",
    # QR / image
    "qrcode", "qrcode.image.pil",
    "PIL", "PIL.Image", "PIL.ImageDraw",
    # config
    "yaml", "dotenv", "pydantic", "pydantic_core",
    # webview — platform-specific backends
    "webview",
]

if IS_MAC:
    HIDDEN_IMPORTS += ["webview.platforms.cocoa"]
if IS_WIN:
    HIDDEN_IMPORTS += [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",          # pythonnet (Windows WebView2 bridge)
        "asyncio",
        "asyncio.windows_events",
        "asyncio.windows_utils",
    ]

# ── Analysis ───────────────────────────────────────────────────────────────

# Build selective webui file list — exclude docs, tests, Docker, and heavy
# markdown docs that are never read at runtime.  Reduces .app size by ~5 MB.
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
            "bootstrap.py", ".dockerignore",
        ):
            continue
        # Keep: api/*.py, static/*, server.py, requirements.txt, etc.
        _dest = str(_f.parent)
        _webui_datas.append((str(_f), _dest))

a = Analysis(
    ["main.py", "app.py"],      # explicitly analyse app.py so it's bundled
    pathex=["."],
    binaries=[],
    datas=(
        [
            ("index.html", "."),   # installer wizard UI
            ("app.py",     "."),   # fallback: include as raw file too
        ]
        + _webui_datas
        # Bundle zip is optional: present → offline install; absent → git clone at runtime
        + ([("hermes_agent_bundle.zip", ".")] if Path("hermes_agent_bundle.zip").exists() else [])
    ),
    hiddenimports=HIDDEN_IMPORTS + ["app"],
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
        a.binaries,     # embed everything into one file on Windows
        a.zipfiles,
        a.datas,
        exclude_binaries=False,
        name="Hermes Installer",
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
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Hermes Installer",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=IS_MAC,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
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
        name="Hermes Installer",
    )

# ── BUNDLE (.app) — macOS only ─────────────────────────────────────────────
if IS_MAC:
    app_bundle = BUNDLE(
        coll,
        name="Hermes Installer.app",
        icon="icon.icns" if Path("icon.icns").exists() else None,
        bundle_identifier="com.nousresearch.hermes-installer",
        version="1.0.0",
        info_plist={
            "CFBundleName": "Hermes Installer",
            "CFBundleDisplayName": "Hermes Agent 安装向导",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSAppleEventsUsageDescription": "Hermes Installer automates setup steps.",
            "NSDesktopFolderUsageDescription": "Hermes Installer reads config files.",
            "NSDocumentsFolderUsageDescription": "Hermes Installer may access documents.",
            "NSDownloadsFolderUsageDescription": "Hermes Installer saves downloaded files.",
            "NSNetworkVolumesUsageDescription": "Hermes Installer connects to AI APIs.",
            # Hardened Runtime exceptions (required for pywebview + uvicorn)
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
            "com.apple.security.cs.disable-library-validation": True,
        },
    )
