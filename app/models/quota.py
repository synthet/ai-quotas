from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProviderStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"


class QuotaSurface(str, Enum):
    CHATGPT_CODEX = "chatgpt_codex"
    OPENAI_API = "openai_api"
    CLAUDE_SUBSCRIPTION = "claude_subscription"
    ANTHROPIC_API = "anthropic_api"
    ANTIGRAVITY = "antigravity"
    GEMINI_API = "gemini_api"
    GEMINI_CLI = "gemini_cli"
    CURSOR = "cursor"


class MetricWindow(str, Enum):
    RPM = "rpm"
    TPM = "tpm"
    DAILY = "daily"
    BILLING_MONTH = "billing_month"
    FIVE_H = "5h"
    WEEKLY = "weekly"
    UNKNOWN = "unknown"


class MetricSource(str, Enum):
    OFFICIAL_API = "official_api"
    OFFICIAL_LOCAL_CLI = "official_local_cli"
    RESPONSE_HEADERS = "response_headers"
    ADMIN_API = "admin_api"
    PRIVATE_DASHBOARD = "private_dashboard"
    DASHBOARD_API = "dashboard_api"  # legacy alias
    CLOUDCODE_INTERNAL = "cloudcode_internal"
    INFERENCE_ERROR = "inference_error"


class QuotaMetric(BaseModel):
    name: str
    surface: QuotaSurface | None = None
    window: MetricWindow = MetricWindow.UNKNOWN
    window_label: str | None = None
    used: float | None = None
    limit: float | None = None
    remaining: float | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    unit: str = ""
    resets_at: str | None = None
    source: MetricSource = MetricSource.RESPONSE_HEADERS


class ProviderSnapshot(BaseModel):
    provider: str
    surface: QuotaSurface | None = None
    status: ProviderStatus
    message: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    metrics: list[QuotaMetric] = Field(default_factory=list)

    @property
    def card_id(self) -> str:
        if self.surface:
            return f"{self.provider}.{self.surface.value}"
        return self.provider


class DashboardSnapshot(BaseModel):
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    providers: list[ProviderSnapshot] = Field(default_factory=list)
