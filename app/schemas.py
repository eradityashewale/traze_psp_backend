import re
from datetime import datetime
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

from app.models import ChatStatus, CreatedBy, PspStatus, TxKind, TxStatus, UserRole
from app.services.storage import view_url

Currency = Literal["INR"]
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
# Stored S3 keys leave the API as short-lived presigned links.
ScreenshotLink = Annotated[str, AfterValidator(view_url)]

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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)

    @model_validator(mode="after")
    def new_password_differs(self):
        if self.new_password == self.current_password:
            raise ValueError("new_password must be different from current_password")
        return self


class UserCreate(BaseModel):
    """Create another admin, or an extra login for an existing PSP."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=72)
    role: UserRole = UserRole.psp
    psp_code: str | None = Field(default=None, description="Required when role is psp")

    @model_validator(mode="after")
    def psp_code_matches_role(self):
        if self.role == UserRole.psp and not self.psp_code:
            raise ValueError("psp_code is required for a psp login")
        if self.role == UserRole.admin and self.psp_code:
            raise ValueError("an admin is not tied to a PSP; leave psp_code empty")
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


# ---------- PSP management ----------

class PspBase(BaseModel):
    psp_name: str | None = Field(default=None, min_length=1, max_length=200)
    ifsc_code: str | None = None
    account_number: str | None = Field(
        default=None, min_length=6, max_length=34, pattern=r"^[A-Za-z0-9]+$",
        description=(
            "The PSP's one bank account number (digits/letters only, no spaces). Deposits (bank_account_id) "
            "and withdrawals (source_account_id) must send this value."
        ),
    )
    contact_email: EmailStr | None = None

    @field_validator("ifsc_code")
    @classmethod
    def check_ifsc(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.upper()
            if not IFSC_RE.match(v):
                raise ValueError("ifsc_code must be an 11-character IFSC code, e.g. HDFC0001234")
        return v


class PspCreate(PspBase):
    psp_name: str = Field(min_length=1, max_length=200)
    account_number: str = Field(
        min_length=6, max_length=34, pattern=r"^[A-Za-z0-9]+$",
        description=(
            "The PSP's one bank account number (digits/letters only, no spaces). Deposits (bank_account_id) "
            "and withdrawals (source_account_id) must send this value."
        ),
    )
    login_email: EmailStr = Field(description="Portal login for this PSP; it reviews its own requests")
    login_password: str = Field(min_length=10, max_length=72)


class PspUpdate(PspBase):
    """Only send fields you want to change — others stay as-is."""

    status: PspStatus | None = None


class PspOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    psp_code: str
    psp_name: str
    status: PspStatus
    ifsc_code: str | None
    account_number: str | None
    contact_email: str | None
    api_token_expires_at: datetime
    credentials_rotated_at: datetime | None
    prev_valid_until: datetime | None
    created_at: datetime
    updated_at: datetime


class RotateRequest(BaseModel):
    grace_hours: int | None = Field(
        default=None, ge=0, le=720, description="How long the old token keeps working (default from config)"
    )
    rotate_salt: bool = Field(default=False, description="Also issue a new MD5 signature salt")


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


class PspList(BaseModel):
    psps: list[PspOut]


# ---------- CRM-facing requests ----------

class ScreenshotForm(BaseModel):
    """Deposits and withdrawals are submitted as multipart/form-data so the screenshot travels with them."""

    screenshot: UploadFile | None = Field(
        default=None, description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3."
    )

    @model_validator(mode="before")
    @classmethod
    def drop_empty_fields(cls, data):
        # Forms send optional fields that were left blank as "".
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ""}
        return data


class DepositFields(ScreenshotForm):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    amount: Amount
    utr_number: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)


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


class DepositCreate(DepositFields):
    """CRM deposit: always INR, the screenshot file travels with the request, signed without a timestamp."""

    screenshot: UploadFile = Field(description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3.")
    bank_account_id: str = Field(min_length=1, max_length=50)
    signature: str | None = Field(default=None, description="MD5 signature, see README")


class WithdrawalCreate(WithdrawalFields):
    source_account_id: str = Field(min_length=1, max_length=50)
    dest_bank_name: str = Field(min_length=1, max_length=200)
    dest_account_number: str = Field(min_length=4, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    dest_ifsc: str
    dest_account_name: str = Field(min_length=1, max_length=200)
    signature: str | None = Field(default=None, description="MD5 signature, see README")


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


# ---------- portal (review) ----------

class ApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000, description="Always sent to the CRM in the callback")


class ReverseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000, description="Always sent to the CRM in the callback")


class AdminDepositCreate(DepositFields):
    """Admin submits a deposit from the portal on behalf of a PSP (no CRM signature)."""

    psp_code: str = Field(description="PSP that will review this deposit")
    screenshot: UploadFile = Field(description="Screenshot file (PNG, JPEG, WEBP or PDF). Stored in S3.")


class AdminWithdrawalCreate(WithdrawalFields):
    """Admin submits a withdrawal from the portal on behalf of a PSP (no CRM signature)."""

    psp_code: str = Field(description="PSP that will review this withdrawal")
    dest_bank_name: str | None = Field(default=None, min_length=1, max_length=200)
    dest_account_number: str | None = Field(default=None, min_length=4, max_length=50, pattern=r"^[A-Za-z0-9]+$")
    dest_ifsc: str | None = None
    dest_account_name: str | None = Field(default=None, min_length=1, max_length=200)


class TransactionOutBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(validation_alias="public_id")
    psp_code: str | None
    customer_name: str
    customer_email: str
    amount: Decimal
    currency: str
    comment: str | None
    created_by: CreatedBy  # admin = submitted from the portal, crm = submitted through the CRM API
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


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
