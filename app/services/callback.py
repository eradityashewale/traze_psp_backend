"""Notify the CRM after every approve/reject decision (guide Section 8, checklist items 4 and 11).

Delivery works as a database outbox: a decision sets `callback_due_at`, and a worker
delivers every row whose due time has passed. Rows are claimed with
`FOR UPDATE SKIP LOCKED`, so several app instances (primary + DR) can run the worker
without sending the same callback twice, and nothing is lost on restart or failover.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Deposit, Withdrawal
from app.security import compute_signature
from app.services.keys import sign_with_portal_key

log = logging.getLogger(__name__)

Transaction = Deposit | Withdrawal
MODELS: tuple[type[Transaction], ...] = (Deposit, Withdrawal)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kind(tx: Transaction) -> str:
    return "deposit" if isinstance(tx, Deposit) else "withdrawal"


def _json_amount(amount: Decimal) -> int | float:
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def schedule_callback(tx: Transaction) -> None:
    """Queue a fresh delivery cycle (caller commits)."""
    tx.callback_due_at = _now()
    tx.callback_attempts = 0
    tx.callback_last_error = None


def build_payload(tx: Transaction) -> dict:
    kind = _kind(tx)
    id_field = f"{kind}_id"
    timestamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    signed_fields = {
        id_field: tx.public_id,
        "status": tx.status.value,
        "amount": tx.amount,
        "currency": tx.currency,
        "customer_email": tx.customer_email,
        "timestamp": timestamp,
    }
    return {
        "event": f"{kind}.{tx.status.value}",
        id_field: tx.public_id,
        "status": tx.status.value,
        "amount": _json_amount(tx.amount),
        "currency": tx.currency,
        "customer_email": tx.customer_email,
        "reviewed_by": tx.reviewed_by,
        "comment": tx.review_comment,
        "signature": compute_signature(signed_fields, tx.psp.signature_salt),
        "timestamp": timestamp,
    }


def _attempt(client: httpx.Client, tx: Transaction) -> None:
    settings = get_settings()
    psp = tx.psp
    tx.callback_attempts += 1
    error: str | None = None

    if psp is None or not psp.callback_url:
        error = "PSP has no callback URL configured"
    else:
        body = json.dumps(build_payload(tx), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Signature": sign_with_portal_key(body)}
        try:
            resp = client.post(
                psp.callback_url,
                content=body,
                headers=headers,
                auth=(psp.callback_username, psp.callback_password),
            )
            if resp.status_code == 200:
                tx.callback_sent = True
                tx.callback_sent_at = _now()
                tx.callback_last_error = None
                tx.callback_due_at = None
                log.info("Callback delivered for %s", tx.public_id)
                return
            error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"

    tx.callback_last_error = error
    if psp is not None and tx.callback_attempts <= settings.callback_max_retries:
        tx.callback_due_at = _now() + timedelta(seconds=2**tx.callback_attempts)  # 2s, 4s, 8s
        log.warning("Callback attempt %d for %s failed: %s", tx.callback_attempts, tx.public_id, error)
    else:
        tx.callback_due_at = None
        log.error("Callback for %s gave up after %d attempts: %s", tx.public_id, tx.callback_attempts, error)


def deliver_due(model: type[Transaction], only_id: int | None = None, batch: int = 50) -> int:
    """Deliver due callbacks for one table, one row per DB transaction. Returns rows processed."""
    settings = get_settings()
    processed = 0
    with SessionLocal() as db, httpx.Client(timeout=settings.callback_timeout_seconds) as client:
        while processed < batch:
            query = (
                select(model)
                .where(model.callback_due_at.is_not(None), model.callback_due_at <= _now())
                .order_by(model.callback_due_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if only_id is not None:
                query = query.where(model.id == only_id)
            tx = db.scalar(query)
            if tx is None:
                break
            _attempt(client, tx)
            db.commit()
            processed += 1
            if only_id is not None:
                break
    return processed


class CallbackWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="callback-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)

    def _run(self) -> None:
        interval = get_settings().callback_worker_interval_seconds
        while not self._stop.is_set():
            for model in MODELS:
                try:
                    deliver_due(model)
                except Exception:  # keep the worker alive on DB hiccups
                    log.exception("Callback worker error")
            self._stop.wait(interval)
