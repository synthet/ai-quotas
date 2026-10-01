import json
import shutil
import subprocess
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


def _window_from_id(window: str) -> MetricWindow:
    w = (window or "").lower()
    if w == "5h" or "5" in w and "hour" in w:
        return MetricWindow.FIVE_H
    if w == "weekly" or "week" in w:
        return MetricWindow.WEEKLY
    return MetricWindow.UNKNOWN


class AntigravityAdapter(ProviderAdapter):
    provider_id = "google.antigravity"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        exe = settings.antigravity_command or shutil.which("agy")
        if not exe:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.ANTIGRAVITY,
                status=ProviderStatus.SKIPPED,
                message="Antigravity CLI (agy) not found on PATH",
                fetched_at=now,
            )

        try:
            proc = subprocess.run(
                [exe, "-p", "/usage", "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.ANTIGRAVITY,
                status=ProviderStatus.ERROR,
                message="agy /usage timed out",
                fetched_at=now,
            )

        if proc.returncode != 0:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.ANTIGRAVITY,
                status=ProviderStatus.ERROR,
                message=(proc.stderr or proc.stdout or "agy failed")[:300],
                fetched_at=now,
            )

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return ProviderSnapshot(
                provider="google",
                surface=QuotaSurface.ANTIGRAVITY,
                status=ProviderStatus.ERROR,
                message="Invalid JSON from agy /usage",
                fetched_at=now,
            )

        cmd = payload.get("command") or {}
        data = cmd.get("data") or {}
        groups = data.get("groups") or []
        metrics: list[QuotaMetric] = []

        for group in groups:
            if not isinstance(group, dict):
                continue
            group_name = str(group.get("name") or "group")
            for bucket in group.get("buckets") or []:
                if not isinstance(bucket, dict):
                    continue
                frac = bucket.get("remaining_fraction")
                if frac is None:
                    continue
                try:
                    remaining_pct = float(frac) * 100.0
                except (TypeError, ValueError):
                    continue
                used_pct = max(0.0, 100.0 - remaining_pct)
                bid = bucket.get("id") or bucket.get("name") or "bucket"
                metrics.append(
                    QuotaMetric(
                        name=f"{group_name}:{bid}",
                        surface=QuotaSurface.ANTIGRAVITY,
                        window=_window_from_id(str(bucket.get("window") or "")),
                        used_percent=used_pct,
                        remaining_percent=remaining_pct,
                        unit="percent",
                        resets_at=bucket.get("reset_time"),
                        source=MetricSource.OFFICIAL_LOCAL_CLI,
                    )
                )

        return ProviderSnapshot(
            provider="google",
            surface=QuotaSurface.ANTIGRAVITY,
            status=ProviderStatus.OK if metrics else ProviderStatus.DEGRADED,
            message="Antigravity subscription (agy /usage)",
            fetched_at=now,
            metrics=metrics,
        )
