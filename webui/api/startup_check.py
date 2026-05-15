"""Server startup self-checks.

Runs once on webui boot to surface configuration mismatches IMMEDIATELY,
instead of letting users discover them mid-chat with cryptic errors.

The Phase ζ.5 motivating bug: chat.neowow.studio booted "healthy" with
config.yaml carrying `provider: neowow-coding-plan` but the agent CLI's
PROVIDER_REGISTRY didn't have that entry. Every chat call errored
"Unknown provider 'neowow-coding-plan'". The container's healthcheck
(`curl /health`) returned ok the whole time — health = "HTTP server
alive", NOT "chat will dispatch correctly".

These checks log at ERROR level when something's misconfigured. They
don't fail container startup (we don't want a chat-only misconfiguration
to take down deploy / settings / skills UI for the user), but they
make the problem extremely visible in `docker logs hermes-webui`.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_config_provider_registered() -> None:
    """Verify config.yaml's model.provider exists in PROVIDER_REGISTRY.

    The runtime-failure-at-chat-time bug class. If config.yaml says
    `provider: xyz` but hermes_cli's PROVIDER_REGISTRY has no entry for
    'xyz', every chat call errors "Unknown provider 'xyz'". We don't
    crash the server (the user might still want to access non-chat
    pages while they re-onboard), but we log loudly so operators see
    the problem in container logs."""
    try:
        from api.config import get_config
        from hermes_cli.auth import PROVIDER_REGISTRY
    except ImportError as e:
        logger.debug("startup_check: dependencies not importable: %s", e)
        return

    cfg = get_config()
    if not isinstance(cfg, dict):
        return
    model_cfg = cfg.get("model")
    if not isinstance(model_cfg, dict):
        return
    provider = str(model_cfg.get("provider", "") or "").strip()
    if not provider:
        # No provider configured yet — onboarding hasn't run. Fine.
        return

    # hermes_cli intentionally keeps these out of PROVIDER_REGISTRY and
    # handles them via special-case logic in resolve_provider() (aggregator
    # + user-supplied endpoint paths). If we treated "missing from registry"
    # as a fail uniformly, we'd produce false-positive errors for known-good
    # configs — the dict-omission is deliberate, not a bug. Stays in sync
    # with the exclusion list in hermes_cli/auth.py:~453.
    _REGISTRY_BYPASS = {"openrouter", "custom", "copilot",
                        "kimi-coding", "kimi-coding-cn", "zai"}
    entry = PROVIDER_REGISTRY.get(provider)
    if entry is None:
        if provider in _REGISTRY_BYPASS:
            logger.info(
                "startup_check: provider=%r uses agent special-case resolution "
                "(not in PROVIDER_REGISTRY by design) — skipping registry check.",
                provider,
            )
            return
        logger.error(
            "STARTUP_CHECK_FAIL: config.yaml has provider=%r but hermes_cli's "
            "PROVIDER_REGISTRY has no such entry. Chat will fail at dispatch "
            "with 'Unknown provider %r'. Fix options:\n"
            "  1. Run docker/patch_hermes_agent.py to register the provider, OR\n"
            "  2. Edit config.yaml to use a real provider (run `hermes model` to list).\n"
            "Registered providers: %s",
            provider, provider, sorted(PROVIDER_REGISTRY.keys()),
        )
        return
    # ProviderConfig objects have inference_base_url; placeholders (rare
    # legacy entries) are plain empty dicts which dispatch fails on.
    if not hasattr(entry, "inference_base_url") and not (
        isinstance(entry, dict) and entry
    ):
        logger.error(
            "STARTUP_CHECK_FAIL: config.yaml provider=%r matches a placeholder "
            "PROVIDER_REGISTRY entry (no real config). Chat dispatch will fail. "
            "Use a fully-registered provider.",
            provider,
        )
        return
    logger.info(
        "startup_check: config provider=%r registered (base_url=%s) ✓",
        provider,
        getattr(entry, "inference_base_url", "?"),
    )


def _check_neowow_only_consistency() -> None:
    """When HERMES_NEOWOW_ONLY=1, the .env should carry the Coding Plan
    API key. Surface missing-key state at startup instead of waiting
    for the first chat 401."""
    import os
    if os.environ.get("HERMES_NEOWOW_ONLY", "").strip().lower() not in {"1", "true", "yes"}:
        return
    has_canonical = bool(os.environ.get("NEOWOW_CODING_PLAN_API_KEY", "").strip())
    has_fallback  = bool(os.environ.get("OPENAI_API_KEY",            "").strip())
    if has_canonical or has_fallback:
        return
    logger.error(
        "STARTUP_CHECK_FAIL: HERMES_NEOWOW_ONLY=1 but neither "
        "NEOWOW_CODING_PLAN_API_KEY nor OPENAI_API_KEY is set. Coding Plan "
        "chat will return 401 immediately. Did .env get cleared after the "
        "last container restart? Run OAuth or re-onboard."
    )


def run_startup_checks() -> None:
    """Entry point called from server.py at boot.

    Wraps each check in its own try/except so one check's exception
    can't take down the others or the server itself.
    """
    for check_name, check_fn in (
        ("config_provider_registered", _check_config_provider_registered),
        ("neowow_only_consistency",     _check_neowow_only_consistency),
    ):
        try:
            check_fn()
        except Exception:
            logger.exception("startup_check %s threw", check_name)
