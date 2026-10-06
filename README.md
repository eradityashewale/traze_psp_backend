# PSP Portal Backend

Small backend for the PSP Portal. The CRM submits deposits and withdrawals, the PSP team approves or rejects them, and the portal sends a signed callback back to the CRM.

**Stack:** Python 3.12 · FastAPI · MySQL 8 · SQLAlchemy 2.0 · PyMySQL · cryptography

## New PSP Checklist coverage

| # | Checklist question | How this backend answers it |
|---|---|---|
| 1.1 | Documentation | This README + Swagger at `/docs` + `/openapi.json` |
| 1.2 / 1.3 | UAT / Production credentials | `ENVIRONMENT=uat\|production`, `PUBLIC_API_BASE_URL`. Portal logins are created per user by an admin. |
| 2 | HTTPS/SSL | `ENFORCE_HTTPS=true` rejects plain HTTP with `E1008` and sends HSTS. Callback URLs must be `https://`. |
| 3 | Request authentication | `Authorization: Bearer <API_TOKEN>` + `X-API-Secret` |
| 4 | HTTP Basic auth for webhooks | `CRM_CALLBACK_USERNAME` / `CRM_CALLBACK_PASSWORD` are sent on every callback and are **required** in UAT and production |
| 5 | Periodic token rotation | Tokens expire after `API_TOKEN_VALIDITY_DAYS` (90 = quarterly). Rotation keeps the old token valid for `ROTATION_GRACE_HOURS`. |
| 6 | Salted hash of critical fields | MD5 signature on requests **and** callbacks |
| 7 | Public/private key | CRM signs request bodies with RSA (`X-Signature`). The portal signs callbacks with its own key (`GET /api/v1/meta/public-key`). |
| 8 | Key length | RSA keys under `MIN_RSA_KEY_BITS` (2048) are rejected. TLS cert on the proxy: 2048+ bits. |
| 9 | MFA | **Not implemented** (removed by decision). Portal login is password-only, with bcrypt hashing and a lockout after 5 failed attempts. |
| 10 | PCI compliance | Audit trail (`/api/v1/audit-logs`), hashed credentials, no card data stored. The **level itself** is a business answer. |
| 11 | DR site | Stateless app plus a DB-backed callback queue: nothing is lost on restart or failover, and several instances can run safely. DR infrastructure itself is ops. |
| 12 | Availability monitoring | `GET /health`, `GET /health/ready` (DB + callback backlog), stable error codes (`GET /api/v1/meta/error-codes`) |
| 13 | Contacts & service hours | One `contact_email` on each PSP |

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
* With `ENVIRONMENT=uat` or `production`, the app **refuses to start** if any of these hold: HTTPS not enforced, HTTP callbacks allowed, the CRM callback URL or its username/password not set, signatures off, or a short JWT secret.

## Database migrations

Schema changes are managed with **Alembic**. Migration files live in `alembic/versions/` and are applied in order.

| Revision | What it does |
|---|---|
| `0001` | Initial schema: all tables |
| `0002` | Adds `psps.account_number` |
| `0003` | Makes `deposits.bank_account_id` optional (admin-submitted deposits have none) |
| `0004` | Makes the withdrawal destination bank details optional (admin-submitted withdrawals may omit them) |
| `0005` | Adds `withdrawals.screenshot_url` (optional screenshot) |
| `0006` | Resets every PSP's `allowed_currencies` to `["INR"]` |
| `0007` | Drops `psps.contacts` (a PSP has one `contact_email`) |
| `0008` | Replaces the `psps.bank_accounts` list with a single `psps.bank_account_id` (keeps the first entry) |
| `0009` | Drops `psps.callback_url`, `callback_username` and `callback_password` (the CRM callback is set in `.env`) |
| `0010` | Drops `psps.allowed_currencies` (INR is the only currency) |
| `0011` | Drops `psps.bank_account_id`; `psps.account_number` is the PSP's one bank account |
| `0012` | Drops `psps.client_public_key` (the CRM's key is a file set in `.env`) |
| `0013` | Adds the `reversed` status to deposits and withdrawals |
| `0014` | Adds `created_by` (`admin` or `crm`) to deposits and withdrawals; existing rows are filled in from the audit log |
| `0015` | Adds `chats` and `chat_messages` (one chat per deposit or withdrawal) |

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
| `admin` | You (the portal operator) | Add, edit and delete PSPs, manage logins, view **all** deposits and withdrawals, submit a deposit or withdrawal for any PSP from the portal, approve or reject any PSP's requests, audit logs. |
| `psp` | Each PSP | Log in, see **only its own** deposits and withdrawals, approve or reject them. |

Creating a PSP also creates its portal login (`login_email`, `login_password`). A PSP can have more logins via `POST /api/v1/users`. When a PSP is deactivated its logins stop working; when it's deleted its logins are removed.

## Endpoints

### Auth & users
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/auth/login` | `{email, password}` → `access_token`, for admins and PSP logins. 5 wrong passwords lock the account for 15 minutes. |
| GET | `/api/v1/auth/me` | |
| POST | `/api/v1/auth/change-password` | `{current_password, new_password}` — change your own password. Same call for admins and PSP logins; the token decides whose it is. |
| GET | `/api/v1/users?psp_code=` | admin: list logins, optionally for one PSP |
| POST | `/api/v1/users` | admin: create an admin (`role: admin`) or an extra PSP login (`role: psp`, `psp_code`) |
| PATCH | `/api/v1/users/{id}` | admin: rename, deactivate, reset password, `unlock` |

### PSP management (admin)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/psps` | list |
| POST | `/api/v1/psps` | create the PSP **and its portal login** (`login_email`, `login_password`). `psp_code` is auto-generated (`PSP-XXXXXXXX`) and returned in the response; do not send it. A PSP has one bank account, given by `account_number` (required); the CRM sends that same number as `bank_account_id` on deposits and `source_account_id` on withdrawals. **The response shows `api_token`, `api_secret` and `signature_salt` once.** |
| GET | `/api/v1/psps/{psp_code}` | |
| PUT | `/api/v1/psps/{psp_code}` | partial update: status, `ifsc_code`, `account_number`, `contact_email` |
| POST | `/api/v1/psps/{psp_code}/rotate-credentials` | `{grace_hours?, rotate_salt?}` |
| DELETE | `/api/v1/psps/{psp_code}` | refused while requests are pending or processing. Removes the PSP's logins; transaction history is kept. |

### CRM API
Headers: `Authorization: Bearer <API_TOKEN>`, `X-API-Secret: <API_SECRET>`, plus `X-Signature` on POSTs if `CRM_PUBLIC_KEY_PATH` is set.

| Method | Path |
|---|---|
| POST | `/api/v1/deposits` |
| GET | `/api/v1/deposits/{deposit_id}` |
| POST | `/api/v1/withdrawals` |
| GET | `/api/v1/withdrawals/{withdrawal_id}` |

Every accepted `POST` creates a new record and returns `201`. There is no `idempotency_key`: sending the same request twice creates two records.

`INR` is the only currency. A deposit has no `currency` field (CRM API and portal) and is always stored as `INR`. On a withdrawal `currency` defaults to `INR` when it is not sent, and any other value is rejected with `E1000`. A PSP has no currency setting. CRM requests carry no `timestamp`; the portal records `created_at` itself when the request arrives.

### Portal review (JWT)
A PSP login only ever sees and acts on its own PSP's requests. An admin sees all of them, can submit new ones, and can approve or reject any of them. Paths are the same for `deposits` and `withdrawals`:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/portal/deposits` | filters: `status, psp_code (admin only), currency, customer, callback_failed, date_from, date_to, limit, offset` |
| POST | `/api/v1/portal/deposits` | admin: submit a request for a PSP. Same fields as the CRM API plus `psp_code`; no `signature`, no `bank_account_id` on a deposit, and no `source_account_id` on a withdrawal (the PSP's `account_number` is used). Starts as `pending`, and that PSP's login or an admin reviews it. |
| GET | `/api/v1/portal/deposits/{id}` | |
| POST | `/api/v1/portal/deposits/{id}/processing` | admin or PSP login: claim a pending request |
| POST | `/api/v1/portal/deposits/{id}/approve` | admin or PSP login: `{comment?}` → callback |
| POST | `/api/v1/portal/deposits/{id}/reject` | admin or PSP login: `{reason}` (required) → callback |
| POST | `/api/v1/portal/deposits/{id}/reverse` | admin or PSP login: `{reason}` (required). Only an approved request, and only once → callback |
| POST | `/api/v1/portal/deposits/{id}/resend-callback` | admin or PSP login: new delivery cycle |

Status flow: `pending → processing → approved | rejected`, or `pending → approved | rejected` directly. An approved request can be reversed once (`approved → reversed`). Rejected and reversed are final. Rows are locked during a decision, so two logins cannot both decide the same request.

Every deposit and withdrawal records who submitted it in `created_by`: `admin` when an admin submitted it from the portal, `crm` when it came through the CRM API. The portal responses include it.

### Dashboard (JWT)
One endpoint feeds the overview page for both roles. The token decides the scope: an admin gets the figures across every PSP, a PSP login gets them for its own PSP's requests only.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/portal/dashboard` | `days` (default 30, request cards), `activity_days` (default 14, activity chart), `recent_limit` (default 10) |

The response holds `role`, `full_name`, `psp_code` / `psp_name` (`null` for an admin), and one block per widget:

- `deposits`, `withdrawals`: requests created in the last `days` days: `total`, `today`, `peak`, `low`, `avg` (per day) and a zero-filled daily `series`.
- `pending_requests`: deposits and withdrawals now in `pending`.
- `pending_deposits`, `pending_withdrawals`, `approved_deposits`, `approved_withdrawals`, `rejected_deposits`, `rejected_withdrawals`, `reversed_deposits`, `reversed_withdrawals`: requests now in that status, split by kind.
- `total_psp_count`: number of PSPs, whatever their status. Always `1` for a PSP login.
- `approval_rate`: approved share of all requests, in percent.
- `activity`: `{date, deposits, withdrawals}` per day for the last `activity_days` days.
- `requests_overview`: deposits and withdrawals together by current status (`pending, processing, approved, rejected, reversed, total`).
- `recent_transactions`: the latest deposits and withdrawals mixed, newest first, each with `kind`.

Days are UTC calendar days. `pending_requests`, the per-status counts, `approval_rate` and `requests_overview` cover all requests, not only the period.

### Chat on a request (JWT)
Each deposit or withdrawal can have one chat between the admins and that PSP's logins, e.g. when a request was approved but the customer has still not received the money. Any admin and any login of that PSP can write in it. A PSP login only reaches the chats of its own PSP's requests. Paths are the same for `deposits` and `withdrawals`:

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/portal/chats` | inbox, latest activity first, with `unread_count`. Filters: `status (open\|closed), kind (deposit\|withdrawal), unread, limit, offset` |
| GET | `/api/v1/portal/deposits/{id}/chat` | the chat and its messages, oldest first. `status` is `null` while nobody has written. `after_id` returns only newer messages (polling). Opening it marks the messages as read for your side. |
| POST | `/api/v1/portal/deposits/{id}/chat/messages` | admin or PSP login: `{message}`. The first message opens the chat. |
| POST | `/api/v1/portal/deposits/{id}/chat/close` | admin: close the chat once the issue is settled |
| POST | `/api/v1/portal/deposits/{id}/chat/reopen` | admin: reopen a closed chat |

A closed chat stays readable but refuses new messages with `E3006` until an admin reopens it. Read state is kept per side (admins / the PSP's logins), not per login. The chat does not change the request's status and is not sent to the CRM. Opening, closing and reopening are written to the audit log (`chat.opened`, `chat.closed`, `chat.reopened`).

### Screenshots (S3)

Deposits and withdrawals are submitted as **`multipart/form-data`** (not JSON), on both the CRM API and the portal: the usual fields as form fields, plus the screenshot as a file field named `screenshot`. The file is stored in a private S3 bucket (`S3_BUCKET`, `AWS_REGION`), not on the app server.

* Accepted: PNG, JPEG, WEBP or PDF, up to `SCREENSHOT_MAX_MB` (5). The type is checked from the file's content.
* Deposit: the `screenshot` file is required, on the CRM API and when an admin submits from the portal.
* Withdrawal: the `screenshot` file is optional.

```bash
curl -X POST https://<host>/api/v1/deposits \
  -H "Authorization: Bearer <API_TOKEN>" -H "X-API-Secret: <API_SECRET>" \
  -F customer_name="Rahul Sharma" -F customer_email=rahul@example.com \
  -F amount=1200 -F bank_account_id=50100123456789 \
  -F signature=<md5> \
  -F screenshot=@receipt.png
```

Files are stored under `screenshots/deposit/` and `screenshots/withdrawal/` in the bucket. In portal responses `screenshot_url` is a presigned link valid for `S3_URL_EXPIRES_SECONDS` (900); fetch the record again for a fresh one. On EC2, give the instance an IAM role with `s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::<bucket>/screenshots/*` and leave `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` empty. Keep **Block Public Access** on for the bucket.

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
{ "success": false, "error_code": "E2003", "message": "Bank account sbi is not managed by this PSP", "details": [] }
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
Every CRM `POST` includes a `signature`. No `timestamp` is sent.

```
signature = md5("k1=v1&k2=v2&...&salt=<SIGNATURE_SALT>")   # keys sorted alphabetically
```

| Request | Signed fields |
|---|---|
| Deposit | `amount, bank_account_id, customer_email` |
| Withdrawal | `amount, currency, customer_email, dest_account_number, source_account_id` |

Amounts are written without trailing zeros (`12500`, `999.5`).

### 2. RSA signature (when `CRM_PUBLIC_KEY_PATH` is set)
One CRM serves every PSP, so its public key is a PEM file on the server, pointed to by `CRM_PUBLIC_KEY_PATH` in `.env`. It is not part of a PSP. Leave it empty to skip this check.

The CRM signs the **exact raw request body** (the multipart bytes as sent, boundaries and file included) with its private key: RSA PKCS#1 v1.5 + SHA-256, base64-encoded in the `X-Signature` header. Keys must be RSA with at least 2048 bits.

To get the MD5 `signature` by hand: `python scripts/sign_request.py deposit body.json <salt>`, then send the printed values as form fields.

## Callbacks (portal → CRM)

One CRM serves every PSP, so its callback endpoint is set once in `.env` (`CRM_CALLBACK_URL`, `CRM_CALLBACK_USERNAME`, `CRM_CALLBACK_PASSWORD`) and is not part of a PSP. After each approve, reject or reverse, the portal `POST`s to `CRM_CALLBACK_URL` with:
* HTTP Basic auth (`CRM_CALLBACK_USERNAME` / `CRM_CALLBACK_PASSWORD`)
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
