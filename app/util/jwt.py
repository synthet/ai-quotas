import base64
import json


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("invalid JWT")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(payload + padding)
    return json.loads(raw)


def user_id_from_cursor_jwt(token: str) -> str:
    """WorkOS user id for Cursor dashboard cookie (suffix of sub after |)."""
    payload = decode_jwt_payload(token)
    sub = payload.get("sub", "")
    if not isinstance(sub, str):
        raise ValueError("JWT missing sub")
    if "|" in sub:
        return sub.split("|", 1)[1]
    return sub


def jwt_exp_unix(token: str) -> int | None:
    payload = decode_jwt_payload(token)
    exp = payload.get("exp")
    return int(exp) if exp is not None else None
