"""MySQL column helpers."""

from datetime import datetime, timezone

from sqlalchemy import String, func
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """DATETIME(6) that always stores UTC and always returns timezone-aware UTC values.

    MySQL DATETIME has no time zone, so aware datetimes are converted to naive UTC on the
    way in and tagged as UTC on the way out. The connection time_zone is also pinned to
    +00:00 (see database.py) so NOW() server defaults are UTC too.
    """

    impl = DATETIME(fsp=6)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime given; use datetime.now(timezone.utc)")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def now6():
    """NOW(6): server-side current time with microseconds."""
    return func.now(6)


def BinaryString(length: int) -> String:
    """Case-sensitive VARCHAR, for tokens, hashes and client-supplied keys.

    The default utf8mb4 collation is case-insensitive, which would make
    'order-1' and 'ORDER-1' collide as idempotency keys.
    """
    return String(length, collation="utf8mb4_bin")
