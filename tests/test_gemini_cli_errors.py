from app.models.quota import ProviderStatus
from app.providers.gemini_cli_quota import metrics_from_buckets, quota_error_status


def test_quota_403_no_license_is_unavailable_with_code():
    body = (
        '{"error":{"code":403,"message":"You do not have a valid license of this product. '
        'Please contact your administrator"}}'
    )
    status, msg, code = quota_error_status(403, body)
    assert status == ProviderStatus.UNAVAILABLE
    assert code == "license_not_available"
    assert "entitlement" in msg.lower()
    assert "0" not in msg


def test_quota_401_unauthorized():
    status, _, code = quota_error_status(401, "unauthorized")
    assert status == ProviderStatus.UNAUTHORIZED
    assert code == "oauth_rejected"


def test_buckets_normalize_fraction():
    metrics = metrics_from_buckets(
        [
            {
                "modelId": "gemini-3.5-flash",
                "remainingFraction": 0.91,
                "resetTime": "2026-10-01T12:00:00Z",
            }
        ],
        source=__import__("app.models.quota", fromlist=["MetricSource"]).MetricSource.CLOUDCODE_INTERNAL,
    )
    assert len(metrics) == 1
    m = metrics[0]
    assert m.name == "gemini-3.5-flash"
    assert m.remaining_percent == 91.0
    assert m.used_percent == 9.0
    assert m.limit is None
    assert m.remaining is None
    assert m.resets_at == "2026-10-01T12:00:00Z"
