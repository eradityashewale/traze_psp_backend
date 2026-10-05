"""Chat about one deposit or withdrawal, between the admins and that PSP's logins.

Used by portal logins (JWT). Each request has at most one chat:
* either side starts it by sending the first message, and both sides reply;
* a PSP login only reaches the chats of its own PSP's requests, an admin reaches all of them;
* an admin closes the chat once the issue is settled, and can reopen it.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.deps import AdminUser, CurrentUser, DbSession
from app.errors import AppError, ErrorCode
from app.models import Chat, ChatMessage, ChatStatus, PortalUser, TxKind, UserRole
from app.schemas import ChatMessageCreate, ChatMessageOut, ChatOut, ChatSummaryOut, Page
from app.services.audit import audit
from app.services.transactions import Transaction

router = APIRouter(prefix="/api/v1/portal/chats", tags=["Portal · Chat"])


def _read_marker(user: PortalUser) -> str:
    """The Chat column holding the last message id this user's side has seen."""
    return "admin_last_read_id" if user.role == UserRole.admin else "psp_last_read_id"


@router.get("", response_model=Page[ChatSummaryOut])
def list_chats(
    db: DbSession,
    user: CurrentUser,
    status_: ChatStatus | None = Query(default=None, alias="status"),
    kind: TxKind | None = None,
    unread: bool | None = Query(default=None, description="true = only chats with messages you have not opened"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Inbox: the chats you can see, latest activity first, each with its unread count."""
    unread_count = (
        select(func.count())
        .where(
            ChatMessage.chat_id == Chat.id,
            ChatMessage.id > getattr(Chat, _read_marker(user)),
            ChatMessage.sender_role != user.role,
        )
        .correlate(Chat)
        .scalar_subquery()
    )
    query = select(Chat, unread_count)
    if user.role == UserRole.psp:
        query = query.where(Chat.psp_id == user.psp_id)
    if status_:
        query = query.where(Chat.status == status_)
    if kind:
        query = query.where(Chat.kind == kind)
    if unread:
        query = query.where(unread_count > 0)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Chat.last_message_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for chat, count in rows:
        chat.unread_count = count
        items.append(chat)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def build_chat_router(model: type[Transaction], kind: TxKind) -> APIRouter:
    chat_router = APIRouter(
        prefix=f"/api/v1/portal/{kind.value}s/{{public_id}}/chat", tags=["Portal · Chat"]
    )

    def _get_tx(db, public_id: str, user: PortalUser, lock: bool = False) -> Transaction:
        # A PSP login only reaches its own PSP's requests; others look like "not found".
        query = select(model).where(model.public_id == public_id)
        if user.role == UserRole.psp:
            query = query.where(model.psp_id == user.psp_id)
        # Writers lock the request row, so two first messages cannot both create the chat
        # and a message cannot slip into a chat that is being closed.
        tx = db.scalar(query.with_for_update() if lock else query)
        if tx is None:
            raise AppError(ErrorCode.TX_NOT_FOUND, f"{public_id} not found")
        return tx

    def _get_chat(db, public_id: str) -> Chat | None:
        return db.scalar(select(Chat).where(Chat.transaction_id == public_id))

    @chat_router.get("", response_model=ChatOut)
    def get_chat(
        public_id: str,
        db: DbSession,
        user: CurrentUser,
        after_id: int = Query(default=0, ge=0, description="Only messages newer than this id, for polling"),
    ):
        """The chat and its messages, oldest first. Opening it marks the messages as read for your side."""
        tx = _get_tx(db, public_id, user)
        chat = _get_chat(db, public_id)
        if chat is None:
            return ChatOut(transaction_id=public_id, kind=kind, psp_code=tx.psp_code)
        messages = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id, ChatMessage.id > after_id)
            .order_by(ChatMessage.id)
        ).all()
        marker = _read_marker(user)
        if messages and messages[-1].id > getattr(chat, marker):
            setattr(chat, marker, messages[-1].id)
            db.commit()
        chat.messages = messages
        return chat

    @chat_router.post("/messages", response_model=ChatMessageOut, status_code=status.HTTP_201_CREATED)
    def send_message(public_id: str, body: ChatMessageCreate, db: DbSession, user: CurrentUser, request: Request):
        """Send a message. The first message on a request opens its chat."""
        tx = _get_tx(db, public_id, user, lock=True)
        chat = _get_chat(db, public_id)
        if chat is None:
            chat = Chat(kind=kind, transaction_id=public_id, psp_id=tx.psp_id, opened_by_id=user.id)
            db.add(chat)
            db.flush()
            audit(db, "chat.opened", actor_type="user", actor_id=user.id, target=public_id,
                  request=request, commit=False)
        elif chat.status == ChatStatus.closed:
            raise AppError(ErrorCode.CHAT_CLOSED, f"The chat for {public_id} is closed; an admin can reopen it")

        message = ChatMessage(
            chat_id=chat.id, sender_id=user.id, sender_name=user.full_name, sender_role=user.role,
            message=body.message,
        )
        db.add(message)
        db.flush()
        chat.last_message_at = datetime.now(timezone.utc)
        setattr(chat, _read_marker(user), message.id)  # the sender has seen everything up to their own message
        db.commit()
        db.refresh(message)
        return message

    @chat_router.post("/close", response_model=ChatSummaryOut)
    def close_chat(public_id: str, db: DbSession, user: AdminUser, request: Request):
        """Admin: close the chat once the issue is settled. No more messages can be sent."""
        _get_tx(db, public_id, user, lock=True)
        chat = _get_chat(db, public_id)
        if chat is None:
            raise AppError(ErrorCode.CHAT_NOT_FOUND, f"{public_id} has no chat yet")
        if chat.status == ChatStatus.closed:
            raise AppError(ErrorCode.CHAT_CLOSED, f"The chat for {public_id} is already closed")
        chat.status = ChatStatus.closed
        chat.closed_by_id = user.id
        chat.closed_at = datetime.now(timezone.utc)
        audit(db, "chat.closed", actor_type="user", actor_id=user.id, target=public_id, request=request)
        db.refresh(chat)
        return chat

    @chat_router.post("/reopen", response_model=ChatSummaryOut)
    def reopen_chat(public_id: str, db: DbSession, user: AdminUser, request: Request):
        """Admin: reopen a closed chat, e.g. when the same issue comes back."""
        _get_tx(db, public_id, user, lock=True)
        chat = _get_chat(db, public_id)
        if chat is None:
            raise AppError(ErrorCode.CHAT_NOT_FOUND, f"{public_id} has no chat yet")
        if chat.status == ChatStatus.closed:
            chat.status = ChatStatus.open
            chat.closed_by_id = None
            chat.closed_at = None
            audit(db, "chat.reopened", actor_type="user", actor_id=user.id, target=public_id, request=request)
            db.refresh(chat)
        return chat

    return chat_router
