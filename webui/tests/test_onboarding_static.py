import pathlib


REPO = pathlib.Path(__file__).parent.parent


def read(path):
    return (REPO / path).read_text(encoding="utf-8")


def test_index_uses_single_blocking_neowow_login_gate():
    html = read("static/index.html")
    assert 'id="onboardingOverlay"' not in html
    assert 'id="onboardingBody"' not in html
    assert 'id="onboardingNextBtn"' not in html
    assert 'src="static/onboarding.js?v=__WEBUI_VERSION__"' not in html
    assert 'id="neowowBootOverlay"' in html
    assert 'id="neowowBootLoginBtn"' in html
    assert 'id="neowowBootActivateBtn"' in html
    assert 'id="neowowBootManualLink"' in html
    assert "neowowHideBootOverlay({ success: false, reason: 'timeout' })" not in html
    assert 'id="neowowAvatarRail"' in html


def test_neowow_login_gate_waits_for_coding_plan_and_refreshes_models():
    js = read("static/neowow.js")
    assert "window.neowowStartOAuth" in js
    assert "async function activateCodingPlanAfterLogin(neowowOnly)" in js
    assert "await activateCodingPlanAfterLogin(!!d.neowowOnly)" in js
    assert "neowowShowLoginRequired" in js
    assert "window.neowowActivateCodingPlanFromGate" in js
    assert "overlay.dataset.statusResolved = '1'" in js
    assert "_setWorkspaceInert(true)" in js
    assert "_setWorkspaceInert(false)" in js
    assert 'fetch(\'/api/neowow/activate-provider\'' in js
    boot_start = js.index("window.neowowResolveBootOverlay = async function")
    boot_end = js.index("window.neowowActivateCodingPlanFromGate", boot_start)
    boot = js[boot_start:boot_end]
    assert "fetch('/api/onboarding/status'" in boot
    assert "activateCodingPlanAfterLogin(true)" not in boot
    assert "populateModelDropdown({ force: true })" in js


def test_chat_routes_enforce_login_and_activation_invalidates_model_caches():
    routes = read("api/routes.py")
    guard_start = routes.index("def _reject_neowow_chat_without_login(handler)")
    guard_end = routes.index("# Approval system", guard_start)
    guard = routes[guard_start:guard_end]
    assert "from api.neowow import _neowow_only, get_jwt" in guard
    assert "if not _neowow_only() or get_jwt()" in guard

    start = routes.index('if parsed.path == "/api/chat/start":')
    sync = routes.index('if parsed.path == "/api/chat":', start)
    assert "_reject_neowow_chat_without_login(handler)" in routes[start:sync]

    activation_start = routes.index('if parsed.path == "/api/neowow/activate-provider":')
    activation_end = routes.index('if parsed.path == "/api/neowow/oauth/launch":', activation_start)
    activation = routes[activation_start:activation_end]
    assert "invalidate_models_cache()" in activation
    assert "_clear_live_models_cache()" in activation


def test_forced_model_refresh_bypasses_pre_login_browser_cache():
    js = read("static/ui.js")
    assert "_fetchLiveModels(data.active_provider, sel, !!opts.force)" in js
    assert "async function _fetchLiveModels(provider, sel, force=false)" in js
    assert "if(force) delete _liveModelCache[provider]" in js


def test_bootstrap_script_contains_official_installer_and_windows_guard():
    src = read("bootstrap.py")
    assert (
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
        in src
    )
    assert "Native Windows is not supported" in src
