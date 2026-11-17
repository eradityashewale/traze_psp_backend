"""Withdrawal module (guide Sections 6 and 8)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status

from app.deps import CallingPsp, DbSession, VerifiedPsp
from app.models import CreatedBy, TxKind, Withdrawal
from app.routers.chats import build_chat_router
from app.routers.review import build_psp_submit_router, build_review_router
from app.schemas import (
    SIGNING_FIELDS,
    AdminWithdrawalCreate,
    PspWithdrawalCreate,
    WithdrawalCreate,
    WithdrawalOut,
    WithdrawalStatusOut,
    WithdrawalSubmitted,
)
from app.services.audit import audit
from app.services.signature import verify_request_signature
from app.services.transactions import create_transaction, default_source_account, ensure_psp_can_accept, get_for_psp

router = APIRouter(prefix="/api/v1/withdrawals", tags=["CRM · Withdrawals"])
review_router = build_review_router(Withdrawal, WithdrawalOut, AdminWithdrawalCreate, "withdrawal", "source_account_id")
psp_submit_router = build_psp_submit_router(
    Withdrawal, WithdrawalOut, PspWithdrawalCreate, "withdrawal", "source_account_id"
)
chat_router = build_chat_router(Withdrawal, TxKind.withdrawal)


@router.post("", response_model=WithdrawalSubmitted, status_code=status.HTTP_201_CREATED)
def submit_withdrawal(
    body: Annotated[WithdrawalCreate, Form(media_type="multipart/form-data")],
    db: DbSession,
    psp: VerifiedPsp,
    request: Request,
):
    verify_request_signature(
        db,
        request,
        psp,
        {
            "amount": body.amount,
            "currency": body.currency,
            "customer_email": body.customer_email,
            "dest_account_number": body.dest_account_number,
        },
        body,
    )
    ensure_psp_can_accept(psp)

    data = body.model_dump(exclude=SIGNING_FIELDS | {"screenshot"})
    # The CRM does not send a source account; the PSP's first payment method account is used.
    data["source_account_id"] = default_source_account(psp)
    tx = create_transaction(db, Withdrawal, psp, data, CreatedBy.crm, body.screenshot)
    audit(db, "withdrawal.submitted", actor_type="psp", actor_id=psp.psp_code, target=tx.public_id, request=request)
    return WithdrawalSubmitted(
        withdrawal_id=tx.public_id,
        status=tx.status,
        message="Withdrawal received and queued for review",
    )


@router.get("/{withdrawal_id}", response_model=WithdrawalStatusOut)
def get_withdrawal_status(withdrawal_id: str, db: DbSession, psp: CallingPsp):
    tx = get_for_psp(db, Withdrawal, psp, withdrawal_id)
    return WithdrawalStatusOut(
        withdrawal_id=tx.public_id,
        status=tx.status,
        amount=tx.amount,
        currency=tx.currency,
        customer_email=tx.customer_email,
        comment=tx.review_comment,
        reviewed_at=tx.reviewed_at,
        created_at=tx.created_at,
    )
