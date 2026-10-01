from app.config import Settings
from app.providers import adapters_for_settings


def test_cli_scope_excludes_api():
    settings = Settings(quota_scope="cli")
    ids = [a.provider_id for a in adapters_for_settings(settings)]
    assert ids == [
        "anthropic.claude_subscription",
        "openai.chatgpt_codex",
        "cursor.cursor",
        "google.antigravity",
    ]


def test_api_scope_excludes_cli():
    settings = Settings(quota_scope="api")
    ids = [a.provider_id for a in adapters_for_settings(settings)]
    assert "cursor" not in ids
    assert "openai.openai_api" in ids
