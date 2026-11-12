from app.config import Settings
from app.providers.ant_credit_adapter import AntCreditAdapter
from app.providers.antigravity_adapter import AntigravityAdapter
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.base import ProviderAdapter
from app.providers.claude_subscription_adapter import ClaudeSubscriptionAdapter
from app.providers.codex_adapter import CodexSubscriptionAdapter
from app.providers.cursor_adapter import CursorAdapter
from app.providers.gemini_api_adapter import GeminiApiAdapter
from app.providers.gemini_cli_adapter import GeminiCliAdapter
from app.models.quota import ProviderSnapshot
from app.providers.openai_adapter import OpenAIAdapter

# Dashboard card order (Anthropic → OpenAI → Cursor → Google)
CLI_CARD_ORDER: tuple[str, ...] = (
    "anthropic.claude_subscription",
    "anthropic.anthropic_api_credits",
    "openai.chatgpt_codex",
    "cursor.cursor",
    "google.antigravity",
    "anthropic.anthropic_api",
    "openai.openai_api",
    "google.gemini_api",
)

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

CREDIT_ADAPTERS: list[ProviderAdapter] = [
    AntCreditAdapter(),
]

ALL_ADAPTERS: list[ProviderAdapter] = API_ADAPTERS + CLI_ADAPTERS + CREDIT_ADAPTERS + LEGACY_CLI_ADAPTERS


def sort_provider_snapshots(snapshots: list[ProviderSnapshot]) -> list[ProviderSnapshot]:
    """Keep CLI cards in CLI_CARD_ORDER; other surfaces follow alphabetically."""
    rank = {card_id: i for i, card_id in enumerate(CLI_CARD_ORDER)}

    def sort_key(s):
        cid = s.card_id
        if cid in rank:
            return (0, rank[cid])
        return (1, cid)

    return sorted(snapshots, key=sort_key)


def adapters_for_settings(settings: Settings) -> list[ProviderAdapter]:
    scope = settings.quota_scope
    if scope == "cli":
        adapters = list(CLI_ADAPTERS) + list(CREDIT_ADAPTERS)
        if settings.include_gemini_cli_collector:
            adapters.append(GeminiCliAdapter())
        return adapters
    if scope == "api":
        return list(API_ADAPTERS) + list(CREDIT_ADAPTERS)
    adapters = list(API_ADAPTERS + CLI_ADAPTERS + CREDIT_ADAPTERS)
    if settings.include_gemini_cli_collector:
        adapters.append(GeminiCliAdapter())
    return adapters
