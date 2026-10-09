"""File storage on S3: deposit / withdrawal screenshots and direct-chat attachments.

The bucket stays private. The database stores only the object key; API responses
turn it into a short-lived presigned URL. Files live outside the app server, so
instances stay stateless and nothing is lost on restart or DR failover.
"""

import logging
import re
import uuid
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from app.config import get_settings
from app.errors import AppError, ErrorCode

log = logging.getLogger(__name__)

KEY_PREFIX = "screenshots/"
# screenshots/deposit/<id>.<ext> or screenshots/withdrawal/<id>.<ext>; older files sit directly under screenshots/.
KEY_RE = re.compile(r"^screenshots/((deposit|withdrawal)/)?[0-9a-f]{32}\.(png|jpg|webp|pdf)$")
ATTACHMENT_PREFIX = "chat/"  # chat/<psp id>/<id>.<ext>


def _sniff(data: bytes) -> tuple[str, str] | None:
    """(extension, content type) from the file's first bytes; the client's Content-Type is not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    return None


@lru_cache
def _client():
    s = get_settings()
    # Keys are only for local runs; on EC2 leave them empty so boto3 uses the instance IAM role.
    return boto3.client(
        "s3",
        region_name=s.aws_region,
        # Regional endpoint, so presigned links are not redirected through the global one.
        endpoint_url=f"https://s3.{s.aws_region}.amazonaws.com",
        aws_access_key_id=s.aws_access_key_id or None,
        aws_secret_access_key=s.aws_secret_access_key or None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, retries={"max_attempts": 3}),
    )


def _bucket() -> str:
    bucket = get_settings().s3_bucket
    if not bucket:
        raise AppError(ErrorCode.STORAGE_UNAVAILABLE, "Screenshot storage is not configured (S3_BUCKET)")
    return bucket


def upload_screenshot(file: UploadFile, folder: str) -> str:
    """Validate and store an uploaded screenshot in its folder (deposit / withdrawal). Returns the S3 object key."""
    bucket = _bucket()
    max_bytes = get_settings().screenshot_max_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(ErrorCode.FILE_INVALID, f"Screenshot is larger than {get_settings().screenshot_max_mb} MB")
    kind = _sniff(data)
    if kind is None:
        raise AppError(ErrorCode.FILE_INVALID, "Screenshot must be a PNG, JPEG, WEBP or PDF file")

    ext, content_type = kind
    key = f"{KEY_PREFIX}{folder}/{uuid.uuid4().hex}.{ext}"
    try:
        _client().put_object(
            Bucket=bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256"
        )
    except (BotoCoreError, ClientError):
        log.exception("S3 upload failed")
        raise AppError(ErrorCode.STORAGE_UNAVAILABLE, "Could not store the screenshot; try again")
    return key


def upload_attachment(file: UploadFile, folder: str) -> dict:
    """Store a chat attachment of any file type. Returns its S3 key, file name, content type and size.

    Images and PDFs open in the browser; every other type is stored as a plain download, so a
    file can never run as a page from the bucket's address.
    """
    bucket = _bucket()
    max_mb = get_settings().chat_attachment_max_mb
    data = file.file.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise AppError(ErrorCode.FILE_INVALID, f"Attachment is larger than {max_mb} MB")
    if not data:
        raise AppError(ErrorCode.FILE_INVALID, "Attachment is empty")

    # Keep only the last path part of the client's file name, without control characters.
    name = re.sub(r"[\x00-\x1f\x7f]", "", re.split(r"[\\/]", file.filename or "")[-1]).strip()[:255] or "file"
    kind = _sniff(data)
    if kind:
        ext, content_type = kind
        disposition = "inline"
    else:
        found = re.search(r"\.([A-Za-z0-9]{1,10})$", name)
        ext, content_type = (found.group(1).lower() if found else "bin"), "application/octet-stream"
        disposition = "attachment"

    key = f"{ATTACHMENT_PREFIX}{folder}/{uuid.uuid4().hex}.{ext}"
    try:
        _client().put_object(
            Bucket=bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256",
            ContentDisposition=f"{disposition}; filename*=UTF-8''{quote(name, safe='')}",
        )
    except (BotoCoreError, ClientError):
        log.exception("S3 upload failed")
        raise AppError(ErrorCode.STORAGE_UNAVAILABLE, "Could not store the attachment; try again")
    return {"key": key, "name": name, "content_type": content_type, "size": len(data)}


def _presign(key: str) -> str:
    s = get_settings()
    if not s.s3_bucket:
        return key
    try:
        return _client().generate_presigned_url(
            "get_object", Params={"Bucket": s.s3_bucket, "Key": key}, ExpiresIn=s.s3_url_expires_seconds
        )
    except (BotoCoreError, ClientError):
        log.exception("Could not presign %s", key)
        return key


def view_url(value: str | None) -> str | None:
    """Presigned GET URL for a stored key. External URLs (older deposits) pass through unchanged."""
    if not value or not KEY_RE.match(value):
        return value
    return _presign(value)


def attachment_url(key: str | None) -> str | None:
    """Presigned GET URL for a chat attachment."""
    return _presign(key) if key else None
