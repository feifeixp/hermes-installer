"""Optional advisor contract: settings, real HTTP shape, and unchanged Agent behavior."""
import copy
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from api import laya


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / 'laya-settings.json'
    monkeypatch.setattr(laya, '_path', lambda: path)
    laya._recent.clear()
    return path


def answer(label='canvas', confidence=.96):
    probabilities = {key: (1-confidence)/5 for key in laya.WORKFLOWS}
    probabilities[label] = confidence
    return {'answers': {'workflow': {'choice': label, 'probabilities': probabilities,
                                    'confidence': 1, 'answer_confidence': 1}}}


@pytest.fixture
def service():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, payload, self.headers.get('Authorization')))
            body = json.dumps(answer()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', received
    server.shutdown()
    server.server_close()


def test_disabled_by_default_and_no_network(storage, monkeypatch):
    monkeypatch.setattr(laya, '_request', lambda *args: pytest.fail('Disabled mode called service'))
    assert laya.load_settings()['mode'] == 'off'
    assert laya.hint_for_turn('生成一张海报') == ''
    assert not storage.exists()


def test_roundtrip_secret_redaction_and_permissions(storage):
    public = laya.save_settings({'mode':'observe', 'api_key':'test-only-key', 'threshold':.92})
    assert public['has_api_key'] and 'api_key' not in public
    assert 'test-only-key' not in json.dumps(public)
    assert storage.stat().st_mode & 0o777 == 0o600
    assert laya.load_settings()['api_key'] == 'test-only-key'
    laya.save_settings({'api_key':''})
    assert laya.load_settings()['api_key'] == 'test-only-key'
    laya.save_settings({'clear_api_key':True})
    assert laya.load_settings()['api_key'] == ''


def test_changed_endpoint_does_not_reuse_key(storage):
    laya.save_settings({'api_key':'test-only-key'})
    draft = laya.normalize({'endpoint':'https://example.org'}, laya.load_settings())
    assert draft['api_key'] == ''
    assert laya.load_settings()['api_key'] == 'test-only-key'


@pytest.mark.parametrize('patch', [
    {'mode':'execute'}, {'mode':[]}, {'model':'arbitrary'}, {'threshold':True},
    {'threshold':float('nan')}, {'threshold':.49}, {'timeout_ms':1}, {'timeout_ms':True},
    {'timeout_ms':1.5}, {'api_key':'line\nbreak'}, {'endpoint':'file:///etc/passwd'},
    {'endpoint':'http://example.org'}, {'endpoint':'https://user:pass@example.org'},
    {'endpoint':'http://169.254.169.254'}, {'endpoint':'https://example.org?key=abc'},
])
def test_invalid_settings_do_not_overwrite(storage, patch):
    laya.save_settings({'mode':'observe'})
    before = storage.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        laya.save_settings(patch)
    assert storage.read_bytes() == before


def test_ipv6_loopback_supported():
    assert laya.validate_endpoint('http://[::1]:8082/') == 'http://[::1]:8082'


def test_corrupt_settings_fail_closed(storage):
    storage.write_text('{ broken')
    assert laya.load_settings()['mode'] == 'off'
    storage.write_text(json.dumps({'mode':'assist','threshold':False}))
    assert laya.load_settings()['mode'] == 'off'


def test_probe_uses_unsaved_settings_and_real_inference_contract(storage, service):
    url, received = service
    result = laya.probe({'kind':'sample','text':'只修改提示词，不生成',
        'settings':{'endpoint':url,'api_key':'test-only-key','timeout_ms':1000}})
    assert result['status'] == 'ok' and result['workflow'] == 'canvas'
    assert result['confidence'] == .96  # ignores a service's misleading confidence=1
    path, body, auth = received[0]
    assert path == '/v1/systemone'
    assert body['model'] == 'multilingual'
    assert body['state'][-1]['content'] == '只修改提示词，不生成'
    assert auth == 'Bearer test-only-key'
    assert not storage.exists()  # testing never saves settings


def test_connection_tests_authenticated_prediction_not_public_health(storage, service):
    url, received = service
    result = laya.probe({'kind':'connection','settings':{'endpoint':url,'timeout_ms':1000}})
    assert result['status'] == 'ok'
    assert received[0][0] == '/v1/systemone'


@pytest.mark.parametrize('data', [
    {}, {'answers':{'workflow':{'choice':'execute_shell','probabilities':{}}}},
    answer(confidence=float('nan')), answer(confidence=2),
])
def test_bad_decisions_rejected(monkeypatch, data):
    monkeypatch.setattr(laya, '_request', lambda *args: data)
    with pytest.raises(ValueError):
        laya._predict(laya.DEFAULTS, [])


def test_assist_only_adds_fixed_hint_and_preserves_context(storage, monkeypatch):
    laya.save_settings({'mode':'assist'})
    monkeypatch.setattr(laya, '_request', lambda *args: answer())
    history = [{'role':'user','content':'之前的请求'}, {'role':'assistant','content':'之前的回答'}]
    before = copy.deepcopy(history)
    hint = laya.hint_for_turn('只修改节点提示词', history)
    assert 'canvas' in hint and 'does not authorize' in hint
    assert history == before
    assert not any('content' in row or 'text' in row for row in laya.recent_results())


@pytest.mark.parametrize('label,confidence', [('canvas',.7), ('other',.99)])
def test_low_probability_or_other_returns_original_flow(storage, monkeypatch, label, confidence):
    laya.save_settings({'mode':'assist'})
    monkeypatch.setattr(laya, '_request', lambda *args: answer(label,confidence))
    assert laya.hint_for_turn('一个请求') == ''


def test_observe_is_nonblocking_and_never_supplies_hint(storage, monkeypatch):
    laya.save_settings({'mode':'observe'})
    entered = threading.Event(); release = threading.Event(); finished = threading.Event()
    def slow(*args):
        entered.set(); release.wait(1); finished.set(); return answer()
    monkeypatch.setattr(laya, '_request', slow)
    start = time.monotonic()
    assert laya.hint_for_turn('请排列节点') == ''
    assert time.monotonic()-start < .15
    assert entered.wait(.5)
    release.set(); assert finished.wait(.5)


def test_wall_clock_timeout_includes_stalled_transport(storage, monkeypatch):
    laya.save_settings({'mode':'assist','timeout_ms':100})
    release = threading.Event(); finished = threading.Event()
    def slow(*args):
        release.wait(1); finished.set(); return answer()
    monkeypatch.setattr(laya, '_request', slow)
    start=time.monotonic()
    assert laya.hint_for_turn('请排列节点') == ''
    assert time.monotonic()-start < .4
    release.set(); assert finished.wait(.5)


def test_cancelled_multimodal_and_long_turns_skip(storage, monkeypatch):
    laya.save_settings({'mode':'assist'})
    monkeypatch.setattr(laya, '_request', lambda *args: pytest.fail('Should skip'))
    cancelled=threading.Event(); cancelled.set()
    assert laya.hint_for_turn('test',cancel_event=cancelled) == ''
    assert laya.hint_for_turn('test',attachments=[{'type':'image'}]) == ''
    assert laya.hint_for_turn('x'*6001) == ''


def test_redirects_are_not_followed():
    assert laya._NoRedirect().redirect_request(None,None,302,'',{},'https://example.org') is None


def test_streaming_integration_does_not_replace_user_or_model():
    source=(Path(__file__).parent.parent/'api/streaming.py').read_text()
    start=source.index('from api.laya import hint_for_turn')
    chunk=source[start:source.index('result = agent.run_conversation(',start)]
    assert 'workspace_system_msg =' in chunk
    assert 'user_message =' not in chunk and '_agent_kwargs' not in chunk
    assert 'except Exception:' in chunk
