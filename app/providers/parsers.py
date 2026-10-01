import re
from typing import Any

from app.models.quota import MetricSource, MetricWindow, QuotaMetric

httpx_headers_like = Any

_TIME_MULTIPLIERS = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}


def _header_int(headers: dict[str, str], key: str) -> int | None:
    val = headers.get(key) or headers.get(key.lower())
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def parse_openai_ratelimit_headers(headers: httpx_headers_like) -> list[QuotaMetric]:
    """Parse OpenAI x-ratelimit-* headers from a response."""
    h = _normalize_headers(headers)
    metrics: list[QuotaMetric] = []

    pairs = [
        ("requests", MetricWindow.RPM, "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests"),
        ("tokens", MetricWindow.TPM, "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens"),
        ("project_tokens", MetricWindow.TPM, "x-ratelimit-limit-project-tokens", "x-ratelimit-remaining-project-tokens", "x-ratelimit-reset-project-tokens"),
        ("tokens_usage_based", MetricWindow.DAILY, "x-ratelimit-limit-tokens-usage-based", "x-ratelimit-remaining-tokens-usage-based", "x-ratelimit-reset-tokens-usage-based"),
    ]
    for name, window, limit_k, rem_k, reset_k in pairs:
        limit = _header_int(h, limit_k)
        remaining = _header_int(h, rem_k)
        if limit is None and remaining is None:
            continue
        used = None
        if limit is not None and remaining is not None:
            used = float(limit - remaining)
        metrics.append(
            QuotaMetric(
                name=name,
                window=window,
                used=used,
                limit=float(limit) if limit is not None else None,
                remaining=float(remaining) if remaining is not None else None,
                unit="count" if window == MetricWindow.RPM else "tokens",
                resets_at=h.get(reset_k),
                source=MetricSource.RESPONSE_HEADERS,
            )
        )
    return metrics


def parse_anthropic_ratelimit_headers(headers: httpx_headers_like) -> list[QuotaMetric]:
    h = _normalize_headers(headers)
    metrics: list[QuotaMetric] = []
    prefix = "anthropic-ratelimit-"

    def add_from_suffix(suffix: str, window: MetricWindow, unit: str) -> None:
        limit = _header_int(h, f"{prefix}{suffix}-limit")
        remaining = _header_int(h, f"{prefix}{suffix}-remaining")
        reset = h.get(f"{prefix}{suffix}-reset")
        if limit is None and remaining is None:
            return
        used = None
        if limit is not None and remaining is not None:
            used = float(limit - remaining)
        metrics.append(
            QuotaMetric(
                name=suffix.replace("-", "_"),
                window=window,
                used=used,
                limit=float(limit) if limit is not None else None,
                remaining=float(remaining) if remaining is not None else None,
                unit=unit,
                resets_at=reset,
                source=MetricSource.RESPONSE_HEADERS,
            )
        )

    add_from_suffix("requests", MetricWindow.RPM, "count")
    add_from_suffix("tokens", MetricWindow.TPM, "tokens")
    add_from_suffix("input-tokens", MetricWindow.TPM, "input_tokens")
    add_from_suffix("output-tokens", MetricWindow.TPM, "output_tokens")
    return metrics


def parse_openai_error_body(body: dict[str, Any]) -> tuple[str, str | None]:
    err = body.get("error") or {}
    code = err.get("code") or err.get("type") or "api_error"
    message = err.get("message") or str(body)
    return str(code), str(message)


def _normalize_headers(headers: httpx_headers_like) -> dict[str, str]:
    if hasattr(headers, "items"):
        return {k.lower(): v for k, v in headers.items()}
    return {str(k).lower(): str(v) for k, v in dict(headers).items()}


def parse_gemini_retry_delay(error_body: dict[str, Any]) -> str | None:
    details = error_body.get("error", {}).get("details") or []
    for item in details:
        if not isinstance(item, dict):
            continue
        if "retryDelay" in item:
            return str(item["retryDelay"])
        retry = item.get("retryInfo") or item.get("@type", "")
        if "RetryInfo" in str(retry) and "retryDelay" in item:
            return str(item.get("retryDelay", ""))
    msg = str(error_body)
    match = re.search(r'"retryDelay":\s*"([^"]+)"', msg)
    if match:
        return match.group(1)
    return None
