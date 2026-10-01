import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import Settings
from app.models.quota import (
    MetricSource,
    ProviderSnapshot,
    ProviderStatus,
    QuotaSurface,
)
from app.providers.base import ProviderAdapter
from app.providers.gemini_cli_quota import (
    companion_project_from_load_response,
    load_code_assist_body,
    parse_quota_payload,
    parse_stats_cli_json,
    quota_error_status,
    retrieve_quota_bodies,
)
from app.util.google_oauth import oauth_access_token_expired, resolve_oauth_client_id

LOAD_CODE_ASSIST = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
RETRIEVE_QUOTA = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
TOKEN_URL = "https://oauth2.googleapis.com/token"

COLLECTOR_STATS = "gemini_cli_stats"
COLLECTOR_CODE_ASSIST = "gemini_cli_code_assist"


class GeminiCliAdapter(ProviderAdapter):
    provider_id = "gemini_cli"

    async def fetch(self, client: httpx.AsyncClient, settings: Settings) -> ProviderSnapshot:
        now = datetime.utcnow()
        gemini_exe = settings.gemini_command or shutil.which("gemini")

        stats_metrics = await self._fetch_stats_cli(gemini_exe, settings.gemini_cli_timeout_sec)
        if stats_metrics:
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.OK,
                message="Gemini CLI `/stats model`",
                collector_source=COLLECTOR_STATS,
                fetched_at=now,
                metrics=stats_metrics,
            )

        creds_path = settings.gemini_dir / "oauth_creds.json"
        if not creds_path.exists():
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.SKIPPED,
                message=(
                    f"No OAuth creds at {creds_path}. Run Gemini CLI auth or use Antigravity (agy)."
                ),
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
            )

        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.ERROR,
                message=f"Failed to read oauth_creds.json: {e}",
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
            )

        try:
            access_token = await self._ensure_access_token(client, creds, creds_path)
        except Exception as e:
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.ERROR,
                message=f"OAuth refresh failed: {e}",
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        env_project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
            or ""
        )

        quota_resp, last_error = await self._retrieve_quota_with_fallback(
            client,
            headers,
            settings.gemini_cli_use_env_gcp_project,
            env_project,
        )

        if quota_resp is None:
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.ERROR,
                message=last_error or "retrieveUserQuota failed",
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
            )

        if quota_resp.status_code >= 400:
            status, message, error_code = quota_error_status(
                quota_resp.status_code, quota_resp.text or ""
            )
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=status,
                message=message,
                error_code=error_code,
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
                metrics=[],
            )

        try:
            data = quota_resp.json()
        except Exception:
            return ProviderSnapshot(
                provider=self.provider_id,
                surface=QuotaSurface.GEMINI_CLI,
                status=ProviderStatus.DEGRADED,
                message="Unexpected quota response format",
                collector_source=COLLECTOR_CODE_ASSIST,
                fetched_at=now,
            )

        metrics = parse_quota_payload(data, MetricSource.CLOUDCODE_INTERNAL)
        status = ProviderStatus.OK if metrics else ProviderStatus.DEGRADED
        return ProviderSnapshot(
            provider=self.provider_id,
            surface=QuotaSurface.GEMINI_CLI,
            status=status,
            message="Gemini CLI Code Assist quota (retrieveUserQuota)",
            collector_source=COLLECTOR_CODE_ASSIST,
            fetched_at=now,
            metrics=metrics,
        )

    async def _retrieve_quota_with_fallback(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        use_env_project: bool,
        env_project: str,
    ) -> tuple[httpx.Response | None, str | None]:
        try:
            last: httpx.Response | None = None
            for body in retrieve_quota_bodies(None):
                resp = await client.post(RETRIEVE_QUOTA, headers=headers, json=body)
                last = resp
                if resp.status_code < 400:
                    return resp, None

            assist_body = load_code_assist_body(use_env_project, env_project)
            assist_resp = await client.post(LOAD_CODE_ASSIST, headers=headers, json=assist_body)
            companion: str | None = None
            if assist_resp.status_code < 400:
                try:
                    companion = companion_project_from_load_response(assist_resp.json())
                except Exception:
                    companion = None

            if companion:
                for body in retrieve_quota_bodies(companion):
                    resp = await client.post(RETRIEVE_QUOTA, headers=headers, json=body)
                    last = resp
                    if resp.status_code < 400:
                        return resp, None

            if last is not None:
                return last, None
            return None, "retrieveUserQuota returned no response"
        except httpx.HTTPError as e:
            return None, f"Cloud Code quota request failed: {e}"

    async def _fetch_stats_cli(self, gemini_exe: str | None, timeout_sec: float) -> list:
        if not gemini_exe:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                gemini_exe,
                "-p",
                "/stats model",
                "-o",
                "json",
                "-y",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except (asyncio.TimeoutError, OSError):
            return []
        if proc.returncode != 0 and not stdout:
            return []
        return parse_stats_cli_json(stdout.decode("utf-8", errors="replace"))

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
