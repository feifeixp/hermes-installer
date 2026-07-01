"""chat_credential_is_expired() — the /api/chat/start expired-JWT guard.

Converts the silent "Waiting on model" hang into a 401 + re-login, but ONLY
when the chat credential is truly an expired login JWT — never for a
non-expiring nws_dt_ deploy token (#45) or a valid JWT (no false-blocks).

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


def test_neodomain_expired_cookie_jwt_blocks(monkeypatch):
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: True)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) - 3600))
    assert nw.chat_credential_is_expired() is True


def test_neodomain_valid_cookie_jwt_ok(monkeypatch):
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: True)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) + 3600))
    assert nw.chat_credential_is_expired() is False


def test_desktop_expired_jwt_cred_blocks(monkeypatch):
    import api.neowow as nw
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", _jwt(int(time.time()) - 3600))
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    assert nw.chat_credential_is_expired() is True


def test_desktop_valid_jwt_cred_ok(monkeypatch):
    import api.neowow as nw
    monkeypatch.setenv("NEOWOW_CODING_PLAN_API_KEY", _jwt(int(time.time()) + 3600))
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    assert nw.chat_credential_is_expired() is False


def test_own_provider_no_neowow_cred_ok(monkeypatch):
    # User's own provider: no coding-plan cred, not neodomain → never block,
    # even if a stale file JWT lingers (we only read the env cred here).
    import api.neowow as nw
    monkeypatch.delenv("NEOWOW_CODING_PLAN_API_KEY", raising=False)
    monkeypatch.setattr(nw, "_is_neodomain_mode", lambda: False)
    monkeypatch.setattr(nw, "get_jwt", lambda: _jwt(int(time.time()) - 3600))
    assert nw.chat_credential_is_expired() is False
