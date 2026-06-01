"""Bundled preset-persona SOUL.md catalogue for the WebUI.

The installer ships 16 localized "星火创意" personas as full SOUL.md files
under ``docker/assets/personas/SOUL/<中文名>.SOUL.md``. The 智能体灵魂
(Agent Soul) panel lets a user pick one of these as a starting point: it
fills the Soul editor with the chosen persona's full SOUL.md text, which
the user then reviews and saves to ``~/.hermes/SOUL.md``.

This module locates that bundled directory across the three runtime
layouts (dev checkout / frozen desktop exe / cloud Docker image) and
parses each file into ``{id, name, summary, content}``. Missing directory
→ empty list (the UI just shows no presets — never an error).

Distinct from ``default_personalities.py``: that seeds the SHORT
``agent.personalities`` prompts into config.yaml for ``/personality``
switching; this serves the FULL single-identity SOUL.md documents.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Relative path of the bundled persona SOUL directory, from each base root.
_REL = Path("docker") / "assets" / "personas" / "SOUL"


def _candidate_dirs() -> list[Path]:
    """Ordered list of places the bundled SOUL/ directory might live.

    1. HERMES_INSTALLER_BASE_DIR — set by the desktop launcher (main.py) to
       the installer root (the frozen exe's _MEIxxx dir or the dev checkout).
    2. Repo root relative to this file — webui/api/personas_presets.py →
       parents[2] is the installer root in a normal checkout / the cloud
       image's /opt/hermes/hermes-installer.
    3. PyInstaller's _MEIPASS — the frozen-exe extraction dir, in case the
       env var is unset.
    """
    bases: list[Path] = []
    env_base = os.getenv("HERMES_INSTALLER_BASE_DIR")
    if env_base:
        bases.append(Path(env_base))
    try:
        bases.append(Path(__file__).resolve().parents[2])
    except IndexError:  # pragma: no cover - defensive
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    # De-dup while preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for b in bases:
        d = b / _REL
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _find_soul_dir() -> Path | None:
    for d in _candidate_dirs():
        try:
            if d.is_dir():
                return d
        except OSError:
            continue
    return None


def _parse_name_summary(text: str, fallback_name: str) -> tuple[str, str]:
    """Extract (name, summary) from a SOUL.md.

    The first heading line looks like ``# 江岚 — 创始合伙人兼总经理``. We
    split the heading on an em dash (— / – / -) into name + summary. If the
    heading has no dash, the whole heading is the name and the summary is the
    first non-empty body line. Falls back to the filename stem for the name.
    """
    name = fallback_name
    summary = ""
    lines = text.splitlines()
    heading = ""
    body_first = ""
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if not heading and s.startswith("#"):
            heading = s.lstrip("#").strip()
            continue
        if heading and not body_first:
            body_first = s
            break
    if heading:
        for dash in (" — ", " – ", " - ", "—", "–"):
            if dash in heading:
                left, right = heading.split(dash, 1)
                name = left.strip() or fallback_name
                summary = right.strip()
                break
        else:
            name = heading
            summary = body_first
    elif body_first:
        summary = body_first
    return name, summary


def list_persona_presets() -> list[dict]:
    """Return the bundled persona presets as a sorted list of dicts.

    Each entry: ``{"id": <stem>, "name": str, "summary": str, "content": str}``.
    Returns ``[]`` when the bundled directory is absent (never raises).
    """
    soul_dir = _find_soul_dir()
    if soul_dir is None:
        return []
    presets: list[dict] = []
    try:
        files = sorted(soul_dir.glob("*.SOUL.md"))
    except OSError:
        return []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        stem = f.name[: -len(".SOUL.md")] if f.name.endswith(".SOUL.md") else f.stem
        name, summary = _parse_name_summary(content, stem)
        presets.append(
            {"id": stem, "name": name, "summary": summary, "content": content}
        )
    return presets
