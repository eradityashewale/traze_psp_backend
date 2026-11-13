"""Deposit module (guide Sections 5 and 8)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status

from app.deps import CallingPsp, DbSession, VerifiedPsp
from app.errors import AppError, ErrorCode
from app.models import CreatedBy, Deposit, TxKind
from app.routers.chats import build_chat_router
from app.routers.review import build_review_router
from app.schemas import (
    SIGNING_FIELDS,
    AdminDepositCreate,
    DepositCreate,
    DepositOut,
    DepositStatusOut,
    DepositSubmitted,
    PaymentDetailsOut,
    PaymentMethodDetailsOut,
)
from app.services.audit import audit
from app.services.signature import verify_request_signature
from app.services.transactions import create_transaction, deposit_method, ensure_psp_can_accept, get_for_psp

router = APIRouter(prefix="/api/v1/deposits", tags=["CRM · Deposits"])
review_router = build_review_router(Deposit, DepositOut, AdminDepositCreate, "deposit")
chat_router = build_chat_router(Deposit, TxKind.deposit)


@router.post("", response_model=DepositSubmitted, status_code=status.HTTP_201_CREATED)
def submit_deposit(
    body: Annotated[DepositCreate, Form(media_type="multipart/form-data")],
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
            "bank_account_id": body.bank_account_id,
            "customer_email": body.customer_email,
        },
        body,
    )
    ensure_psp_can_accept(psp)
    deposit_method(psp, body.payment_method, body.bank_account_id)

    data = body.model_dump(exclude=SIGNING_FIELDS | {"screenshot"})
    tx = create_transaction(db, Deposit, psp, data, CreatedBy.crm, body.screenshot)
    audit(db, "deposit.submitted", actor_type="psp", actor_id=psp.psp_code, target=tx.public_id, request=request)
    return DepositSubmitted(
        deposit_id=tx.public_id,
        status=tx.status,
        message="Deposit received and queued for review",
    )


@router.get("/payment-details", response_model=PaymentDetailsOut)
def get_payment_details(psp: CallingPsp, response: Response):
    """Where the customer pays, shown by the CRM before a deposit: one entry per payment method
    (imps / rtgs bank account, upi ID and QR code) the PSP takes.

    Call it from the CRM backend each time a customer opens the deposit screen; do not cache it,
    because an admin can change the details at any time. The QR link expires after a few minutes.
    """
    ensure_psp_can_accept(psp)
    methods = psp.ready_methods
    if not methods:
        raise AppError(ErrorCode.PSP_NO_BANK_DETAILS)
    response.headers["Cache-Control"] = "no-store"
    return PaymentDetailsOut(
        psp_code=psp.psp_code,
        psp_name=psp.psp_name,
        payment_methods=[m.method for m in methods],
        methods=[PaymentMethodDetailsOut.model_validate(m) for m in methods],
        updated_at=max([psp.updated_at, *(m.updated_at for m in methods)]),
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
