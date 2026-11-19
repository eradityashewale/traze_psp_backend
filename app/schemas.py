import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from fastapi import UploadFile
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models import (
    DETAIL_FIELDS,
    METHOD_FIELDS,
    ChatStatus,
    CreatedBy,
    PaymentMethod,
    PspStatus,
    TxKind,
    TxStatus,
    UserRole,
    WalletEntryType,
    missing_method_fields,
)
from app.services.storage import attachment_url, qr_code_url as presign_qr_code, view_url

Currency = Literal["INR"]
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
# Stored S3 keys leave the API as short-lived presigned links.
ScreenshotLink = Annotated[str, AfterValidator(view_url)]
AttachmentLink = Annotated[str, AfterValidator(attachment_url)]

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
SWIFT_RE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


# ---------- auth / users ----------

class LoginRequest(BaseModel):
    # Swagger prefill only: the bootstrap admin defaults from .env.example
    model_config = ConfigDict(
        json_schema_extra={"example": {"email": "admin@example.com", "password": "ChangeMe123!"}}
    )

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class PasswordChanged(TokenResponse):
    """Changing the password signs out every other session; this token replaces the one just used."""

    success: bool = True
    message: str = "Password changed"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)

    @model_validator(mode="after")
    def new_password_differs(self):
        if self.new_password == self.current_password:
            raise ValueError("new_password must be different from current_password")
        return self


class UserCreate(BaseModel):
    """Create another admin, a read-only sub admin, or an extra login for an existing PSP."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=72)
    role: UserRole = UserRole.psp
    psp_code: str | None = Field(default=None, description="Required when role is psp")

    @model_validator(mode="after")
    def psp_code_matches_role(self):
        if self.role == UserRole.psp and not self.psp_code:
            raise ValueError("psp_code is required for a psp login")
        if self.role != UserRole.psp and self.psp_code:
            raise ValueError("an admin or sub admin is not tied to a PSP; leave psp_code empty")
        return self


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=72)
    unlock: bool | None = Field(default=None, description="true clears a failed-login lockout")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole
    psp_code: str | None
    is_active: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime


# ---------- forms ----------

class FormModel(BaseModel):
    """Body of a multipart/form-data request."""

    @model_validator(mode="before")
    @classmethod
    def drop_empty_fields(cls, data):
        # Forms send optional fields that were left blank as "".
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ""}
        return data


# ---------- PSP management ----------

ACCOUNT_NUMBER_HELP = "Bank account number (digits/letters only, no spaces)"
UPI_ID_RE = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$")


class PaymentMethodFields(BaseModel):
    """Details of one payment method. Which ones are needed depends on the method:

    * imps / rtgs: bank_name, account_name, account_number, ifsc_code, branch
    * upi: upi_id and qr_code (QR code image), both required; no bank details
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    bank_name: str | None = Field(default=None, min_length=1, max_length=200)
    account_name: str | None = Field(
        default=None, min_length=1, max_length=200, description="Account holder name"
    )
    account_number: str | None = Field(
        default=None, min_length=6, max_length=34, pattern=r"^[A-Za-z0-9]+$", description=ACCOUNT_NUMBER_HELP
    )
    ifsc_code: str | None = None
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    upi_id: str | None = Field(default=None, max_length=100, description="UPI ID / VPA, e.g. traze@hdfcbank")
    qr_code: UploadFile | None = Field(
        default=None, description="UPI only, required there: payment QR code image (PNG, JPEG or WEBP). Stored in S3."
    )

    @field_validator("ifsc_code")
    @classmethod
    def check_ifsc(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.upper()
            if not IFSC_RE.match(v):
                raise ValueError("ifsc_code must be an 11-character IFSC code, e.g. HDFC0001234")
        return v

    @field_validator("upi_id")
    @classmethod
    def check_upi_id(cls, v: str | None) -> str | None:
        if v is not None and not UPI_ID_RE.match(v):
            raise ValueError("upi_id must look like name@bank, e.g. traze@hdfcbank")
        return v

    def detail_values(self) -> dict:
        """The details that were sent (blank form fields are dropped before this)."""
        return self.model_dump(include=set(DETAIL_FIELDS), exclude_none=True)

    def check_fits(self, method: PaymentMethod) -> None:
        """Reject details that do not belong to the method, e.g. upi_id on imps."""
        allowed = METHOD_FIELDS[method]
        sent = set(self.detail_values()) | ({"qr_code"} if self.qr_code is not None else set())
        extra = sorted(sent - set(allowed))
        if extra:
            raise ValueError(f"{', '.join(extra)} not used by {method.value}; it takes {', '.join(allowed)}")


class PspCreate(FormModel, PaymentMethodFields):
    """Sent as multipart/form-data so the UPI QR code image can travel with it.

    A PSP is registered with one payment method (payment_method) and that method's details;
    the other methods are added later with PUT /api/v1/psps/{psp_code}/payment-methods/{method}.
    """

    psp_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    payment_method: PaymentMethod = Field(description="The first payment method: imps, rtgs or upi")
    login_email: EmailStr = Field(description="Portal login for this PSP; it reviews its own requests")
    login_password: str = Field(min_length=10, max_length=72)

    @field_validator("payment_method", mode="before")
    @classmethod
    def lower_payment_method(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @model_validator(mode="after")
    def check_method_details(self):
        self.check_fits(self.payment_method)
        missing = missing_method_fields(
            self.payment_method, self.detail_values(), has_qr=self.qr_code is not None
        )
        if missing:
            raise ValueError(f"{self.payment_method.value} needs {', '.join(missing)}")
        return self


class PaymentMethodUpsert(FormModel, PaymentMethodFields):
    """Add a method (send all its details) or change one (send only the details to change)."""


class PaymentMethodStatusUpdate(BaseModel):
    is_active: bool = Field(description="false switches the method off (details are kept); true switches it back on")


class PspUpdate(BaseModel):
    """Only send fields you want to change — others stay as-is. Payment methods have their own endpoints."""

    model_config = ConfigDict(str_strip_whitespace=True)

    psp_name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    status: PspStatus | None = None


class PaymentMethodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: PaymentMethod
    bank_name: str | None
    account_name: str | None = Field(description="Account holder name")
    account_number: str | None
    ifsc_code: str | None
    branch: str | None
    upi_id: str | None
    qr_code_url: Annotated[str | None, AfterValidator(presign_qr_code)] = Field(
        default=None, validation_alias="qr_code_key", description="Short-lived link to the UPI QR code image"
    )
    is_active: bool = Field(description="Switched on by the admin; inactive methods are not offered or accepted")
    is_complete: bool = Field(description="All details are set")
    is_ready: bool = Field(description="Active and complete, so deposits can use this method")
    missing_fields: list[str]
    updated_at: datetime


class PspOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    psp_code: str
    psp_name: str
    status: PspStatus
    payment_methods: list[PaymentMethodOut] = Field(description="Configured methods (imps, rtgs, upi)")
    contact_email: str | None
    portal_email: str | None = Field(default=None, description="Email the PSP uses to log in to the portal")
    api_token_expires_at: datetime
    credentials_rotated_at: datetime | None
    credentials_revoked_at: datetime | None
    prev_valid_until: datetime | None
    created_at: datetime
    updated_at: datetime


class RotateRequest(BaseModel):
    grace_hours: int | None = Field(
        default=None, ge=0, le=720, description="How long the old token keeps working (default from config)"
    )
    rotate_salt: bool = Field(default=False, description="Also issue a new signature salt (HMAC key)")


class RevokeRequest(BaseModel):
    previous_only: bool = Field(
        default=False,
        description="true only ends the rotation grace period (kills the old token, keeps the current one)",
    )
    revoke_sessions: bool = Field(default=False, description="Also sign out every portal login of this PSP")


class PspCredentials(BaseModel):
    api_token: str
    api_secret: str
    signature_salt: str
    api_token_expires_at: datetime
    previous_token_valid_until: datetime | None = None
    note: str = "Store these now. The token and secret cannot be shown again."


class PspCreated(BaseModel):
    psp: PspOut
    credentials: PspCredentials
    portal_login: UserOut
    welcome_email_queued: bool = Field(
        description="True when the portal login and password were queued for emailing to login_email. "
        "False when SMTP is not configured; share the login yourself then."
    )


class PspList(BaseModel):
    psps: list[PspOut]


# ---------- CRM-facing requests ----------

class ScreenshotForm(FormModel):
    """Deposits and withdrawals are submitted as multipart/form-data so the screenshot travels with them."""

    screenshot: UploadFile | None = Field(
        default=None, description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3."
    )


class DepositFields(ScreenshotForm):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    amount: Amount
    payment_method: PaymentMethod = Field(description="How the customer paid: imps, rtgs or upi")
    utr_number: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("payment_method", mode="before")
    @classmethod
    def lower_payment_method(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


class WithdrawalFields(ScreenshotForm):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    amount: Amount
    currency: Currency = "INR"
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("dest_ifsc", check_fields=False)
    @classmethod
    def check_ifsc_or_swift(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if not (IFSC_RE.match(v) or SWIFT_RE.match(v)):
            raise ValueError("dest_ifsc must be a valid IFSC (11 chars) or SWIFT/BIC (8 or 11 chars) code")
        return v


class SignedRequest(BaseModel):
    """Signing fields of a CRM request. Optional here; enforced when REQUIRE_SIGNATURE is on."""

    signature: str | None = Field(default=None, description="HMAC-SHA256 signature (hex), see README")
    timestamp: int | None = Field(default=None, ge=0, description="Unix time in seconds (UTC) when the request was signed")
    nonce: str | None = Field(
        default=None,
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Random value, unique per request (e.g. a UUID)",
    )


SIGNING_FIELDS = set(SignedRequest.model_fields)


class DepositCreate(DepositFields, SignedRequest):
    """CRM deposit: always INR, the screenshot file travels with the request."""

    screenshot: UploadFile = Field(description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3.")
    bank_account_id: str = Field(min_length=1, max_length=50)


class WithdrawalCreate(WithdrawalFields, SignedRequest):
    """CRM withdrawal. No source account: the PSP's first payment method account is used."""

    dest_bank_name: str = Field(min_length=1, max_length=200)
    dest_account_number: str = Field(min_length=4, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    dest_ifsc: str
    dest_account_name: str = Field(min_length=1, max_length=200)


class PaymentMethodDetailsOut(BaseModel):
    """Where the customer pays with one method."""

    model_config = ConfigDict(from_attributes=True)

    method: PaymentMethod
    bank_account_id: str = Field(
        validation_alias="account_id",
        description="Send this back as bank_account_id on POST /api/v1/deposits with this payment_method "
        "(the account number, or the UPI ID for upi)",
    )
    bank_name: str | None
    account_name: str | None = Field(description="Account holder name (null for UPI)")
    account_number: str | None
    ifsc_code: str | None
    branch: str | None
    upi_id: str | None
    qr_code_url: Annotated[str | None, AfterValidator(presign_qr_code)] = Field(
        default=None,
        validation_alias="qr_code_key",
        description="UPI only: short-lived link to the QR image (S3_URL_EXPIRES_SECONDS); null for imps / rtgs",
    )


class PaymentDetailsOut(BaseModel):
    """How the customer can pay a deposit to the calling PSP: one entry per method it takes."""

    psp_code: str
    psp_name: str
    payment_methods: list[PaymentMethod] = Field(description="Methods the PSP takes: imps, rtgs and/or upi")
    methods: list[PaymentMethodDetailsOut]
    currency: Currency = "INR"
    updated_at: datetime = Field(description="When the PSP's payment details last changed")


class DepositSubmitted(BaseModel):
    success: bool = True
    deposit_id: str
    status: TxStatus
    message: str


class WithdrawalSubmitted(BaseModel):
    success: bool = True
    withdrawal_id: str
    status: TxStatus
    message: str


class DepositStatusOut(BaseModel):
    deposit_id: str
    status: TxStatus
    amount: Decimal
    currency: str
    customer_email: str
    comment: str | None
    reviewed_at: datetime | None
    created_at: datetime


class WithdrawalStatusOut(BaseModel):
    withdrawal_id: str
    status: TxStatus
    amount: Decimal
    currency: str
    customer_email: str
    comment: str | None
    reviewed_at: datetime | None
    created_at: datetime


class BalanceOut(BaseModel):
    """A PSP wallet. available_balance = balance - held."""

    model_config = ConfigDict(from_attributes=True)

    psp_code: str
    psp_name: str
    currency: str
    balance: Decimal  # approved deposits - approved withdrawals
    held: Decimal  # pending + processing withdrawals, reserved against the balance
    available_balance: Decimal  # the most a new withdrawal can be
    total_deposit: Decimal = Decimal("0.00")  # sum of approved deposits
    total_withdrawal: Decimal = Decimal("0.00")  # sum of approved withdrawals
    updated_at: datetime


class WalletTotals(BaseModel):
    balance: Decimal
    held: Decimal
    available_balance: Decimal
    total_deposit: Decimal
    total_withdrawal: Decimal


class WalletList(BaseModel):
    items: list[BalanceOut]
    total: int
    limit: int
    offset: int
    totals: WalletTotals  # across every PSP, not only this page


class WalletTransactionOut(BaseModel):
    """One wallet movement. amount is always positive; entry_type gives the direction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    psp_code: str | None
    psp_name: str | None
    entry_type: WalletEntryType
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    held_before: Decimal
    held_after: Decimal
    reference: str | None  # DEP-/WDL- id of the request that caused it
    actor_type: str | None
    actor_id: str | None
    note: str | None
    created_at: datetime


# ---------- portal (review) ----------

class ApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000, description="Always sent to the CRM in the callback")


class ReverseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000, description="Always sent to the CRM in the callback")


class PspDepositCreate(DepositFields):
    """A PSP login submits a deposit from the portal for its own PSP (no CRM signature)."""

    screenshot: UploadFile = Field(description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3.")


class AdminDepositCreate(PspDepositCreate):
    """Admin submits a deposit from the portal on behalf of a PSP (no CRM signature)."""

    psp_code: str = Field(description="PSP that will review this deposit")


class PspWithdrawalCreate(WithdrawalFields):
    """A PSP login submits a withdrawal from the portal for its own PSP (no CRM signature)."""

    dest_bank_name: str | None = Field(default=None, min_length=1, max_length=200)
    dest_account_number: str | None = Field(default=None, min_length=4, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    dest_ifsc: str | None = None
    dest_account_name: str | None = Field(default=None, min_length=1, max_length=200)


class AdminWithdrawalCreate(PspWithdrawalCreate):
    """Admin submits a withdrawal from the portal on behalf of a PSP (no CRM signature)."""

    psp_code: str = Field(description="PSP that will review this withdrawal")


class TransactionOutBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(validation_alias="public_id")
    psp_code: str | None
    psp_name: str | None
    customer_name: str
    customer_email: str
    amount: Decimal
    currency: str
    comment: str | None
    created_by: CreatedBy  # admin / psp = submitted from the portal by that login, crm = through the CRM API
    status: TxStatus
    review_comment: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    callback_sent: bool
    callback_attempts: int
    callback_last_error: str | None
    callback_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DepositOut(TransactionOutBase):
    bank_account_id: str | None  # empty for deposits submitted by an admin
    screenshot_url: ScreenshotLink
    utr_number: str | None
    payment_method: PaymentMethod | None


class WithdrawalOut(TransactionOutBase):
    screenshot_url: ScreenshotLink | None  # only when one was attached at submission
    # destination details may be empty on withdrawals submitted by an admin
    dest_bank_name: str | None
    dest_account_number: str | None
    dest_ifsc: str | None
    dest_account_name: str | None
    source_account_id: str


# ---------- portal (chat) ----------

class ChatMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be blank")
        return v


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_name: str
    sender_role: UserRole
    message: str
    created_at: datetime


class ChatSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    kind: TxKind
    psp_code: str | None
    status: ChatStatus | None = None  # null = nobody has written yet
    opened_by: str | None = None
    closed_by: str | None = None
    closed_at: datetime | None = None
    created_at: datetime | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0  # messages from the other side that your side has not opened


class ChatOut(ChatSummaryOut):
    messages: list[ChatMessageOut] = []


# ---------- portal (direct chat) ----------

class DirectMessageCreate(FormModel):
    """Sent as multipart/form-data so the attachment travels with the message."""

    psp_code: str | None = Field(
        default=None, description="Admin: the PSP to write to. PSP logins always write to the admins."
    )
    message: str | None = Field(default=None, max_length=4000)
    attachment: UploadFile | None = Field(default=None, description="Any file type. Stored in S3.")

    @field_validator("message")
    @classmethod
    def blank_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v else None

    @model_validator(mode="after")
    def has_content(self):
        # A browser form with no file chosen still sends an empty, nameless file part.
        if self.attachment is not None and not self.attachment.filename:
            self.attachment = None
        if self.message is None and self.attachment is None:
            raise ValueError("send a message, an attachment, or both")
        return self


class DirectMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_name: str
    sender_role: UserRole
    message: str | None  # null when only a file was sent
    # The attachment fields are null when no file was sent. attachment_url is a short-lived link.
    attachment_url: AttachmentLink | None = Field(validation_alias="attachment_key")
    attachment_name: str | None
    attachment_content_type: str | None
    attachment_size: int | None  # bytes
    read_at: datetime | None  # when the other side first opened it
    created_at: datetime


class DirectChatSummaryOut(BaseModel):
    psp_code: str
    psp_name: str
    unread_count: int  # messages from the other side that your side has not opened
    last_message: DirectMessageOut | None  # null = nobody has written yet


class DirectChatOut(BaseModel):
    psp_code: str
    psp_name: str
    messages: list[DirectMessageOut]


# ---------- portal (dashboard) ----------

class DailyCount(BaseModel):
    date: date
    count: int


class RequestCard(BaseModel):
    """The "Deposit requests" / "Withdrawal requests" card."""

    period_days: int
    total: int  # requests created in the period
    today: int
    peak: int  # busiest day in the period
    low: int  # quietest day in the period
    avg: float  # requests per day
    series: list[DailyCount]  # one entry per day, oldest first, zero-filled


class ActivityPoint(BaseModel):
    date: date
    deposits: int
    withdrawals: int


class RequestsOverview(BaseModel):
    """Deposits and withdrawals together, by current status."""

    pending: int
    processing: int
    approved: int
    rejected: int
    reversed: int
    total: int


class RecentTransaction(BaseModel):
    id: str
    kind: TxKind
    psp_code: str | None
    psp_name: str | None
    customer_name: str
    customer_email: str
    amount: Decimal
    currency: str
    status: TxStatus
    created_at: datetime


class DashboardOut(BaseModel):
    role: UserRole  # admin / sub_admin = every PSP's requests, psp = only this login's PSP
    full_name: str
    psp_code: str | None  # null for an admin
    psp_name: str | None
    deposits: RequestCard
    withdrawals: RequestCard
    pending_requests: int
    # Requests by current status, split by kind (all requests, not only the period).
    pending_deposits: int
    pending_withdrawals: int
    approved_deposits: int
    approved_withdrawals: int
    rejected_deposits: int
    rejected_withdrawals: int
    reversed_deposits: int
    reversed_withdrawals: int
    total_psp_count: int  # every PSP for an admin, 1 for a PSP login
    approval_rate: float  # approved share of all requests, in percent
    activity: list[ActivityPoint]
    requests_overview: RequestsOverview
    recent_transactions: list[RecentTransaction]  # deposits and withdrawals mixed, newest first
    generated_at: datetime


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
