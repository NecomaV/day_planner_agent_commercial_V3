import hashlib
import hmac
import secrets

from app.settings import settings


API_KEY_PREFIX = "dp_"


def _require_api_key_secret() -> str:
    secret = settings.API_KEY_SECRET
    if not secret:
        raise RuntimeError("API_KEY_SECRET is required to hash API keys")
    return secret


def generate_api_key() -> str:
    token = secrets.token_urlsafe(32)
    return f"{API_KEY_PREFIX}{token}"


def hash_api_key(raw_key: str) -> str:
    secret = _require_api_key_secret()
    return hmac.new(secret.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def api_key_prefix(raw_key: str, length: int = 8) -> str:
    return raw_key[:length]
