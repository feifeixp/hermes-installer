"""Expired JWT → re-login at startup.

A present-but-EXPIRED Neodomain JWT used to report hasJwt=True ("已登录") while
every chat 401'd, because the agent was bridged a dead key → the desktop froze
on "Waiting on model …". get_status now treats an expired JWT as logged-out
(+ a jwtExpired flag) so the onboarding login prompt shows at startup. No silent
refresh is possible (the platform has no refresh-token endpoint), so re-login is
the only recovery.

Run: python3.13 -m pytest webui/tests/test_jwt_expiry_relogin.py -q
"""

from __future__ import annotations

import base64
import json
import time


def _make_jwt(exp) -> str:
    """Build an UNSIGNED 3-segment JWT carrying the given exp claim.
    _jwt_is_expired never verifies the signature — it only decodes the
    payload — so a dummy signature segment is fine."""
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = json.dumps({"exp": exp, "userId": "u1"}).encode()
    pay = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{hdr}.{pay}.sig"


class TestJwtIsExpired:
    def test_future_exp_is_valid(self):
        from api.neowow import _jwt_is_expired
        assert _jwt_is_expired(_make_jwt(int(time.time()) + 3600)) is False

    def test_past_exp_is_expired(self):
        from api.neowow import _jwt_is_expired
        assert _jwt_is_expired(_make_jwt(int(time.time()) - 3600)) is True

    def test_within_skew_still_valid(self):
        # 30s past exp is within the 60s skew (matches dashboard exp+60s) → valid.
        from api.neowow import _jwt_is_expired
        assert _jwt_is_expired(_make_jwt(int(time.time()) - 30)) is False

    def test_no_exp_claim_not_flagged(self):
        # Can't read an exp → don't force re-login (avoid false positives).
        from api.neowow import _jwt_is_expired
        hdr = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
        pay = base64.urlsafe_b64encode(b'{"userId":"u1"}').decode().rstrip("=")
        assert _jwt_is_expired(f"{hdr}.{pay}.sig") is False

    def test_garbage_not_flagged(self):
        from api.neowow import _jwt_is_expired
        assert _jwt_is_expired("not-a-jwt") is False
        assert _jwt_is_expired("") is False


class TestGetStatusExpiry:
    def test_expired_jwt_reports_logged_out(self, monkeypatch):
        import api.neowow as neowow
        monkeypatch.delenv("HERMES_WEBUI_AUTH_MODE", raising=False)  # desktop/file mode
        monkeypatch.setattr(
            neowow, "_read_state",
            lambda: {"jwt": _make_jwt(int(time.time()) - 3600)},
        )
        st = neowow.get_status()
        assert st["hasJwt"] is False, "expired JWT must read as logged-out"
        assert st["jwtExpired"] is True

    def test_valid_jwt_reports_logged_in(self, monkeypatch):
        import api.neowow as neowow
        monkeypatch.delenv("HERMES_WEBUI_AUTH_MODE", raising=False)
        monkeypatch.setattr(
            neowow, "_read_state",
            lambda: {"jwt": _make_jwt(int(time.time()) + 3600)},
        )
        st = neowow.get_status()
        assert st["hasJwt"] is True
        assert st["jwtExpired"] is False

    def test_no_jwt_is_not_expired(self, monkeypatch):
        import api.neowow as neowow
        monkeypatch.delenv("HERMES_WEBUI_AUTH_MODE", raising=False)
        monkeypatch.setattr(neowow, "_read_state", lambda: {})
        st = neowow.get_status()
        assert st["hasJwt"] is False
        assert st["jwtExpired"] is False, "absent JWT is not 'expired', just missing"
