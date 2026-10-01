from app.providers.gemini_cli_adapter import _quota_error_status
from app.models.quota import ProviderStatus


def test_quota_403_no_license_is_unavailable_not_api_key_issue():
    body = (
        '{"error":{"code":403,"message":"You do not have a valid license of this product. '
        'Please contact your administrator"}}'
    )
    status, msg = _quota_error_status(403, body)
    assert status == ProviderStatus.UNAVAILABLE
    assert "Antigravity" in msg
    assert "API" in msg


def test_quota_401_skipped():
    status, _ = _quota_error_status(401, "unauthorized")
    assert status == ProviderStatus.SKIPPED
