"""
NeoMuse — single source of truth for application metadata.

All other files (spec, webui, main.py) import from here so a version
bump or legal-text change only needs one edit.

Version resolution order:
  1. INSTALLER_VERSION env var  (set by build.yml CI: refs/tags/vX.Y.Z)
  2. git describe               (local dev builds)
  3. Fallback constant below    (offline / no git)
"""

import os
import re
import subprocess
from pathlib import Path


# ── Identity ────────────────────────────────────────────────────────────────
APP_NAME         = "NeoMuse"
APP_FULL_NAME    = "NeoMuse"
# Kept as the legacy reverse-DNS bundle ID so existing installs are treated as
# the SAME app by macOS/Windows (seamless upgrade; user data lives in ~/.hermes).
BUNDLE_ID        = "cn.neodomain.hermes"          # reverse-DNS macOS bundle ID
# Kept as the legacy output basename so the download filenames stay
# "Hermes Installer.exe" / the macOS internal executable name is unchanged.
# Only the user-visible app name (APP_NAME / APP_FULL_NAME, .app bundle) rebrands.
EXE_NAME         = "Hermes Installer"             # output filename (no extension)

# ── Ownership ───────────────────────────────────────────────────────────────
COMPANY          = "Neodomain Inc."
COPYRIGHT_YEARS  = "2024-2026"
COPYRIGHT        = f"Copyright © {COPYRIGHT_YEARS} {COMPANY}"
CONTACT          = "contact@neodomain.cn"
HOMEPAGE         = "https://neowow.studio"
SUPPORT_URL      = "https://neowow.studio"

# ── Fallback version (updated manually when there's no CI / git) ────────────
_FALLBACK_VERSION = "1.3.7"


def _resolve_version() -> str:
    """Return the version string (no 'v' prefix).

    Priority:
      1. INSTALLER_VERSION env var  — CI sets this to the git tag name
      2. git describe               — local builds on a tagged commit
      3. _FALLBACK_VERSION          — offline / no git repo
    """
    # 1. CI env var (set in build.yml)
    env_ver = os.environ.get("INSTALLER_VERSION", "").strip().lstrip("v")
    if env_ver:
        return env_ver

    # 2. git describe
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=Path(__file__).parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            return out.lstrip("v").split("-dirty")[0]
    except Exception:
        pass

    return _FALLBACK_VERSION


VERSION: str = _resolve_version()

# Tuple form for Windows VS_FIXEDFILEINFO  (major, minor, patch, build)
def _version_tuple(v: str) -> tuple[int, int, int, int]:
    parts = re.findall(r"\d+", v)
    parts = (parts + ["0", "0", "0", "0"])[:4]
    return tuple(int(p) for p in parts)   # type: ignore[return-value]

VERSION_TUPLE: tuple[int, int, int, int] = _version_tuple(VERSION)


# ── macOS Info.plist extras ─────────────────────────────────────────────────
MACOS_INFO_PLIST: dict = {
    "CFBundleName":                 APP_NAME,
    "CFBundleDisplayName":          APP_NAME,
    "CFBundleIdentifier":           BUNDLE_ID,
    "CFBundleVersion":              VERSION,
    "CFBundleShortVersionString":   VERSION,
    "CFBundleGetInfoString":        f"{APP_FULL_NAME} {VERSION}, {COPYRIGHT}",
    "NSHumanReadableCopyright":     COPYRIGHT,
    "NSPrincipalClass":             "NSApplication",
    "NSHighResolutionCapable":      True,
    "LSMinimumSystemVersion":       "11.0",
    # Privacy usage descriptions
    "NSAppleEventsUsageDescription":      "NeoMuse automates setup steps.",
    "NSDesktopFolderUsageDescription":    "NeoMuse reads config files from the Desktop.",
    "NSDocumentsFolderUsageDescription":  "NeoMuse may access documents during setup.",
    "NSDownloadsFolderUsageDescription":  "NeoMuse saves downloaded files here.",
    "NSNetworkVolumesUsageDescription":   "NeoMuse connects to AI APIs over the network.",
    # Hardened Runtime (required for PyObjC WKWebView)
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation":       True,
}


def windows_version_info_text() -> str:
    """Return a PyInstaller-compatible VSVersionInfo string for the EXE."""
    vt = VERSION_TUPLE
    ver_str = f"{vt[0]}.{vt[1]}.{vt[2]}.{vt[3]}"
    return f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vt},
    prodvers={vt},
    mask=0x3f,
    flags=0x0,
    OS=0x4,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName',      u'{COMPANY}'),
          StringStruct(u'FileDescription',  u'{APP_FULL_NAME}'),
          StringStruct(u'FileVersion',      u'{ver_str}'),
          StringStruct(u'InternalName',     u'hermes-installer'),
          StringStruct(u'LegalCopyright',   u'{COPYRIGHT}'),
          StringStruct(u'OriginalFilename', u'{EXE_NAME}.exe'),
          StringStruct(u'ProductName',      u'{APP_NAME}'),
          StringStruct(u'ProductVersion',   u'{ver_str}'),
          StringStruct(u'Contact',          u'{CONTACT}'),
          StringStruct(u'WWW',              u'{HOMEPAGE}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [0x0409, 1200])])
  ]
)
"""


if __name__ == "__main__":
    # Quick sanity check: python _meta.py
    print(f"App:       {APP_FULL_NAME}")
    print(f"Version:   {VERSION}  {VERSION_TUPLE}")
    print(f"Bundle ID: {BUNDLE_ID}")
    print(f"Copyright: {COPYRIGHT}")
    print(f"Contact:   {CONTACT}")
    print(f"Homepage:  {HOMEPAGE}")
