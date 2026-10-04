"""Monitoring, keys, error codes, audit trail and the PSP integration questionnaire."""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.deps import AdminUser, DbSession
from app.errors import ErrorCode, error_catalog
from app.models import FINAL_STATUSES, AuditLog, Deposit, Psp, Withdrawal
from app.routers.psps import _get_psp
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


@router.get("/api/v1/psps/{psp_code}/questionnaire")
def questionnaire(psp_code: str, db: DbSession, _: AdminUser):
    """The 'New PSP Checklist' filled in from this deployment's configuration and the PSP record."""
    s = get_settings()
    psp: Psp = _get_psp(db, psp_code)
    env_label = {"uat": "UAT", "production": "Production"}.get(s.environment, "Local")

    def contact(fallback: str) -> str:
        return psp.contact_email or fallback or "Not provided"

    return {
        "psp_name": psp.psp_name,
        "answers": [
            {"no": "1.1", "question": "Documentation",
             "answer": f"{s.public_api_base_url}/docs (OpenAPI: {s.public_api_base_url}/openapi.json) + README"},
            {"no": "1.2/1.3", "question": f"{env_label} Site Credential",
             "answer": f"Admin portal: {s.admin_portal_url or 'not configured'}\n"
                       f"API request address: {s.public_api_base_url}/api/v1/\n"
                       "Login/Password: each PSP gets its own portal login from the admin; it reviews only its own requests"},
            {"no": "2", "question": "Is https/ssl used for api interaction?",
             "answer": "Yes" if s.enforce_https else "Yes (HTTPS enforcement is OFF in this environment)"},
            {"no": "3", "question": "How requests are authenticated?",
             "answer": "API Token (Authorization: Bearer) & API secret key (X-API-Secret)"},
            {"no": "4", "question": "HTTP Basic authorization for webhooks",
             "answer": f"Yes, callbacks to {s.crm_callback_url or 'the CRM (URL not configured)'} use HTTP Basic auth "
                       f"(user '{s.crm_callback_username or 'not configured'}')"},
            {"no": "5", "question": "Could API token be changed periodically?",
             "answer": f"Yes, every {s.api_token_validity_days} days (quarterly); old token valid for "
                       f"{s.rotation_grace_hours}h after rotation. Current token expires "
                       f"{psp.api_token_expires_at:%Y-%m-%d}"},
            {"no": "6", "question": "Hash of critical fields with salt?",
             "answer": "md5 signature of sorted critical fields + timestamp + shared salt, on requests and callbacks"},
            {"no": "7", "question": "Public-server certificate / key for authentication?",
             "answer": "Public key & private key: CRM signs requests (RSA-SHA256, X-Signature)"
                       + (" [CRM key registered]" if s.crm_public_key_path else " [CRM key NOT registered yet]")
                       + "; portal signs callbacks, public key at /api/v1/meta/public-key"},
            {"no": "8", "question": "Key length for encryption (HTTPS/SSL)?",
             "answer": f"RSA {s.min_rsa_key_bits}-bit minimum (TLS certificate and signing keys)"},
            {"no": "9", "question": "Is MFA in place?",
             "answer": f"No MFA. Password login (bcrypt) with lockout after {s.max_failed_logins} failed attempts"},
            {"no": "10", "question": "PCI compliance level", "answer": s.pci_dss_level},
            {"no": "11", "question": "DR site / how to switch?", "answer": s.dr_description},
            {"no": "12", "question": "How can we monitor availability of PSP?", "answer": s.monitoring_channel},
            {"no": "13.1", "question": "Technical Support", "answer": contact(s.support_technical)},
            {"no": "13.2", "question": "Business", "answer": contact(s.support_business)},
            {"no": "13.3", "question": "Customer Service", "answer": contact(s.support_customer_service)},
        ],
    }
