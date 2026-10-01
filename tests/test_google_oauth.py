import time

from app.util.google_oauth import oauth_access_token_expired


def test_expiry_date_milliseconds():
    creds = {"access_token": "x", "expiry_date": int((time.time() + 3600) * 1000)}
    assert oauth_access_token_expired(creds) is False

    creds["expiry_date"] = int((time.time() - 60) * 1000)
    assert oauth_access_token_expired(creds) is True
