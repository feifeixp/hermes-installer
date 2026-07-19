#!/usr/bin/env python3
"""Collect privacy-bounded feedback metadata for the Product Loop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "https://app.neowow.studio/api/admin/reports"
SAFE_FIELDS = (
    "reportId",
    "createdAt",
    "source",
    "appVersion",
    "platform",
    "description",
    "status",
)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_reports(payload: Any, since: datetime | None = None) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("reports"), list):
        raise ValueError("expected an object containing a reports array")

    normalized: list[dict[str, str]] = []
    for raw in payload["reports"]:
        if not isinstance(raw, dict):
            continue
        report_id = _clean_text(raw.get("reportId"), 80)
        created_at = _clean_text(raw.get("createdAt"), 64)
        if not report_id or not created_at:
            continue
        created = _parse_time(created_at)
        if since is not None and (created is None or created < since):
            continue
        item = {field: _clean_text(raw.get(field), 2000 if field == "description" else 120)
                for field in SAFE_FIELDS}
        normalized.append(item)

    normalized.sort(key=lambda item: item["createdAt"], reverse=True)
    return normalized


def fetch_payload(api_url: str, token: str, timeout: int) -> Any:
    request = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "neowow-product-loop/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"feedback API returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"feedback API unavailable: {exc.reason}") from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.environ.get("NEOWOW_REPORTS_API", DEFAULT_API_URL))
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--input", type=Path, help="Read a saved API response instead of making a request")
    args = parser.parse_args()

    if args.since_hours < 1 or args.since_hours > 24 * 31:
        parser.error("--since-hours must be between 1 and 744")
    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)

    try:
        if args.input:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            source_state = "fixture"
        else:
            token = os.environ.get("NEOWOW_ADMIN_JWT", "").strip()
            if not token:
                print("NEOWOW_ADMIN_JWT is not configured; production feedback source is unauthorized.", file=sys.stderr)
                return 2
            payload = fetch_payload(args.api_url, token, args.timeout)
            source_state = "authorized"

        output = {
            "source": "neowow-admin-reports",
            "source_state": source_state,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "since": since.isoformat(),
            "reports": normalize_reports(payload, since),
        }
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"feedback collection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
