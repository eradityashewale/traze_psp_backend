import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.db_types import BinaryString, UTCDateTime, now6


class UserRole(str, enum.Enum):
    admin = "admin"  # manages PSPs, views and decides every PSP's requests
    psp = "psp"  # a PSP login: reviews and decides its own PSP's requests
    sub_admin = "sub_admin"  # read-only admin: sees every PSP and request, changes nothing


class PspStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class TxStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    approved = "approved"
    rejected = "rejected"
    reversed = "reversed"  # an approved request that was taken back; can happen once


class PaymentMethod(str, enum.Enum):
    imps = "imps"
    rtgs = "rtgs"
    upi = "upi"

    @classmethod
    def _missing_(cls, value):
        # Accept any case and stray spaces: "IMPS", " Upi ".
        if isinstance(value, str):
            return cls.__members__.get(value.strip().lower())
        return None


class CreatedBy(str, enum.Enum):
    admin = "admin"  # submitted by an admin from the portal
    crm = "crm"  # submitted by the CRM through the API
    psp = "psp"  # submitted by a PSP login from the portal, for its own PSP


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
    """A portal login: an admin, a read-only sub admin, or a login belonging to one PSP."""

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
    # Copied into every access token; raising it signs out all of this login's sessions.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())

    psp: Mapped["Psp | None"] = relationship(lazy="selectin")

    @property
    def psp_code(self) -> str | None:
        return self.psp.psp_code if self.psp else None

    def revoke_sessions(self) -> None:
        self.token_version += 1


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
    # Set when the credentials are revoked; the PSP has no working token until the next rotation.
    credentials_revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # The salt is the HMAC key, so it has to be readable to compute signatures.
    signature_salt: Mapped[str] = mapped_column(BinaryString(128))


    status: Mapped[PspStatus] = mapped_column(_enum(PspStatus, "psp_status"), default=PspStatus.active)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=now6(), onupdate=now6()
    )

    # Read-only; loaded only when accessed, so CRM API auth does not pay for it.
    logins: Mapped[list["PortalUser"]] = relationship(
        viewonly=True, order_by="PortalUser.id", lazy="select"
    )

    @property
    def portal_email(self) -> str | None:
        """Email of the PSP's portal login (the first one if there are several)."""
        return self.logins[0].email if self.logins else None

    # IMPS, RTGS and UPI, each with its own details; at most one row per method.
    payment_methods: Mapped[list["PspPaymentMethod"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="PspPaymentMethod.method", lazy="select"
    )

    # One wallet per PSP; created with the PSP. Loaded only when accessed.
    wallet: Mapped["PspWallet | None"] = relationship(back_populates="psp", uselist=False, lazy="select")

    def get_method(self, method: PaymentMethod) -> "PspPaymentMethod | None":
        return next((m for m in self.payment_methods if m.method == method), None)

    @property
    def ready_methods(self) -> list["PspPaymentMethod"]:
        """Active methods with all their details filled in: the ones the PSP can take payments with."""
        return [m for m in self.payment_methods if m.is_ready]


# Details each payment method needs. IMPS and RTGS pay into a bank account. UPI needs no bank
# details: only the UPI ID and the QR code image (scanner), both required.
BANK_FIELDS = ("bank_name", "account_name", "account_number", "ifsc_code", "branch")
METHOD_FIELDS: dict[PaymentMethod, tuple[str, ...]] = {
    PaymentMethod.imps: BANK_FIELDS,
    PaymentMethod.rtgs: BANK_FIELDS,
    PaymentMethod.upi: ("upi_id", "qr_code"),
}
DETAIL_FIELDS = (*BANK_FIELDS, "upi_id")


def missing_method_fields(method: PaymentMethod, values: dict, *, has_qr: bool = False) -> list[str]:
    """Required details not set. `qr_code` (UPI) is a file, so its presence is passed as has_qr."""
    return [
        f for f in METHOD_FIELDS[method]
        if not (has_qr if f == "qr_code" else values.get(f))
    ]


class PspPaymentMethod(Base):
    """One way a PSP takes payments (IMPS, RTGS or UPI) with that method's details."""

    __tablename__ = "psp_payment_methods"
    __table_args__ = (UniqueConstraint("psp_id", "method", name="uq_psp_payment_method"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    psp_id: Mapped[int] = mapped_column(ForeignKey("psps.id", ondelete="CASCADE"))
    method: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod, "payment_method"))
    # IMPS / RTGS
    bank_name: Mapped[str | None] = mapped_column(String(200))
    account_name: Mapped[str | None] = mapped_column(String(200))  # account holder name
    account_number: Mapped[str | None] = mapped_column(String(34))
    ifsc_code: Mapped[str | None] = mapped_column(String(11))
    branch: Mapped[str | None] = mapped_column(String(200))
    # UPI
    upi_id: Mapped[str | None] = mapped_column(String(100))
    qr_code_key: Mapped[str | None] = mapped_column(String(1000))  # S3 key of the UPI QR image
    # Switched off by an admin: the details stay, but the method is not offered or accepted.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6(), onupdate=now6())

    @property
    def missing_fields(self) -> list[str]:
        return missing_method_fields(
            self.method, {f: getattr(self, f) for f in DETAIL_FIELDS}, has_qr=bool(self.qr_code_key)
        )

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    @property
    def is_ready(self) -> bool:
        """Active and complete, so payments can use it."""
        return self.is_active and self.is_complete

    @property
    def account_id(self) -> str | None:
        """What the CRM sends as bank_account_id: the account number, or the UPI ID for UPI."""
        return self.upi_id if self.method == PaymentMethod.upi else self.account_number


class WalletEntryType(str, enum.Enum):
    opening_balance = "opening_balance"  # set once when wallets were introduced
    deposit_credit = "deposit_credit"  # deposit approved: balance +
    deposit_reversal = "deposit_reversal"  # approved deposit reversed: balance -
    withdrawal_hold = "withdrawal_hold"  # withdrawal submitted: held +
    withdrawal_release = "withdrawal_release"  # withdrawal rejected: held -
    withdrawal_debit = "withdrawal_debit"  # withdrawal approved: balance - and held -
    withdrawal_reversal = "withdrawal_reversal"  # approved withdrawal reversed: balance +


class PspWallet(Base):
    """A PSP's money: balance from approved deposits minus approved withdrawals.

    held = pending / processing withdrawals, reserved so they cannot be spent twice.
    Only app.services.wallet changes these numbers, always together with a ledger row.
    """

    __tablename__ = "psp_wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_psp_wallets_balance"),
        CheckConstraint("held >= 0", name="ck_psp_wallets_held"),
        CheckConstraint("held <= balance", name="ck_psp_wallets_held_le_balance"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    psp_id: Mapped[int] = mapped_column(ForeignKey("psps.id", ondelete="CASCADE"), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), server_default="0")
    held: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6(), onupdate=now6())

    psp: Mapped[Psp] = relationship(back_populates="wallet", lazy="selectin")

    @property
    def available_balance(self) -> Decimal:
        return self.balance - self.held

    @property
    def psp_code(self) -> str:
        return self.psp.psp_code

    @property
    def psp_name(self) -> str:
        return self.psp.psp_name


class PspWalletTransaction(Base):
    """One movement of a PSP wallet. Append-only: never updated or deleted by the app."""

    __tablename__ = "psp_wallet_transactions"
    __table_args__ = (
        UniqueConstraint("entry_type", "deposit_id", name="uq_wallet_tx_deposit"),
        UniqueConstraint("entry_type", "withdrawal_id", name="uq_wallet_tx_withdrawal"),
        Index("ix_wallet_tx_wallet_created", "wallet_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("psp_wallets.id", ondelete="CASCADE"))
    entry_type: Mapped[WalletEntryType] = mapped_column(_enum(WalletEntryType, "wallet_entry_type"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # always positive; entry_type says the direction
    balance_before: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    held_before: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    held_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    deposit_id: Mapped[int | None] = mapped_column(ForeignKey("deposits.id", ondelete="SET NULL"))
    withdrawal_id: Mapped[int | None] = mapped_column(ForeignKey("withdrawals.id", ondelete="SET NULL"))
    reference: Mapped[str | None] = mapped_column(String(20))  # DEP-/WDL- id, kept if the request row goes
    actor_type: Mapped[str | None] = mapped_column(String(10))  # "user" (portal) or "psp" (CRM API)
    actor_id: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=now6())

    wallet: Mapped[PspWallet] = relationship(lazy="selectin")

    @property
    def psp_code(self) -> str | None:
        return self.wallet.psp.psp_code if self.wallet and self.wallet.psp else None

    @property
    def psp_name(self) -> str | None:
        return self.wallet.psp.psp_name if self.wallet and self.wallet.psp else None


class RequestNonce(Base):
    """Nonces of signed CRM requests, kept until their timestamp expires, so a request cannot be replayed."""

    __tablename__ = "request_nonces"
    __table_args__ = (UniqueConstraint("psp_id", "nonce", name="uq_request_nonce"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    psp_id: Mapped[int] = mapped_column(ForeignKey("psps.id", ondelete="CASCADE"))
    nonce: Mapped[str] = mapped_column(BinaryString(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


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
    def psp_name(self) -> str | None:
        return self.psp.psp_name if self.psp else None

    @property
    def reviewed_by(self) -> str | None:
        return self.reviewer.full_name if self.reviewer else None


class Deposit(TransactionMixin, Base):
    __tablename__ = "deposits"
    __table_args__ = (UniqueConstraint("psp_id", "idempotency_key", name="uq_deposit_idempotency"),)
    PREFIX = "DEP"

    # The PSP account paid into (account number or UPI ID). Empty on older admin-submitted deposits.
    bank_account_id: Mapped[str | None] = mapped_column(String(50))
    screenshot_url: Mapped[str] = mapped_column(String(1000))  # S3 key of the uploaded screenshot
    utr_number: Mapped[str | None] = mapped_column(String(100), index=True)
    # How the customer paid. Empty on deposits made before the field existed.
    payment_method: Mapped[PaymentMethod | None] = mapped_column(_enum(PaymentMethod, "payment_method"))

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


class DirectMessage(Base):
    """A message in the direct chat between the admins and one PSP's logins.

    Each PSP has one such thread; it is not tied to a deposit or withdrawal and is never closed.
    A message carries text, an attachment, or both.
    """

    __tablename__ = "direct_messages"
    __table_args__ = (Index("ix_direct_messages_psp_id_id", "psp_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    psp_id: Mapped[int] = mapped_column(ForeignKey("psps.id", ondelete="CASCADE"))
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("portal_users.id", ondelete="SET NULL"))
    # Name and role are copied in so the history stays readable after a login is removed.
    sender_name: Mapped[str] = mapped_column(String(200))
    sender_role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    message: Mapped[str | None] = mapped_column(Text)
    attachment_key: Mapped[str | None] = mapped_column(String(1000))  # S3 key, when a file was attached
    attachment_name: Mapped[str | None] = mapped_column(String(255))  # file name as uploaded
    attachment_content_type: Mapped[str | None] = mapped_column(String(100))
    attachment_size: Mapped[int | None] = mapped_column(Integer)  # bytes
    # Read state is kept per side, not per login: set when the other side first opens the message.
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
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
