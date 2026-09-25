"""Optional HTTP decision advisor. Never executes tools or changes model permissions."""
import collections
import ipaddress
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULTS = {
    'mode': 'off', 'endpoint': 'http://127.0.0.1:8082',
    'model': 'multilingual', 'threshold': 0.9, 'timeout_ms': 500,
    'api_key': '',
}
WORKFLOWS = {
    'explain': '聊天、解释、翻译、写文本；不操作外部工具',
    'research': '搜索网页、读取外部网址、核实最新资料',
    'coding': '编写修改源代码、执行测试、调试项目',
    'media': '调用模型生成或编辑图片、视频、音频、音乐',
    'canvas': '创建移动修改画布节点、连线、上传已有素材；不生成媒体',
    'other': '账户账单客服、请求不清楚、多个不同工作流或无法归类',
}
QUESTIONS = {'workflow': {
    'type': 'choice',
    'instructions': '根据用户最新的实际要求选择下一步工作流。区分咨询和执行，遵守否定、暂停和改口。无法确定或多个不同工作流选 other。',
    'criteria': WORKFLOWS,
}}
_lock = threading.RLock()
_slots = threading.BoundedSemaphore(2)
_recent = collections.deque(maxlen=20)


def _path():
    from api.config import STATE_DIR
    return Path(STATE_DIR) / 'laya-settings.json'


def validate_endpoint(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Invalid service URL')
    value = value.strip().rstrip('/')
    try:
        url = urllib.parse.urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError('Invalid service URL') from None
    if (url.scheme not in ('http', 'https') or not url.hostname or
            url.username is not None or url.password is not None or url.query or url.fragment or
            any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise ValueError('Use an HTTP(S) base URL without credentials, query or fragment')
    host = url.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_loopback and (address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved):
        raise ValueError('This service address is not supported')
    local = host == 'localhost' or (address is not None and address.is_loopback)
    if url.scheme == 'http' and not local:
        raise ValueError('Remote services require HTTPS; HTTP is supported for loopback only')
    if port == 0:
        raise ValueError('Invalid service port')
    return value


def normalize(data, previous=None):
    if not isinstance(data, dict):
        raise ValueError('Settings must be an object')
    result = dict(DEFAULTS if previous is None else previous)
    for key in DEFAULTS:
        if key in data and key != 'api_key':
            result[key] = data[key]
    if result['mode'] not in ('off', 'observe', 'assist'):
        raise ValueError('Invalid mode')
    result['endpoint'] = validate_endpoint(result['endpoint'])
    if result['model'] not in ('multilingual', 'english', 'typed-decisions'):
        raise ValueError('Invalid checkpoint')
    threshold = result['threshold']
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold) or not 0.5 <= threshold <= 1:
        raise ValueError('Threshold must be between 0.5 and 1')
    timeout = result['timeout_ms']
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 100 <= timeout <= 5000:
        raise ValueError('Timeout must be an integer between 100 and 5000 ms')
    if 'api_key' in data:
        key = data['api_key']
        if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in key):
            raise ValueError('Invalid API key')
        # Blank input preserves a saved key; clearing is explicit.
        if key.strip():
            result['api_key'] = key.strip()
    if data.get('clear_api_key') is True:
        result['api_key'] = ''
    if previous and result['endpoint'] != previous['endpoint'] and not data.get('api_key', '').strip():
        result['api_key'] = ''
    return result


def load_settings():
    with _lock:
        try:
            return normalize(json.loads(_path().read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            return dict(DEFAULTS)


def public_settings(settings=None):
    settings = dict(load_settings() if settings is None else settings)
    settings['has_api_key'] = bool(settings.pop('api_key', ''))
    return settings


def save_settings(data):
    with _lock:
        settings = normalize(data, load_settings())
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.laya-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(settings, out, ensure_ascii=False, indent=2)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return public_settings(settings)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(settings, path, payload=None):
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    if settings['api_key']:
        headers['Authorization'] = 'Bearer ' + settings['api_key']
    req = urllib.request.Request(settings['endpoint'] + path, headers=headers,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(req, timeout=settings['timeout_ms'] / 1000) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Service response is too large')
    return json.loads(raw)


def _state(text, history=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError('Enter between 1 and 6000 characters')
    from api.helpers import _redact_text
    turns = []
    for item in list(history or [])[-4:]:
        if isinstance(item, dict) and item.get('role') in ('user', 'assistant') and isinstance(item.get('content'), str):
            turns.append({'role': item['role'], 'content': _redact_text(item['content'][-1000:], _enabled=True)})
    turns.append({'role': 'user', 'content': _redact_text(text, _enabled=True)})
    return turns


def _predict(settings, state):
    data = _request(settings, '/v1/systemone', {'state': state, 'questions': QUESTIONS, 'model': settings['model']})
    try:
        answer = data['answers']['workflow']
        choice = answer['choice']
        probabilities = answer['probabilities']
        if choice not in WORKFLOWS or set(probabilities) != set(WORKFLOWS):
            raise ValueError()
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError()
        if abs(sum(probabilities.values()) - 1) > .01 or probabilities[choice] != max(probabilities.values()):
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        raise ValueError('Invalid decision response') from None
    confidence = probabilities[choice]
    return {'status': 'ok', 'workflow': choice, 'confidence': confidence,
            'accepted': choice != 'other' and confidence >= settings['threshold']}


def recent_results():
    with _lock:
        return list(reversed(_recent))


def _bounded(settings, operation, *, background=False, record=False):
    """Wall-clock budget, including DNS. At most two daemon calls may be in flight."""
    if not _slots.acquire(blocking=False):
        return {'status': 'busy', 'accepted': False}
    done = threading.Event()
    result = {}
    start = time.monotonic()
    expired = threading.Event()

    def worker():
        try:
            value = operation()
        except urllib.error.HTTPError as exc:
            value = {'status': 'unauthorized' if exc.code in (401, 403) else 'unavailable', 'accepted': False}
        except (TimeoutError,):
            value = {'status': 'timeout', 'accepted': False}
        except Exception:
            # Do not echo URLs, tokens, response bodies or user messages.
            value = {'status': 'unavailable', 'accepted': False}
        elapsed = round((time.monotonic() - start) * 1000, 1)
        if elapsed > settings['timeout_ms'] or expired.is_set():
            value = {'status': 'timeout', 'accepted': False}
        value['elapsed_ms'] = elapsed
        result.update(value)
        if record:
            with _lock:
                _recent.append({**value, 'mode': settings['mode'], 'time': time.time()})
        done.set()
        _slots.release()

    threading.Thread(target=worker, daemon=True, name='laya-advisor').start()
    if background:
        return {'status': 'queued', 'accepted': False}
    if not done.wait(settings['timeout_ms'] / 1000):
        expired.set()
        return {'status': 'timeout', 'accepted': False, 'elapsed_ms': settings['timeout_ms']}
    return dict(result)


def probe(data):
    if not isinstance(data, dict):
        raise ValueError('Request must be an object')
    settings = normalize(data.get('settings', {}), load_settings())
    kind = data.get('kind', 'connection')
    if kind == 'connection':
        def check():
            # Validate the actual inference protocol and bearer auth, not only
            # the public /health endpoint. This sends a fixed synthetic greeting.
            _predict(settings, _state('你好，请介绍一下你能做什么。'))
            return {'status': 'ok'}
        return _bounded(settings, check)
    if kind != 'sample':
        raise ValueError('Invalid test kind')
    state = _state(data.get('text'))
    return _bounded(settings, lambda: _predict(settings, state))


def hint_for_turn(text, history=(), attachments=None, cancel_event=None):
    """Returns fixed advisory prose only. No raw service text enters model context."""
    settings = load_settings()
    if settings['mode'] == 'off' or attachments or (cancel_event and cancel_event.is_set()):
        return ''
    try:
        state = _state(text, history)
    except (ValueError, TypeError):
        return ''
    result = _bounded(settings, lambda: _predict(settings, state),
                      background=settings['mode'] == 'observe', record=True)
    if settings['mode'] != 'assist' or not result.get('accepted') or (cancel_event and cancel_event.is_set()):
        return ''
    workflow = result['workflow']
    return ('Optional Laya workflow suggestion: ' + workflow + '. '
            'This classifier can be wrong. Verify against the latest user request and context. '
            'It does not authorize tool use or override instructions, permissions, or required approvals. '
            'Use it only to help choose relevant skills; retain all existing tools and the selected model.')
