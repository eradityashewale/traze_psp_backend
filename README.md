# PSP Portal Backend

Small backend for the PSP Portal. The CRM submits deposits and withdrawals, the PSP team approves or rejects them, and the portal sends a signed callback back to the CRM.

**Stack:** Python 3.12 · FastAPI · MySQL 8 · SQLAlchemy 2.0 · PyMySQL · cryptography

## New PSP Checklist coverage

| # | Checklist question | How this backend answers it |
|---|---|---|
| 1.1 | Documentation | This README + Swagger at `/docs` + `/openapi.json` |
| 1.2 / 1.3 | UAT / Production credentials | `ENVIRONMENT=uat\|production`, `PUBLIC_API_BASE_URL`, `ADMIN_PORTAL_URL`. Portal logins are created per user by an admin. |
| 2 | HTTPS/SSL | `ENFORCE_HTTPS=true` rejects plain HTTP with `E1008` and sends HSTS. Callback URLs must be `https://`. |
| 3 | Request authentication | `Authorization: Bearer <API_TOKEN>` + `X-API-Secret` |
| 4 | HTTP Basic auth for webhooks | Callback username and password are **required** on every PSP |
| 5 | Periodic token rotation | Tokens expire after `API_TOKEN_VALIDITY_DAYS` (90 = quarterly). Rotation keeps the old token valid for `ROTATION_GRACE_HOURS`. |
| 6 | Salted hash of critical fields | MD5 signature on requests **and** callbacks |
| 7 | Public/private key | CRM signs request bodies with RSA (`X-Signature`). The portal signs callbacks with its own key (`GET /api/v1/meta/public-key`). |
| 8 | Key length | RSA keys under `MIN_RSA_KEY_BITS` (2048) are rejected. TLS cert on the proxy: 2048+ bits. |
| 9 | MFA | **Not implemented** (removed by decision). Portal login is password-only, with bcrypt hashing and a lockout after 5 failed attempts. |
| 10 | PCI compliance | Audit trail (`/api/v1/audit-logs`), hashed credentials, no card data stored. The **level itself** is a business answer (`PCI_DSS_LEVEL`). |
| 11 | DR site | Stateless app plus a DB-backed callback queue: nothing is lost on restart or failover, and several instances can run safely. DR infrastructure itself is ops. |
| 12 | Availability monitoring | `GET /health`, `GET /health/ready` (DB + callback backlog), stable error codes (`GET /api/v1/meta/error-codes`) |
| 13 | Contacts & service hours | `contacts.technical / business / customer_service` on each PSP (email, phone, hours) |

`GET /api/v1/psps/{psp_code}/questionnaire` returns the whole checklist filled in for a PSP.

## Run locally

Requires a running MySQL 8.0 or newer (needed for `SKIP LOCKED` and JSON columns).

```bash
python -m venv .venv
.venv\Scripts\activate               # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
cp .env.example .env                 # then set the DB_* values, JWT_SECRET and the bootstrap admin password
uvicorn app.main:app --reload
```

* Set `DB_NAME`, `DB_USER`, `DB_PASS`, `DB_HOST` (e.g. `localhost:3306`) in `.env`, or a full `DATABASE_URL=mysql://root:secret@localhost:3306/psp_portal`, which takes priority. The database is created with `utf8mb4`, and every connection runs in UTC.
* Swagger UI: http://localhost:8000/docs
* On startup the database is created if it doesn't exist (when the user has permission), then pending migrations are applied (see **Database migrations**). The first admin comes from `BOOTSTRAP_ADMIN_*` and is created only if no users exist yet.
* On first start the portal RSA key pair is generated at `keys/portal_private_key.pem`. In UAT and production, mount a backed-up key there; CRMs pin its public key. The `keys/` folder is git-ignored.
* With `ENVIRONMENT=uat` or `production`, the app **refuses to start** if any of these hold: HTTPS not enforced, HTTP callbacks allowed, signatures off, or a short JWT secret.

## Database migrations

Schema changes are managed with **Alembic**. Migration files live in `alembic/versions/` and are applied in order.

| Revision | What it does |
|---|---|
| `0001` | Initial schema: all tables |
| `0002` | Adds `psps.account_number` |

By default the app runs `alembic upgrade head` when it starts (`RUN_MIGRATIONS_ON_STARTUP=true`), so a new migration is applied on the next start. Set it to `false` to apply migrations yourself.

```bash
alembic current                 # which revision the database is on
alembic history                 # list all migrations
alembic upgrade head            # apply all pending migrations
alembic downgrade -1            # undo the last migration
alembic upgrade head --sql      # print the SQL without running it
```

To change the schema:

1. Edit the model in `app/models.py`.
2. Generate a migration: `alembic revision --autogenerate -m "add xyz to psps" --rev-id 0003`
3. Open the new file in `alembic/versions/` and check it does what you expect.
4. Apply it: `alembic upgrade head`, or just restart the app.

A database created before migrations existed is adopted automatically on startup: it's marked with the revision that matches its tables, and nothing is recreated.

## Roles

| Role | Who | Can do |
|---|---|---|
| `admin` | You (the portal operator) | Add, edit and delete PSPs, manage logins, view **all** deposits and withdrawals, audit logs. Does **not** approve or reject. |
| `psp` | Each PSP | Log in, see **only its own** deposits and withdrawals, approve or reject them. |

Creating a PSP also creates its portal login (`login_email`, `login_password`). A PSP can have more logins via `POST /api/v1/users`. When a PSP is deactivated its logins stop working; when it's deleted its logins are removed.

## Endpoints

### Auth & users
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/auth/login` | `{email, password}` → `access_token`, for admins and PSP logins. 5 wrong passwords lock the account for 15 minutes. |
| GET | `/api/v1/auth/me` | |
| GET | `/api/v1/users?psp_code=` | admin: list logins, optionally for one PSP |
| POST | `/api/v1/users` | admin: create an admin (`role: admin`) or an extra PSP login (`role: psp`, `psp_code`) |
| PATCH | `/api/v1/users/{id}` | admin: rename, deactivate, reset password, `unlock` |

### PSP management (admin)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/psps` | list |
| POST | `/api/v1/psps` | create the PSP **and its portal login** (`login_email`, `login_password`). `psp_code` is auto-generated (`PSP-XXXXXXXX`) and returned in the response; do not send it. **The response shows `api_token`, `api_secret` and `signature_salt` once.** |
| GET | `/api/v1/psps/{psp_code}` | |
| PUT | `/api/v1/psps/{psp_code}` | partial update: status, callback, bank accounts, `ifsc_code`, `account_number`, contacts, `client_public_key` |
| POST | `/api/v1/psps/{psp_code}/rotate-credentials` | `{grace_hours?, rotate_salt?}` |
| DELETE | `/api/v1/psps/{psp_code}` | refused while requests are pending or processing. Removes the PSP's logins; transaction history is kept. |
| GET | `/api/v1/psps/{psp_code}/questionnaire` | filled New PSP Checklist |

### CRM API
Headers: `Authorization: Bearer <API_TOKEN>`, `X-API-Secret: <API_SECRET>`, plus `X-Signature` on POSTs if the PSP has a `client_public_key`.

| Method | Path |
|---|---|
| POST | `/api/v1/deposits` |
| GET | `/api/v1/deposits/{deposit_id}` |
| POST | `/api/v1/withdrawals` |
| GET | `/api/v1/withdrawals/{withdrawal_id}` |

A repeated `idempotency_key` returns the existing record with `200`; a new one returns `201`.

### Portal review (JWT)
A PSP login only ever sees and acts on its own PSP's requests. An admin sees all of them but can't decide. Paths are the same for `deposits` and `withdrawals`:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/portal/deposits` | filters: `status, psp_code (admin only), currency, customer, callback_failed, date_from, date_to, limit, offset` |
| GET | `/api/v1/portal/deposits/{id}` | |
| POST | `/api/v1/portal/deposits/{id}/processing` | PSP login: claim a pending request |
| POST | `/api/v1/portal/deposits/{id}/approve` | PSP login: `{comment?}` → callback |
| POST | `/api/v1/portal/deposits/{id}/reject` | PSP login: `{reason}` (required) → callback |
| POST | `/api/v1/portal/deposits/{id}/resend-callback` | admin or PSP login: new delivery cycle |

Status flow: `pending → processing → approved | rejected`, or `pending → approved | rejected` directly. Approved and rejected are final. Rows are locked during a decision, so two logins cannot both decide the same request.

### Meta & monitoring
| Method | Path | Auth |
|---|---|---|
| GET | `/health` | none. Liveness. |
| GET | `/health/ready` | none. DB check, `callbacks_pending`, `callbacks_failed`. Returns 503 if the DB is down. |
| GET | `/api/v1/meta/error-codes` | none |
| GET | `/api/v1/meta/public-key` | none. Portal RSA public key (PEM). |
| GET | `/api/v1/audit-logs` | admin. Filters: `action` (prefix), `target` |

## Error format

```json
{ "success": false, "error_code": "E2002", "message": "Currency EUR is not allowed for this PSP", "details": [] }
```

| Range | Area |
|---|---|
| `E1xxx` | authentication, signatures, HTTPS |
| `E2xxx` | PSP configuration |
| `E3xxx` | transactions |
| `E4xxx` | users, not found |
| `E5xxx` | server / availability |

The full list is at `GET /api/v1/meta/error-codes`.

## Request security (CRM → portal)

### 1. MD5 signature (required)
Every CRM `POST` includes `timestamp` (ISO-8601, UTC `...Z` or with an offset such as IST `...+05:30`, within ±5 min) and `signature`:

```
signature = md5("k1=v1&k2=v2&...&salt=<SIGNATURE_SALT>")   # keys sorted alphabetically
```

| Request | Signed fields |
|---|---|
| Deposit | `amount, bank_account_id, currency, customer_email, timestamp` |
| Withdrawal | `amount, currency, customer_email, dest_account_number, source_account_id, timestamp` |

Amounts are written without trailing zeros (`12500`, `999.5`).

### 2. RSA signature (when the PSP has a `client_public_key`)
The CRM signs the **exact raw request body** with its private key: RSA PKCS#1 v1.5 + SHA-256, base64-encoded in the `X-Signature` header. Keys must be RSA with at least 2048 bits.

To sign by hand: `python scripts/sign_request.py deposit body.json <salt> [crm_private_key.pem]` (add `--ist` for an IST timestamp).

## Callbacks (portal → CRM)

After each approve or reject, the portal `POST`s to the PSP's `callback_url` with:
* HTTP Basic auth (`callback_username` / `callback_password`)
* `X-Signature`: RSA-SHA256 of the raw body, signed with the portal key. Verify it with `/api/v1/meta/public-key`.
* A `signature` field in the body: MD5 over `amount, currency, customer_email, <deposit_id|withdrawal_id>, status, timestamp` plus the salt.

```json
{
  "event": "deposit.approved",
  "deposit_id": "DEP-00001",
  "status": "approved",
  "amount": 12500,
  "currency": "INR",
  "customer_email": "rahul@clientcrm.com",
  "reviewed_by": "Priya Desai",
  "comment": "UTR and screenshot verified",
  "signature": "…",
  "timestamp": "2026-09-25T09:45:00Z"
}
```

Delivery goes through a database queue. The decision and the pending callback are saved in the same transaction, and a background worker delivers it. Any response other than `200` is retried up to 3 times (2 s, 4 s, 8 s). Pending callbacks survive restarts and DR failover, and rows are claimed with `SKIP LOCKED`, so several instances never double-send. Failed deliveries appear in `callbacks_failed` and under `?callback_failed=true`, and can be re-sent.

## Production notes

* Put a TLS-terminating proxy in front (nginx or a load balancer) with an RSA ≥ 2048-bit certificate. Forward `X-Forwarded-Proto` and run uvicorn with `--proxy-headers`. To serve under `/PSPtest/` or `/PSP/`, add `--root-path /PSPtest`.
* API tokens and secrets are stored only as SHA-256 hashes. Portal passwords use bcrypt.
* Not built yet: the Report module (PDF).
* With several app instances, set `RUN_MIGRATIONS_ON_STARTUP=false` and run `alembic upgrade head` once as a deploy step.
