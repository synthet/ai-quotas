import asyncio
import json
import re
from datetime import datetime, timezone
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
from app.util.local_cli import resolve_cli, subprocess_kwargs

_SESSION_RE = re.compile(
    r"Current session:\s*(\d+(?:\.\d+)?)\s*%\s*used",
    re.IGNORECASE,
)
_WEEK_RE = re.compile(
    r"Current week\s*\(([^)]+)\):\s*(\d+(?:\.\d+)?)\s*%\s*used(?:\s*·\s*resets\s+(.+?))?(?:\n|$)",
    re.IGNORECASE,
)

BRIDGE_FILES = (
    ".cc-statusbar-quota.json",
    "usage-exact.json",
    "rate-limits.json",
)


def _metric_from_used_percent(
    name: str,
    used_pct: float,
    window: MetricWindow,
    resets_at: str | None,
    source: MetricSource,
) -> QuotaMetric:
    return QuotaMetric(
        name=name,
        surface=QuotaSurface.CLAUDE_SUBSCRIPTION,
        window=window,
        used_percent=used_pct,
        remaining_percent=max(0.0, 100.0 - used_pct),
        unit="percent",
        resets_at=resets_at,
        source=source,
    )


def _parse_usage_text(text: str) -> list[QuotaMetric]:
    metrics: list[QuotaMetric] = []
    session = _SESSION_RE.search(text)
    if session:
        metrics.append(
            _metric_from_used_percent(
                "session",
                float(session.group(1)),
                MetricWindow.FIVE_H,
                None,
                MetricSource.OFFICIAL_LOCAL_CLI,
            )
        )
    for match in _WEEK_RE.finditer(text):
        label = match.group(1).strip().lower().replace(" ", "_")
        used = float(match.group(2))
        reset_hint = (match.group(3) or "").strip() or None
        metrics.append(
            _metric_from_used_percent(
                f"week_{label}",
                used,
                MetricWindow.WEEKLY,
                reset_hint,
                MetricSource.OFFICIAL_LOCAL_CLI,
            )
        )
    return metrics


def _parse_bridge_file(path: Path) -> list[QuotaMetric]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    metrics: list[QuotaMetric] = []
    limits = data.get("rate_limits") or data
    if isinstance(limits, dict):
        mapping = (
            ("five_hour", "five_hour", MetricWindow.FIVE_H),
            ("seven_day", "seven_day", MetricWindow.WEEKLY),
        )
        for key, name, window in mapping:
            block = limits.get(key)
            if not isinstance(block, dict):
                continue
            used = block.get("used_percentage") or block.get("utilization")
            if used is None:
                continue
            resets = block.get("resets_at")
            resets_at = None
            if isinstance(resets, (int, float)):
                resets_at = datetime.fromtimestamp(int(resets), tz=timezone.utc).isoformat()
            elif isinstance(resets, str):
                resets_at = resets
            metrics.append(
                _metric_from_used_percent(
                    name,
                    float(used),
                    window,
                    resets_at,
                    MetricSource.OFFICIAL_LOCAL_CLI,
                )
            )
    return metrics


async def _run_claude_usage(claude_exe: str, timeout_sec: float) -> tuple[str | None, str]:
    proc = await asyncio.create_subprocess_exec(
        claude_exe,
        "-p",
        "/usage",
        "--output-format",
        "json",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        return None, "timed out"
    err = (stderr or b"").decode("utf-8", errors="replace").strip()[:300]
    if proc.returncode != 0 or not stdout:
        return None, err or f"exit {proc.returncode}"
    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None, err or "invalid JSON"
    if not isinstance(payload, dict):
        return None, err or "unexpected payload"
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        return None, err or "empty result"
    return result, ""


class ClaudeSubscriptionAdapter(ProviderAdapter):
    provider_id = "anthropic.claude_subscription"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        claude_dir = Path.home() / ".claude"

        for name in BRIDGE_FILES:
            metrics = _parse_bridge_file(claude_dir / name)
            if metrics:
                return ProviderSnapshot(
                    provider="anthropic",
                    surface=QuotaSurface.CLAUDE_SUBSCRIPTION,
                    status=ProviderStatus.OK,
                    message=f"Claude subscription (cached {name})",
                    fetched_at=now,
                    metrics=metrics,
                )

        claude_exe = settings.claude_command or resolve_cli("claude")
        if not claude_exe:
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.CLAUDE_SUBSCRIPTION,
                status=ProviderStatus.SKIPPED,
                message="Install Claude Code CLI (`claude`) and sign in",
                fetched_at=now,
            )

        text, err = await _run_claude_usage(claude_exe, settings.claude_usage_timeout_sec)
        if not text:
            hint = f" ({err})" if err else ""
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.CLAUDE_SUBSCRIPTION,
                status=ProviderStatus.ERROR,
                message=f"`claude -p /usage` failed or timed out{hint}",
                fetched_at=now,
            )

        metrics = _parse_usage_text(text)
        return ProviderSnapshot(
            provider="anthropic",
            surface=QuotaSurface.CLAUDE_SUBSCRIPTION,
            status=ProviderStatus.OK if metrics else ProviderStatus.DEGRADED,
            message="Claude Code subscription (`/usage`, no API tokens)",
            fetched_at=now,
            metrics=metrics,
        )
