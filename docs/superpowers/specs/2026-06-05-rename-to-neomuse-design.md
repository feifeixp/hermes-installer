# Rename desktop app: "Hermes Installer" → "NeoMuse"

**Status:** Approved (design accepted 2026-06-05)

**Goal:** Rebrand all user-visible app names to **NeoMuse** while keeping download
filenames, bundle identifier, data dir, repo name, and the underlying Hermes
agent unchanged — so existing users upgrade seamlessly and nothing cross-repo
breaks.

---

## Scope decision (from brainstorming)

- **Rename scope:** all *visible* names → `NeoMuse`. Download filenames + bundle
  id stay. (`~/.hermes`, GitHub repo name, underlying Hermes agent all kept.)
- **"Hermes" handling:** rename the *app/product* brand → `NeoMuse`; keep
  references to the *AI engine* (`Hermes Agent`/HermesAgent/model name/
  `/personality`) and *technical identifiers* (`X-Hermes-CSRF-Token`).

## Naming rules

| Class | Examples | Action |
|---|---|---|
| App/product brand | `<title>Hermes`, titlebar, `<h1>`, window title, "Hermes Installer", "Hermes WebUI"/"Hermes Web UI", onboarding welcome, self-update banner, macOS privacy strings, About box, alert dialog titles | → **NeoMuse** |
| AI engine | `Hermes Agent`, `HermesAgent`, model names, `/personality` | **keep** |
| Technical identifiers | `X-Hermes-CSRF-Token` header, internal keys | **keep** |
| Download filenames | `Hermes-Installer-macOS.dmg`, `Hermes Installer.exe`, `Hermes-Installer-Windows.zip` | **keep** |
| Bundle id | `cn.neodomain.hermes` | **keep** |
| Data dir / repo / company | `~/.hermes`, `feifeixp/hermes-installer`, `Neodomain Inc.`, copyright, contact | **keep** |

## Core change — `_meta.py`

| Constant | Current | New |
|---|---|---|
| `APP_NAME` | `Hermes` | `NeoMuse` |
| `APP_FULL_NAME` | `Hermes Installer` | `NeoMuse` |
| `EXE_NAME` | `Hermes Installer` | **unchanged** (keeps Windows download .exe name + macOS internal exe name; build.yml's hardcoded `Hermes Installer.exe` keeps working) |
| `BUNDLE_ID` | `cn.neodomain.hermes` | **unchanged** |
| Privacy strings ("Hermes automates setup steps." etc.) | | "NeoMuse …" |
| Module docstring | "Hermes Installer — …" | "NeoMuse — …" |

`APP_FULL_NAME` drives the macOS `.app` name (`{APP_FULL_NAME}.app`) → `NeoMuse.app`.
`APP_NAME` drives macOS `CFBundleName`/`CFBundleDisplayName` (menu bar + Dock label).

## Outcomes

- **macOS:** download still `Hermes-Installer-macOS.dmg`; after install Finder/Dock/
  menu bar show **NeoMuse**, bundle = `NeoMuse.app`; mounted dmg volume name → `NeoMuse`.
- **Windows:** download still `Hermes.Installer.exe`; running window/About/file
  ProductName show **NeoMuse**.
- **Existing users:** bundle id unchanged + data in `~/.hermes` → settings/sessions/
  SOUL all carry over.

## Files to change

| File | Change |
|---|---|
| `_meta.py` | `APP_NAME`, `APP_FULL_NAME` → NeoMuse; privacy strings; docstring. Keep `EXE_NAME`, `BUNDLE_ID`, `COMPANY`, copyright, contact. |
| `webui/api/__init__.py` | Fallback literals `"Hermes"`/`"Hermes Installer"` → `"NeoMuse"`. |
| `main.py` | Window title source → NeoMuse; ~10 `_alert("Hermes Installer", …)` → `"NeoMuse"`; user-facing error text "Hermes Installer" → "NeoMuse" (keep agent-install references that mean the Hermes agent). |
| `desktop_menu.py` | About box `Hermes Installer v{version}` → NeoMuse (prefer `APP_FULL_NAME`). |
| `app.py` | FastAPI `title="Hermes Installer"` → NeoMuse. |
| `webui/static/index.html` | `<title>`, `apple-mobile-web-app-title`, `#appTitlebarTitle`, `<h1>` → NeoMuse; boot splash `Hermes Agent 正在登录…` → `NeoMuse 正在登录…`. |
| `webui/static/i18n.js` | 7 locales: app-brand strings ("Hermes Installer", "Hermes WebUI"/"Hermes Web UI", welcome, self-update banner) → NeoMuse; `Hermes Dashboard` tooltip → `NeoMuse Dashboard`. Keep "Hermes Agent"/agent/model/personality strings. |
| `.github/workflows/build.yml` | macOS: `dist/Hermes Installer.app` → `dist/NeoMuse.app` (Verify .app, Verify signature, Create .dmg ditto src+dest, staged codesign verify); `-volname "Hermes Installer"` → `-volname "NeoMuse"`. Keep dmg filename, artifact names, Windows job. |
| `build.sh` / `build.bat` | Mirror the same visible-name + `.app`/volume changes for local builds; keep download filenames. |
| `README.md`, `docs/*` | Prose "Hermes Installer" → "NeoMuse" where it means the app (keep `.dmg`/`.app` example paths accurate to the new `.app` name). |

## Boundary strings (approved defaults)

1. Boot splash `Hermes Agent 正在登录 Neowow 账号` → `NeoMuse 正在登录 Neowow 账号` (app boot state).
2. `Hermes Dashboard` sidebar tooltip → `NeoMuse Dashboard`.

## Explicitly NOT changed

Download filenames (`Hermes-Installer-macOS.dmg`, Windows `.exe`/`.zip`), `BUNDLE_ID`,
`~/.hermes`, GitHub repo name, `Neodomain Inc.`/copyright/contact, `Hermes Agent`/
HermesAgent/model/`/personality`, `X-Hermes-CSRF-Token` and other technical keys.

## Verification

Build a local `.app` with the changed `_meta.py`/spec and confirm:
- bundle is `dist/NeoMuse.app`;
- `CFBundleName`/`CFBundleDisplayName` = `NeoMuse` (menu bar/Dock);
- pywebview window title = NeoMuse; index.html title/titlebar/h1 = NeoMuse;
- preset personas still load (regression guard from v1.5.9);
- `codesign --verify --deep --strict` still passes (signing path intact);
- a sweep finds no remaining app-brand "Hermes Installer" in user-visible surfaces,
  and "Hermes Agent"/technical identifiers are preserved.
- `pytest webui/` green.
