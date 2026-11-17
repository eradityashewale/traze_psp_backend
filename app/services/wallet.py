"""PSP wallets: the only place a wallet's balance / held amounts change.

Every change locks the wallet row, updates it and writes one psp_wallet_transactions row with the
before/after figures. Nothing here commits: the caller's commit saves the request's status change,
the wallet and the ledger row together, or none of them.

| event                | entry_type          | balance | held  |
|----------------------|---------------------|---------|-------|
| withdrawal submitted | withdrawal_hold     |         | + amt |  needs available >= amt
| withdrawal rejected  | withdrawal_release  |         | - amt |
| withdrawal approved  | withdrawal_debit    | - amt   | - amt |
| withdrawal reversed  | withdrawal_reversal | + amt   |       |
| deposit approved     | deposit_credit      | + amt   |       |
| deposit reversed     | deposit_reversal    | - amt   |       |  needs available >= amt
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models import (
    Deposit,
    PortalUser,
    Psp,
    PspWallet,
    PspWalletTransaction,
    TxStatus,
    WalletEntryType as E,
    Withdrawal,
)

# (change to balance, change to held) per entry, as a multiple of the amount.
_EFFECT: dict[E, tuple[int, int]] = {
    E.deposit_credit: (1, 0),
    E.deposit_reversal: (-1, 0),
    E.withdrawal_hold: (0, 1),
    E.withdrawal_release: (0, -1),
    E.withdrawal_debit: (-1, -1),
    E.withdrawal_reversal: (1, 0),
}
# Entries that spend money, so they need it to be available.
_NEEDS_AVAILABLE = {E.withdrawal_hold, E.deposit_reversal}


def new_wallet(psp: Psp) -> PspWallet:
    """Attach an empty wallet to a PSP being created."""
    psp.wallet = PspWallet(currency="INR", balance=Decimal("0.00"), held=Decimal("0.00"))
    return psp.wallet


def lock_wallet(db: Session, psp_id: int | None) -> PspWallet:
    """The PSP's wallet, locked until the caller commits. Created if the PSP has none yet."""
    if psp_id is None:
        raise AppError(ErrorCode.INSUFFICIENT_BALANCE, "This request has no PSP, so it has no wallet")
    query = select(PspWallet).where(PspWallet.psp_id == psp_id).with_for_update().execution_options(
        populate_existing=True
    )
    wallet = db.scalar(query)
    if wallet is None:
        db.add(PspWallet(psp_id=psp_id, currency="INR", balance=Decimal("0.00"), held=Decimal("0.00")))
        db.flush()
        wallet = db.scalar(query)
    return wallet


def wallet_figures(wallet: PspWallet) -> dict[str, str]:
    return {
        "balance": str(wallet.balance),
        "held": str(wallet.held),
        "available_balance": str(wallet.available_balance),
    }


def post(
    db: Session,
    wallet: PspWallet,
    entry_type: E,
    tx: Deposit | Withdrawal,
    *,
    actor_type: str,
    actor_id: str | int | None,
    note: str | None = None,
) -> PspWalletTransaction:
    """Apply one movement to a locked wallet and record it in the ledger."""
    amount = tx.amount
    if entry_type in _NEEDS_AVAILABLE and amount > wallet.available_balance:
        what = "withdrawal" if entry_type == E.withdrawal_hold else "reversal of this deposit"
        hint = "" if entry_type == E.withdrawal_hold else "; reject pending withdrawals first"
        raise AppError(
            ErrorCode.INSUFFICIENT_BALANCE,
            f"{tx.public_id}: {what} of {amount} INR is more than the available balance of "
            f"{wallet.available_balance} INR{hint}",
            details=wallet_figures(wallet),
        )

    d_balance, d_held = _EFFECT[entry_type]
    entry = PspWalletTransaction(
        wallet_id=wallet.id,
        entry_type=entry_type,
        amount=amount,
        balance_before=wallet.balance,
        held_before=wallet.held,
        balance_after=wallet.balance + d_balance * amount,
        held_after=wallet.held + d_held * amount,
        deposit_id=tx.id if isinstance(tx, Deposit) else None,
        withdrawal_id=tx.id if isinstance(tx, Withdrawal) else None,
        reference=tx.public_id,
        actor_type=actor_type,
        actor_id=str(actor_id) if actor_id is not None else None,
        note=note,
    )
    wallet.balance, wallet.held = entry.balance_after, entry.held_after
    db.add(entry)
    return entry


def entry_for_status_change(tx: Deposit | Withdrawal, old: TxStatus, new: TxStatus) -> E | None:
    """The wallet entry a review decision causes, if any."""
    if isinstance(tx, Deposit):
        return {TxStatus.approved: E.deposit_credit, TxStatus.reversed: E.deposit_reversal}.get(new)
    if new == TxStatus.approved:
        return E.withdrawal_debit
    if new == TxStatus.rejected:
        return E.withdrawal_release
    if new == TxStatus.reversed:
        return E.withdrawal_reversal
    return None  # pending -> processing: the money stays held


def post_status_change(
    db: Session, tx: Deposit | Withdrawal, old: TxStatus, new: TxStatus, user: PortalUser, note: str | None
) -> None:
    entry_type = entry_for_status_change(tx, old, new)
    if entry_type is None:
        return
    if tx.psp_id is None and entry_type == E.withdrawal_release:
        return  # its PSP (and wallet) was deleted; rejecting it must still work
    wallet = lock_wallet(db, tx.psp_id)
    post(db, wallet, entry_type, tx, actor_type="user", actor_id=user.id, note=note)
