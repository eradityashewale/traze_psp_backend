"""Overview page of the portal.

Used by portal logins (JWT). One endpoint serves both roles, and the token decides the scope:
* an admin gets the figures across every PSP;
* a PSP login gets the same figures for its own PSP's requests only.
"""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models import Deposit, PortalUser, Psp, TxKind, TxStatus, UserRole, Withdrawal
from app.schemas import DashboardOut
from app.services.transactions import Transaction

router = APIRouter(prefix="/api/v1/portal/dashboard", tags=["Portal · Dashboard"])

KINDS: tuple[tuple[TxKind, type[Transaction]], ...] = ((TxKind.deposit, Deposit), (TxKind.withdrawal, Withdrawal))


def _scoped(query, model: type[Transaction], user: PortalUser):
    return query.where(model.psp_id == user.psp_id) if user.role == UserRole.psp else query


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, timezone.utc)


def _status_counts(db, model: type[Transaction], user: PortalUser) -> dict[TxStatus, int]:
    rows = db.execute(_scoped(select(model.status, func.count()), model, user).group_by(model.status)).all()
    return dict(rows)


def _psp_count(db, user: PortalUser) -> int:
    """Every PSP for an admin; a PSP login only ever sees its own."""
    if user.role == UserRole.psp:
        return 1
    return db.scalar(select(func.count()).select_from(Psp))


def _daily_counts(db, model: type[Transaction], user: PortalUser, first_day: date) -> dict[date, int]:
    day = func.date(model.created_at)
    query = select(day, func.count()).where(model.created_at >= _day_start(first_day)).group_by(day)
    return dict(db.execute(_scoped(query, model, user)).all())


def _recent(db, model: type[Transaction], kind: TxKind, user: PortalUser, limit: int) -> list[dict]:
    query = (
        select(
            model.public_id, Psp.psp_code, model.customer_name, model.customer_email,
            model.amount, model.currency, model.status, model.created_at,
        )
        .outerjoin(Psp, model.psp_id == Psp.id)
        .order_by(model.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": r.public_id, "kind": kind, "psp_code": r.psp_code, "customer_name": r.customer_name,
            "customer_email": r.customer_email, "amount": r.amount, "currency": r.currency,
            "status": r.status, "created_at": r.created_at,
        }
        for r in db.execute(_scoped(query, model, user)).all()
    ]


def _request_card(daily: dict[date, int], days: list[date]) -> dict:
    """One "Deposit requests" / "Withdrawal requests" card: the daily series and its summary."""
    counts = [daily.get(d, 0) for d in days]
    return {
        "period_days": len(days),
        "total": sum(counts),
        "today": counts[-1],
        "peak": max(counts),
        "low": min(counts),
        "avg": round(sum(counts) / len(counts), 1),
        "series": [{"date": d, "count": c} for d, c in zip(days, counts)],
    }


@router.get("", response_model=DashboardOut)
def dashboard(
    db: DbSession,
    user: CurrentUser,
    days: int = Query(default=30, ge=1, le=90, description="Period of the deposit / withdrawal request cards"),
    activity_days: int = Query(default=14, ge=1, le=90, description="Period of the request activity chart"),
    recent_limit: int = Query(default=10, ge=1, le=50, description="Rows in recent transactions"),
):
    """Everything the overview page shows. Days are UTC calendar days and the last one is today."""
    today = datetime.now(timezone.utc).date()
    span = max(days, activity_days)
    all_days = [today - timedelta(days=n) for n in range(span - 1, -1, -1)]

    daily, by_status, recent = {}, {}, []
    for kind, model in KINDS:
        daily[kind] = _daily_counts(db, model, user, all_days[0])
        by_status[kind] = _status_counts(db, model, user)
        recent += _recent(db, model, kind, user, recent_limit)
    recent.sort(key=lambda r: r["created_at"], reverse=True)

    overview = {s.value: sum(by_status[kind].get(s, 0) for kind, _ in KINDS) for s in TxStatus}
    total = sum(overview.values())
    deposits_by_status, withdrawals_by_status = by_status[TxKind.deposit], by_status[TxKind.withdrawal]

    return {
        "role": user.role,
        "full_name": user.full_name,
        "psp_code": user.psp_code,
        "psp_name": user.psp.psp_name if user.psp else None,
        "deposits": _request_card(daily[TxKind.deposit], all_days[-days:]),
        "withdrawals": _request_card(daily[TxKind.withdrawal], all_days[-days:]),
        "pending_requests": overview[TxStatus.pending.value],
        "pending_deposits": deposits_by_status.get(TxStatus.pending, 0),
        "pending_withdrawals": withdrawals_by_status.get(TxStatus.pending, 0),
        "approved_deposits": deposits_by_status.get(TxStatus.approved, 0),
        "approved_withdrawals": withdrawals_by_status.get(TxStatus.approved, 0),
        "rejected_deposits": deposits_by_status.get(TxStatus.rejected, 0),
        "rejected_withdrawals": withdrawals_by_status.get(TxStatus.rejected, 0),
        "reversed_deposits": deposits_by_status.get(TxStatus.reversed, 0),
        "reversed_withdrawals": withdrawals_by_status.get(TxStatus.reversed, 0),
        "total_psp_count": _psp_count(db, user),
        "approval_rate": round(overview[TxStatus.approved.value] * 100 / total, 1) if total else 0,
        "activity": [
            {
                "date": d,
                "deposits": daily[TxKind.deposit].get(d, 0),
                "withdrawals": daily[TxKind.withdrawal].get(d, 0),
            }
            for d in all_days[-activity_days:]
        ],
        "requests_overview": {**overview, "total": total},
        "recent_transactions": recent[:recent_limit],
        "generated_at": datetime.now(timezone.utc),
    }
