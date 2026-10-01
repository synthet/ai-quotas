from datetime import datetime, timezone

import httpx

from app.config import Settings
from app.models.quota import (
    MetricSource,
    MetricWindow,
    ProviderSnapshot,
    ProviderStatus,
    QuotaMetric,
    QuotaSurface,
)
from app.providers.base import ProviderAdapter
from app.util.codex_app_server import read_rate_limits


def _window_label(minutes: int | None) -> MetricWindow:
    if minutes is None:
        return MetricWindow.UNKNOWN
    if minutes <= 360:
        return MetricWindow.FIVE_H
    if minutes >= 10000:
        return MetricWindow.WEEKLY
    return MetricWindow.UNKNOWN


def _metric_from_window(
    name: str,
    window: dict,
    now: datetime,
) -> QuotaMetric | None:
    used_pct = window.get("usedPercent")
    if used_pct is None:
        return None
    try:
        used = float(used_pct)
    except (TypeError, ValueError):
        return None
    mins = window.get("windowDurationMins")
    resets_at = None
    raw_reset = window.get("resetsAt")
    if raw_reset is not None:
        try:
            resets_at = datetime.fromtimestamp(int(raw_reset), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            pass
    return QuotaMetric(
        name=name,
        surface=QuotaSurface.CHATGPT_CODEX,
        window=_window_label(int(mins) if mins is not None else None),
        window_label=f"{mins}m" if mins else None,
        used_percent=used,
        remaining_percent=max(0.0, 100.0 - used),
        unit="percent",
        resets_at=resets_at,
        source=MetricSource.OFFICIAL_LOCAL_CLI,
    )


class CodexSubscriptionAdapter(ProviderAdapter):
    provider_id = "openai.chatgpt_codex"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        try:
            result = read_rate_limits()
        except FileNotFoundError:
            return ProviderSnapshot(
                provider="openai",
                surface=QuotaSurface.CHATGPT_CODEX,
                status=ProviderStatus.SKIPPED,
                message="Install Codex CLI and sign in (ChatGPT/Codex)",
                fetched_at=now,
            )
        except Exception as e:
            return ProviderSnapshot(
                provider="openai",
                surface=QuotaSurface.CHATGPT_CODEX,
                status=ProviderStatus.ERROR,
                message=str(e),
                fetched_at=now,
            )

        limits = result.get("rateLimits") or result
        metrics: list[QuotaMetric] = []
        primary = limits.get("primary")
        secondary = limits.get("secondary")
        if isinstance(primary, dict):
            m = _metric_from_window("five_hour", primary, now)
            if m:
                metrics.append(m)
        if isinstance(secondary, dict):
            m = _metric_from_window("weekly", secondary, now)
            if m:
                metrics.append(m)

        plan = limits.get("planType")
        msg = f"ChatGPT/Codex plan: {plan}" if plan else "ChatGPT/Codex subscription limits"
        return ProviderSnapshot(
            provider="openai",
            surface=QuotaSurface.CHATGPT_CODEX,
            status=ProviderStatus.OK if metrics else ProviderStatus.DEGRADED,
            message=msg,
            fetched_at=now,
            metrics=metrics,
        )
