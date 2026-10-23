from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.quota import MetricWindow
from app.providers.cursor_adapter import (
    CursorAdapter,
    _detect_cli_weekly_limit,
    _parse_transcript_error_line,
)

SAMPLE = {
    "billingCycleEnd": "2026-10-24T01:18:31.000Z",
    "individualUsage": {
        "plan": {
            "used": 2000,
            "limit": 2000,
            "remaining": 0,
            "autoPercentUsed": 9.5,
            "apiPercentUsed": 0,
            "totalPercentUsed": 9.3,
        }
    },
}


def test_parse_usage_summary_individual_plan():
    metrics = CursorAdapter()._parse_usage_summary(SAMPLE)
    names = {m.name for m in metrics}
    assert "composer_auto" in names
    assert "total" in names
    total = next(m for m in metrics if m.name == "total")
    assert total.used_percent == 9.3
    assert total.resets_at == "2026-10-24T01:18:31.000Z"
    assert total.window == MetricWindow.BILLING_MONTH
    assert total.window_label == "Monthly billing"


def test_parse_transcript_error_line_active():
    now = datetime(2026, 10, 23, 2, 0, 0, tzinfo=timezone.utc)
    mtime = datetime(2026, 10, 23, 1, 0, 0, tzinfo=timezone.utc)
    line = '{"type":"turn_ended","status":"error","error":"Weekly usage limit reached. It resets in 3 days. Buy credits to keep going now, or upgrade your plan for more weekly usage."}'

    hit = _parse_transcript_error_line(line, mtime, now)
    assert hit is not None
    resets_at, val, unit = hit
    assert val == 3
    assert unit == "day"
    assert resets_at == mtime + timedelta(days=3)
    assert resets_at > now


def test_parse_transcript_error_line_expired():
    now = datetime(2026, 10, 30, 2, 0, 0, tzinfo=timezone.utc)
    mtime = datetime(2026, 10, 23, 1, 0, 0, tzinfo=timezone.utc)
    line = '{"type":"turn_ended","status":"error","error":"Weekly usage limit reached. It resets in 3 days. Buy credits to keep going now, or upgrade your plan for more weekly usage."}'

    hit = _parse_transcript_error_line(line, mtime, now)
    assert hit is None


def test_parse_transcript_error_line_irrelevant():
    now = datetime(2026, 10, 23, 2, 0, 0, tzinfo=timezone.utc)
    mtime = datetime(2026, 10, 23, 1, 0, 0, tzinfo=timezone.utc)
    line = '{"type":"turn_ended","status":"ok"}'

    hit = _parse_transcript_error_line(line, mtime, now)
    assert hit is None


def test_detect_cli_weekly_limit_from_dir(tmp_path: Path):
    now = datetime(2026, 10, 23, 2, 0, 0, tzinfo=timezone.utc)
    trans_dir = tmp_path / "projects" / "test-project" / "agent-transcripts" / "test-chat"
    trans_dir.mkdir(parents=True)
    jsonl_file = trans_dir / "test-chat.jsonl"
    jsonl_file.write_text(
        '{"type":"turn_ended","status":"error","error":"Weekly usage limit reached. It resets in 2 days."}\n',
        encoding="utf-8",
    )

    hit = _detect_cli_weekly_limit(now, cursor_dir=tmp_path)
    assert hit is not None
    resets_at, val, unit = hit
    assert val == 2
    assert unit == "day"
