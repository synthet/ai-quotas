from app.providers.claude_subscription_adapter import _parse_usage_text

SAMPLE = """You are currently using your subscription to power your Claude Code usage

Current session: 0% used
Current week (all models): 100% used · resets Oct 1, 12am (America/Chicago)
"""


def test_parse_claude_usage_text():
    metrics = _parse_usage_text(SAMPLE)
    names = {m.name for m in metrics}
    assert "session" in names
    assert "week_all_models" in names
    week = next(m for m in metrics if m.name == "week_all_models")
    assert week.used_percent == 100.0
    assert week.remaining_percent == 0.0


def test_parse_claude_usage_session_with_reset():
    text = (
        "Current session: 100% used · resets Oct 22, 11pm (America/Chicago)\n"
        "Current week (all models): 23% used · resets Oct 29, 12am (America/Chicago)\n"
    )
    metrics = _parse_usage_text(text)
    session = next(m for m in metrics if m.name == "session")
    assert session.used_percent == 100.0
    assert session.remaining_percent == 0.0
    assert session.resets_at == "Oct 22, 11pm (America/Chicago)"
