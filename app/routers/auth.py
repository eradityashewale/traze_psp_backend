from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import AdminUser, CurrentUser, DbSession, ensure_psp_login_allowed
from app.errors import AppError, ErrorCode
from app.models import PortalUser, Psp
from app.schemas import LoginRequest, TokenResponse, UserCreate, UserOut, UserUpdate
from app.security import create_access_token, hash_password, verify_password
from app.services.audit import audit
from app.services.users import new_portal_user

router = APIRouter(prefix="/api/v1", tags=["Auth & Users"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_for(db, user: PortalUser, request: Request) -> TokenResponse:
    user.last_login_at = _now()
    audit(db, "auth.login_success", actor_type="user", actor_id=user.id, request=request)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role.value),
        expires_in_minutes=get_settings().jwt_expires_minutes,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbSession, request: Request):
    """Email + password -> access token, for admins and PSP logins.

    5 wrong passwords lock the account for 15 minutes. A PSP login stops working while its PSP is inactive.
    """
    settings = get_settings()
    user = db.scalar(select(PortalUser).where(func.lower(PortalUser.email) == body.email.lower()))

    if user and user.locked_until and user.locked_until > _now():
        audit(db, "auth.login_locked", actor_type="user", actor_id=user.id, request=request)
        raise AppError(ErrorCode.ACCOUNT_LOCKED)

    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.max_failed_logins:
                user.locked_until = _now() + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_count = 0
        audit(db, "auth.login_failed", actor_type="user", actor_id=body.email.lower(), request=request)
        raise AppError(ErrorCode.AUTH_INVALID, "Invalid email or password")

    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    ensure_psp_login_allowed(user)
    return _token_for(db, user, request)


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, _: AdminUser, psp_code: str | None = None):
    """All logins, or only one PSP's logins with ?psp_code=."""
    query = select(PortalUser)
    if psp_code:
        query = query.join(Psp, PortalUser.psp_id == Psp.id).where(Psp.psp_code == psp_code)
    return db.scalars(query.order_by(PortalUser.id)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession, admin: AdminUser, request: Request):
    """Create another admin, or an extra login for an existing PSP."""
    psp = None
    if body.psp_code:
        psp = db.scalar(select(Psp).where(Psp.psp_code == body.psp_code))
        if psp is None:
            raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {body.psp_code} not found")
    user = new_portal_user(
        db, email=body.email, full_name=body.full_name, password=body.password, role=body.role, psp=psp
    )
    db.flush()
    audit(db, "user.created", actor_type="user", actor_id=admin.id, target=str(user.id),
          details={"email": user.email, "role": user.role.value, "psp_code": body.psp_code}, request=request)
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: DbSession, admin: AdminUser, request: Request):
    user = db.get(PortalUser, user_id)
    if user is None:
        raise AppError(ErrorCode.USER_NOT_FOUND)
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and changes.get("is_active") is False:
        raise AppError(ErrorCode.USER_SELF_CHANGE)
    if changes.pop("unlock", None):
        user.locked_until = None
        user.failed_login_count = 0
    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
        changes["password"] = "***"
    for field, value in changes.items():
        if field != "password":
            setattr(user, field, value)
    audit(db, "user.updated", actor_type="user", actor_id=admin.id, target=str(user.id),
          details={k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()}, request=request)
    db.refresh(user)
    return user
