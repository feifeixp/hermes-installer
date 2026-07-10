"""chat_credential_is_expired() — retired; never pre-blocks a send on exp.

The accessToken is long-lived + server-refreshed, so the client must NOT
pre-judge token expiry from the local JWT `exp` claim. The desktop agent rides
a non-expiring nws_dt_ deploy token, and a truly dead session surfaces as
errCode 2001 (JwtRevokedError) from a business API — not a local exp check.

chat_credential_is_expired() therefore ALWAYS returns False now, so the
/api/chat/start guard in routes.py can no longer pre-block sends. These tests
pin that contract across every credential shape.

Run: python3.13 -m pytest webui/tests/test_chat_credential_expiry.py -q
"""

from __future__ import annotations

import base64
import json
import time


def _jwt(exp) -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    pay = base64.urlsafe_b64encode(json.dumps({"exp": exp, "userId": "u1"}).encode()).decode().rstrip("=")
    return f"{hdr}.{pay}.sig"


def test_deploy_token_never_blocks(monkeypatch):
    import api.neowow as nw
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", "nws_dt_deadbeef")
    # Even in neodomain mode with an expired cookie JWT, a deploy token wins.
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: True)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) - 3600))
    assert nw.chat_credential_is_expired() is False


def test_neodomain_expired_cookie_jwt_does_not_block(monkeypatch):
    # Retired: even an expired cookie JWT in neodomain mode no longer pre-blocks
    # — a dead session surfaces as errCode 2001, not this local exp check.
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: True)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) - 3600))
    assert nw.chat_credential_is_expired() is False


def test_neodomain_valid_cookie_jwt_ok(monkeypatch):
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: True)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) + 3600))
    assert nw.chat_credential_is_expired() is False


def test_desktop_expired_jwt_cred_does_not_block(monkeypatch):
    # Retired: an expired desktop JWT credential no longer pre-blocks either.
    import api.neowow as nw
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", _jwt(int(time.time()) - 3600))
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    assert nw.chat_credential_is_expired() is False


def test_desktop_valid_jwt_cred_ok(monkeypatch):
    import api.neowow as nw
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", _jwt(int(time.time()) + 3600))
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    assert nw.chat_credential_is_expired() is False


def test_own_provider_no_neowow_cred_ok(monkeypatch):
    # User's own provider: no coding-plan cred, not neodomain → never block.
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) - 3600))
    assert nw.chat_credential_is_expired() is False
