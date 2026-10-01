import time

from app.util.jwt import decode_jwt_payload

# Gemini CLI / Code Assist installed-app OAuth client (public; secret not required for refresh).
GEMINI_CLI_CLIENT_ID = (
    "681255809395-oo8ft2oprdnrp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)


def oauth_access_token_expired(creds: dict, skew_sec: int = 120) -> bool:
    now = time.time()
    expiry = creds.get("expiry") or creds.get("expires_at")
    if expiry is not None:
        try:
            exp = float(expiry)
            if exp > 1e12:
                exp = exp / 1000.0
            return now >= exp - skew_sec
        except (TypeError, ValueError):
            pass
    expiry_date = creds.get("expiry_date")
    if expiry_date is not None:
        try:
            exp_ms = float(expiry_date)
            return now >= (exp_ms / 1000.0) - skew_sec
        except (TypeError, ValueError):
            pass
    return False


def resolve_oauth_client_id(creds: dict) -> str:
    if creds.get("client_id"):
        return str(creds["client_id"])
    id_token = creds.get("id_token")
    if id_token:
        try:
            payload = decode_jwt_payload(str(id_token))
            azp = payload.get("azp") or payload.get("aud")
            if azp:
                return str(azp)
        except ValueError:
            pass
    return GEMINI_CLI_CLIENT_ID
