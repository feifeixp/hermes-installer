import pathlib


REPO = pathlib.Path(__file__).parent.parent


def read(path):
    return (REPO / path).read_text(encoding="utf-8")


def test_index_has_no_blocking_onboarding_markup_or_script():
    html = read("static/index.html")
    assert 'id="onboardingOverlay"' not in html
    assert 'id="onboardingBody"' not in html
    assert 'id="onboardingNextBtn"' not in html
    assert 'src="static/onboarding.js?v=__WEBUI_VERSION__"' not in html
    assert 'id="neowowAvatarRail"' in html


def test_neowow_login_starts_from_account_avatar_and_prepares_coding_plan():
    js = read("static/neowow.js")
    assert "window.neowowStartOAuth" in js
    assert "async function activateCodingPlanAfterLogin(neowowOnly)" in js
    assert "void activateCodingPlanAfterLogin(!!d.neowowOnly);" in js
    assert 'fetch(\'/api/neowow/activate-provider\'' in js


def test_bootstrap_script_contains_official_installer_and_windows_guard():
    src = read("bootstrap.py")
    assert (
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
        in src
    )
    assert "Native Windows is not supported" in src
