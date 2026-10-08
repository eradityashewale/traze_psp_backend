"""PSP Management module (guide Section 3). Admin only."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, status
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import AdminUser, DbSession
from app.errors import AppError, ErrorCode
from app.models import OPEN_STATUSES, Deposit, PortalUser, Psp, UserRole, Withdrawal
from app.schemas import (
    PspCreate,
    PspCreated,
    PspCredentials,
    PspList,
    PspOut,
    PspUpdate,
    RevokeRequest,
    RotateRequest,
    UserOut,
)
from app.security import generate_psp_code, generate_psp_credentials, sha256_hex
from app.services.audit import audit
from app.services.users import new_portal_user

router = APIRouter(prefix="/api/v1/psps", tags=["PSP Management"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_psp(db: Session, psp_code: str) -> Psp:
    psp = db.scalar(select(Psp).where(Psp.psp_code == psp_code))
    if psp is None:
        raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {psp_code} not found")
    return psp


def _issue_credentials(psp: Psp, *, rotate_salt: bool = True) -> dict:
    creds = generate_psp_credentials()
    psp.api_token_hash = sha256_hex(creds["api_token"])
    psp.api_secret_hash = sha256_hex(creds["api_secret"])
    psp.api_token_expires_at = _now() + timedelta(days=get_settings().api_token_validity_days)
    if rotate_salt:
        psp.signature_salt = creds["signature_salt"]
    return creds


def _new_psp_code(db: Session) -> str:
    for _ in range(10):
        code = generate_psp_code()
        if not db.scalar(select(exists().where(Psp.psp_code == code))):
            return code
    raise AppError(ErrorCode.PSP_ALREADY_EXISTS, "Could not generate a unique PSP code, try again")


@router.get("", response_model=PspList)
def list_psps(db: DbSession, _: AdminUser):
    return PspList(psps=db.scalars(select(Psp).order_by(Psp.created_at)).all())


@router.post("", response_model=PspCreated, status_code=status.HTTP_201_CREATED)
def create_psp(body: PspCreate, db: DbSession, admin: AdminUser, request: Request):
    """Add a PSP together with its portal login (login_email / login_password).

    The PSP logs in with that login to approve or reject its own deposits and withdrawals.
    `psp_code` is generated here and returned in the response; it is not accepted in the body.
    """
    psp = Psp(**body.model_dump(exclude={"login_email", "login_password"}), psp_code=_new_psp_code(db))
    creds = _issue_credentials(psp)
    db.add(psp)
    login = new_portal_user(
        db, email=body.login_email, full_name=body.psp_name, password=body.login_password,
        role=UserRole.psp, psp=psp,
    )
    db.flush()
    audit(db, "psp.created", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"login_email": login.email}, request=request)
    db.refresh(psp)
    db.refresh(login)
    return PspCreated(
        psp=PspOut.model_validate(psp),
        credentials=PspCredentials(
            api_token=creds["api_token"],
            api_secret=creds["api_secret"],
            signature_salt=psp.signature_salt,
            api_token_expires_at=psp.api_token_expires_at,
        ),
        portal_login=UserOut.model_validate(login),
    )


@router.get("/{psp_code}", response_model=PspOut)
def get_psp(psp_code: str, db: DbSession, _: AdminUser):
    return _get_psp(db, psp_code)


@router.put("/{psp_code}", response_model=PspOut)
def update_psp(psp_code: str, body: PspUpdate, db: DbSession, admin: AdminUser, request: Request):
    psp = _get_psp(db, psp_code)
    changes = body.model_dump(exclude_unset=True)
    for field in ("psp_name", "account_number", "status"):
        if field in changes and changes[field] is None:
            raise AppError(ErrorCode.PSP_CONFIG_INVALID, f"{field} cannot be null")

    for field, value in changes.items():
        setattr(psp, field, value)
    audit(db, "psp.updated", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()}, request=request)
    db.refresh(psp)
    return psp


@router.post("/{psp_code}/rotate-credentials", response_model=PspCredentials)
def rotate_credentials(
    psp_code: str, db: DbSession, admin: AdminUser, request: Request, body: RotateRequest | None = None
):
    """Issue a new API token and secret (quarterly rotation).

    The old pair keeps working for `grace_hours` so the CRM can switch without downtime.
    The signature salt only changes when `rotate_salt` is true.
    """
    body = body or RotateRequest()
    psp = _get_psp(db, psp_code)
    grace = body.grace_hours if body.grace_hours is not None else get_settings().rotation_grace_hours
    if psp.credentials_revoked_at is not None:
        grace = 0  # a revoked token must not come back as the "previous" one
        psp.credentials_revoked_at = None

    psp.prev_api_token_hash = psp.api_token_hash if grace > 0 else None
    psp.prev_api_secret_hash = psp.api_secret_hash if grace > 0 else None
    psp.prev_valid_until = _now() + timedelta(hours=grace) if grace > 0 else None
    creds = _issue_credentials(psp, rotate_salt=body.rotate_salt)
    psp.credentials_rotated_at = _now()
    audit(db, "psp.credentials_rotated", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"grace_hours": grace, "rotate_salt": body.rotate_salt}, request=request)
    return PspCredentials(
        api_token=creds["api_token"],
        api_secret=creds["api_secret"],
        signature_salt=psp.signature_salt,
        api_token_expires_at=psp.api_token_expires_at,
        previous_token_valid_until=psp.prev_valid_until,
    )


def _revoke_psp_sessions(db: Session, psp: Psp) -> int:
    logins = db.scalars(select(PortalUser).where(PortalUser.psp_id == psp.id)).all()
    for login in logins:
        login.revoke_sessions()
    return len(logins)


@router.post("/{psp_code}/revoke-credentials", response_model=PspOut)
def revoke_credentials(
    psp_code: str, db: DbSession, admin: AdminUser, request: Request, body: RevokeRequest | None = None
):
    """Kill the API token and secret right away, with no grace period (use when a key has leaked).

    The CRM gets `E1014` until new credentials are issued with `rotate-credentials`; pass
    `rotate_salt: true` there if the signature salt may have leaked too.
    `previous_only` only ends the grace period of the last rotation and keeps the current token.
    """
    body = body or RevokeRequest()
    psp = _get_psp(db, psp_code)
    psp.prev_api_token_hash = None
    psp.prev_api_secret_hash = None
    psp.prev_valid_until = None
    if not body.previous_only:
        psp.credentials_revoked_at = _now()
    sessions = _revoke_psp_sessions(db, psp) if body.revoke_sessions else 0
    audit(db, "psp.credentials_revoked", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"previous_only": body.previous_only, "logins_signed_out": sessions}, request=request)
    db.refresh(psp)
    return psp


@router.post("/{psp_code}/revoke-sessions")
def revoke_sessions(psp_code: str, db: DbSession, admin: AdminUser, request: Request):
    """Sign out every portal login of this PSP. API credentials are not touched."""
    psp = _get_psp(db, psp_code)
    count = _revoke_psp_sessions(db, psp)
    audit(db, "psp.sessions_revoked", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"logins_signed_out": count}, request=request)
    return {"success": True, "message": f"{count} login(s) of {psp_code} signed out"}


@router.delete("/{psp_code}")
def delete_psp(psp_code: str, db: DbSession, admin: AdminUser, request: Request):
    psp = _get_psp(db, psp_code)
    has_open = db.scalar(
        select(
            exists().where(Deposit.psp_id == psp.id, Deposit.status.in_(OPEN_STATUSES))
            | exists().where(Withdrawal.psp_id == psp.id, Withdrawal.status.in_(OPEN_STATUSES))
        )
    )
    if has_open:
        raise AppError(
            ErrorCode.PSP_HAS_OPEN_TRANSACTIONS,
            "PSP has pending or processing deposits/withdrawals. Deactivate it and resolve them first.",
        )
    db.delete(psp)  # its portal logins are removed by the FK cascade
    audit(db, "psp.deleted", actor_type="user", actor_id=admin.id, target=psp_code, request=request)
    return {"success": True, "message": f"PSP {psp_code} deleted"}
