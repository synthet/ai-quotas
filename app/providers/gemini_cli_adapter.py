import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.util.google_oauth import oauth_access_token_expired, resolve_oauth_client_id
from app.models.quota import MetricSource, MetricWindow, ProviderSnapshot, ProviderStatus, QuotaMetric
from app.providers.base import ProviderAdapter

LOAD_CODE_ASSIST = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
RETRIEVE_QUOTA = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
TOKEN_URL = "https://oauth2.googleapis.com/token"

_NO_LICENSE_MARKERS = (
    "valid license",
    "do not have a valid license",
    "enterprise",
    "code assist",
)


def _quota_error_status(status_code: int, body: str) -> tuple[ProviderStatus, str]:
    lower = body.lower()
    if status_code == 401:
        return (
            ProviderStatus.SKIPPED,
            (
                "Cloud Code session rejected. Individual Gemini CLI sign-in is deprecated; "
                "use Antigravity (agy) for subscription quota in this dashboard."
            ),
        )
    if status_code == 403 and any(m in lower for m in _NO_LICENSE_MARKERS):
        return (
            ProviderStatus.UNAVAILABLE,
            (
                "No Gemini Code Assist / enterprise license for this Google account. "
                "This collector calls Cloud Code quota APIs — a GOOGLE_API_KEY or "
                "GEMINI_API_KEY does not apply. For personal subscription quota use "
                "Antigravity (agy /usage). Disable this card: INCLUDE_GEMINI_CLI_COLLECTOR=false "
                "and QUOTA_SCOPE=cli."
            ),
        )
    if status_code == 403:
        return (
            ProviderStatus.UNAVAILABLE,
            (
                "Cloud Code quota API denied access (HTTP 403). Not an AI Studio API-key issue. "
                "Use Antigravity for consumer quota, or set INCLUDE_GEMINI_CLI_COLLECTOR=false."
            ),
        )
    return (
        ProviderStatus.ERROR,
        f"Quota API HTTP {status_code}: {body[:240]}",
    )


def _window_from_label(label: str) -> MetricWindow:
    lower = label.lower()
    if "week" in lower:
        return MetricWindow.WEEKLY
    if "5" in lower and "hour" in lower:
        return MetricWindow.FIVE_H
    if "day" in lower or "daily" in lower:
        return MetricWindow.DAILY
    if "hour" in lower:
        return MetricWindow.FIVE_H
    return MetricWindow.UNKNOWN


class GeminiCliAdapter(ProviderAdapter):
    provider_id = "gemini_cli"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        creds_path = settings.gemini_dir / "oauth_creds.json"
        if not creds_path.exists():
            return ProviderSnapshot(
                provider=self.provider_id,
                status=ProviderStatus.SKIPPED,
                message=(
                    f"No OAuth creds at {creds_path}. Legacy Gemini CLI quota is deprecated; "
                    "use Antigravity (agy) instead."
                ),
                fetched_at=now,
            )

        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return ProviderSnapshot(
                provider=self.provider_id,
                status=ProviderStatus.ERROR,
                message=f"Failed to read oauth_creds.json: {e}",
                fetched_at=now,
            )

        try:
            access_token = await self._ensure_access_token(client, creds, creds_path)
        except Exception as e:
            return ProviderSnapshot(
                provider=self.provider_id,
                status=ProviderStatus.ERROR,
                message=f"OAuth refresh failed: {e}",
                fetched_at=now,
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        assist_body = self._code_assist_body(settings)
        try:
            await client.post(LOAD_CODE_ASSIST, headers=headers, json=assist_body)
            quota_resp = await client.post(RETRIEVE_QUOTA, headers=headers, json={})
        except httpx.HTTPError as e:
            return ProviderSnapshot(
                provider=self.provider_id,
                status=ProviderStatus.ERROR,
                message=f"Cloud Code quota request failed: {e}",
                fetched_at=now,
            )

        if quota_resp.status_code >= 400:
            status, message = _quota_error_status(
                quota_resp.status_code, quota_resp.text or ""
            )
            return ProviderSnapshot(
                provider=self.provider_id,
                status=status,
                message=message,
                fetched_at=now,
            )

        try:
            data = quota_resp.json()
        except Exception:
            return ProviderSnapshot(
                provider=self.provider_id,
                status=ProviderStatus.DEGRADED,
                message="Unexpected quota response format",
                fetched_at=now,
            )

        metrics = self._parse_quota_response(data)
        status = ProviderStatus.OK if metrics else ProviderStatus.DEGRADED
        return ProviderSnapshot(
            provider=self.provider_id,
            status=status,
            message="Quota from Cloud Code (unofficial)",
            fetched_at=now,
            metrics=metrics,
        )

    def _code_assist_body(self, settings: Settings) -> dict:
        project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
            or ""
        )
        body: dict = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        if project:
            body["cloudaicompanionProject"] = (
                project if project.startswith("projects/") else f"projects/{project}"
            )
        return body

    async def _ensure_access_token(
        self,
        client: httpx.AsyncClient,
        creds: dict,
        creds_path: Path,
    ) -> str:
        access = creds.get("access_token") or creds.get("token")
        if access and not oauth_access_token_expired(creds):
            return str(access)

        refresh = creds.get("refresh_token")
        client_id = resolve_oauth_client_id(creds)
        client_secret = creds.get("client_secret", "")
        if not refresh:
            if access:
                return str(access)
            raise ValueError("missing refresh_token in oauth_creds.json")

        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code >= 400:
            if access:
                return str(access)
            raise ValueError(resp.text[:200])
        token_data = resp.json()
        new_access = token_data.get("access_token")
        if not new_access:
            raise ValueError("no access_token in refresh response")
        creds["access_token"] = new_access
        expires_in = token_data.get("expires_in", 3600)
        creds["expiry_date"] = int((time.time() + int(expires_in)) * 1000)
        creds["expiry"] = time.time() + int(expires_in)
        try:
            creds_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
        except OSError:
            pass
        return str(new_access)

    def _parse_quota_response(self, data: dict) -> list[QuotaMetric]:
        metrics: list[QuotaMetric] = []
        buckets = data.get("quotaBuckets") or data.get("quotas") or data.get("userQuota") or []
        if isinstance(data, dict) and not buckets:
            for key, val in data.items():
                if isinstance(val, list):
                    buckets = val
                    break

        if not isinstance(buckets, list):
            return metrics

        for i, bucket in enumerate(buckets):
            if not isinstance(bucket, dict):
                continue
            label = (
                bucket.get("displayName")
                or bucket.get("modelId")
                or bucket.get("name")
                or bucket.get("tier")
                or f"quota_{i}"
            )
            frac = bucket.get("remainingFraction")
            remaining_amt = bucket.get("remainingAmount")
            remaining: float | None = None
            limit = 100.0
            if frac is not None:
                try:
                    remaining = float(frac) * 100.0
                except (TypeError, ValueError):
                    pass
            elif remaining_amt is not None:
                try:
                    remaining = float(remaining_amt)
                    limit = 100.0
                except (TypeError, ValueError):
                    pass

            if remaining is None:
                continue

            used = limit - remaining if limit is not None else None
            metrics.append(
                QuotaMetric(
                    name=str(label),
                    window=_window_from_label(str(label)),
                    used=used,
                    limit=limit,
                    remaining=remaining,
                    unit="%",
                    source=MetricSource.CLOUDCODE_INTERNAL,
                )
            )
        return metrics
