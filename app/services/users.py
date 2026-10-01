from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models import PortalUser, Psp, UserRole
from app.security import hash_password


def new_portal_user(
    db: Session, *, email: str, full_name: str, password: str, role: UserRole, psp: Psp | None = None
) -> PortalUser:
    """Add (not commit) a portal login. PSP logins must belong to a PSP; admins must not."""
    if db.scalar(select(PortalUser).where(func.lower(PortalUser.email) == email.lower())):
        raise AppError(ErrorCode.USER_ALREADY_EXISTS, f"A login with email {email} already exists")
    user = PortalUser(
        email=email.lower(),
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        psp=psp if role == UserRole.psp else None,
    )
    db.add(user)
    return user
