from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


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
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=str(actor_id) if actor_id is not None else None,
            action=action,
            target=target,
            details=details,
            ip_address=client_ip(request),
        )
    )
    if commit:
        db.commit()
