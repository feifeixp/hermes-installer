"""The 报告问题 UI is wired: button in index.html, handler in panels.js.

Run: python3.13 -m pytest webui/tests/test_report_issue_ui.py -q
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
PANELS = (STATIC / "panels.js").read_text(encoding="utf-8")


def test_button_present_in_index():
    assert "reportIssueBtn" in INDEX


def test_handler_calls_endpoint():
    assert "__reportIssue" in PANELS
    assert "/api/report-issue" in PANELS
