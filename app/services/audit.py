from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Deposit, PortalUser, Psp, Withdrawal


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _psp_of_user(db: Session, *where) -> int | None:
    return db.scalar(select(PortalUser.psp_id).where(*where, PortalUser.psp_id.is_not(None)))


def resolve_psp_id(db: Session, action: str, actor_type: str, actor_id: str | None, target: str | None) -> int | None:
    """The PSP an audit entry concerns, or None (e.g. admin-only actions).

    Same rules as the backfill in migration 0028: the CRM caller, the target (psp code,
    DEP-/WDL- id, a login's user id), or the acting PSP login.
    """
    if actor_type == "psp" and actor_id:
        return db.scalar(select(Psp.id).where(Psp.psp_code == actor_id))
    if target:
        for model in (Deposit, Withdrawal):
            if target.startswith(f"{model.PREFIX}-"):
                return db.scalar(select(model.psp_id).where(model.public_id == target))
        psp_id = db.scalar(select(Psp.id).where(Psp.psp_code == target))
        if psp_id is not None:
            return psp_id
        if action.startswith("user.") and target.isdigit():
            psp_id = _psp_of_user(db, PortalUser.id == int(target))
            if psp_id is not None:
                return psp_id
    if actor_type == "user" and actor_id:
        if actor_id.isdigit():
            return _psp_of_user(db, PortalUser.id == int(actor_id))
        if action == "auth.login_failed":  # recorded with the email typed in
            return _psp_of_user(db, PortalUser.email == actor_id)
    return None


def audit(
    db: Session,
    action: str,
    *,
    actor_type: str,
    actor_id: str | int | None = None,
    target: str | None = None,
    details: dict | None = None,
    request: Request | None = None,
    commit: bool = True,
) -> None:
    actor_id = str(actor_id) if actor_id is not None else None
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target=target,
            details=details,
            ip_address=client_ip(request),
            psp_id=resolve_psp_id(db, action, actor_type, actor_id, target),
        )
    )
    if commit:
        db.commit()
