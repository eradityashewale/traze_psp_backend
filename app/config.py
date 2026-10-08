from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- environment (checklist 1.2 / 1.3) ---
    environment: Literal["local", "uat", "production"] = "local"
    public_api_base_url: str = "http://localhost:8000"  # e.g. https://<uat-host>/PSPtest

    # Either set DB_NAME / DB_USER / DB_PASS / DB_HOST, or a full DATABASE_URL (which wins).
    db_name: str | None = None
    db_user: str | None = None
    db_pass: str = ""
    db_host: str = "localhost:3306"  # host or host:port
    database_url: str = ""
    # Apply pending Alembic migrations when the app starts. Turn off to run `alembic upgrade head` yourself.
    run_migrations_on_startup: bool = True

    @model_validator(mode="after")
    def build_database_url(self):
        url = self.database_url
        if not url:
            if not (self.db_name and self.db_user):
                raise ValueError("Set DB_NAME and DB_USER (plus DB_PASS, DB_HOST) or DATABASE_URL in .env")
            user, password = quote(self.db_user, safe=""), quote(self.db_pass, safe="")
            url = f"mysql+pymysql://{user}:{password}@{self.db_host}/{self.db_name}"
        elif url.startswith("mysql://"):
            url = "mysql+pymysql://" + url[len("mysql://"):]
        elif not url.startswith("mysql+"):
            raise ValueError("DATABASE_URL must be a MySQL link, e.g. mysql://user:pass@localhost:3306/psp_portal")
        self.database_url = url
        return self

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # --- HTTPS (checklist 2 / 8) ---
    enforce_https: bool = False  # set true in UAT/production
    trust_forwarded_proto: bool = True  # honour X-Forwarded-Proto from the TLS proxy
    min_rsa_key_bits: int = 2048

    # --- API credentials (checklist 3 / 5) ---
    api_token_validity_days: int = 90  # quarterly rotation
    rotation_grace_hours: int = 72  # old token keeps working this long after a rotation

    # --- HMAC-SHA256 signature (checklist 6) ---
    require_signature: bool = True
    # A signed request is accepted while its timestamp is within this many seconds of the server time.
    signature_window_seconds: int = 300

    # --- public / private key (checklist 7) ---
    portal_private_key_path: str = "keys/portal_private_key.pem"
    # The CRM's RSA public key (PEM file). When set, every CRM POST must carry an X-Signature header.
    crm_public_key_path: str | None = None

    # --- login protection ---
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- callbacks (checklist 4 / 11) ---
    callback_timeout_seconds: float = 10
    callback_max_retries: int = 3
    callback_worker_interval_seconds: float = 2
    allow_http_callbacks: bool = False
    # One CRM serves every PSP, so its callback endpoint is set here and not per PSP.
    crm_callback_url: str | None = None
    crm_callback_username: str | None = None  # HTTP Basic auth for the callback
    crm_callback_password: str | None = None

    @model_validator(mode="after")
    def check_crm_callback_url(self):
        url = self.crm_callback_url
        if url and not url.startswith("https://") and not (self.allow_http_callbacks and url.startswith("http://")):
            raise ValueError("CRM_CALLBACK_URL must use HTTPS")
        return self

    # --- screenshot storage (S3) ---
    s3_bucket: str | None = None  # private bucket; uploads are refused while unset
    aws_region: str = "ap-south-1"
    # Local runs only. On EC2 leave both empty and attach an IAM role to the instance.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    s3_url_expires_seconds: int = 900  # lifetime of the view links in API responses
    screenshot_max_mb: int = 5

    cors_origins: str = ""  # comma-separated

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
