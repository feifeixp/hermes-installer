"""Agent chat credential = non-expiring deploy token, not the 30-day JWT.

The agent's NEOWOW_CODING_PLAN_API_KEY used to be the raw login JWT, which
expires after 30 days with no refresh endpoint — when it lapsed every chat
401'd and the desktop froze on "Waiting on model …" until the user re-logged
in. _coding_plan_agent_credential() now mints a non-expiring nws_dt_ deploy
token (chat:invoke), caches it per-account, and returns THAT; it only falls
back to the JWT when minting is unavailable.

Run: python3.13 -m pytest webui/tests/test_coding_plan_token_credential.py -q
"""

from __future__ import annotations

import base64
import json


def _jwt_for(user_id: str) -> str:
    """Unsigned 3-segment JWT carrying a userId claim (payload isn't verified)."""
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    pay = base64.urlsafe_b64encode(
        json.dumps({"userId": user_id, "exp": 9999999999}).encode()
    ).decode().rstrip("=")
    return f"{hdr}.{pay}.sig"


class TestJwtUserId:
    def test_extracts_user_id(self):
        from api.neowow import _jwt_user_id
        assert _jwt_user_id(_jwt_for("u42")) == "u42"

    def test_undecodable_returns_empty(self):
        from api.neowow import _jwt_user_id
        assert _jwt_user_id("not-a-jwt") == ""
        assert _jwt_user_id("") == ""


class TestCodingPlanAgentCredential:
    def test_mints_and_caches_deploy_token(self, monkeypatch):
        import api.neowow as neowow
        saved: dict = {}
        monkeypatch.setattr(neowow, "_read_state", lambda: {"jwt": _jwt_for("u1")})
        monkeypatch.setattr(neowow, "_write_state", lambda s: saved.update(s))
        monkeypatch.setattr(neowow, "_mint_coding_plan_token", lambda jwt: "nws_dt_minted")

        cred = neowow._coding_plan_agent_credential(_jwt_for("u1"))

        assert cred == "nws_dt_minted", "should bridge the deploy token, not the JWT"
        assert saved.get("codingPlanToken") == "nws_dt_minted"
        assert saved.get("codingPlanTokenUserId") == "u1"

    def test_reuses_cached_token_without_reminting(self, monkeypatch):
        import api.neowow as neowow
        monkeypatch.setattr(neowow, "_read_state", lambda: {
            "codingPlanToken": "nws_dt_cached", "codingPlanTokenUserId": "u1",
        })
        monkeypatch.setattr(neowow, "_write_state", lambda s: None)

        def _boom(jwt):
            raise AssertionError("must not re-mint when a valid cache exists")

        monkeypatch.setattr(neowow, "_mint_coding_plan_token", _boom)
        assert neowow._coding_plan_agent_credential(_jwt_for("u1")) == "nws_dt_cached"

    def test_remints_on_account_switch(self, monkeypatch):
        import api.neowow as neowow
        saved: dict = {}
        monkeypatch.setattr(neowow, "_read_state", lambda: {
            "codingPlanToken": "nws_dt_old", "codingPlanTokenUserId": "u1",
        })
        monkeypatch.setattr(neowow, "_write_state", lambda s: saved.update(s))
        monkeypatch.setattr(neowow, "_mint_coding_plan_token", lambda jwt: "nws_dt_new")

        cred = neowow._coding_plan_agent_credential(_jwt_for("u2"))  # different user
        assert cred == "nws_dt_new", "a cache from another account must not be reused"
        assert saved.get("codingPlanTokenUserId") == "u2"

    def test_falls_back_to_jwt_when_mint_fails(self, monkeypatch):
        import api.neowow as neowow
        jwt = _jwt_for("u1")
        monkeypatch.setattr(neowow, "_read_state", lambda: {"jwt": jwt})
        monkeypatch.setattr(neowow, "_write_state", lambda s: None)
        monkeypatch.setattr(neowow, "_mint_coding_plan_token", lambda j: None)
        assert neowow._coding_plan_agent_credential(jwt) == jwt, "never write a keyless agent"

    def test_none_jwt_returns_none(self):
        import api.neowow as neowow
        assert neowow._coding_plan_agent_credential(None) is None


class TestMintCodingPlanToken:
    def _fake_urlopen(self, body: str):
        class _Resp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def read(self_inner):
                return body.encode("utf-8")
        return lambda req, timeout=8: _Resp()

    def test_parses_token_from_response(self, monkeypatch):
        import urllib.request
        import api.neowow as neowow
        monkeypatch.setattr(urllib.request, "urlopen",
                            self._fake_urlopen('{"ok":true,"token":"nws_dt_abc123"}'))
        assert neowow._mint_coding_plan_token(_jwt_for("u1")) == "nws_dt_abc123"

    def test_rejects_non_deploy_token_shape(self, monkeypatch):
        import urllib.request
        import api.neowow as neowow
        monkeypatch.setattr(urllib.request, "urlopen",
                            self._fake_urlopen('{"token":"eyJ-looks-like-jwt"}'))
        assert neowow._mint_coding_plan_token(_jwt_for("u1")) is None

    def test_network_failure_returns_none(self, monkeypatch):
        import urllib.request
        import api.neowow as neowow

        def _raise(req, timeout=8):
            raise OSError("boom")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        assert neowow._mint_coding_plan_token(_jwt_for("u1")) is None
