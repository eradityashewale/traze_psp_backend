"""PSP wallets: balance and wallet transaction history.

CRM (API token), always the calling PSP:
* GET /api/v1/balance
* GET /api/v1/balance/transactions

Portal (JWT):
* GET /api/v1/portal/balance               PSP login: own. Staff: psp_code, or the PSP if only one exists.
* GET /api/v1/portal/balance/transactions  PSP login: own. Staff: psp_code to filter, else every PSP.
* GET /api/v1/portal/wallets               Staff: every PSP's wallet plus totals.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.deps import CallingPsp, CurrentUser, DbSession, StaffUser
from app.errors import AppError, ErrorCode
from app.models import (
    Deposit, Psp, PspWallet, PspWalletTransaction, TxStatus, UserRole, WalletEntryType, Withdrawal,
)
from app.schemas import BalanceOut, Page, WalletList, WalletTransactionOut

crm_router = APIRouter(prefix="/api/v1/balance", tags=["CRM · Balance"])
portal_router = APIRouter(prefix="/api/v1/portal", tags=["Portal · Wallet"])

PSP_CODE_HELP = "Admin / sub admin only; PSP logins always get their own"


def _wallet(db, psp_id: int) -> PspWallet:
    wallet = db.scalar(select(PspWallet).where(PspWallet.psp_id == psp_id))
    if wallet is None:  # PSPs created before wallets existed get one from migration 0026
        raise AppError(ErrorCode.NOT_FOUND, "This PSP has no wallet")
    return wallet


def _psp_for_staff(db, psp_code: str | None) -> Psp:
    if psp_code:
        psp = db.scalar(select(Psp).where(Psp.psp_code == psp_code))
        if psp is None:
            raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {psp_code} not found")
        return psp
    psps = db.scalars(select(Psp).limit(2)).all()
    if not psps:
        raise AppError(ErrorCode.PSP_NOT_FOUND, "No PSP has been added yet")
    if len(psps) > 1:
        raise AppError(ErrorCode.VALIDATION_ERROR, "psp_code is required when there is more than one PSP")
    return psps[0]


def _ledger_page(db, wallet_ids_query, entry_type, date_from, date_to, limit, offset) -> dict:
    query = select(PspWalletTransaction)
    if wallet_ids_query is not None:
        query = query.where(PspWalletTransaction.wallet_id.in_(wallet_ids_query))
    if entry_type:
        query = query.where(PspWalletTransaction.entry_type == entry_type)
    if date_from:
        query = query.where(PspWalletTransaction.created_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), time.min, timezone.utc)
        query = query.where(PspWalletTransaction.created_at < end)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(PspWalletTransaction.created_at.desc(), PspWalletTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _wallet_ids(psp_id: int):
    return select(PspWallet.id).where(PspWallet.psp_id == psp_id)


def _approved_sums(db, model, psp_ids: list[int] | None) -> dict[int | None, Decimal]:
    """Sum of approved amounts per psp_id (or one overall sum under key None when psp_ids is None)."""
    query = select(func.coalesce(func.sum(model.amount), 0)).where(model.status == TxStatus.approved)
    if psp_ids is None:
        return {None: Decimal(db.scalar(query))}
    rows = db.execute(query.add_columns(model.psp_id).where(model.psp_id.in_(psp_ids)).group_by(model.psp_id)).all()
    return {psp_id: Decimal(total) for total, psp_id in rows}


def _balance_out(db, wallets: list[PspWallet]) -> list[BalanceOut]:
    psp_ids = [w.psp_id for w in wallets]
    deposits = _approved_sums(db, Deposit, psp_ids)
    withdrawals = _approved_sums(db, Withdrawal, psp_ids)
    zero = Decimal("0.00")
    return [
        BalanceOut.model_validate(w).model_copy(update={
            "total_deposit": deposits.get(w.psp_id, zero),
            "total_withdrawal": withdrawals.get(w.psp_id, zero),
        })
        for w in wallets
    ]


# ---------- CRM ----------

@crm_router.get("", response_model=BalanceOut)
def crm_balance(db: DbSession, psp: CallingPsp):
    """The calling PSP's wallet. A withdrawal above `available_balance` is refused with E2011."""
    return _balance_out(db, [_wallet(db, psp.id)])[0]


@crm_router.get("/transactions", response_model=Page[WalletTransactionOut])
def crm_wallet_transactions(
    db: DbSession,
    psp: CallingPsp,
    entry_type: WalletEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """The calling PSP's wallet movements, newest first."""
    return _ledger_page(db, _wallet_ids(psp.id), entry_type, date_from, date_to, limit, offset)


# ---------- portal ----------

@portal_router.get("/balance", response_model=BalanceOut)
def portal_balance(db: DbSession, user: CurrentUser, psp_code: str | None = Query(default=None, description=PSP_CODE_HELP)):
    """A PSP's wallet: balance, held (pending withdrawals) and available balance."""
    psp = user.psp if user.role == UserRole.psp else _psp_for_staff(db, psp_code)
    return _balance_out(db, [_wallet(db, psp.id)])[0]


@portal_router.get("/balance/transactions", response_model=Page[WalletTransactionOut])
def portal_wallet_transactions(
    db: DbSession,
    user: CurrentUser,
    psp_code: str | None = Query(default=None, description=PSP_CODE_HELP + "; staff without it see every PSP"),
    entry_type: WalletEntryType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Wallet movements, newest first. Each row has the before / after balance and the request that caused it."""
    if user.role == UserRole.psp:
        wallet_ids = _wallet_ids(user.psp_id)
    elif psp_code:
        wallet_ids = _wallet_ids(_psp_for_staff(db, psp_code).id)
    else:
        wallet_ids = None
    return _ledger_page(db, wallet_ids, entry_type, date_from, date_to, limit, offset)


@portal_router.get("/wallets", response_model=WalletList)
def list_wallets(
    db: DbSession,
    _: StaffUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Admin / sub admin: every PSP's wallet, plus totals across all PSPs."""
    total = db.scalar(select(func.count()).select_from(PspWallet))
    items = db.scalars(
        select(PspWallet).join(Psp, PspWallet.psp_id == Psp.id).order_by(Psp.created_at).limit(limit).offset(offset)
    ).all()
    balance, held = db.execute(
        select(func.coalesce(func.sum(PspWallet.balance), 0), func.coalesce(func.sum(PspWallet.held), 0))
    ).one()
    balance, held = Decimal(balance), Decimal(held)
    return {
        "items": _balance_out(db, items), "total": total, "limit": limit, "offset": offset,
        "totals": {
            "balance": balance, "held": held, "available_balance": balance - held,
            "total_deposit": _approved_sums(db, Deposit, None)[None],
            "total_withdrawal": _approved_sums(db, Withdrawal, None)[None],
        },
    }
