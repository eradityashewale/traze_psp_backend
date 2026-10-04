"""Portal-side review endpoints, shared by deposits and withdrawals.

Used by portal logins (JWT), not by the CRM:
* a PSP login sees only its own PSP's requests and is the one who approves / rejects them;
* an admin can view every PSP's requests, submit new ones for a PSP and re-send callbacks,
  but does not approve / reject.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Query, Request, status
from sqlalchemy import func, or_, select

from app.deps import AdminUser, CurrentUser, DbSession, PspUser
from app.errors import AppError, ErrorCode
from app.models import FINAL_STATUSES, PortalUser, Psp, TxStatus, UserRole
from app.schemas import ApproveRequest, Page, RejectRequest
from app.services.audit import audit
from app.services.callback import deliver_due, schedule_callback
from app.services.transactions import Transaction, change_status, create_transaction, ensure_psp_can_accept


def build_review_router(
    model: type[Transaction], out_schema: type, create_schema: type, kind: str, account_field: str | None = None
) -> APIRouter:
    router = APIRouter(prefix=f"/api/v1/portal/{kind}s", tags=[f"Portal · {kind.title()} review"])

    def _scoped(query, user: PortalUser):
        return query.where(model.psp_id == user.psp_id) if user.role == UserRole.psp else query

    def _get(db, public_id: str, user: PortalUser) -> Transaction:
        tx = db.scalar(_scoped(select(model).where(model.public_id == public_id), user))
        if tx is None:
            raise AppError(ErrorCode.TX_NOT_FOUND, f"{public_id} not found")
        return tx

    @router.get("", response_model=Page[out_schema])
    def list_items(
        db: DbSession,
        user: CurrentUser,
        status_: TxStatus | None = Query(default=None, alias="status"),
        psp_code: str | None = Query(default=None, description="Admin only; PSP logins always see their own"),
        currency: str | None = None,
        customer: str | None = Query(default=None, description="Search by customer name or email"),
        callback_failed: bool | None = Query(default=None, description="true = decided but CRM not notified"),
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        query = _scoped(select(model), user)
        if status_:
            query = query.where(model.status == status_)
        if psp_code and user.role == UserRole.admin:
            query = query.join(Psp, model.psp_id == Psp.id).where(Psp.psp_code == psp_code)
        if currency:
            query = query.where(model.currency == currency.upper())
        if customer:
            pattern = f"%{customer}%"
            query = query.where(or_(model.customer_name.ilike(pattern), model.customer_email.ilike(pattern)))
        if callback_failed:
            query = query.where(
                model.status.in_(FINAL_STATUSES), model.callback_sent.is_(False), model.callback_due_at.is_(None)
            )
        if date_from:
            query = query.where(model.created_at >= datetime.combine(date_from, time.min, timezone.utc))
        if date_to:
            end = datetime.combine(date_to + timedelta(days=1), time.min, timezone.utc)
            query = query.where(model.created_at < end)

        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.scalars(query.order_by(model.created_at.desc()).limit(limit).offset(offset)).all()
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.post("", response_model=out_schema, status_code=status.HTTP_201_CREATED)
    def create_item(
        body: Annotated[create_schema, Form(media_type="multipart/form-data")],
        db: DbSession,
        user: AdminUser,
        request: Request,
    ):
        """Admin: submit a request from the portal for a PSP. That PSP's login then reviews it as usual."""
        psp = db.scalar(select(Psp).where(Psp.psp_code == body.psp_code))
        if psp is None:
            raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {body.psp_code} not found")
        ensure_psp_can_accept(psp, getattr(body, account_field) if account_field else None)

        data = body.model_dump(exclude={"psp_code", "screenshot"})
        tx, _ = create_transaction(db, model, psp, data, body.screenshot)
        audit(db, f"{kind}.submitted", actor_type="user", actor_id=user.id, target=tx.public_id,
              details={"psp_code": psp.psp_code, "source": "admin_portal"}, request=request)
        return tx

    @router.get("/{public_id}", response_model=out_schema)
    def get_item(public_id: str, db: DbSession, user: CurrentUser):
        return _get(db, public_id, user)

    @router.post("/{public_id}/processing", response_model=out_schema)
    def mark_processing(public_id: str, db: DbSession, user: PspUser, request: Request):
        """PSP login: claim a pending request to show it is being handled."""
        tx = change_status(db, model, public_id, user, TxStatus.processing)
        audit(db, f"{kind}.processing", actor_type="user", actor_id=user.id, target=public_id, request=request)
        return tx

    @router.post("/{public_id}/approve", response_model=out_schema)
    def approve(
        public_id: str, body: ApproveRequest, db: DbSession, user: PspUser, bg: BackgroundTasks, request: Request
    ):
        """PSP login: approve its own PSP's request. The CRM is notified."""
        tx = change_status(db, model, public_id, user, TxStatus.approved, body.comment)
        audit(db, f"{kind}.approved", actor_type="user", actor_id=user.id, target=public_id,
              details={"comment": body.comment}, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return tx

    @router.post("/{public_id}/reject", response_model=out_schema)
    def reject(
        public_id: str, body: RejectRequest, db: DbSession, user: PspUser, bg: BackgroundTasks, request: Request
    ):
        """PSP login: reject its own PSP's request. The reason is sent to the CRM."""
        tx = change_status(db, model, public_id, user, TxStatus.rejected, body.reason)
        audit(db, f"{kind}.rejected", actor_type="user", actor_id=user.id, target=public_id,
              details={"reason": body.reason}, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return tx

    @router.post("/{public_id}/resend-callback", status_code=status.HTTP_202_ACCEPTED)
    def resend_callback(public_id: str, db: DbSession, user: CurrentUser, bg: BackgroundTasks, request: Request):
        """Re-send the decision to the CRM, e.g. after all automatic retries failed."""
        tx = _get(db, public_id, user)
        if tx.status not in FINAL_STATUSES:
            raise AppError(ErrorCode.TX_INVALID_TRANSITION, "Only approved or rejected requests have a callback")
        schedule_callback(tx)
        audit(db, f"{kind}.callback_resent", actor_type="user", actor_id=user.id, target=public_id, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return {"success": True, "message": f"Callback for {public_id} queued"}

    return router
