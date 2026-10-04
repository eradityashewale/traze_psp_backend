"""Monitoring, keys, error codes and audit trail."""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.deps import AdminUser, DbSession
from app.errors import ErrorCode, error_catalog
from app.models import FINAL_STATUSES, AuditLog, Deposit, Withdrawal
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
    _: AdminUser,
    action: str | None = None,
    target: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.like(f"{action}%"))
    if target:
        query = query.where(AuditLog.target == target)
    rows = db.scalars(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return {
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at,
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "action": r.action,
                "target": r.target,
                "details": r.details,
                "ip_address": r.ip_address,
            }
            for r in rows
        ]
    }
