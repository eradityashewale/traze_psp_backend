"""Deposit module (guide Sections 5 and 8)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status

from app.deps import CallingPsp, DbSession, VerifiedPsp
from app.models import CreatedBy, Deposit
from app.routers.review import build_review_router
from app.schemas import AdminDepositCreate, DepositCreate, DepositOut, DepositStatusOut, DepositSubmitted
from app.services.audit import audit
from app.services.signature import verify_request_signature
from app.services.transactions import create_transaction, ensure_psp_can_accept, get_for_psp

router = APIRouter(prefix="/api/v1/deposits", tags=["CRM · Deposits"])
review_router = build_review_router(Deposit, DepositOut, AdminDepositCreate, "deposit")


@router.post("", response_model=DepositSubmitted, status_code=status.HTTP_201_CREATED)
def submit_deposit(
    body: Annotated[DepositCreate, Form(media_type="multipart/form-data")],
    db: DbSession,
    psp: VerifiedPsp,
    request: Request,
):
    verify_request_signature(
        psp,
        {
            "amount": body.amount,
            "bank_account_id": body.bank_account_id,
            "customer_email": body.customer_email,
        },
        body.signature,
    )
    ensure_psp_can_accept(psp, body.bank_account_id)

    data = body.model_dump(exclude={"signature", "screenshot"})
    tx = create_transaction(db, Deposit, psp, data, CreatedBy.crm, body.screenshot)
    audit(db, "deposit.submitted", actor_type="psp", actor_id=psp.psp_code, target=tx.public_id, request=request)
    return DepositSubmitted(
        deposit_id=tx.public_id,
        status=tx.status,
        message="Deposit received and queued for review",
    )


@router.get("/{deposit_id}", response_model=DepositStatusOut)
def get_deposit_status(deposit_id: str, db: DbSession, psp: CallingPsp):
    tx = get_for_psp(db, Deposit, psp, deposit_id)
    return DepositStatusOut(
        deposit_id=tx.public_id,
        status=tx.status,
        amount=tx.amount,
        currency=tx.currency,
        customer_email=tx.customer_email,
        comment=tx.review_comment,
        reviewed_at=tx.reviewed_at,
        created_at=tx.created_at,
    )
