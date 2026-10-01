import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)

# Every connection runs in UTC so NOW() defaults match the UTC values the app writes.
_CONNECT_ARGS = {"charset": "utf8mb4", "init_command": "SET time_zone = '+00:00'"}

engine = create_engine(
    get_settings().database_url,
    connect_args=_CONNECT_ARGS,
    pool_pre_ping=True,
    pool_recycle=3600,  # stay under MySQL's wait_timeout
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def ensure_database_exists() -> None:
    """Create the target database (utf8mb4) on first start if the user is allowed to."""
    url = make_url(get_settings().database_url)
    # URL.set() ignores None, so drop the database name with _replace to connect to the bare server.
    server_engine = create_engine(url._replace(database=None), connect_args=_CONNECT_ARGS)
    try:
        with server_engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :n"), {"n": url.database}
            )
            if not exists:
                name = url.database.replace("`", "``")
                conn.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                log.info("Created database %s", url.database)
    except Exception as exc:  # no CREATE rights: assume the database already exists
        log.warning("Could not check/create database %s: %s", url.database, exc)
    finally:
        server_engine.dispose()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
