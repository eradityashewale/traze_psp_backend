"""Apply Alembic migrations from inside the app (used at startup)."""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.database import engine

log = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _adopt_pre_alembic_database(config: Config) -> None:
    """Databases created before migrations existed have tables but no alembic_version.

    Mark them with the revision that matches what they already contain, so the
    initial migration does not try to create tables that are already there.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or "psps" not in tables:
        return
    psp_columns = {c["name"] for c in inspector.get_columns("psps")}
    revision = "0002" if "account_number" in psp_columns else "0001"
    log.warning("Existing database without migration history; marking it as revision %s", revision)
    command.stamp(config, revision)


def run_migrations() -> None:
    """Bring the database to the latest revision (same as `alembic upgrade head`)."""
    config = Config(str(ALEMBIC_INI))
    _adopt_pre_alembic_database(config)
    command.upgrade(config, "head")
