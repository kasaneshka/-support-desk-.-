import hashlib
import hmac
import os

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

ITERATIONS = 100_000
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="session")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, _, digest_hex = stored.partition("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def make_token(user_id: int) -> str:
    return _serializer.dumps(user_id)


def read_token(token: str) -> int | None:
    try:
        return _serializer.loads(token, max_age=settings.session_max_age)
    except BadSignature:
        return None
