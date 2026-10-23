import json
import os
import re
from datetime import datetime, timedelta, timezone
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


def _parse_transcript_error_line(
    line: str,
    file_mtime: datetime,
    now: datetime,
) -> tuple[datetime, int, str] | None:
    """Parse 'Weekly usage limit reached. It resets in X days/hours' from a transcript line.

    Returns (resets_at, val, unit) if active (resets_at > now), else None.
    """
    if "weekly usage limit reached" not in line.lower():
        return None
    m = re.search(
        r"weekly usage limit reached\.\s*it resets in\s*(\d+)\s*(day|hour|minute)s?",
        line,
        re.IGNORECASE,
    )
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2).lower()
    delta = (
        timedelta(days=val)
        if unit == "day"
        else (timedelta(hours=val) if unit == "hour" else timedelta(minutes=val))
    )
    resets_at = file_mtime + delta
    if resets_at > now:
        return resets_at, val, unit
    return None


def _detect_cli_weekly_limit(
    now: datetime,
    cursor_dir: Path | None = None,
) -> tuple[datetime, int, str] | None:
    """Scan ~/.cursor/projects for recent agent transcripts containing active weekly usage limit errors."""
    base = cursor_dir or (Path.home() / ".cursor")
    proj_dir = base / "projects"
    if not proj_dir.is_dir():
        return None

    cutoff = now - timedelta(days=7)
    latest_hit: tuple[datetime, int, str] | None = None

    try:
        for p_dir in proj_dir.iterdir():
            trans_dir = p_dir / "agent-transcripts"
            if not trans_dir.is_dir():
                continue
            for sub in trans_dir.iterdir():
                if not sub.is_dir():
                    continue
                for f in sub.glob("*.jsonl"):
                    try:
                        st = f.stat()
                        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                        if mtime < cutoff:
                            continue
                        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                            lines = fp.readlines()
                        for line in reversed(lines[-10:]):
                            hit = _parse_transcript_error_line(line, mtime, now)
                            if hit:
                                if latest_hit is None or hit[0] > latest_hit[0]:
                                    latest_hit = hit
                                break
                    except OSError:
                        continue
    except OSError:
        pass

    return latest_hit


class CursorAdapter(ProviderAdapter):
    provider_id = "cursor.cursor"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.now(timezone.utc)
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
        weekly_hit = _detect_cli_weekly_limit(now)

        if weekly_hit:
            resets_at, val, unit = weekly_hit
            unit_str = f"{val} {unit}{'s' if val != 1 else ''}"
            resets_at_str = resets_at.isoformat()
            metrics.insert(
                0,
                QuotaMetric(
                    name="weekly_usage_limit",
                    surface=QuotaSurface.CURSOR,
                    window=MetricWindow.WEEKLY,
                    window_label="Weekly usage limit (CLI)",
                    used_percent=100.0,
                    remaining_percent=0.0,
                    unit="percent",
                    resets_at=resets_at_str,
                    source=MetricSource.INFERENCE_ERROR,
                ),
            )
            status = ProviderStatus.BLOCKED
            message = (
                f"Weekly usage limit reached (resets in {unit_str}) · Monthly billing usage shown below"
            )
        else:
            status = ProviderStatus.PARTIAL if metrics else ProviderStatus.DEGRADED
            message = (
                f"Cursor private dashboard ({source}) — monthly billing only (weekly quota unexposed)"
            )

        return ProviderSnapshot(
            provider="cursor",
            surface=QuotaSurface.CURSOR,
            status=status,
            message=message,
            fetched_at=now,
            metrics=metrics,
        )

    def _resolve_access_token(self, settings: Settings) -> tuple[str | None, str]:
        if settings.cursor_auth_path:
            path = Path(settings.cursor_auth_path)
            if path.is_file():
                return self._read_token_file(path), "Cursor IDE session"

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
                        window_label="Monthly billing",
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
                        window_label="Monthly billing",
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
                    window_label="Monthly billing",
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
                            window_label="Monthly billing",
                            used=float(val),
                            limit=100.0,
                            remaining=max(0.0, 100.0 - float(val)),
                            unit="%",
                            source=MetricSource.PRIVATE_DASHBOARD,
                        )
                    )
        return metrics
