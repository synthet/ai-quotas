from datetime import datetime

import httpx

from app.config import Settings
from app.models.quota import ProviderSnapshot, ProviderStatus, QuotaSurface
from app.providers.base import ProviderAdapter
from app.providers.parsers import parse_anthropic_ratelimit_headers
from app.util.probe_models import ANTHROPIC_PROBE_FALLBACKS


def _anthropic_error_message(data: dict, status: int, raw: str) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        parts = [err.get("type"), err.get("message")]
        msg = " — ".join(p for p in parts if p)
        if msg:
            return msg
    if isinstance(data, dict):
        for key in ("message", "detail"):
            if data.get(key):
                return str(data[key])
    return raw[:300] or f"HTTP {status}"


class AnthropicAdapter(ProviderAdapter):
    provider_id = "anthropic.anthropic_api"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        if not settings.anthropic_api_key:
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.ANTHROPIC_API,
                status=ProviderStatus.SKIPPED,
                message="Set ANTHROPIC_API_KEY in .env",
                fetched_at=now,
            )
        if not settings.probe_enabled:
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.ANTHROPIC_API,
                status=ProviderStatus.SKIPPED,
                message="Probes disabled (PROBE_ENABLED=false)",
                fetched_at=now,
            )

        models: list[str] = []
        for m in (settings.anthropic_probe_model, *ANTHROPIC_PROBE_FALLBACKS):
            if m and m not in models:
                models.append(m)

        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        last_msg = "Probe failed"
        last_metrics = []

        for model in models:
            body = {
                "model": model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            }
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            )
            metrics = parse_anthropic_ratelimit_headers(resp.headers)
            if resp.status_code < 400:
                status = ProviderStatus.OK if metrics else ProviderStatus.DEGRADED
                return ProviderSnapshot(
                    provider="anthropic",
                    surface=QuotaSurface.ANTHROPIC_API,
                    status=status,
                    message=f"Rate limits from response headers (model: {model})",
                    fetched_at=now,
                    metrics=metrics,
                )

            try:
                data = resp.json()
            except Exception:
                data = {}
            last_msg = _anthropic_error_message(data, resp.status_code, resp.text)
            last_metrics = metrics
            if resp.status_code == 404 or "model" in last_msg.lower():
                continue
            break

        return ProviderSnapshot(
            provider="anthropic",
            surface=QuotaSurface.ANTHROPIC_API,
            status=ProviderStatus.ERROR,
            message=last_msg,
            fetched_at=now,
            metrics=last_metrics,
        )
