import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_status_exposes_neowow_block(monkeypatch):
    import api.onboarding as ob
    monkeypatch.setattr(ob, "_safe_neowow_status", lambda: {"hasJwt": True, "points": 1200})
    st = ob.get_onboarding_status()
    assert "neowow" in st
    assert st["neowow"]["hasJwt"] is True
