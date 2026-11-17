"""Portal-side review endpoints, shared by deposits and withdrawals.

Used by portal logins (JWT), not by the CRM:
* a PSP login sees only its own PSP's requests and is the only one who decides them
  (processing, approve, reject, reverse);
* an admin can view every PSP's requests, submit new ones for a PSP and re-send callbacks,
  but cannot decide them;
* a sub admin can view and export every PSP's requests but cannot change any of them.
"""

import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request, Response, status
from sqlalchemy import func, or_, select

from app.deps import AdminUser, CurrentUser, DbSession, PspLogin, WriterUser
from app.errors import AppError, ErrorCode
from app.models import FINAL_STATUSES, CreatedBy, PortalUser, Psp, TxStatus, UserRole
from app.schemas import ApproveRequest, Page, RejectRequest, ReverseRequest
from app.services.audit import audit
from app.services.callback import deliver_due, schedule_callback
from app.services.transactions import (
    Transaction,
    change_status,
    create_transaction,
    default_source_account,
    deposit_method,
    ensure_psp_can_accept,
)


EXPORT_MAX_ROWS = 100_000


class TxFilters:
    """Filters shared by the list and the export endpoints."""

    def __init__(
        self,
        status_: TxStatus | None = Query(default=None, alias="status"),
        psp_code: str | None = Query(default=None, description="Admin / sub admin only; PSP logins always see their own"),
        currency: str | None = None,
        customer: str | None = Query(default=None, description="Search by customer name or email"),
        callback_failed: bool | None = Query(default=None, description="true = decided but CRM not notified"),
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        self.status = status_
        self.psp_code = psp_code
        self.currency = currency
        self.customer = customer
        self.callback_failed = callback_failed
        self.date_from = date_from
        self.date_to = date_to

    def as_dict(self) -> dict:
        return {k: _csv_cell(v) for k, v in vars(self).items() if v is not None}


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    text = str(value)
    # Stop spreadsheet apps from running customer-supplied text as a formula.
    if text and text[0] in "=+-@\t\r":
        text = "'" + text
    return text


def submit_from_portal(
    db, model: type[Transaction], kind: str, account_field: str | None, psp: Psp, body,
    user: PortalUser, created_by: CreatedBy, request: Request,
) -> Transaction:
    """Create a pending request from the portal (an admin for any PSP, or a PSP login for its own)."""
    ensure_psp_can_accept(psp)
    data = body.model_dump(exclude={"psp_code", "screenshot"})
    if kind == "deposit":
        # The PSP must take the method the customer paid with; record that method's account.
        data["bank_account_id"] = deposit_method(psp, body.payment_method, None).account_id
    if account_field:
        # Not picked in the portal; the PSP's first payment method account is used.
        data[account_field] = default_source_account(psp)
    tx = create_transaction(db, model, psp, data, created_by, body.screenshot, actor_id=user.id)
    source = "admin_portal" if created_by == CreatedBy.admin else "psp_portal"
    audit(db, f"{kind}.submitted", actor_type="user", actor_id=user.id, target=tx.public_id,
          details={"psp_code": psp.psp_code, "source": source}, request=request)
    return tx


def build_psp_submit_router(
    model: type[Transaction], out_schema: type, create_schema: type, kind: str, account_field: str | None = None
) -> APIRouter:
    """POST /api/v1/portal/psp/{kind}s: a PSP login submits a request for its own PSP."""
    router = APIRouter(prefix="/api/v1/portal/psp", tags=["Portal · PSP requests"])

    @router.post(f"/{kind}s", response_model=out_schema, status_code=status.HTTP_201_CREATED)
    def submit(
        body: Annotated[create_schema, Form(media_type="multipart/form-data")],
        db: DbSession,
        user: PspLogin,
        request: Request,
    ):
        tx = submit_from_portal(db, model, kind, account_field, user.psp, body, user, CreatedBy.psp, request)
        return tx

    submit.__doc__ = (
        f"PSP login: submit a {kind} for your own PSP (multipart/form-data). It starts as pending and is "
        "reviewed like any other request; created_by is psp."
        + (" The amount is held in the PSP wallet; E2011 if the available balance is too low."
           if kind == "withdrawal" else "")
    )
    return router


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

    def _filtered(user: PortalUser, f: "TxFilters"):
        query = _scoped(select(model), user)
        if f.status:
            query = query.where(model.status == f.status)
        if f.psp_code and user.role != UserRole.psp:
            query = query.join(Psp, model.psp_id == Psp.id).where(Psp.psp_code == f.psp_code)
        if f.currency:
            query = query.where(model.currency == f.currency.upper())
        if f.customer:
            pattern = f"%{f.customer}%"
            query = query.where(or_(model.customer_name.ilike(pattern), model.customer_email.ilike(pattern)))
        if f.callback_failed:
            query = query.where(
                model.status.in_(FINAL_STATUSES), model.callback_sent.is_(False), model.callback_due_at.is_(None)
            )
        if f.date_from:
            query = query.where(model.created_at >= datetime.combine(f.date_from, time.min, timezone.utc))
        if f.date_to:
            end = datetime.combine(f.date_to + timedelta(days=1), time.min, timezone.utc)
            query = query.where(model.created_at < end)
        return query

    @router.get("", response_model=Page[out_schema])
    def list_items(
        db: DbSession,
        user: CurrentUser,
        filters: Annotated[TxFilters, Depends()],
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        query = _filtered(user, filters)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = db.scalars(query.order_by(model.created_at.desc()).limit(limit).offset(offset)).all()
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # Same columns as the list response, minus the screenshot link (presigned links expire within minutes).
    export_fields = [
        (name, field.validation_alias if isinstance(field.validation_alias, str) else name)
        for name, field in out_schema.model_fields.items()
        if name != "screenshot_url"
    ]

    @router.get("/export", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
    def export_items(db: DbSession, user: CurrentUser, filters: Annotated[TxFilters, Depends()], request: Request):
        """Download every request matching the list filters as a CSV file (newest first, no paging).

        A PSP login gets only its own PSP's requests.
        """
        query = _filtered(user, filters)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        if total > EXPORT_MAX_ROWS:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"{total} rows match; narrow the filters (e.g. a date range) to at most {EXPORT_MAX_ROWS}",
            )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([name for name, _ in export_fields])
        for tx in db.scalars(query.order_by(model.created_at.desc())):
            writer.writerow([_csv_cell(getattr(tx, attr)) for _, attr in export_fields])

        audit(db, f"{kind}.exported", actor_type="user", actor_id=user.id,
              details={"rows": total, **filters.as_dict()}, request=request)
        filename = f"{kind}s_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
        # The BOM makes Excel read the file as UTF-8.
        return Response(
            content="\ufeff" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("", response_model=out_schema, status_code=status.HTTP_201_CREATED)
    def create_item(
        body: Annotated[create_schema, Form(media_type="multipart/form-data")],
        db: DbSession,
        user: AdminUser,
        request: Request,
    ):
        """Admin: submit a request from the portal for a PSP. That PSP's login then reviews it."""
        psp = db.scalar(select(Psp).where(Psp.psp_code == body.psp_code))
        if psp is None:
            raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {body.psp_code} not found")
        return submit_from_portal(db, model, kind, account_field, psp, body, user, CreatedBy.admin, request)

    @router.get("/{public_id}", response_model=out_schema)
    def get_item(public_id: str, db: DbSession, user: CurrentUser):
        return _get(db, public_id, user)

    @router.post("/{public_id}/processing", response_model=out_schema)
    def mark_processing(public_id: str, db: DbSession, user: PspLogin, request: Request):
        """PSP login: claim one of your pending requests to show it is being handled."""
        tx = change_status(db, model, public_id, user, TxStatus.processing)
        audit(db, f"{kind}.processing", actor_type="user", actor_id=user.id, target=public_id, request=request)
        return tx

    @router.post("/{public_id}/approve", response_model=out_schema)
    def approve(
        public_id: str, body: ApproveRequest, db: DbSession, user: PspLogin, bg: BackgroundTasks, request: Request
    ):
        """PSP login: approve one of your PSP's requests. The CRM is notified. Admins cannot decide requests."""
        tx = change_status(db, model, public_id, user, TxStatus.approved, body.comment)
        audit(db, f"{kind}.approved", actor_type="user", actor_id=user.id, target=public_id,
              details={"comment": body.comment}, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return tx

    @router.post("/{public_id}/reject", response_model=out_schema)
    def reject(
        public_id: str, body: RejectRequest, db: DbSession, user: PspLogin, bg: BackgroundTasks, request: Request
    ):
        """PSP login: reject one of your PSP's requests. The reason is sent to the CRM. Admins cannot decide requests."""
        tx = change_status(db, model, public_id, user, TxStatus.rejected, body.reason)
        audit(db, f"{kind}.rejected", actor_type="user", actor_id=user.id, target=public_id,
              details={"reason": body.reason}, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return tx

    @router.post("/{public_id}/reverse", response_model=out_schema)
    def reverse(
        public_id: str, body: ReverseRequest, db: DbSession, user: PspLogin, bg: BackgroundTasks, request: Request
    ):
        """PSP login: reverse one of your approved requests. Allowed once per request; the reason is sent to the CRM."""
        tx = change_status(db, model, public_id, user, TxStatus.reversed, body.reason)
        audit(db, f"{kind}.reversed", actor_type="user", actor_id=user.id, target=public_id,
              details={"reason": body.reason}, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return tx

    @router.post("/{public_id}/resend-callback", status_code=status.HTTP_202_ACCEPTED)
    def resend_callback(public_id: str, db: DbSession, user: WriterUser, bg: BackgroundTasks, request: Request):
        """Re-send the decision to the CRM, e.g. after all automatic retries failed."""
        tx = _get(db, public_id, user)
        if tx.status not in FINAL_STATUSES:
            raise AppError(ErrorCode.TX_INVALID_TRANSITION, "Only approved, rejected or reversed requests have a callback")
        schedule_callback(tx)
        audit(db, f"{kind}.callback_resent", actor_type="user", actor_id=user.id, target=public_id, request=request)
        bg.add_task(deliver_due, model, tx.id)
        return {"success": True, "message": f"Callback for {public_id} queued"}

    return router
