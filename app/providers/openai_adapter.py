from datetime import datetime

import httpx

from app.config import Settings
from app.models.quota import (
    ProviderSnapshot,
    ProviderStatus,
    QuotaSurface,
)
from app.providers.base import ProviderAdapter
from app.providers.parsers import parse_openai_error_body, parse_openai_ratelimit_headers


class OpenAIAdapter(ProviderAdapter):
    provider_id = "openai.openai_api"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        if not settings.openai_api_key:
            return ProviderSnapshot(
                provider="openai",
                surface=QuotaSurface.OPENAI_API,
                status=ProviderStatus.SKIPPED,
                message="Set OPENAI_API_KEY in .env",
                fetched_at=now,
            )
        if not settings.probe_enabled:
            return ProviderSnapshot(
                provider="openai",
                surface=QuotaSurface.OPENAI_API,
                status=ProviderStatus.SKIPPED,
                message="Probes disabled (PROBE_ENABLED=false)",
                fetched_at=now,
            )

        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": settings.openai_probe_model,
            "input": "hi",
            "max_output_tokens": 16,
        }
        resp = await client.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=body,
        )
        if resp.status_code >= 400:
            return self._handle_error(resp, now)

        metrics = parse_openai_ratelimit_headers(resp.headers)
        status = ProviderStatus.OK if metrics else ProviderStatus.DEGRADED
        msg = "Rate limits from response headers" if metrics else "Probe succeeded but no rate-limit headers"
        return ProviderSnapshot(
            provider="openai",
            surface=QuotaSurface.OPENAI_API,
            status=status,
            message=msg,
            fetched_at=now,
            metrics=metrics,
        )

    def _handle_error(self, resp: httpx.Response, now: datetime) -> ProviderSnapshot:
        try:
            data = resp.json()
        except Exception:
            data = {}
        code, message = parse_openai_error_body(data) if isinstance(data, dict) else ("http_error", resp.text[:200])

        header_metrics = parse_openai_ratelimit_headers(resp.headers)

        billing_blocked = code in (
            "insufficient_quota",
            "credit_balance_exhausted",
            "billing_not_active",
        )
        return ProviderSnapshot(
            provider="openai",
            surface=QuotaSurface.OPENAI_API,
            status=ProviderStatus.BLOCKED if billing_blocked else ProviderStatus.ERROR,
            message=(
                f"API billing: {code} — {message} "
                "(separate from ChatGPT/Codex subscription; add credits at "
                "https://platform.openai.com/settings/organization/billing/)"
                if billing_blocked
                else f"{code}: {message}"
            ),
            fetched_at=now,
            metrics=header_metrics,
        )
