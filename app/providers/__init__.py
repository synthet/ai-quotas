from app.config import Settings
from app.providers.antigravity_adapter import AntigravityAdapter
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.base import ProviderAdapter
from app.providers.claude_subscription_adapter import ClaudeSubscriptionAdapter
from app.providers.codex_adapter import CodexSubscriptionAdapter
from app.providers.cursor_adapter import CursorAdapter
from app.providers.gemini_api_adapter import GeminiApiAdapter
from app.providers.gemini_cli_adapter import GeminiCliAdapter
from app.providers.openai_adapter import OpenAIAdapter

CLI_ADAPTERS: list[ProviderAdapter] = [
    ClaudeSubscriptionAdapter(),
    CodexSubscriptionAdapter(),
    CursorAdapter(),
    AntigravityAdapter(),
]

API_ADAPTERS: list[ProviderAdapter] = [
    OpenAIAdapter(),
    AnthropicAdapter(),
    GeminiApiAdapter(),
]

LEGACY_CLI_ADAPTERS: list[ProviderAdapter] = [
    GeminiCliAdapter(),
]

ALL_ADAPTERS: list[ProviderAdapter] = API_ADAPTERS + CLI_ADAPTERS + LEGACY_CLI_ADAPTERS


def adapters_for_settings(settings: Settings) -> list[ProviderAdapter]:
    scope = settings.quota_scope
    if scope == "cli":
        adapters = list(CLI_ADAPTERS)
        if settings.include_gemini_cli_collector:
            adapters.append(GeminiCliAdapter())
        return adapters
    if scope == "api":
        return list(API_ADAPTERS)
    adapters = list(API_ADAPTERS + CLI_ADAPTERS)
    if settings.include_gemini_cli_collector:
        adapters.append(GeminiCliAdapter())
    return adapters
