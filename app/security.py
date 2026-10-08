import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import bcrypt
import jwt

from app.config import get_settings


# ---------- portal user passwords & JWT ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode()[:72], password_hash.encode())


def create_access_token(user_id: int, role: str, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expires_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------- PSP API credentials ----------

def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_psp_credentials() -> dict[str, str]:
    return {
        "api_token": "pspt_" + secrets.token_urlsafe(32),
        "api_secret": "psps_" + secrets.token_urlsafe(32),
        "signature_salt": secrets.token_hex(32),  # 256-bit HMAC key
    }


# No 0/O or 1/I: the code is read out and typed by people
PSP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_psp_code() -> str:
    return "PSP-" + "".join(secrets.choice(PSP_CODE_ALPHABET) for _ in range(8))


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ---------- HMAC-SHA256 signature (Section 10) ----------

def format_signature_value(value: Any) -> str:
    """Canonical string form of a field value.

    Amounts drop trailing zeros so 12500, 12500.0 and 12500.00 all sign as "12500".
    """
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, float):
        return format_signature_value(Decimal(str(value)))
    if value is None:
        return ""
    return str(value)


def compute_signature(fields: dict[str, Any], salt: str) -> str:
    """hex(HMAC-SHA256(key=SALT, msg="k1=v1&k2=v2&...")) with keys sorted alphabetically."""
    raw = "&".join(f"{k}={format_signature_value(fields[k])}" for k in sorted(fields))
    return hmac.new(salt.encode(), raw.encode(), hashlib.sha256).hexdigest()
