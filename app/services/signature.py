from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models import Psp
from app.security import compute_signature, constant_time_equals


def verify_request_signature(
    psp: Psp, fields: dict[str, Any], timestamp: str | None, signature: str | None
) -> None:
    """Reject the request unless `signature` matches md5(sorted fields + timestamp + salt)."""
    settings = get_settings()
    if not settings.require_signature:
        return
    if not timestamp or not signature:
        raise AppError(ErrorCode.SIGNATURE_MISSING)

    if settings.signature_max_skew_seconds > 0:
        try:
            signed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(
                ErrorCode.TIMESTAMP_INVALID,
                "timestamp must be ISO-8601, e.g. 2026-09-25T09:45:00Z or 2026-09-25T15:15:00+05:30",
            )
        if signed_at.tzinfo is None:
            signed_at = signed_at.replace(tzinfo=timezone.utc)
        skew = abs((datetime.now(timezone.utc) - signed_at).total_seconds())
        if skew > settings.signature_max_skew_seconds:
            raise AppError(ErrorCode.TIMESTAMP_INVALID, "timestamp is too old or too far in the future")

    expected = compute_signature({**fields, "timestamp": timestamp}, psp.signature_salt)
    if not constant_time_equals(expected, signature.lower()):
        raise AppError(ErrorCode.SIGNATURE_INVALID)
