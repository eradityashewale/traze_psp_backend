"""Shared create / review logic for deposits and withdrawals."""

from datetime import datetime, timezone
from typing import Any

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models import (
    FINAL_STATUSES,
    CreatedBy,
    Deposit,
    PaymentMethod,
    PortalUser,
    Psp,
    PspPaymentMethod,
    PspStatus,
    TxStatus,
    UserRole,
    Withdrawal,
)
from app.services.callback import schedule_callback
from app.services.storage import upload_screenshot

Transaction = Deposit | Withdrawal


def ensure_psp_can_accept(psp: Psp) -> None:
    if psp.status != PspStatus.active:
        raise AppError(ErrorCode.PSP_INACTIVE)


def deposit_method(psp: Psp, method: PaymentMethod, bank_account_id: str | None) -> PspPaymentMethod:
    """The PSP's details for the method the customer paid with.

    The method must be fully set up and active for this PSP, and bank_account_id (when given) must be that
    method's account: the account number for imps / rtgs, the UPI ID for upi.
    """
    row = psp.get_method(method)
    if row is None or not row.is_complete:
        raise AppError(ErrorCode.PAYMENT_METHOD_NOT_AVAILABLE, f"This PSP does not accept {method.value} payments")
    if not row.is_active:
        raise AppError(
            ErrorCode.PAYMENT_METHOD_NOT_AVAILABLE, f"This PSP's {method.value} payments are currently inactive"
        )
    if bank_account_id is not None and bank_account_id != row.account_id:
        raise AppError(
            ErrorCode.BANK_ACCOUNT_NOT_ALLOWED,
            f"{bank_account_id} is not this PSP's {method.value} account",
        )
    return row


def ensure_source_account(psp: Psp, account_id: str) -> None:
    """A withdrawal's source_account_id must be one of the PSP's payment method accounts."""
    if account_id not in {m.account_id for m in psp.ready_methods}:
        raise AppError(ErrorCode.BANK_ACCOUNT_NOT_ALLOWED, f"Bank account {account_id} is not managed by this PSP")


def default_source_account(psp: Psp) -> str:
    """Account an admin-submitted withdrawal is paid from: IMPS first, then RTGS, then UPI."""
    ready = psp.ready_methods
    return ready[0].account_id if ready else ""


def create_transaction(
    db: Session,
    model: type[Transaction],
    psp: Psp,
    data: dict[str, Any],
    created_by: CreatedBy,
    screenshot: UploadFile | None = None,
) -> Transaction:
    """Insert a new pending transaction.

    created_by records who submitted it: the CRM through the API, or an admin from the portal.
    An attached screenshot is stored in S3 and its key saved as screenshot_url.
    """
    if screenshot is not None:
        folder = "deposit" if model is Deposit else "withdrawal"
        data = {**data, "screenshot_url": upload_screenshot(screenshot, folder)}
    tx = model(**data, psp_id=psp.id, status=TxStatus.pending, created_by=created_by)
    db.add(tx)
    db.flush()
    tx.public_id = f"{model.PREFIX}-{tx.id:05d}"
    db.commit()
    db.refresh(tx)
    return tx


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
    """Move a transaction to processing / approved / rejected / reversed.

    The row is locked so two people can never both decide the same request.
    A final decision also queues the CRM callback in the same DB transaction.
    """
    # A PSP login can only act on its own PSP's requests; others look like "not found". An admin can act on any.
    query = select(model).where(model.public_id == public_id)
    if user.role == UserRole.psp:
        query = query.where(model.psp_id == user.psp_id)
    tx = db.scalar(query.with_for_update())
    if tx is None:
        raise AppError(ErrorCode.TX_NOT_FOUND, f"{public_id} not found")
    if new_status == TxStatus.reversed:
        # Only an approved request can be reversed, and reversed is final, so it happens at most once.
        if tx.status != TxStatus.approved:
            raise AppError(
                ErrorCode.TX_INVALID_TRANSITION,
                f"{public_id} is {tx.status.value}; only an approved request can be reversed, and only once",
            )
    elif tx.status in FINAL_STATUSES:
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
