from datetime import datetime

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
from app.providers.parsers import parse_gemini_retry_delay
from app.util.probe_models import GEMINI_PROBE_FALLBACKS


class GeminiApiAdapter(ProviderAdapter):
    provider_id = "google.gemini_api"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        if not settings.google_api_key:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.GEMINI_API,
                status=ProviderStatus.SKIPPED,
                message="Set GOOGLE_API_KEY in .env",
                fetched_at=now,
            )
        if not settings.probe_enabled:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.GEMINI_API,
                status=ProviderStatus.SKIPPED,
                message="Probes disabled (PROBE_ENABLED=false)",
                fetched_at=now,
            )

        models: list[str] = []
        for m in (settings.gemini_probe_model, *GEMINI_PROBE_FALLBACKS):
            if m and m not in models:
                models.append(m)

        last_err = "Probe failed"
        for model in models:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={settings.google_api_key}"
            )
            body = {
                "contents": [{"parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 16},
            }
            resp = await client.post(url, json=body)

            if resp.status_code == 429:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                retry = parse_gemini_retry_delay(data if isinstance(data, dict) else {})
                metrics = [
                    QuotaMetric(
                        name="rate_limited",
                        window=MetricWindow.RPM,
                        remaining=0.0,
                        resets_at=retry,
                        unit="count",
                        source=MetricSource.INFERENCE_ERROR,
                    )
                ]
                return ProviderSnapshot(
                    provider="google",
                surface=QuotaSurface.GEMINI_API,
                    status=ProviderStatus.DEGRADED,
                    message=f"Rate limited (429) on {model}. See AI Studio for RPM/TPM/RPD.",
                    fetched_at=now,
                    metrics=metrics,
                )

            if resp.status_code >= 400:
                try:
                    last_err = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    last_err = resp.text[:200]
                if resp.status_code in (404, 400) and (
                    "no longer available" in str(last_err).lower()
                    or "not found" in str(last_err).lower()
                ):
                    continue
                return ProviderSnapshot(
                    provider="google",
                surface=QuotaSurface.GEMINI_API,
                    status=ProviderStatus.ERROR,
                    message=str(last_err),
                    fetched_at=now,
                )

            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.GEMINI_API,
                status=ProviderStatus.OK,
                message=(
                    f"Probe OK ({model}). Gemini API does not expose remaining quota on success; "
                    "see AI Studio limits."
                ),
                fetched_at=now,
                metrics=[
                    QuotaMetric(
                        name="api_reachable",
                        window=MetricWindow.UNKNOWN,
                        remaining=100.0,
                        limit=100.0,
                        unit="%",
                        source=MetricSource.RESPONSE_HEADERS,
                    )
                ],
            )

        return ProviderSnapshot(
            provider="google",
            surface=QuotaSurface.GEMINI_API,
            status=ProviderStatus.ERROR,
            message=str(last_err),
            fetched_at=now,
        )
