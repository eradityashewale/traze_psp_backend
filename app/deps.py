from datetime import datetime, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError, ErrorCode
from app.models import PortalUser, Psp, PspStatus, UserRole
from app.security import constant_time_equals, decode_access_token, sha256_hex
from app.services.keys import verify_signature

DbSession = Annotated[Session, Depends(get_db)]

_portal_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="PortalJWT",
    description="PSP team login. Paste the `access_token` (eyJ...) from POST /api/v1/auth/login. "
    "Used by /auth/me, /auth/change-password, /users, /psps, /portal/..., /audit-logs.",
)
_crm_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="PspApiToken",
    description="CRM calls only. Paste the `api_token` (pspt_...) from POST /api/v1/psps. "
    "Used by /api/v1/deposits and /api/v1/withdrawals; also fill the X-API-Secret field.",
)


# ---------- portal users (JWT) ----------

def get_current_user(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_portal_bearer)],
) -> PortalUser:
    if creds is None:
        raise AppError(ErrorCode.AUTH_MISSING, "Missing bearer token")
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise AppError(ErrorCode.AUTH_INVALID, "Invalid or expired token")
    user = db.get(PortalUser, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AppError(ErrorCode.AUTH_INVALID, "User not found or inactive")
    ensure_psp_login_allowed(user)
    return user


def ensure_psp_login_allowed(user: PortalUser) -> None:
    """A PSP login only works while its PSP exists and is active."""
    if user.role == UserRole.psp and (user.psp is None or user.psp.status != PspStatus.active):
        raise AppError(ErrorCode.PSP_INACTIVE, "Your PSP is inactive; contact the admin")


CurrentUser = Annotated[PortalUser, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> PortalUser:
    if user.role != UserRole.admin:
        raise AppError(ErrorCode.FORBIDDEN, "Admin role required")
    return user


AdminUser = Annotated[PortalUser, Depends(require_admin)]


def require_psp_user(user: CurrentUser) -> PortalUser:
    if user.role != UserRole.psp:
        raise AppError(ErrorCode.FORBIDDEN, "Only a PSP login can approve or reject requests")
    return user


PspUser = Annotated[PortalUser, Depends(require_psp_user)]


# ---------- CRM calls (API Token + API Secret) ----------

def get_calling_psp(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_crm_bearer)],
    x_api_secret: Annotated[str | None, Header(alias="X-API-Secret")] = None,
) -> Psp:
    if creds is None or not x_api_secret:
        raise AppError(
            ErrorCode.AUTH_MISSING, "Authorization: Bearer <API_TOKEN> and X-API-Secret headers are required"
        )
    token_hash, secret_hash = sha256_hex(creds.credentials), sha256_hex(x_api_secret)
    psp = db.scalar(
        select(Psp).where(or_(Psp.api_token_hash == token_hash, Psp.prev_api_token_hash == token_hash))
    )
    if psp is None:
        raise AppError(ErrorCode.AUTH_INVALID, "Invalid API credentials")

    now = datetime.now(timezone.utc)
    if psp.api_token_hash == token_hash:
        if not constant_time_equals(psp.api_secret_hash, secret_hash):
            raise AppError(ErrorCode.AUTH_INVALID, "Invalid API credentials")
        if psp.api_token_expires_at <= now:
            raise AppError(ErrorCode.API_TOKEN_EXPIRED)
    else:
        # Previous credentials, accepted only during the rotation grace period.
        if not psp.prev_api_secret_hash or not constant_time_equals(psp.prev_api_secret_hash, secret_hash):
            raise AppError(ErrorCode.AUTH_INVALID, "Invalid API credentials")
        if psp.prev_valid_until is None or psp.prev_valid_until <= now:
            raise AppError(ErrorCode.API_TOKEN_EXPIRED, "This API token was rotated and its grace period has ended")
    return psp


CallingPsp = Annotated[Psp, Depends(get_calling_psp)]


async def get_verified_psp(request: Request, psp: CallingPsp) -> Psp:
    """CallingPsp plus the RSA body signature, when the PSP has registered a public key."""
    if psp.client_public_key:
        signature = request.headers.get("X-Signature")
        body = getattr(request.state, "raw_body", None)
        if body is None:
            body = await request.body()
        if not signature or not verify_signature(psp.client_public_key, body, signature):
            raise AppError(ErrorCode.RSA_SIGNATURE_INVALID)
    return psp


VerifiedPsp = Annotated[Psp, Depends(get_verified_psp)]
