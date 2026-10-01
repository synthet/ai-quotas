import base64
import json

from app.models.quota import DashboardSnapshot, ProviderSnapshot, ProviderStatus
from app.providers.parsers import (
    parse_anthropic_ratelimit_headers,
    parse_openai_error_body,
    parse_openai_ratelimit_headers,
)
from app.util.jwt import user_id_from_cursor_jwt


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_parse_openai_headers():
    headers = {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "99",
        "x-ratelimit-reset-requests": "1s",
        "x-ratelimit-limit-tokens": "1000",
        "x-ratelimit-remaining-tokens": "900",
    }
    metrics = parse_openai_ratelimit_headers(headers)
    assert len(metrics) >= 2
    req = next(m for m in metrics if m.name == "requests")
    assert req.limit == 100
    assert req.remaining == 99
    assert req.used == 1


def test_parse_anthropic_headers():
    headers = {
        "anthropic-ratelimit-requests-limit": "1000",
        "anthropic-ratelimit-requests-remaining": "999",
        "anthropic-ratelimit-requests-reset": "2026-01-01T00:01:00Z",
    }
    metrics = parse_anthropic_ratelimit_headers(headers)
    assert len(metrics) == 1
    assert metrics[0].remaining == 999


def test_parse_openai_error_body():
    code, msg = parse_openai_error_body(
        {"error": {"code": "credit_balance_exhausted", "message": "no credits"}}
    )
    assert code == "credit_balance_exhausted"
    assert "credits" in msg


def test_cursor_jwt_sub():
    payload = _b64url({"sub": "google-oauth2|user_01ABC"})
    token = f"hdr.{payload}.sig"
    assert user_id_from_cursor_jwt(token) == "user_01ABC"


def test_dashboard_snapshot_json_shape():
    snap = DashboardSnapshot(
        providers=[
            ProviderSnapshot(
                provider="openai",
                status=ProviderStatus.OK,
                message="ok",
            )
        ]
    )
    data = snap.model_dump(mode="json")
    assert "providers" in data
    assert data["providers"][0]["status"] == "ok"
