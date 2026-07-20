#!/usr/bin/env python3
"""Audit the Neodomain chat-model catalog without exposing credentials or output."""

from __future__ import annotations

import argparse
import ast
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_GA_MODELS_URL = "https://ga.neodomain.cn/v1/models"
DEFAULT_GA_CHAT_URL = "https://ga.neodomain.cn/v1/chat/completions"
DEFAULT_PLAN_URL = "https://app.neowow.studio/api/me/plan"
DEFAULT_PROXY_CHAT_URL = "https://app.neowow.studio/api/me/chat/completions"
DEFAULT_CATALOG_FILE = Path("webui/api/config.py")


def _request_json(request: urllib.request.Request, timeout: int) -> tuple[int, Any]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(getattr(response, "status", 200)), json.load(response)


def _model_ids_from_payload(payload: Any) -> list[str]:
    """Accept either OpenAI /v1/models or the Neowow /api/me/plan shape."""
    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    raw_models = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("models")
    if not isinstance(raw_models, list):
        raise ValueError("model response contains no data/models array")
    result: set[str] = set()
    for raw in raw_models:
        model_id = raw.get("id") if isinstance(raw, dict) else raw
        model_id = str(model_id or "").strip()
        if model_id:
            result.add(model_id)
    return sorted(result)


def load_static_catalog(path: Path) -> list[str]:
    """Read the literal neodomain catalog without importing WebUI stateful code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_PROVIDER_MODELS" for target in node.targets):
            continue
        catalog = ast.literal_eval(node.value)
        return sorted(
            str(item.get("id") or "").strip()
            for item in catalog.get("neodomain", [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        )
    raise ValueError(f"_PROVIDER_MODELS not found in {path}")


def fetch_online_models(
    *, ga_token: str, admin_jwt: str, ga_models_url: str, plan_url: str, timeout: int
) -> tuple[list[str], str, str, str]:
    if ga_token:
        url, token, source = ga_models_url, ga_token, "ga-direct"
        chat_url = DEFAULT_GA_CHAT_URL
    elif admin_jwt:
        url, token, source = plan_url, admin_jwt, "neowow-plan-proxy"
        chat_url = DEFAULT_PROXY_CHAT_URL
    else:
        raise RuntimeError("NEODOMAIN_API_KEY or NEOWOW_ADMIN_JWT is required")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Hermes/model-audit",
        },
    )
    _, payload = _request_json(request, timeout)
    return _model_ids_from_payload(payload), source, chat_url, token


def _error_metadata(payload: Any) -> tuple[str | None, str | None]:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return (
            str(error.get("code") or "")[:120] or None,
            str(error.get("type") or "")[:120] or None,
        )
    if isinstance(error, str):
        return error[:120] or None, None
    return None, None


def probe_model(model_id: str, *, chat_url: str, token: str, timeout: int) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 1,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        chat_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Hermes/model-audit",
        },
    )
    started = time.monotonic()
    status: int | None = None
    payload: Any = None
    transport_error: str | None = None
    try:
        status, payload = _request_json(request, timeout)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = json.load(exc)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            payload = None
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        transport_error = "transport_error"

    error_code, error_type = _error_metadata(payload)
    has_choices = isinstance(payload, dict) and isinstance(payload.get("choices"), list) and bool(payload["choices"])
    if status is not None and 200 <= status < 300 and has_choices:
        state = "available"
    elif status in (401, 403):
        state = "auth_blocked"
    elif status in (408, 409, 425, 429) or (status is not None and status >= 500) or transport_error:
        state = "inconclusive"
    else:
        state = "unavailable"
    return {
        "model": model_id,
        "state": state,
        "http_status": status,
        "error_code": error_code,
        "error_type": error_type,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-file", type=Path, default=DEFAULT_CATALOG_FILE)
    parser.add_argument("--ga-models-url", default=DEFAULT_GA_MODELS_URL)
    parser.add_argument("--plan-url", default=DEFAULT_PLAN_URL)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--skip-probes", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1 or args.workers < 1 or args.workers > 8:
        parser.error("timeout must be positive and workers must be between 1 and 8")

    try:
        static_models = load_static_catalog(args.catalog_file)
        online_models, source, chat_url, token = fetch_online_models(
            ga_token=os.environ.get("NEODOMAIN_API_KEY", "").strip(),
            admin_jwt=os.environ.get("NEOWOW_ADMIN_JWT", "").strip(),
            ga_models_url=args.ga_models_url,
            plan_url=args.plan_url,
            timeout=args.timeout,
        )
        static_set, online_set = set(static_models), set(online_models)
        probes: list[dict[str, Any]] = []
        if not args.skip_probes:
            # Probe the union: online-only entries prove additions work, while
            # static-only entries prove whether a removal is really unusable.
            probe_models = sorted(static_set | online_set)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(probe_model, model_id, chat_url=chat_url, token=token, timeout=args.timeout): model_id
                    for model_id in probe_models
                }
                for future in as_completed(futures):
                    result = future.result()
                    result["listed_online"] = result["model"] in online_set
                    result["listed_static"] = result["model"] in static_set
                    probes.append(result)
            probes.sort(key=lambda item: item["model"])

        states = {state: sum(1 for item in probes if item["state"] == state) for state in (
            "available", "unavailable", "inconclusive", "auth_blocked"
        )}
        output = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "catalog_file": str(args.catalog_file),
            "summary": {
                "online_count": len(online_models),
                "static_count": len(static_models),
                "probe_count": len(probes),
                "added_online": sorted(online_set - static_set),
                "removed_online": sorted(static_set - online_set),
                **states,
            },
            "online_models": online_models,
            "probes": probes,
        }
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"model audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
