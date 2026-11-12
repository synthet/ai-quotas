from app.main import _fmt_reset
from app.providers.codex_adapter import _window_text


def test_iso_resets_are_human_readable_utc():
    assert _fmt_reset("2026-11-12T18:53:17+00:00") == "Nov 12, 18:53 UTC"
    assert _fmt_reset("2026-12-01T00:00:00.000Z") == "Dec 1, 00:00 UTC"
    assert _fmt_reset("2026-11-14T06:26:55Z") == "Nov 14, 06:26 UTC"


def test_non_iso_resets_pass_through():
    text = "Nov 18, 10:59pm (America/Chicago)"
    assert _fmt_reset(text) == text


def test_codex_window_minutes_read_as_hours_and_days():
    assert _window_text(300) == "5-hour"
    assert _window_text(10080) == "7-day"
    assert _window_text(45) == "45-minute"
