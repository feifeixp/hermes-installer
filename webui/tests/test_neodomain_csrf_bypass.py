"""
Regression test: neodomain CSRF bypass.

Bug: when HERMES_WEBUI_AUTH_MODE=neodomain, the OAuth callback at
app.neowow.studio only writes the cross-subdomain `neoToken` JWT cookie
(Domain=.neowow.studio). It does NOT write `hermes_session`. But
_check_csrf used to call parse_cookie() which only reads hermes_session,
making verify_csrf_token() always fail. Result: every browser POST in
cloud-deployed (chat-<userId>.neowow.studio) instances got stuck in the
"Session expired - reload the page" loop forever.

Fix: in neodomain mode, when a valid neoToken JWT is present and the
same-origin check above has already passed, accept the request without
requiring a separate hermes_session-backed CSRF token. The same-origin
check is the CSRF surface in this mode; the JWT is the auth surface.

This test locks the bypass behavior in so a future refactor doesn't
silently regress every cloud user back into the 403 loop.
"""
from types import SimpleNamespace

import pytest


# ── helpers ─────────────────────────────────────────────────────────────


def _call_check_csrf(monkeypatch, *, auth_mode, neo_jwt, jwt_valid,
                     hermes_session_cookie_value=None,
                     csrf_header_value=None,
                     csrf_token_valid=False,
                     headers_overrides=None):
    """Run _check_csrf with the surrounding auth state monkeypatched.

    Default headers are same-origin so the origin gate above the bypass
    always passes; tests can override via headers_overrides.
    """
    import api.auth as auth_mod
    from api import routes

    monkeypatch.setattr(auth_mod, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth_mod, "get_auth_mode", lambda: auth_mode)
    monkeypatch.setattr(
        auth_mod, "parse_neo_cookie",
        lambda _handler: neo_jwt,
    )
    monkeypatch.setattr(
        auth_mod, "_neodomain_jwt_looks_valid",
        lambda _jwt: jwt_valid,
    )
    monkeypatch.setattr(
        auth_mod, "parse_cookie",
        lambda _handler: hermes_session_cookie_value,
    )
    monkeypatch.setattr(
        auth_mod, "verify_csrf_token",
        lambda _cookie, _submitted: csrf_token_valid,
    )

    headers = {
        "Origin": "https://chat-1.neowow.studio",
        "Host": "chat-1.neowow.studio",
    }
    if headers_overrides:
        headers.update(headers_overrides)
    if csrf_header_value is not None:
        headers[auth_mod.CSRF_HEADER_NAME] = csrf_header_value

    handler = SimpleNamespace(headers=headers)
    return routes._check_csrf(handler)


# ── neodomain bypass: positive cases ────────────────────────────────────


def test_neodomain_with_valid_jwt_passes_csrf(monkeypatch):
    """Same-origin POST + valid neoToken JWT must pass CSRF check, even
    when hermes_session cookie is absent (the cloud-deployment default)."""
    assert _call_check_csrf(
        monkeypatch,
        auth_mode="neodomain",
        neo_jwt="eyJhbGciOiJIUzUxMiJ9.fake.signature",
        jwt_valid=True,
        hermes_session_cookie_value=None,
        csrf_header_value=None,
        csrf_token_valid=False,
    )


def test_neodomain_with_valid_jwt_ignores_missing_csrf_header(monkeypatch):
    """The bypass must not require X-Hermes-CSRF-Token in neodomain mode.
    Cloud instances' JS never sets it because there's no hermes_session
    to derive it from."""
    assert _call_check_csrf(
        monkeypatch,
        auth_mode="neodomain",
        neo_jwt="valid.jwt.here",
        jwt_valid=True,
        csrf_header_value=None,  # no header sent
    )


# ── neodomain bypass: negative cases ────────────────────────────────────


def test_neodomain_without_jwt_falls_through_to_token_check(monkeypatch):
    """No neoToken cookie at all → bypass skipped, normal CSRF check runs
    (and rejects since hermes_session is also absent)."""
    assert not _call_check_csrf(
        monkeypatch,
        auth_mode="neodomain",
        neo_jwt=None,
        jwt_valid=False,
        hermes_session_cookie_value=None,
        csrf_token_valid=False,
    )


def test_neodomain_with_invalid_jwt_falls_through_to_token_check(monkeypatch):
    """neoToken present but JWT failed expiration/signature/owner check →
    bypass skipped, normal CSRF check runs and rejects."""
    assert not _call_check_csrf(
        monkeypatch,
        auth_mode="neodomain",
        neo_jwt="expired.or.wrong-user.jwt",
        jwt_valid=False,
        hermes_session_cookie_value=None,
        csrf_token_valid=False,
    )


# ── non-neodomain mode is unaffected ────────────────────────────────────


def test_password_mode_still_requires_csrf_token(monkeypatch):
    """In password (default) mode, the bypass must not trigger. CSRF token
    check is still mandatory even if a stray neoToken happens to be set."""
    # Neodomain JWT present and "looks valid", but auth_mode is password.
    # Bypass must be skipped; without a valid csrf token, request is rejected.
    assert not _call_check_csrf(
        monkeypatch,
        auth_mode="password",
        neo_jwt="some.jwt",
        jwt_valid=True,
        hermes_session_cookie_value="some_session",
        csrf_token_valid=False,
    )


def test_password_mode_with_valid_csrf_token_passes(monkeypatch):
    """Sanity check: password mode + valid CSRF token still passes."""
    assert _call_check_csrf(
        monkeypatch,
        auth_mode="password",
        neo_jwt=None,
        jwt_valid=False,
        hermes_session_cookie_value="session_value",
        csrf_header_value="matching_token",
        csrf_token_valid=True,
    )


# ── origin check still gates everything ─────────────────────────────────


def test_neodomain_cross_origin_still_rejected(monkeypatch):
    """The bypass must NOT short-circuit the same-origin check above it.
    Cross-origin POSTs with a valid JWT in cookies are still rejected —
    that's the whole point of keeping the origin gate."""
    assert not _call_check_csrf(
        monkeypatch,
        auth_mode="neodomain",
        neo_jwt="valid.jwt",
        jwt_valid=True,
        headers_overrides={
            "Origin": "https://evil.example.com",
            "Host": "chat-1.neowow.studio",
        },
    )
