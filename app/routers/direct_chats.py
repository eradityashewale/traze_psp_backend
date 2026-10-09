"""Direct chat between the admins and one PSP's logins, not tied to a deposit or withdrawal.

Used by portal logins (JWT). Each PSP has one thread:
* an admin writes to any PSP, a PSP login writes to the admins and only reaches its own thread;
* a message carries text, an attachment of any file type, or both;
* the thread is never closed.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, Query, status
from sqlalchemy import case, func, select, update

from app.deps import CurrentUser, DbSession
from app.errors import AppError, ErrorCode
from app.models import DirectMessage, PortalUser, Psp, UserRole
from app.schemas import DirectChatOut, DirectChatSummaryOut, DirectMessageCreate, DirectMessageOut, Page
from app.services.storage import upload_attachment

router = APIRouter(prefix="/api/v1/portal/direct-chats", tags=["Portal · Direct chat"])


def _thread_psp(db, user: PortalUser, psp_code: str | None) -> Psp:
    """The PSP whose thread this call is about: a PSP login's own, or the one an admin names."""
    if user.role == UserRole.psp:
        return user.psp
    if not psp_code:
        raise AppError(ErrorCode.VALIDATION_ERROR, "psp_code is required for an admin")
    psp = db.scalar(select(Psp).where(Psp.psp_code == psp_code))
    if psp is None:
        raise AppError(ErrorCode.PSP_NOT_FOUND, f"PSP {psp_code} not found")
    return psp


@router.get("", response_model=Page[DirectChatSummaryOut])
def list_direct_chats(
    db: DbSession,
    user: CurrentUser,
    unread: bool | None = Query(default=None, description="true = only threads with messages you have not opened"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Inbox: an admin gets one thread per PSP, latest activity first; a PSP login gets its own thread."""
    is_unread = (DirectMessage.sender_role != user.role) & DirectMessage.read_at.is_(None)
    stats = (
        select(
            DirectMessage.psp_id,
            func.max(DirectMessage.id).label("last_id"),
            func.sum(case((is_unread, 1), else_=0)).label("unread"),
        )
        .group_by(DirectMessage.psp_id)
        .subquery()
    )
    query = (
        select(Psp, stats.c.unread, DirectMessage)
        .outerjoin(stats, stats.c.psp_id == Psp.id)
        .outerjoin(DirectMessage, DirectMessage.id == stats.c.last_id)
    )
    if user.role == UserRole.psp:
        query = query.where(Psp.id == user.psp_id)
    if unread:
        query = query.where(stats.c.unread > 0)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    # PSPs nobody has written to yet come last, by name.
    order = (stats.c.last_id.is_(None), stats.c.last_id.desc(), Psp.psp_name)
    rows = db.execute(query.order_by(*order).limit(limit).offset(offset)).all()
    items = [
        {"psp_code": psp.psp_code, "psp_name": psp.psp_name, "unread_count": int(count or 0), "last_message": last}
        for psp, count, last in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/messages", response_model=DirectChatOut)
def get_messages(
    db: DbSession,
    user: CurrentUser,
    psp_code: str | None = Query(default=None, description="Admin: the PSP's thread. PSP logins always get their own."),
    after_id: int = Query(default=0, ge=0, description="Only messages newer than this id, for polling"),
    before_id: int | None = Query(default=None, ge=1, description="Only messages older than this id, to load history"),
    limit: int = Query(default=100, ge=1, le=200),
):
    """A thread's messages, oldest first. Opening it marks the other side's messages as read."""
    psp = _thread_psp(db, user, psp_code)
    query = select(DirectMessage).where(DirectMessage.psp_id == psp.id, DirectMessage.id > after_id)
    if before_id:
        query = query.where(DirectMessage.id < before_id)
    if after_id:
        # Polling continues from after_id; otherwise the page is the latest messages.
        messages = db.scalars(query.order_by(DirectMessage.id).limit(limit)).all()
    else:
        messages = db.scalars(query.order_by(DirectMessage.id.desc()).limit(limit)).all()[::-1]

    if messages:
        marked = db.execute(
            update(DirectMessage)
            .where(
                DirectMessage.psp_id == psp.id,
                DirectMessage.id <= messages[-1].id,
                DirectMessage.sender_role != user.role,
                DirectMessage.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        if marked.rowcount:
            db.commit()
            # Load the page again so the response carries the new read_at values.
            ids = [m.id for m in messages]
            messages = db.scalars(
                select(DirectMessage)
                .where(DirectMessage.id.in_(ids))
                .order_by(DirectMessage.id)
                .execution_options(populate_existing=True)
            ).all()
    return {"psp_code": psp.psp_code, "psp_name": psp.psp_name, "messages": messages}


@router.post("/messages", response_model=DirectMessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    body: Annotated[DirectMessageCreate, Form(media_type="multipart/form-data")],
    db: DbSession,
    user: CurrentUser,
):
    """Send a message, an attachment, or both. An admin names the PSP; a PSP login writes to the admins."""
    psp = _thread_psp(db, user, body.psp_code)
    message = DirectMessage(
        psp_id=psp.id, sender_id=user.id, sender_name=user.full_name, sender_role=user.role, message=body.message
    )
    if body.attachment is not None:
        stored = upload_attachment(body.attachment, str(psp.id))
        message.attachment_key = stored["key"]
        message.attachment_name = stored["name"]
        message.attachment_content_type = stored["content_type"]
        message.attachment_size = stored["size"]
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
