"""Monitoring, keys, error codes and audit trail."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.deps import DbSession, PspLogin, StaffUser
from app.errors import ErrorCode, error_catalog
from app.models import FINAL_STATUSES, AuditLog, Deposit, PortalUser, Psp, Withdrawal
from app.services.keys import portal_public_key_pem

router = APIRouter(tags=["Meta & Monitoring"])


@router.get("/health")
def health():
    """Liveness: the process is up."""
    return {"status": "ok", "environment": get_settings().environment}


@router.get("/health/ready")
def ready():
    """Readiness: database reachable, plus callback backlog for monitoring (checklist 11/12)."""
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            backlog = failed = 0
            for model in (Deposit, Withdrawal):
                backlog += db.scalar(select(func.count()).where(model.callback_due_at.is_not(None)))
                failed += db.scalar(
                    select(func.count()).where(
                        model.status.in_(FINAL_STATUSES),
                        model.callback_sent.is_(False),
                        model.callback_due_at.is_(None),
                    )
                )
    except Exception:
        return JSONResponse(
            {
                "success": False,
                "error_code": ErrorCode.SERVICE_UNAVAILABLE.code,
                "message": "Database unreachable",
                "status": "unavailable",
            },
            status_code=503,
        )
    return {
        "status": "ok",
        "environment": get_settings().environment,
        "database": "ok",
        "callbacks_pending": backlog,
        "callbacks_failed": failed,
        "checked_at": now,
    }


@router.get("/api/v1/meta/error-codes")
def error_codes():
    """Catalog of every error_code the API can return."""
    return {"error_codes": error_catalog()}


@router.get("/api/v1/meta/public-key", response_class=PlainTextResponse)
def public_key():
    """Portal RSA public key (PEM). Use it to verify the X-Signature header on callbacks."""
    return portal_public_key_pem()


@router.get("/api/v1/audit-logs")
def audit_logs(
    db: DbSession,
    _: StaffUser,
    action: str | None = None,
    target: str | None = None,
    psp_code: str | None = Query(default=None, description="Only entries about this PSP"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Admin or sub admin (read-only): the full audit trail, newest first."""
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))
    if target:
        query = query.where(AuditLog.target == target)
    if psp_code:
        query = query.join(Psp, AuditLog.psp_id == Psp.id).where(Psp.psp_code == psp_code)
    rows = db.scalars(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    actors = _actors(db, rows)
    return {"items": [_entry(r, actors[r.id][0]) for r in rows]}


def _actors(db, rows: list[AuditLog]) -> dict[int, tuple[str | None, int | None]]:
    """For each entry: who acted (display name) and the PSP that actor belongs to, if any.

    * a portal login (actor_id = user id): its full name;
    * a failed login (actor_id = the email typed in): that login's name, if the email exists;
    * a CRM call (actor_id = psp code): "<psp name> (CRM)";
    * the system: "System". A deleted login has no name.
    """
    ids = {int(r.actor_id) for r in rows if r.actor_type == "user" and r.actor_id and r.actor_id.isdigit()}
    emails = {r.actor_id.lower() for r in rows if r.action == "auth.login_failed" and r.actor_id}
    codes = {r.actor_id for r in rows if r.actor_type == "psp" and r.actor_id}
    by_id, by_email, by_code = {}, {}, {}
    if ids or emails:
        for uid, email, name, psp_id in db.execute(
            select(PortalUser.id, PortalUser.email, PortalUser.full_name, PortalUser.psp_id)
            .where(PortalUser.id.in_(ids) | func.lower(PortalUser.email).in_(emails))
        ):
            by_id[str(uid)] = by_email[email.lower()] = (name, psp_id)
    if codes:
        for code, name, psp_id in db.execute(select(Psp.psp_code, Psp.psp_name, Psp.id).where(Psp.psp_code.in_(codes))):
            by_code[code] = (f"{name} (CRM)", psp_id)

    def one(r: AuditLog) -> tuple[str | None, int | None]:
        if r.actor_type == "psp":
            return by_code.get(r.actor_id, (f"{r.actor_id} (CRM)", None))
        if r.actor_type == "system":
            return ("System", None)
        if r.action == "auth.login_failed" and r.actor_id:
            return by_email.get(r.actor_id.lower(), (None, None))
        return by_id.get(r.actor_id, (None, None))

    return {r.id: one(r) for r in rows}


def _entry(r: AuditLog, actor_name: str | None, *, show_ip: bool = True) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at,
        "actor_type": r.actor_type,
        "actor_id": r.actor_id,
        "actor_name": actor_name,
        "action": r.action,
        "target": r.target,
        "details": r.details,
        "ip_address": r.ip_address if show_ip else None,
    }


@router.get("/api/v1/portal/audit-logs", tags=["Portal · Audit logs"])
def psp_audit_logs(
    db: DbSession,
    user: PspLogin,
    action: str | None = Query(default=None, description="Prefix, e.g. deposit. or auth.login"),
    target: str | None = Query(default=None, description="e.g. DEP-00001"),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """PSP login: your PSP's audit trail, newest first.

    Covers your CRM's API calls, your logins' sign-ins and actions, everything done on your
    deposits / withdrawals and their chats, and admin changes to your PSP (payment methods,
    credentials, status, logins). IP addresses are shown only for your own side's actions.
    """
    query = select(AuditLog).where(AuditLog.psp_id == user.psp_id)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))
    if target:
        query = query.where(AuditLog.target == target)
    if date_from:
        query = query.where(AuditLog.created_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        query = query.where(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min, timezone.utc))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()

    actors = _actors(db, rows)
    # The IP is shown only when the actor is on this PSP's side (its CRM or one of its logins).
    items = [
        _entry(r, actors[r.id][0], show_ip=actors[r.id][1] == user.psp_id)
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
