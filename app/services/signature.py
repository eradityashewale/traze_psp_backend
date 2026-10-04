from typing import Any

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models import Psp
from app.security import compute_signature, constant_time_equals


def verify_request_signature(psp: Psp, fields: dict[str, Any], signature: str | None) -> None:
    """Reject the request unless `signature` matches md5(sorted fields + salt)."""
    if not get_settings().require_signature:
        return
    if not signature:
        raise AppError(ErrorCode.SIGNATURE_MISSING)
    expected = compute_signature(fields, psp.signature_salt)
    if not constant_time_equals(expected, signature.lower()):
        raise AppError(ErrorCode.SIGNATURE_INVALID)
