"""Regression: the 8 messaging routes stay wired in routes.py handlers."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api import routes


def test_get_messaging_routes_wired():
    src = inspect.getsource(routes.handle_get)
    assert '/api/messaging/channels' in src
    assert '/api/messaging/weixin/qr/status' in src


def test_post_messaging_routes_wired():
    src = inspect.getsource(routes.handle_post)
    for path in [
        '/api/messaging/weixin/qr/start',
        '/api/messaging/weixin/disconnect',
        '/api/messaging/feishu/config',
        '/api/messaging/feishu/disconnect',
        '/api/messaging/wecom/config',
        '/api/messaging/wecom/disconnect',
    ]:
        assert path in src, path
