import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app import models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.database import SessionLocal, ensure_database_exists
from app.errors import ErrorCode, register_error_handlers
from app.migrations import run_migrations
from app.models import PortalUser, UserRole
from app.routers import auth, deposits, meta, psps, withdrawals
from app.security import hash_password
from app.services.callback import CallbackWorker
from app.services.keys import crm_public_key_pem, portal_private_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("psp_portal")


def bootstrap_admin() -> None:
    settings = get_settings()
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    with SessionLocal() as db:
        if db.scalar(select(PortalUser.id).limit(1)) is not None:
            return
        db.add(
            PortalUser(
                email=settings.bootstrap_admin_email.lower(),
                full_name="Administrator",
                password_hash=hash_password(settings.bootstrap_admin_password),
                role=UserRole.admin,
            )
        )
        db.commit()
        log.info("Created bootstrap admin %s", settings.bootstrap_admin_email)


def check_production_settings() -> None:
    s = get_settings()
    if s.environment == "local":
        return
    problems = []
    if not s.enforce_https:
        problems.append("ENFORCE_HTTPS must be true")
    if s.allow_http_callbacks:
        problems.append("ALLOW_HTTP_CALLBACKS must be false")
    if not (s.crm_callback_url and s.crm_callback_username and s.crm_callback_password):
        problems.append("CRM_CALLBACK_URL, CRM_CALLBACK_USERNAME and CRM_CALLBACK_PASSWORD must be set")
    if not s.require_signature:
        problems.append("REQUIRE_SIGNATURE must be true")
    if len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be at least 32 characters")
    if problems:
        raise RuntimeError(f"Unsafe configuration for {s.environment}: " + "; ".join(problems))


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_production_settings()
    ensure_database_exists()
    if get_settings().run_migrations_on_startup:
        run_migrations()  # alembic upgrade head
    bootstrap_admin()
    portal_private_key()  # load or generate the signing key at startup
    crm_public_key_pem()  # fail now, not on the first request, if the CRM key file is bad
    worker = CallbackWorker()
    worker.start()
    yield
    worker.stop()


settings = get_settings()
app = FastAPI(
    title="PSP Portal API",
    version="1.1.0",
    description="Deposits, withdrawals, approval/rejection and CRM callbacks.",
    lifespan=lifespan,
)
register_error_handlers(app)


@app.middleware("http")
async def require_https(request: Request, call_next):
    """Reject plain-HTTP requests when ENFORCE_HTTPS is on (TLS usually ends at the proxy)."""
    if settings.enforce_https and not request.url.path.startswith("/health"):
        scheme = request.url.scheme
        if settings.trust_forwarded_proto:
            scheme = request.headers.get("x-forwarded-proto", scheme).split(",")[0].strip()
        if scheme != "https":
            return JSONResponse(
                {"success": False, "error_code": ErrorCode.HTTPS_REQUIRED.code, "message": "HTTPS is required"},
                status_code=403,
            )
    # A multipart body can be read only once; keep the raw bytes for the RSA signature check.
    if request.method == "POST" and request.headers.get("content-type", "").startswith("multipart/form-data"):
        request.state.raw_body = await request.body()
    response = await call_next(request)
    if settings.enforce_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(meta.router)
app.include_router(auth.router)
app.include_router(psps.router)
app.include_router(deposits.router)
app.include_router(withdrawals.router)
app.include_router(deposits.review_router)
app.include_router(withdrawals.review_router)
