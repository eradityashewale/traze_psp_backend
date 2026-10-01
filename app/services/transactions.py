"""Shared create / review logic for deposits and withdrawals."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models import FINAL_STATUSES, Deposit, PortalUser, Psp, PspStatus, TxStatus, Withdrawal
from app.services.callback import schedule_callback

Transaction = Deposit | Withdrawal


def ensure_psp_can_accept(psp: Psp, currency: str, bank_account_id: str) -> None:
    if psp.status != PspStatus.active:
        raise AppError(ErrorCode.PSP_INACTIVE)
    if currency not in psp.allowed_currencies:
        raise AppError(
            ErrorCode.CURRENCY_NOT_ALLOWED,
            f"Currency {currency} is not allowed for this PSP (allowed: {', '.join(psp.allowed_currencies)})",
        )
    if bank_account_id not in psp.bank_accounts:
        raise AppError(ErrorCode.BANK_ACCOUNT_NOT_ALLOWED, f"Bank account {bank_account_id} is not managed by this PSP")


def create_transaction(
    db: Session, model: type[Transaction], psp: Psp, data: dict[str, Any]
) -> tuple[Transaction, bool]:
    """Insert a new pending transaction. Returns (tx, created).

    If the idempotency_key was already used by this PSP, the existing record is
    returned with created=False instead of inserting a duplicate.
    """
    key = data.get("idempotency_key")
    if key:
        existing = _find_by_idempotency_key(db, model, psp.id, key)
        if existing:
            return existing, False

    tx = model(**data, psp_id=psp.id, status=TxStatus.pending)
    db.add(tx)
    try:
        db.flush()
        tx.public_id = f"{model.PREFIX}-{tx.id:05d}"
        db.commit()
    except IntegrityError:
        # Two identical requests raced each other; the other one won.
        db.rollback()
        existing = _find_by_idempotency_key(db, model, psp.id, key) if key else None
        if existing is None:
            raise
        return existing, False
    db.refresh(tx)
    return tx, True


def _find_by_idempotency_key(db: Session, model: type[Transaction], psp_id: int, key: str):
    return db.scalar(select(model).where(model.psp_id == psp_id, model.idempotency_key == key))


def get_for_psp(db: Session, model: type[Transaction], psp: Psp, public_id: str) -> Transaction:
    tx = db.scalar(select(model).where(model.public_id == public_id, model.psp_id == psp.id))
    if tx is None:
        raise AppError(ErrorCode.TX_NOT_FOUND, f"{public_id} not found")
    return tx


def change_status(
    db: Session,
    model: type[Transaction],
    public_id: str,
    user: PortalUser,
    new_status: TxStatus,
    comment: str | None = None,
) -> Transaction:
    """Move a transaction to processing / approved / rejected.

    The row is locked so two people can never both decide the same request.
    A final decision also queues the CRM callback in the same DB transaction.
    """
    # A PSP login can only act on its own PSP's requests; others look like "not found".
    tx = db.scalar(
        select(model).where(model.public_id == public_id, model.psp_id == user.psp_id).with_for_update()
    )
    if tx is None:
        raise AppError(ErrorCode.TX_NOT_FOUND, f"{public_id} not found")
    if tx.status in FINAL_STATUSES:
        raise AppError(ErrorCode.TX_ALREADY_FINAL, f"{public_id} is already {tx.status.value}")
    if new_status == TxStatus.processing and tx.status != TxStatus.pending:
        raise AppError(ErrorCode.TX_INVALID_TRANSITION, f"{public_id} is already {tx.status.value}")

    tx.status = new_status
    tx.reviewed_by_id = user.id
    if new_status in FINAL_STATUSES:
        tx.review_comment = comment
        tx.reviewed_at = datetime.now(timezone.utc)
        schedule_callback(tx)
    db.commit()
    db.refresh(tx)
    return tx
