"""PSP Management module (guide Section 3). Admin only; a sub admin can read but not change."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Request, status
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import engine
from app.deps import AdminUser, DbSession, StaffUser
from app.errors import AppError, ErrorCode
from app.models import (
    DETAIL_FIELDS,
    OPEN_STATUSES,
    Deposit,
    PaymentMethod,
    PortalUser,
    Psp,
    PspPaymentMethod,
    UserRole,
    Withdrawal,
    missing_method_fields,
)
from app.schemas import (
    PaymentMethodOut,
    PaymentMethodStatusUpdate,
    PaymentMethodUpsert,
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
from app.services.mailer import mail_enabled, send_psp_welcome_email
from app.services.storage import delete_object, upload_qr_code
from app.services.users import new_portal_user
from app.services.wallet import new_wallet

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


CREATE_LOCK = "psp_portal.create_psp"


def _ensure_psp_limit(db: Session) -> None:
    """Refuse a new PSP once MAX_PSPS exist (0 = no limit). Call while holding CREATE_LOCK."""
    limit = get_settings().max_psps
    if limit <= 0:
        return
    # Locking read: sees PSPs committed after this request's snapshot began.
    count = len(db.scalars(select(Psp.id).with_for_update(read=True)).all())
    if count >= limit:
        raise AppError(
            ErrorCode.PSP_LIMIT_REACHED,
            f"Only {limit} PSP{'s' if limit != 1 else ''} can be added; another PSP cannot be added",
        )


@router.get("", response_model=PspList)
def list_psps(db: DbSession, _: StaffUser):
    psps = db.scalars(
        select(Psp)
        .options(selectinload(Psp.logins), selectinload(Psp.payment_methods))
        .order_by(Psp.created_at)
    ).all()
    return PspList(psps=psps)


@router.post("", response_model=PspCreated, status_code=status.HTTP_201_CREATED)
def create_psp(
    body: Annotated[PspCreate, Form(media_type="multipart/form-data")],
    db: DbSession,
    admin: AdminUser,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Add a PSP with its first payment method and its portal login (login_email / login_password).

    Sent as multipart/form-data. `payment_method` (imps, rtgs or upi) is required, with that
    method's details:
    * imps / rtgs: bank_name, account_name, account_number, ifsc_code, branch
    * upi: upi_id and qr_code (QR code image), both required; no bank details

    Add the other methods later with `PUT /api/v1/psps/{psp_code}/payment-methods/{method}`.

    The PSP logs in with that login to approve or reject its own deposits and withdrawals. `psp_code` is generated here and returned in the response; it is
    not accepted in the body.

    After the PSP is saved, login_email gets a welcome email with the portal link, the login
    email and password, and the steps to change the password (see `welcome_email_queued`).

    At most MAX_PSPS PSPs can exist (default 1); past that this fails with E2010.
    The PSP gets an empty wallet (see /api/v1/portal/balance).
    """
    # A MySQL named lock makes concurrent creates wait for each other, so the limit check holds.
    # It lives on its own connection: the session hands its connection back to the pool on commit.
    with engine.connect() as lock_conn:
        if not lock_conn.scalar(text("SELECT GET_LOCK(:n, 10)"), {"n": CREATE_LOCK}):
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "Another PSP is being added; try again")
        try:
            return _create_psp(body, db, admin, request, background_tasks)
        finally:
            lock_conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": CREATE_LOCK})


def _create_psp(body: PspCreate, db: Session, admin: PortalUser, request: Request, background_tasks: BackgroundTasks):
    _ensure_psp_limit(db)
    psp_code = _new_psp_code(db)
    psp = Psp(psp_name=body.psp_name, contact_email=body.contact_email, psp_code=psp_code)
    psp.payment_methods.append(
        PspPaymentMethod(
            method=body.payment_method,
            **body.detail_values(),
            qr_code_key=upload_qr_code(body.qr_code, psp_code) if body.qr_code else None,
        )
    )
    new_wallet(psp)
    creds = _issue_credentials(psp)
    db.add(psp)
    login = new_portal_user(
        db, email=body.login_email, full_name=body.psp_name, password=body.login_password,
        role=UserRole.psp, psp=psp,
    )
    db.flush()
    audit(db, "psp.created", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"login_email": login.email, "payment_method": body.payment_method.value}, request=request)
    db.refresh(psp)
    db.refresh(login)
    # audit() has committed, so the login exists before the email goes out (after the response).
    email_queued = mail_enabled()
    background_tasks.add_task(
        send_psp_welcome_email,
        to=login.email, psp_name=psp.psp_name, psp_code=psp.psp_code, password=body.login_password,
    )
    return PspCreated(
        psp=PspOut.model_validate(psp),
        credentials=PspCredentials(
            api_token=creds["api_token"],
            api_secret=creds["api_secret"],
            signature_salt=psp.signature_salt,
            api_token_expires_at=psp.api_token_expires_at,
        ),
        portal_login=UserOut.model_validate(login),
        welcome_email_queued=email_queued,
    )


@router.get("/{psp_code}", response_model=PspOut)
def get_psp(psp_code: str, db: DbSession, _: StaffUser):
    return _get_psp(db, psp_code)


@router.put("/{psp_code}", response_model=PspOut)
def update_psp(psp_code: str, body: PspUpdate, db: DbSession, admin: AdminUser, request: Request):
    psp = _get_psp(db, psp_code)
    changes = body.model_dump(exclude_unset=True)
    for field in ("psp_name", "status"):
        if field in changes and changes[field] is None:
            raise AppError(ErrorCode.PSP_CONFIG_INVALID, f"{field} cannot be null")

    for field, value in changes.items():
        setattr(psp, field, value)
    audit(db, "psp.updated", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()}, request=request)
    db.refresh(psp)
    return psp


def _get_method(psp: Psp, method: PaymentMethod) -> PspPaymentMethod:
    row = psp.get_method(method)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"{psp.psp_code} has no {method.value} payment method")
    return row


@router.get("/{psp_code}/payment-methods", response_model=list[PaymentMethodOut])
def list_payment_methods(psp_code: str, db: DbSession, _: StaffUser):
    return _get_psp(db, psp_code).payment_methods


@router.put("/{psp_code}/payment-methods/{method}", response_model=PaymentMethodOut)
def upsert_payment_method(
    psp_code: str,
    method: PaymentMethod,
    body: Annotated[PaymentMethodUpsert, Form(media_type="multipart/form-data")],
    db: DbSession,
    admin: AdminUser,
    request: Request,
):
    """Add a payment method to the PSP, or change one it has (multipart/form-data).

    Adding: send every detail the method needs.
    * imps / rtgs: bank_name, account_name, account_number, ifsc_code, branch
    * upi: upi_id and qr_code (QR code image), both required; no bank details

    Changing: send only the details to change; the rest stay. A new qr_code replaces the old one.
    """
    psp = _get_psp(db, psp_code)
    try:
        body.check_fits(method)
    except ValueError as e:
        raise AppError(ErrorCode.PSP_CONFIG_INVALID, str(e))

    row = psp.get_method(method)
    changes = body.detail_values()
    current = {f: getattr(row, f) for f in DETAIL_FIELDS} if row else {}
    has_qr = body.qr_code is not None or bool(row and row.qr_code_key)
    missing = missing_method_fields(method, {**current, **changes}, has_qr=has_qr)
    if missing:
        raise AppError(ErrorCode.PSP_CONFIG_INVALID, f"{method.value} needs {', '.join(missing)}")

    added = row is None
    if added:
        row = PspPaymentMethod(method=method)
        psp.payment_methods.append(row)
    for field, value in changes.items():
        setattr(row, field, value)
    old_key = None
    if body.qr_code is not None:
        old_key, row.qr_code_key = row.qr_code_key, upload_qr_code(body.qr_code, psp.psp_code)

    db.flush()
    audit(db, "psp.payment_method_added" if added else "psp.payment_method_updated", actor_type="user",
          actor_id=admin.id, target=psp.psp_code,
          details={"method": method.value, **changes, "qr_code_replaced": body.qr_code is not None},
          request=request)
    delete_object(old_key)
    db.refresh(row)
    return row


@router.patch("/{psp_code}/payment-methods/{method}/status", response_model=PaymentMethodOut)
def set_payment_method_status(
    psp_code: str,
    method: PaymentMethod,
    body: PaymentMethodStatusUpdate,
    db: DbSession,
    admin: AdminUser,
    request: Request,
):
    """Activate or deactivate a payment method. Its details are kept either way.

    An inactive method is left out of GET /api/v1/deposits/payment-details and deposits with it
    are refused (E2009). The PSP's last active, complete method cannot be deactivated.
    """
    psp = _get_psp(db, psp_code)
    row = _get_method(psp, method)
    if row.is_active == body.is_active:
        return row
    if not body.is_active and row.is_ready and len(psp.ready_methods) == 1:
        raise AppError(
            ErrorCode.PSP_CONFIG_INVALID,
            "A PSP needs at least one active, complete payment method; add or activate another first",
        )
    row.is_active = body.is_active
    db.flush()
    audit(db, "psp.payment_method_activated" if body.is_active else "psp.payment_method_deactivated",
          actor_type="user", actor_id=admin.id, target=psp.psp_code, details={"method": method.value},
          request=request)
    db.refresh(row)
    return row


@router.delete("/{psp_code}/payment-methods/{method}", response_model=PspOut)
def remove_payment_method(psp_code: str, method: PaymentMethod, db: DbSession, admin: AdminUser, request: Request):
    """Remove a payment method. The PSP's last active, complete method cannot be removed."""
    psp = _get_psp(db, psp_code)
    row = _get_method(psp, method)
    if row.is_ready and len(psp.ready_methods) == 1:
        raise AppError(
            ErrorCode.PSP_CONFIG_INVALID,
            "A PSP needs at least one active, complete payment method; add or activate another first",
        )
    qr_key = row.qr_code_key
    psp.payment_methods.remove(row)
    db.flush()
    audit(db, "psp.payment_method_removed", actor_type="user", actor_id=admin.id, target=psp.psp_code,
          details={"method": method.value}, request=request)
    delete_object(qr_key)
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
    qr_keys = [m.qr_code_key for m in psp.payment_methods if m.qr_code_key]
    db.delete(psp)  # its portal logins and payment methods are removed by the FK cascade
    audit(db, "psp.deleted", actor_type="user", actor_id=admin.id, target=psp_code, request=request)
    db.flush()
    for key in qr_keys:
        delete_object(key)
    return {"success": True, "message": f"PSP {psp_code} deleted"}
