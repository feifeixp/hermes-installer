; ─────────────────────────────────────────────────────────────────────────
; hermes.iss — Inno Setup script for the Neowow Studio Windows installer.
;
; Wraps the PyInstaller onefile build (`dist\Hermes Installer.exe`) into a
; real setup.exe so Windows users get the familiar install experience they
; expect (instead of a bare portable .exe that looks suspicious) + Start Menu
; and Desktop shortcuts.
;
; Per-user install (no admin/UAC) → smoothest for non-technical users.
;
; Built in CI (see .github/workflows/build.yml, windows job):
;   ISCC.exe /DAppVersion=<x.y.z> installer\windows\hermes.iss
; Output: dist\Neowow-Studio-Setup-<version>.exe
;
; SIGNING (TODO): once a code-signing service is set up (Azure Trusted Signing
; recommended), sign BOTH dist\Hermes Installer.exe (before this runs) AND the
; produced setup.exe. Inno can invoke a signtool via [Setup] SignTool= ; for
; Azure Trusted Signing we sign the output exe in a separate CI step instead.
; ─────────────────────────────────────────────────────────────────────────

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName       "Neowow Studio"
#define AppExeName    "Hermes Installer.exe"   ; PyInstaller output (name kept for upgrade continuity)
#define AppPublisher  "Neodomain Inc."
#define AppURL        "https://neowow.studio"

[Setup]
; Stable AppId — DO NOT change across versions (tracks upgrades + uninstall entry).
AppId={{A3F1C2D4-5E6B-47A8-9C0D-1E2F3A4B5C6D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
; Relative paths (Source, icon, OutputDir) resolve from the repo root — the
; .iss lives in installer/windows/, so hop up two levels.
SourceDir=..\..
; Per-user install — no admin rights, no UAC prompt, no "all users?" dialog.
PrivilegesRequired=lowest
DefaultDirName={autopf}\Neowow Studio
DefaultGroupName=Neowow Studio
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=dist
; STABLE filename (version is inside the installer + the release tag) so
; https://…/releases/latest/download/Neowow-Studio-Setup.exe always resolves.
OutputBaseFilename=Neowow-Studio-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "dist\Hermes Installer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu (always).
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
; Desktop (opt-in task, checked by default).
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
