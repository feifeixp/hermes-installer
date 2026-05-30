"""The Neowow account panel must not show「重新登录」on a mere reachability blip.

Bug: when the user IS logged in (status.hasJwt=true) but a points/whoami fetch
hit a transient SSL/network error ("Cannot reach Neodomain: [SSL: ...]"), both
the settings 账号 block and the rail-avatar popover rendered the alarming
「⚠️ … 🔑重新登录 / 退出」card — making a logged-in user think their session
died. The fix distinguishes auth rejection (请重新登录 / 拒绝访问 / 已过期 /
revoked) from a reachability error, and only shows re-login for the former.

Source-grep test (repo convention — see test_skills_toggle.py).
"""
from pathlib import Path

NEOWOW_JS = (Path(__file__).resolve().parent.parent / "static" / "neowow.js").read_text("utf-8")


def test_distinguishes_auth_from_reachability():
    # Both error branches must gate the re-login card behind an auth check.
    assert "isAuthError" in NEOWOW_JS
    assert "请重新登录" in NEOWOW_JS  # part of the auth-signal regex


def test_reachability_error_keeps_user_logged_in():
    # The non-auth branch shows a "still logged in, just can't reach" message,
    # not a re-login prompt.
    assert "暂时无法连接 Neodomain" in NEOWOW_JS
    assert "已登录 Neodomain" in NEOWOW_JS


def test_account_block_has_retry_not_relogin_on_reach_error():
    # Settings 账号 block exposes a retry that re-runs the fetch.
    assert "window.neowowRefreshAccount" in NEOWOW_JS
    assert "neowowRefreshAccount()" in NEOWOW_JS
