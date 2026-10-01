"""Gemini CLI subscription quota parsing and Code Assist API helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from app.models.quota import (
    MetricSource,
    MetricWindow,
    ProviderStatus,
    QuotaMetric,
    QuotaSurface,
)

_NO_LICENSE_MARKERS = (
    "valid license",
    "do not have a valid license",
    "enterprise",
)


def quota_error_status(status_code: int, body: str) -> tuple[ProviderStatus, str, str | None]:
    """Map HTTP failure to dashboard status, message, and optional error_code."""
    lower = body.lower()
    if status_code == 401:
        return (
            ProviderStatus.UNAUTHORIZED,
            (
                "Gemini CLI OAuth session rejected. Re-authenticate the CLI or use "
                "Antigravity (agy) for consumer subscription quota."
            ),
            "oauth_rejected",
        )
    if status_code == 403 and any(m in lower for m in _NO_LICENSE_MARKERS):
        return (
            ProviderStatus.UNAVAILABLE,
            "Google Code Assist quota endpoint rejected the current OAuth entitlement",
            "license_not_available",
        )
    if status_code == 403:
        return (
            ProviderStatus.UNAVAILABLE,
            (
                "Cloud Code quota API denied access (HTTP 403). This is not fixed by "
                "GOOGLE_API_KEY. For personal subscription quota use Antigravity (agy)."
            ),
            "access_denied",
        )
    return (
        ProviderStatus.ERROR,
        f"Quota API HTTP {status_code}: {body[:240]}",
        None,
    )


def load_code_assist_body(include_env_project: bool, env_project: str) -> dict[str, Any]:
    """Minimal loadCodeAssist payload — no GCP project unless explicitly allowed."""
    body: dict[str, Any] = {
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }
    if include_env_project and env_project:
        body["cloudaicompanionProject"] = (
            env_project if env_project.startswith("projects/") else f"projects/{env_project}"
        )
    return body


def companion_project_from_load_response(data: dict[str, Any]) -> str | None:
    """Project returned by Code Assist setup, not from machine env."""
    for key in (
        "cloudaicompanionProject",
        "companionProject",
        "project",
        "cloudAiCompanionProject",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    nested = data.get("codeAssist") or data.get("codeAssistInfo")
    if isinstance(nested, dict):
        return companion_project_from_load_response(nested)
    return None


def retrieve_quota_bodies(companion_project: str | None) -> list[dict[str, Any]]:
    """Try empty body first; then companion project variants."""
    bodies: list[dict[str, Any]] = [{}]
    if not companion_project:
        return bodies
    proj = companion_project
    if not proj.startswith("projects/"):
        proj_id = proj
        bodies.append({"project": proj_id})
        bodies.append({"cloudaicompanionProject": f"projects/{proj_id}"})
    else:
        bodies.append({"cloudaicompanionProject": proj})
        bodies.append({"project": proj.removeprefix("projects/")})
    return bodies


def extract_bucket_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = data.get("buckets")
    if isinstance(buckets, list):
        return [b for b in buckets if isinstance(b, dict)]
    for key in ("quotaBuckets", "quotas", "userQuota"):
        val = data.get(key)
        if isinstance(val, list):
            return [b for b in val if isinstance(b, dict)]
    return []


def metrics_from_buckets(
    buckets: list[dict[str, Any]],
    source: MetricSource,
) -> list[QuotaMetric]:
    metrics: list[QuotaMetric] = []
    for i, bucket in enumerate(buckets):
        label = (
            bucket.get("modelId")
            or bucket.get("displayName")
            or bucket.get("name")
            or bucket.get("tier")
            or f"quota_{i}"
        )
        frac = bucket.get("remainingFraction")
        if frac is None:
            frac = bucket.get("remaining_fraction")
        remaining_pct: float | None = None
        used_pct: float | None = None
        if frac is not None:
            try:
                f = float(frac)
                if f > 1.0:
                    f = f / 100.0
                remaining_pct = round(f * 100.0, 4)
                used_pct = round((1.0 - f) * 100.0, 4)
            except (TypeError, ValueError):
                pass

        if remaining_pct is None and bucket.get("remainingAmount") is not None:
            try:
                remaining_pct = float(bucket["remainingAmount"])
                used_pct = max(0.0, 100.0 - remaining_pct) if remaining_pct <= 100 else None
            except (TypeError, ValueError):
                pass

        if remaining_pct is None:
            continue

        reset = bucket.get("resetTime") or bucket.get("reset_time") or bucket.get("resetsAt")
        metrics.append(
            QuotaMetric(
                name=str(label),
                surface=QuotaSurface.GEMINI_CLI,
                window=_window_from_label(str(label)),
                used_percent=used_pct,
                remaining_percent=remaining_pct,
                unit="percent",
                resets_at=str(reset) if reset else None,
                source=source,
            )
        )
    return metrics


def parse_quota_payload(data: dict[str, Any], source: MetricSource) -> list[QuotaMetric]:
    return metrics_from_buckets(extract_bucket_list(data), source)


def parse_stats_cli_json(stdout: str) -> list[QuotaMetric]:
    """Best-effort parse of `gemini -p '/stats model' -o json`."""
    text = stdout.strip()
    if not text:
        return []
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return _parse_stats_text(text)

    if isinstance(outer, dict):
        if "buckets" in outer or "quotaBuckets" in outer:
            return parse_quota_payload(outer, MetricSource.OFFICIAL_LOCAL_CLI)
        result = outer.get("result") or outer.get("response") or outer.get("text")
        if isinstance(result, str):
            return _parse_stats_text(result)
        if isinstance(result, dict):
            return parse_quota_payload(result, MetricSource.OFFICIAL_LOCAL_CLI)
    return []


_REMAIN_FRAC = re.compile(
    r"(?P<model>[\w.\-]+).*?remaining(?:\s*fraction)?[:\s]+(?P<frac>0?\.\d+|\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_stats_text(text: str) -> list[QuotaMetric]:
    metrics: list[QuotaMetric] = []
    for m in _REMAIN_FRAC.finditer(text):
        try:
            f = float(m.group("frac"))
            if f > 1.0:
                f /= 100.0
        except ValueError:
            continue
        metrics.append(
            QuotaMetric(
                name=m.group("model"),
                surface=QuotaSurface.GEMINI_CLI,
                used_percent=round((1.0 - f) * 100.0, 4),
                remaining_percent=round(f * 100.0, 4),
                unit="percent",
                source=MetricSource.OFFICIAL_LOCAL_CLI,
            )
        )
    return metrics


def _window_from_label(label: str) -> MetricWindow:
    lower = label.lower()
    if "week" in lower:
        return MetricWindow.WEEKLY
    if "5" in lower and "hour" in lower:
        return MetricWindow.FIVE_H
    if "day" in lower or "daily" in lower:
        return MetricWindow.DAILY
    if "flash" in lower or "pro" in lower:
        return MetricWindow.UNKNOWN
    return MetricWindow.UNKNOWN
