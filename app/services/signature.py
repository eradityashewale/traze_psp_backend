from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.errors import AppError, ErrorCode
from app.models import Psp, RequestNonce
from app.schemas import SignedRequest
from app.security import compute_signature, constant_time_equals


def verify_request_signature(
    db: Session, request: Request, psp: Psp, fields: dict[str, Any], body: SignedRequest
) -> None:
    """Reject the request unless it is fresh, correctly signed and not seen before.

    The HMAC-SHA256 signature covers `fields` plus the HTTP method, the route path,
    `timestamp` and `nonce`. The nonce is saved in the caller's DB transaction, so it
    is only used up when the request itself is committed.
    """
    settings = get_settings()
    if not settings.require_signature:
        return
    if not body.signature or body.timestamp is None or not body.nonce:
        raise AppError(ErrorCode.SIGNATURE_MISSING)

    window = settings.signature_window_seconds
    now = datetime.now(timezone.utc)
    if abs(now.timestamp() - body.timestamp) > window:
        raise AppError(ErrorCode.SIGNATURE_EXPIRED, f"timestamp must be within {window} seconds of the server time")

    signed = {
        **fields,
        "method": request.method,
        "path": request.scope["route"].path,  # e.g. /api/v1/deposits, without any proxy prefix
        "timestamp": body.timestamp,
        "nonce": body.nonce,
    }
    expected = compute_signature(signed, psp.signature_salt)
    if not constant_time_equals(expected, body.signature.lower()):
        raise AppError(ErrorCode.SIGNATURE_INVALID)

    # Kept until the timestamp can no longer pass the window check above.
    db.add(RequestNonce(psp_id=psp.id, nonce=body.nonce, expires_at=now + timedelta(seconds=2 * window)))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise AppError(ErrorCode.SIGNATURE_REPLAYED)


def purge_expired_nonces() -> None:
    with SessionLocal() as db:
        db.execute(delete(RequestNonce).where(RequestNonce.expires_at < datetime.now(timezone.utc)))
        db.commit()
