"""Post-install patch — inject neowow-coding-plan provider into the
upstream hermes-agent installation.

Why this exists: bootstrap.py installs hermes-agent from the upstream
NousResearch/hermes-agent main branch (via `install.sh` curled from
github). That installation does NOT include our `neowow-coding-plan`
provider, so without this patch hermes_cli's PROVIDER_REGISTRY has no
entry for the id we write into config.yaml — chat errors out with
"Unknown provider 'neowow-coding-plan'".

This script runs after `bootstrap.py --install-only` in the
Dockerfile and idempotently inserts the missing ProviderConfig +
HermesOverlay. Re-running it is a no-op when the provider is already
present.

For ON-HOST patching (the user's local hermes-agent at
~/.hermes/hermes-agent), invoke with no args — it auto-discovers the
install path the same way bootstrap.py does.

For DOCKER builds, the install path is /opt/hermes/.hermes/hermes-agent
— set via the HERMES_AGENT_DIR env var (Dockerfile sets this) or via
command-line argument.

Why patch source files instead of subclassing PROVIDER_REGISTRY:
the upstream PROVIDER_REGISTRY is a module-level dict that's read at
import time across many call sites. Monkey-patching at runtime from a
sitecustomize.py works but breaks down across subprocess boundaries.
The cleanest fix is to make the on-disk source files declare the
provider — that way every interpreter, every subprocess sees it
consistently.

Format mirrors the existing `neodomain` entry that user feifeixp's
fork already carries (commit cad11081e on hermes-agent's
feifei/neodomain-integration branch). The neowow-coding-plan entry
just points at a different base_url and uses a different env var name.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# ─── Patch payloads ─────────────────────────────────────────────────────────

# Marker string we grep for to detect "already applied" + decide whether
# to inject. Picked from inside our injected blocks so even partial
# patches (one file done, other failed) re-attempt cleanly.
_AUTH_PY_MARKER = '"neowow-coding-plan": ProviderConfig('

_AUTH_PY_INJECT = '''    "neowow-coding-plan": ProviderConfig(
        id="neowow-coding-plan",
        name="Neowow Coding Plan",
        auth_type="api_key",
        # Points at the dashboard's billed proxy — every call through
        # this provider is debited from the user's Coding Plan credits
        # (lib/billed-llm-call + /api/me/chat/completions).
        inference_base_url="https://app.neowow.studio/api/me",
        api_key_env_vars=("NEOWOW_CODING_PLAN_API_KEY", "OPENAI_API_KEY"),
        base_url_env_var="NEOWOW_CODING_PLAN_BASE_URL",
    ),
'''

_PROVIDERS_PY_MARKER = '"neowow-coding-plan": HermesOverlay('

_PROVIDERS_PY_INJECT = '''    "neowow-coding-plan": HermesOverlay(
        transport="openai_chat",
        extra_env_vars=("NEOWOW_CODING_PLAN_API_KEY", "OPENAI_API_KEY"),
        base_url_override="https://app.neowow.studio/api/me",
        base_url_env_var="NEOWOW_CODING_PLAN_BASE_URL",
    ),
'''

_LABEL_OVERRIDE_MARKER = '"neowow-coding-plan":'
_LABEL_OVERRIDE_INJECT = '    "neowow-coding-plan": "Neowow Coding Plan",\n'


# ─── Patch logic ────────────────────────────────────────────────────────────

def _find_agent_dir(explicit: str | None) -> Path:
    """Pick the hermes-agent install dir. Priority:
    1. CLI arg
    2. HERMES_AGENT_DIR env var
    3. /opt/hermes/.hermes/hermes-agent (docker layout)
    4. ~/.hermes/hermes-agent (host layout)
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("HERMES_AGENT_DIR", "").strip()
    if env:
        return Path(env)
    for cand in [
        Path("/opt/hermes/.hermes/hermes-agent"),
        Path.home() / ".hermes" / "hermes-agent",
    ]:
        if cand.is_dir():
            return cand
    raise SystemExit(
        "Could not find hermes-agent install. Set HERMES_AGENT_DIR or pass --agent-dir."
    )


def _patch_auth_py(agent_dir: Path) -> bool:
    """Inject neowow-coding-plan ProviderConfig into PROVIDER_REGISTRY.
    Returns True if changed, False if already applied or impossible."""
    f = agent_dir / "hermes_cli" / "auth.py"
    if not f.exists():
        print(f"[patch] SKIP {f} (not found)")
        return False
    src = f.read_text(encoding="utf-8")
    if _AUTH_PY_MARKER in src:
        print(f"[patch] OK   {f.name} already has neowow-coding-plan entry")
        return False
    # Insert just before the closing `}` of PROVIDER_REGISTRY: Dict[str, ProviderConfig] = { ... }
    # We use the same "right before the final brace" anchor as the upstream neodomain
    # commit. Find the LAST occurrence of `\n}\n` after the PROVIDER_REGISTRY declaration.
    marker = "PROVIDER_REGISTRY: Dict[str, ProviderConfig] = {"
    idx = src.find(marker)
    if idx < 0:
        print(f"[patch] FAIL {f.name}: PROVIDER_REGISTRY declaration not found — "
              "upstream may have refactored. Manual review required.")
        return False
    # Find the closing `}` of THIS dict. Skip nested braces inside the entries
    # by scanning brace depth.
    depth = 0
    end = -1
    for i in range(idx + len(marker), len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                end = i
                break
            depth -= 1
    if end < 0:
        print(f"[patch] FAIL {f.name}: could not locate end of PROVIDER_REGISTRY dict")
        return False
    # Insert our entry just BEFORE the closing brace, on its own indented line.
    new_src = src[:end] + _AUTH_PY_INJECT + src[end:]
    f.write_text(new_src, encoding="utf-8")
    print(f"[patch] DONE {f.name} (added neowow-coding-plan ProviderConfig)")
    return True


def _patch_providers_py(agent_dir: Path) -> bool:
    """Inject HermesOverlay + label override into providers.py.
    Returns True if changed."""
    f = agent_dir / "hermes_cli" / "providers.py"
    if not f.exists():
        print(f"[patch] SKIP {f} (not found)")
        return False
    src = f.read_text(encoding="utf-8")

    changed = False

    # 1. HermesOverlay
    if _PROVIDERS_PY_MARKER in src:
        print(f"[patch] OK   {f.name} already has neowow-coding-plan overlay")
    else:
        marker = "HERMES_OVERLAYS: Dict[str, HermesOverlay] = {"
        idx = src.find(marker)
        if idx < 0:
            print(f"[patch] WARN {f.name}: HERMES_OVERLAYS declaration not found")
        else:
            depth = 0
            end = -1
            for i in range(idx + len(marker), len(src)):
                c = src[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        end = i
                        break
                    depth -= 1
            if end >= 0:
                src = src[:end] + _PROVIDERS_PY_INJECT + src[end:]
                changed = True
                print(f"[patch] DONE {f.name} (added HermesOverlay)")
            else:
                print(f"[patch] WARN {f.name}: could not close HERMES_OVERLAYS dict")

    # 2. Label override (cosmetic — `hermes model` shows friendly name)
    label_marker_check = '"neowow-coding-plan": "Neowow Coding Plan"'
    if label_marker_check in src:
        print(f"[patch] OK   {f.name} already has neowow-coding-plan label")
    else:
        label_anchor = "_LABEL_OVERRIDES: Dict[str, str] = {"
        idx = src.find(label_anchor)
        if idx < 0:
            print(f"[patch] INFO {f.name}: _LABEL_OVERRIDES not present — skipping label")
        else:
            depth = 0
            end = -1
            for i in range(idx + len(label_anchor), len(src)):
                c = src[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        end = i
                        break
                    depth -= 1
            if end >= 0:
                src = src[:end] + _LABEL_OVERRIDE_INJECT + src[end:]
                changed = True
                print(f"[patch] DONE {f.name} (added label override)")

    if changed:
        f.write_text(src, encoding="utf-8")
    return changed


# ─── Entry ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent-dir", help="Override hermes-agent install dir")
    p.add_argument("--check", action="store_true",
                   help="Print which patches are needed without applying")
    args = p.parse_args(argv)

    try:
        agent_dir = _find_agent_dir(args.agent_dir)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1

    print(f"[patch] hermes-agent dir: {agent_dir}")

    if args.check:
        # Read-only audit.
        auth_src = (agent_dir / "hermes_cli" / "auth.py").read_text(encoding="utf-8") if \
            (agent_dir / "hermes_cli" / "auth.py").exists() else ""
        providers_src = (agent_dir / "hermes_cli" / "providers.py").read_text(encoding="utf-8") if \
            (agent_dir / "hermes_cli" / "providers.py").exists() else ""
        needs_auth = _AUTH_PY_MARKER not in auth_src
        needs_overlay = _PROVIDERS_PY_MARKER not in providers_src
        print(f"[patch] auth.py needs patch:     {needs_auth}")
        print(f"[patch] providers.py needs patch: {needs_overlay}")
        return 0 if not (needs_auth or needs_overlay) else 2

    changed_auth = _patch_auth_py(agent_dir)
    changed_prov = _patch_providers_py(agent_dir)
    if not (changed_auth or changed_prov):
        print("[patch] No changes needed (provider already registered).")
        return 0
    print("[patch] Patches applied. hermes_cli will resolve "
          "'neowow-coding-plan' on next import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
