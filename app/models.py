import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.db_types import BinaryString, UTCDateTime, now6


class UserRole(str, enum.Enum):
    admin = "admin"  # manages PSPs, views and decides every PSP's requests
    psp = "psp"  # a PSP login: reviews and decides its own PSP's requests


class PspStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class TxStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    approved = "approved"
    rejected = "rejected"
    reversed = "reversed"  # an approved request that was taken back; can happen once


class CreatedBy(str, enum.Enum):
    admin = "admin"  # submitted by an admin from the portal
    crm = "crm"  # submitted by the CRM through the API


class TxKind(str, enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"


class ChatStatus(str, enum.Enum):
    open = "open"
    closed = "closed"  # settled; no new messages until an admin reopens it


FINAL_STATUSES = {TxStatus.approved, TxStatus.rejected, TxStatus.reversed}
OPEN_STATUSES = {TxStatus.pending, TxStatus.processing}


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class PortalUser(Base):
    """A portal login: either an admin, or a login belonging to one PSP."""

    __tablename__ = "portal_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.psp)
    # Set for role=psp. Deleting the PSP deletes its logins.
    psp_id: Mapped[int | None] = mapped_column(ForeignKey("psps.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())

    psp: Mapped["Psp | None"] = relationship(lazy="selectin")

    @property
    def psp_code(self) -> str | None:
        return self.psp.psp_code if self.psp else None


class Psp(Base):
    __tablename__ = "psps"

    id: Mapped[int] = mapped_column(primary_key=True)
    psp_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    psp_name: Mapped[str] = mapped_column(String(200))

    # Token and secret are stored as SHA-256 hashes; plaintext is shown only once.
    api_token_hash: Mapped[str] = mapped_column(BinaryString(64), unique=True, index=True)
    api_secret_hash: Mapped[str] = mapped_column(BinaryString(64))
    api_token_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    credentials_rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Previous credentials stay valid during the rotation grace period.
    prev_api_token_hash: Mapped[str | None] = mapped_column(BinaryString(64), index=True)
    prev_api_secret_hash: Mapped[str | None] = mapped_column(BinaryString(64))
    prev_valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # The salt has to be readable to compute MD5 signatures.
    signature_salt: Mapped[str] = mapped_column(BinaryString(128))


    status: Mapped[PspStatus] = mapped_column(_enum(PspStatus, "psp_status"), default=PspStatus.active)
    ifsc_code: Mapped[str | None] = mapped_column(String(11))
    account_number: Mapped[str | None] = mapped_column(String(34))  # the PSP's one bank account
    contact_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=now6(), onupdate=now6()
    )


class TransactionMixin:
    """Columns shared by deposits and withdrawals."""

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    psp_id: Mapped[int | None] = mapped_column(ForeignKey("psps.id", ondelete="SET NULL"), index=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_email: Mapped[str] = mapped_column(String(255), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    comment: Mapped[str | None] = mapped_column(Text)  # note from the customer / CRM
    idempotency_key: Mapped[str | None] = mapped_column(BinaryString(100))
    created_by: Mapped[CreatedBy] = mapped_column(
        _enum(CreatedBy, "tx_created_by"), default=CreatedBy.crm, server_default=CreatedBy.crm.value
    )

    status: Mapped[TxStatus] = mapped_column(
        _enum(TxStatus, "tx_status"), default=TxStatus.pending, index=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text)  # approval / rejection / reversal reason
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("portal_users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Callback outbox: the worker delivers any row whose callback_due_at has passed.
    # Stored in the DB so pending callbacks survive restarts and DR failover.
    callback_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    callback_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    callback_last_error: Mapped[str | None] = mapped_column(Text)
    callback_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=now6(), onupdate=now6()
    )

    @property
    def psp_code(self) -> str | None:
        return self.psp.psp_code if self.psp else None

    @property
    def reviewed_by(self) -> str | None:
        return self.reviewer.full_name if self.reviewer else None


class Deposit(TransactionMixin, Base):
    __tablename__ = "deposits"
    __table_args__ = (UniqueConstraint("psp_id", "idempotency_key", name="uq_deposit_idempotency"),)
    PREFIX = "DEP"

    bank_account_id: Mapped[str | None] = mapped_column(String(50))  # not set on admin-submitted deposits
    screenshot_url: Mapped[str] = mapped_column(String(1000))  # S3 key of the uploaded screenshot
    utr_number: Mapped[str | None] = mapped_column(String(100), index=True)

    psp: Mapped[Psp | None] = relationship(lazy="selectin")
    reviewer: Mapped[PortalUser | None] = relationship(lazy="selectin")


class Withdrawal(TransactionMixin, Base):
    __tablename__ = "withdrawals"
    __table_args__ = (UniqueConstraint("psp_id", "idempotency_key", name="uq_withdrawal_idempotency"),)
    PREFIX = "WDL"

    # destination details are optional on admin-submitted withdrawals
    dest_bank_name: Mapped[str | None] = mapped_column(String(200))
    dest_account_number: Mapped[str | None] = mapped_column(String(50))
    dest_ifsc: Mapped[str | None] = mapped_column(String(11))
    dest_account_name: Mapped[str | None] = mapped_column(String(200))
    source_account_id: Mapped[str] = mapped_column(String(50))
    screenshot_url: Mapped[str | None] = mapped_column(String(1000))  # S3 key, when one was attached

    psp: Mapped[Psp | None] = relationship(lazy="selectin")
    reviewer: Mapped[PortalUser | None] = relationship(lazy="selectin")


class Chat(Base):
    """The conversation about one deposit or withdrawal, between the admins and that PSP's logins.

    A request has at most one chat. It is created by the first message and closed by an admin.
    """

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[TxKind] = mapped_column(_enum(TxKind, "tx_kind"))
    # public_id of the deposit or withdrawal (DEP-... / WDL-...), so it is unique across both.
    transaction_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    psp_id: Mapped[int | None] = mapped_column(ForeignKey("psps.id", ondelete="SET NULL"), index=True)
    status: Mapped[ChatStatus] = mapped_column(_enum(ChatStatus, "chat_status"), default=ChatStatus.open, index=True)
    opened_by_id: Mapped[int | None] = mapped_column(ForeignKey("portal_users.id", ondelete="SET NULL"))
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("portal_users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Read markers are kept per side, not per login: the id of the last message that side has seen.
    admin_last_read_id: Mapped[int] = mapped_column(Integer, default=0)
    psp_last_read_id: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())

    psp: Mapped[Psp | None] = relationship(lazy="selectin")
    opener: Mapped[PortalUser | None] = relationship(lazy="selectin", foreign_keys=[opened_by_id])
    closer: Mapped[PortalUser | None] = relationship(lazy="selectin", foreign_keys=[closed_by_id])

    @property
    def psp_code(self) -> str | None:
        return self.psp.psp_code if self.psp else None

    @property
    def opened_by(self) -> str | None:
        return self.opener.full_name if self.opener else None

    @property
    def closed_by(self) -> str | None:
        return self.closer.full_name if self.closer else None


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("portal_users.id", ondelete="SET NULL"))
    # Name and role are copied in so the history stays readable after a login is removed.
    sender_name: Mapped[str] = mapped_column(String(200))
    sender_role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())


class AuditLog(Base):
    """Append-only trail of security-relevant actions (PCI DSS requirement 10)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | psp | system
    actor_id: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str | None] = mapped_column(String(100), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6(), index=True)
