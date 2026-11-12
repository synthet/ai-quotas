"""Claude API credit check through the signed-in `ant` CLI.

A one-token Messages request is the signal: success means the signed-in
organization accepted the call, and a credit-balance error means it did not.
The dollar balance is not on this API; that figure stays on the console.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

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
from app.util.probe_models import ANTHROPIC_PROBE_FALLBACKS

_NO_CREDIT = re.compile(
    r"credit balance is too low|credit_balance_too_low|billing_error|insufficient credits|purchase credits",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"authentication_error|x-api-key header is required|not logged in|please run .?ant auth",
    re.IGNORECASE,
)
_SECRET = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
_PROBE_TEXT = "Reply with the letter p."


def classify_credit_probe(exit_code: int, stdout: str, stderr: str) -> str:
    blob = f"{stdout}\n{stderr}"
    if _NO_CREDIT.search(blob):
        return "unavailable"
    if _AUTH.search(blob):
        return "unauthorized"
    if exit_code == 0 and re.search(r'"type"\s*:\s*"message"', stdout):
        return "available"
    if re.search(r"not_found_error|model_not_found|model:", blob, re.IGNORECASE):
        return "model_missing"
    return "error"


def public_probe_error(text: str) -> str:
    cleaned = _SECRET.sub("sk-ant-…", text or "")
    line = cleaned.strip().splitlines()[0] if cleaned.strip() else "Credit probe failed"
    return line[:200]


def _probe_models(settings: Settings) -> list[str]:
    models: list[str] = []
    for model in (settings.anthropic_probe_model, *ANTHROPIC_PROBE_FALLBACKS, "claude-haiku-5-5"):
        if model and model not in models:
            models.append(model)
    return models[:3]


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    # A set key replaces the signed-in profile, which is the balance this card reads.
    env.pop("ANTHROPIC_API_KEY", None)
    return env


async def run_credit_probe(exe: str, model: str, timeout_sec: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        exe,
        "messages",
        "create",
        "--format",
        "json",
        "--model",
        model,
        "--max-tokens",
        "1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
        **subprocess_kwargs(),
    )
    body = json.dumps({"messages": [{"role": "user", "content": _PROBE_TEXT}]}).encode()
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(body), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "ant credit probe timed out"
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class AntCreditAdapter(ProviderAdapter):
    provider_id = "anthropic.anthropic_api_credits"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        from datetime import datetime

        now = datetime.utcnow()
        if not settings.probe_enabled:
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.ANTHROPIC_API_CREDITS,
                status=ProviderStatus.SKIPPED,
                message="Probes disabled (PROBE_ENABLED=false)",
                fetched_at=now,
            )
        exe = settings.ant_command or resolve_cli("ant")
        if not exe:
            return ProviderSnapshot(
                provider="anthropic",
                surface=QuotaSurface.ANTHROPIC_API_CREDITS,
                status=ProviderStatus.SKIPPED,
                message="Install the Claude Platform CLI (`ant`) and run `ant auth login`",
                fetched_at=now,
            )

        last = "error"
        last_text = "Credit probe failed"
        used_model = ""
        for model in _probe_models(settings):
            used_model = model
            code, stdout, stderr = await run_credit_probe(exe, model, settings.ant_credit_timeout_sec)
            last = classify_credit_probe(code, stdout, stderr)
            last_text = public_probe_error(stderr or stdout)
            if last == "model_missing":
                continue
            break

        if last == "available":
            return self._snapshot(
                now,
                ProviderStatus.OK,
                f"Signed-in Claude API (`ant`). Credits accepted a one-token probe ({used_model}).",
                remaining_percent=100.0,
            )
        if last == "unavailable":
            return self._snapshot(
                now,
                ProviderStatus.BLOCKED,
                "Claude API reported no credits. The console balance is the dollar figure.",
                remaining_percent=0.0,
            )
        if last == "unauthorized":
            return self._snapshot(
                now,
                ProviderStatus.UNAUTHORIZED,
                "ant is not signed in. Run `ant auth login` (leave ANTHROPIC_API_KEY unset).",
                remaining_percent=None,
            )
        return self._snapshot(now, ProviderStatus.ERROR, last_text, remaining_percent=None)

    def _snapshot(self, now, status, message, remaining_percent):
        metrics = []
        if remaining_percent is not None:
            metrics.append(
                QuotaMetric(
                    name="api_credits",
                    surface=QuotaSurface.ANTHROPIC_API_CREDITS,
                    window=MetricWindow.UNKNOWN,
                    window_label="API credits",
                    used_percent=0.0 if remaining_percent else 100.0,
                    remaining_percent=remaining_percent,
                    unit="percent",
                    source=MetricSource.INFERENCE_ERROR,
                )
            )
        return ProviderSnapshot(
            provider="anthropic",
            surface=QuotaSurface.ANTHROPIC_API_CREDITS,
            status=status,
            message=message,
            fetched_at=now,
            metrics=metrics,
            collector_source="ant messages create",
        )
