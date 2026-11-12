from app.config import Settings
from app.models.quota import ProviderSnapshot, ProviderStatus, QuotaSurface
from app.providers import adapters_for_settings, split_reachable


def _ids(settings: Settings) -> list[str]:
    return [a.provider_id for a in adapters_for_settings(settings)]


def test_every_entry_can_be_turned_off():
    settings = Settings(quota_scope="all", enable_cursor=False, enable_openai_api=False)
    ids = _ids(settings)
    assert "cursor.cursor" not in ids
    assert "openai.openai_api" not in ids
    assert "anthropic.claude_subscription" in ids


def test_each_card_has_an_enable_setting():
    settings = Settings(quota_scope="all", enable_gemini_cli=True)
    for adapter in adapters_for_settings(settings):
        surface = adapter.provider_id.rsplit(".", 1)[-1]
        assert hasattr(settings, f"enable_{surface}"), surface


def test_gemini_cli_off_by_default_and_legacy_flag_still_works():
    assert "gemini_cli" not in _ids(Settings(quota_scope="all"))
    assert "gemini_cli" in _ids(Settings(quota_scope="all", enable_gemini_cli=True))
    assert "gemini_cli" in _ids(Settings(quota_scope="all", include_gemini_cli_collector=True))


def _snap(surface: QuotaSurface, status: ProviderStatus) -> ProviderSnapshot:
    return ProviderSnapshot(provider="x", surface=surface, status=status)


def test_split_reachable_hides_error_unauthorized_unavailable_skipped():
    snaps = [
        _snap(QuotaSurface.CLAUDE_SUBSCRIPTION, ProviderStatus.OK),
        _snap(QuotaSurface.CURSOR, ProviderStatus.PARTIAL),
        _snap(QuotaSurface.ANTIGRAVITY, ProviderStatus.DEGRADED),
        _snap(QuotaSurface.ANTHROPIC_API_CREDITS, ProviderStatus.BLOCKED),
        _snap(QuotaSurface.ANTHROPIC_API, ProviderStatus.ERROR),
        _snap(QuotaSurface.OPENAI_API, ProviderStatus.UNAUTHORIZED),
        _snap(QuotaSurface.GEMINI_CLI, ProviderStatus.UNAVAILABLE),
        _snap(QuotaSurface.GEMINI_API, ProviderStatus.SKIPPED),
    ]
    shown, hidden = split_reachable(snaps, hide_unreachable=True)
    assert [s.status for s in shown] == [
        ProviderStatus.OK,
        ProviderStatus.PARTIAL,
        ProviderStatus.DEGRADED,
        ProviderStatus.BLOCKED,
    ]
    assert len(hidden) == 4


def test_split_reachable_can_show_everything():
    snaps = [_snap(QuotaSurface.ANTHROPIC_API, ProviderStatus.ERROR)]
    assert split_reachable(snaps, hide_unreachable=False) == (snaps, [])


def test_example_config_lists_every_setting_and_loads_as_defaults():
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "config.example.env"
    keys = {
        line.split("=", 1)[0].strip().lower()
        for line in example.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert keys == set(Settings.model_fields)
    loaded = Settings(_env_file=example)
    assert loaded.model_dump() == Settings(_env_file=None).model_dump()
