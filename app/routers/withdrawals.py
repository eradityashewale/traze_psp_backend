"""Withdrawal module (guide Sections 6 and 8)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status

from app.deps import CallingPsp, DbSession, VerifiedPsp
from app.models import Withdrawal
from app.routers.review import build_review_router
from app.schemas import (
    AdminWithdrawalCreate,
    WithdrawalCreate,
    WithdrawalOut,
    WithdrawalStatusOut,
    WithdrawalSubmitted,
)
from app.services.audit import audit
from app.services.signature import verify_request_signature
from app.services.transactions import create_transaction, ensure_psp_can_accept, get_for_psp

router = APIRouter(prefix="/api/v1/withdrawals", tags=["CRM · Withdrawals"])
review_router = build_review_router(Withdrawal, WithdrawalOut, AdminWithdrawalCreate, "withdrawal", "source_account_id")


@router.post("", response_model=WithdrawalSubmitted, status_code=status.HTTP_201_CREATED)
def submit_withdrawal(
    body: Annotated[WithdrawalCreate, Form(media_type="multipart/form-data")],
    db: DbSession,
    psp: VerifiedPsp,
    response: Response,
    request: Request,
):
    verify_request_signature(
        psp,
        {
            "amount": body.amount,
            "currency": body.currency,
            "customer_email": body.customer_email,
            "dest_account_number": body.dest_account_number,
            "source_account_id": body.source_account_id,
        },
        body.timestamp,
        body.signature,
    )
    ensure_psp_can_accept(psp, body.source_account_id)

    data = body.model_dump(exclude={"timestamp", "signature", "screenshot"})
    tx, created = create_transaction(db, Withdrawal, psp, data, body.screenshot)
    if created:
        audit(db, "withdrawal.submitted", actor_type="psp", actor_id=psp.psp_code, target=tx.public_id, request=request)
    else:
        response.status_code = status.HTTP_200_OK
    return WithdrawalSubmitted(
        withdrawal_id=tx.public_id,
        status=tx.status,
        message="Withdrawal received and queued for review" if created else "Duplicate idempotency_key; returning existing withdrawal",
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
