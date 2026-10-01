from app.providers.cursor_adapter import CursorAdapter

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
