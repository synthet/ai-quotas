from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

QuotaScope = Literal["cli", "api", "all"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""

    # cli = Claude, Codex, Cursor, Antigravity (default; no API probes)
    # api = OpenAI + Anthropic + Gemini API only
    # all = API adapters + four CLI adapters (optional legacy Gemini CLI via flag)
    quota_scope: QuotaScope = "cli"

    probe_enabled: bool = True
    probe_interval_sec: int = 300

    openai_probe_model: str = "gpt-4o-mini"
    anthropic_probe_model: str = "claude-haiku-4-5"
    gemini_probe_model: str = "gemini-3.8-flash"

    gemini_credentials_dir: str = ""
    cursor_auth_path: str = ""
    antigravity_command: str = ""
    claude_command: str = ""
    claude_usage_timeout_sec: float = 90.0
    gemini_command: str = ""
    gemini_cli_timeout_sec: float = 45.0
    # Passing GOOGLE_CLOUD_PROJECT into Code Assist often triggers enterprise license 403s.
    gemini_cli_use_env_gcp_project: bool = False
    include_gemini_cli_collector: bool = False

    @property
    def gemini_dir(self) -> Path:
        if self.gemini_credentials_dir:
            return Path(self.gemini_credentials_dir)
        return Path.home() / ".gemini"


def get_settings() -> Settings:
    return Settings()
