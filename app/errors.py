"""Stable error codes returned to the CRM and portal (checklist item 12).

Every error response has the shape:
    {"success": false, "error_code": "E2001", "message": "...", "details": [...]?}
"""

import logging
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ErrorCode(Enum):
    # (code, http status, default message)
    VALIDATION_ERROR = ("E1000", 422, "Request validation failed")
    AUTH_MISSING = ("E1001", 401, "Authentication required")
    AUTH_INVALID = ("E1002", 401, "Invalid credentials")
    API_TOKEN_EXPIRED = ("E1003", 401, "API token has expired; ask the PSP team for rotated credentials")
    SIGNATURE_MISSING = ("E1004", 401, "timestamp and signature are required")
    SIGNATURE_INVALID = ("E1005", 401, "Invalid MD5 signature")
    TIMESTAMP_INVALID = ("E1006", 401, "timestamp is malformed, too old or too far in the future")
    RSA_SIGNATURE_INVALID = ("E1007", 401, "Missing or invalid X-Signature (RSA)")
    HTTPS_REQUIRED = ("E1008", 403, "HTTPS is required")
    FORBIDDEN = ("E1009", 403, "You do not have permission for this action")
    ACCOUNT_LOCKED = ("E1011", 423, "Account temporarily locked after too many failed attempts")
    PSP_INACTIVE = ("E2001", 403, "PSP is inactive and cannot receive new requests")
    CURRENCY_NOT_ALLOWED = ("E2002", 422, "Currency is not allowed for this PSP")
    BANK_ACCOUNT_NOT_ALLOWED = ("E2003", 422, "Bank account is not managed by this PSP")
    PSP_NOT_FOUND = ("E2004", 404, "PSP not found")
    PSP_ALREADY_EXISTS = ("E2005", 409, "PSP code already exists")
    PSP_HAS_OPEN_TRANSACTIONS = ("E2006", 409, "PSP has pending or processing transactions")
    PSP_CONFIG_INVALID = ("E2007", 422, "Invalid PSP configuration")
    TX_NOT_FOUND = ("E3001", 404, "Transaction not found")
    TX_ALREADY_FINAL = ("E3002", 409, "Transaction is already approved or rejected")
    TX_INVALID_TRANSITION = ("E3003", 409, "Status change not allowed")
    FILE_INVALID = ("E3004", 422, "Uploaded file is not an accepted screenshot")
    USER_ALREADY_EXISTS =("E4001", 409, "A user with this email already exists")
    USER_NOT_FOUND = ("E4002", 404, "User not found")
    USER_SELF_CHANGE = ("E4003", 400, "You cannot deactivate or demote yourself")
    PASSWORD_INCORRECT = ("E4004", 400, "Current password is incorrect")
    NOT_FOUND = ("E4040", 404, "Resource not found")
    METHOD_NOT_ALLOWED = ("E4050", 405, "Method not allowed")
    INTERNAL_ERROR = ("E5000", 500, "Internal server error")
    SERVICE_UNAVAILABLE = ("E5001", 503, "Service temporarily unavailable")
    STORAGE_UNAVAILABLE = ("E5002", 503, "Screenshot storage is unavailable")

    @property
    def code(self) -> str:
        return self.value[0]

    @property
    def http_status(self) -> int:
        return self.value[1]

    @property
    def default_message(self) -> str:
        return self.value[2]


class AppError(Exception):
    def __init__(self, error: ErrorCode, message: str | None = None, details=None):
        self.error = error
        self.message = message or error.default_message
        self.details = details
        super().__init__(self.message)


def _body(error: ErrorCode, message: str, details=None) -> dict:
    body = {"success": False, "error_code": error.code, "message": message}
    if details is not None:
        body["details"] = details
    return body


_HTTP_FALLBACK = {
    401: ErrorCode.AUTH_MISSING,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.error.http_status == 401 else None
        return JSONResponse(_body(exc.error, exc.message, exc.details), exc.error.http_status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(_body(ErrorCode.VALIDATION_ERROR, "Request validation failed", details), 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        error = _HTTP_FALLBACK.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return JSONResponse(_body(error, str(exc.detail)), exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(_body(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.default_message), 500)


def error_catalog() -> list[dict]:
    return [
        {"error_code": e.code, "name": e.name, "http_status": e.http_status, "message": e.default_message}
        for e in ErrorCode
    ]
