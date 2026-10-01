import json
import os
from datetime import datetime
from pathlib import Path

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
from app.util.jwt import user_id_from_cursor_jwt
from app.util.sqlite_kv import read_item_table_value

CURSOR_USAGE_URL = "https://cursor.com/api/usage-summary"


def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class CursorAdapter(ProviderAdapter):
    provider_id = "cursor.cursor"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        token, source = self._resolve_access_token(settings)
        if not token:
            return ProviderSnapshot(
                provider="cursor",
                surface=QuotaSurface.CURSOR,
                status=ProviderStatus.SKIPPED,
                message="No Cursor session found (log into Cursor IDE or cursor-agent)",
                fetched_at=now,
            )

        try:
            cookie = self._build_session_cookie(token)
        except ValueError as e:
            return ProviderSnapshot(
                provider="cursor",
                surface=QuotaSurface.CURSOR,
                status=ProviderStatus.ERROR,
                message=str(e),
                fetched_at=now,
            )

        headers = {
            "Cookie": f"WorkosCursorSessionToken={cookie}",
            "User-Agent": "cursor-agent/1.0",
            "Accept": "application/json",
        }
        resp = await client.get(CURSOR_USAGE_URL, headers=headers)

        if resp.status_code == 401:
            return ProviderSnapshot(
                provider="cursor",
                surface=QuotaSurface.CURSOR,
                status=ProviderStatus.ERROR,
                message="Session expired — sign in to Cursor again",
                fetched_at=now,
            )
        if resp.status_code >= 400:
            return ProviderSnapshot(
                provider="cursor",
                surface=QuotaSurface.CURSOR,
                status=ProviderStatus.ERROR,
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                fetched_at=now,
            )

        try:
            data = resp.json()
        except Exception:
            return ProviderSnapshot(
                provider="cursor",
                surface=QuotaSurface.CURSOR,
                status=ProviderStatus.ERROR,
                message="Invalid JSON from usage-summary",
                fetched_at=now,
            )

        metrics = self._parse_usage_summary(data)
        return ProviderSnapshot(
            provider="cursor",
            surface=QuotaSurface.CURSOR,
            status=ProviderStatus.OK if metrics else ProviderStatus.DEGRADED,
            message=f"Cursor private dashboard ({source})",
            fetched_at=now,
            metrics=metrics,
        )

    def _resolve_access_token(self, settings: Settings) -> tuple[str | None, str]:
        if settings.cursor_auth_path:
            path = Path(settings.cursor_auth_path)
            if path.is_file():
                return self._read_token_file(path), str(path)

        agent_path = Path.home() / ".config" / "cursor" / "auth.json"
        token = self._read_token_file(agent_path)
        if token:
            return token, "cursor-agent auth.json"

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            vscdb = Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
            token = read_item_table_value(vscdb, "cursorAuth/accessToken")
            if token:
                return token.strip().strip('"'), "Cursor IDE state.vscdb"

        return None, ""

    def _read_token_file(self, path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        token = data.get("accessToken") or data.get("access_token")
        return str(token).strip() if token else None

    def _build_session_cookie(self, token: str) -> str:
        token = token.strip()
        if "%3A%3A" in token:
            return token
        if "::" in token:
            parts = token.split("::", 1)
            return f"{parts[0]}%3A%3A{parts[1]}"
        user_id = user_id_from_cursor_jwt(token)
        return f"{user_id}%3A%3A{token}"

    def _parse_usage_summary(self, data: dict) -> list[QuotaMetric]:
        metrics: list[QuotaMetric] = []
        billing_end = data.get("billingCycleEnd") or data.get("billingCycleEndDate")

        plan = (data.get("individualUsage") or {}).get("plan") or {}
        if plan:
            for key, name in (
                ("autoPercentUsed", "composer_auto"),
                ("apiPercentUsed", "api_pool"),
                ("totalPercentUsed", "total"),
            ):
                if key not in plan:
                    continue
                try:
                    pct = float(plan[key])
                except (TypeError, ValueError):
                    continue
                metrics.append(
                    QuotaMetric(
                        name=name,
                        surface=QuotaSurface.CURSOR,
                        window=MetricWindow.BILLING_MONTH,
                        used_percent=pct,
                        remaining_percent=max(0.0, 100.0 - pct),
                        unit="percent",
                        resets_at=str(billing_end) if billing_end else None,
                        source=MetricSource.PRIVATE_DASHBOARD,
                    )
                )
            if any(k in plan for k in ("used", "limit", "remaining")):
                used = _float_or_none(plan.get("used"))
                limit = _float_or_none(plan.get("limit"))
                remaining = _float_or_none(plan.get("remaining"))
                metrics.append(
                    QuotaMetric(
                        name="plan_included",
                        surface=QuotaSurface.CURSOR,
                        window=MetricWindow.BILLING_MONTH,
                        used=used,
                        limit=limit,
                        remaining=remaining,
                        unit="included_units",
                        resets_at=str(billing_end) if billing_end else None,
                        source=MetricSource.PRIVATE_DASHBOARD,
                    )
                )

        field_map = [
            ("autoComposerUsagePercent", "composer_pool"),
            ("apiUsagePercent", "api_pool"),
            ("totalUsagePercent", "total"),
            ("gpt4UsagePercent", "premium_pool"),
        ]
        for key, name in field_map:
            if key not in data:
                continue
            try:
                pct = float(data[key])
            except (TypeError, ValueError):
                continue
            metrics.append(
                QuotaMetric(
                    name=name,
                    window=MetricWindow.BILLING_MONTH,
                    used=pct,
                    limit=100.0,
                    remaining=max(0.0, 100.0 - pct),
                    unit="%",
                    resets_at=str(billing_end) if billing_end else None,
                    source=MetricSource.PRIVATE_DASHBOARD,
                )
            )

        if not metrics:
            for key, val in data.items():
                if "percent" in key.lower() and isinstance(val, (int, float)):
                    metrics.append(
                        QuotaMetric(
                            name=key,
                            window=MetricWindow.BILLING_MONTH,
                            used=float(val),
                            limit=100.0,
                            remaining=max(0.0, 100.0 - float(val)),
                            unit="%",
                            source=MetricSource.PRIVATE_DASHBOARD,
                        )
                    )
        return metrics
