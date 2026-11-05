"""Build PSP_Portal_CRM_Integration_Guide.pdf, the guide we send to the CRM team.

Edit the content below, then run:
    pip install reportlab
    python scripts/build_crm_guide.py [output.pdf]
"""

import re
import sys
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

TITLE = "PSP Portal - CRM Integration Guide"
VERSION = "Version 1.1 | November 2026 | For the CRM development team"

NAVY = colors.HexColor("#1F3A5F")
MUTED = colors.HexColor("#5A6472")
GRID = colors.HexColor("#C9D1DB")
HEAD_BG = colors.HexColor("#E8EEF5")
CODE_BG = colors.HexColor("#F4F6F8")
NOTE_BG = colors.HexColor("#FFF7E6")
NOTE_LINE = colors.HexColor("#E0A82E")

body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=13.5, spaceAfter=5)
cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.6, leading=11.5)
cell_head = ParagraphStyle("cell_head", parent=cell, fontName="Helvetica-Bold")
h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY,
                    spaceBefore=12, spaceAfter=8)
h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=NAVY,
                    spaceBefore=8, spaceAfter=5)
title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=24, leading=30, textColor=NAVY,
                       alignment=TA_CENTER, spaceAfter=6)
subtitle = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=13, leading=18, textColor=MUTED,
                          alignment=TA_CENTER, spaceAfter=4)
listed = ParagraphStyle("listed", parent=body, leftIndent=14, bulletIndent=3, spaceAfter=2.5,
                        bulletFontName="Helvetica", bulletFontSize=10)
code_style = ParagraphStyle("code", fontName="Courier", fontSize=7.8, leading=10.2, backColor=CODE_BG,
                            borderPadding=7, leftIndent=7, rightIndent=7, spaceBefore=9, spaceAfter=11)
note_style = ParagraphStyle("note", parent=body, backColor=NOTE_BG, borderColor=NOTE_LINE, borderWidth=0.6,
                            borderPadding=7, leftIndent=7, rightIndent=7, spaceBefore=8, spaceAfter=12)


def md(text: str) -> str:
    """Escape text and turn `code` into Courier and **bold** into bold."""
    text = escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.6">\1</font>', text)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)


story: list = []


def H1(text):
    story.append(CondPageBreak(60 * mm))
    story.append(Paragraph(md(text), h1))


def H2(text):
    story.append(CondPageBreak(35 * mm))
    story.append(Paragraph(md(text), h2))


def P(text):
    story.append(Paragraph(md(text), body))


def NOTE(text):
    story.append(Paragraph(md(text), note_style))


def BULLETS(*items):
    for item in items:
        story.append(Paragraph(md(item), listed, bulletText="•"))
    story.append(Spacer(1, 5))


def STEPS(*items):
    for n, item in enumerate(items, 1):
        story.append(Paragraph(md(item), listed, bulletText=f"{n}."))
    story.append(Spacer(1, 5))


def CODE(text):
    story.append(Preformatted(text.strip("\n"), code_style))


def TABLE(widths, header, *rows):
    total = 481
    col = [total * w / sum(widths) for w in widths]
    data = [[Paragraph(md(h), cell_head) for h in header]]
    data += [[Paragraph(md(str(c)), cell) for c in row] for row in rows]
    t = Table(data, colWidths=col, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(Spacer(1, 3))
    story.append(t)
    story.append(Spacer(1, 10))


# ---------------------------------------------------------------- content

story += [
    Spacer(1, 30),
    Paragraph("PSP Portal", title),
    Paragraph("CRM Integration Guide", title),
    Spacer(1, 6),
    Paragraph("Deposits, Withdrawals and the Callback API", subtitle),
    Paragraph(VERSION, subtitle),
    Spacer(1, 16),
]

H1("1. How the integration works")
P("Your CRM sends deposit and withdrawal requests to the PSP Portal. The PSP team reviews each request in the "
  "portal, and the portal then tells your CRM the result by calling a callback API that you build on your side.")
STEPS(
    "Before a customer deposits, your CRM gets the PSP's bank account and payment QR code from the portal "
    "(a GET call) and shows them to the customer.",
    "The customer pays. Your CRM sends the deposit (with the payment screenshot) or a withdrawal request to the "
    "PSP Portal API.",
    "The portal replies `201` with an ID (`DEP-00001` or `WDL-00001`) and status `pending`.",
    "The PSP team approves or rejects the request in the portal. An approved request can later be reversed once.",
    "The portal sends a callback (HTTP POST) to your CRM with the final result.",
    "Your CRM can also ask for the status at any time with a GET call.",
)
TABLE([1, 3.3, 1], ["Status", "Meaning", "Callback sent?"],
      ["`pending`", "Request received, waiting for review", "No"],
      ["`processing`", "A reviewer has picked it up", "No"],
      ["`approved`", "Request approved", "Yes"],
      ["`rejected`", "Request rejected (final)", "Yes"],
      ["`reversed`", "An approved request was taken back (final, can happen once)", "Yes"])

H1("2. What you need to do")
TABLE([0.5, 6, 1], ["Step", "Task", "Section"],
      ["1", "Build one callback POST API in your CRM and protect it with HTTP Basic auth (you choose the "
            "username and password).", "3.1, 4"],
      ["2", "Send us the callback URL, username and password, for UAT and for Production. Use the form in "
            "section 14.", "3.1, 14"],
      ["3", "Receive and store the API credentials we send you for each PSP.", "3.2"],
      ["4", "Show the PSP's bank details and payment QR code on your deposit screen.", "7"],
      ["5", "Build the request signature and the deposit and withdrawal calls.", "5, 6, 8, 9"],
      ["6", "Handle the callback: verify it, update your record, reply 200.", "4"],
      ["7", "Run the UAT tests with us, then go live.", "13"])

H1("3. Credentials")
H2("3.1 Credentials you create and send to us")
P("These three values belong to your CRM. You create them and share them with us; we store them in the portal and "
  "use them on every callback. There is one set for all PSPs.")
TABLE([1, 1.6, 2.6], ["Item", "What it is", "How you create it"],
      ["Callback URL", "The address of the POST API in your CRM that receives our callbacks.",
       "Build the endpoint described in section 4 and give us its full URL, for example "
       "`https://crm.yourdomain.com/api/psp/callback`. It must use HTTPS and be reachable from the internet "
       "(or from our server IP, which we share on request for whitelisting)."],
      ["Callback username", "The HTTP Basic auth username that protects the callback URL.",
       "Choose any name, for example `psp_portal`."],
      ["Callback password", "The HTTP Basic auth password that protects the callback URL.",
       "Generate a long random value (32 characters or more), for example with `openssl rand -base64 32`. "
       "Do not reuse a password from another system."])
P("Create a separate set for UAT and for Production.")
H2("3.2 Credentials we send to you")
P("You receive one set for each PSP. Store every set against its PSP, because each PSP has its own values.")
TABLE([1, 1, 2], ["Item", "Looks like", "Used for"],
      ["Base URL", "`https://<portal-host>`", "All API calls. Different for UAT and Production."],
      ["`api_token`", "`pspt_...`", "`Authorization` header"],
      ["`api_secret`", "`psps_...`", "`X-API-Secret` header"],
      ["`signature_salt`", "64 hex characters", "Key for the HMAC signature on your requests and on our callbacks"],
      ["PSP account number", "digits / letters",
       "Sent as `bank_account_id` on a deposit and `source_account_id` on a withdrawal. It is also returned "
       "by the payment details call (section 7)."])
BULLETS(
    "The API token and secret expire every 90 days. We send you a new pair before that.",
    "After a rotation the old pair keeps working for 72 hours, so you can switch without downtime.",
    "If you think a credential has leaked, tell us at once. We can cancel it immediately and issue a new one.",
)
H2("3.3 Sharing credentials safely")
BULLETS(
    "Share secrets through a password manager or an encrypted channel, not by plain email or chat.",
    "Keep them only on your server, in environment variables or a secret store. Never put them in frontend code "
    "or in source control.",
    "Treat `signature_salt` like a password.",
)

H1("4. The callback API you build")
P("This is the API in your CRM that the portal calls after each approve, reject or reverse.")
H2("4.1 The request we send")
TABLE([1, 3], ["Part", "Value"],
      ["Method and URL", "`POST` to your callback URL"],
      ["Authorization", "`Basic base64(username:password)`, using the username and password you gave us"],
      ["Content-Type", "`application/json`"],
      ["X-Signature", "RSA signature of the raw request body, base64 encoded (see 4.3)"],
      ["Body", "JSON, see below"])
CODE("""
{
  "event": "deposit.approved",
  "deposit_id": "DEP-00001",
  "status": "approved",
  "amount": 12500,
  "currency": "INR",
  "customer_email": "rahul@example.com",
  "reviewed_by": "Priya Desai",
  "comment": "UTR and screenshot verified",
  "signature": "9c1f...e27a",
  "timestamp": "2026-09-25T09:45:00Z",
  "nonce": "3f9c1e0a7b2d4c5e8f6a1b2c3d4e5f60"
}
""")
TABLE([1, 3.5], ["Field", "Description"],
      ["`event`", "`deposit.approved`, `deposit.rejected`, `deposit.reversed`, `withdrawal.approved`, "
                  "`withdrawal.rejected` or `withdrawal.reversed`"],
      ["`deposit_id` or `withdrawal_id`", "The ID we returned when you created the request. A withdrawal callback "
                                          "carries `withdrawal_id` instead of `deposit_id`."],
      ["`status`", "`approved`, `rejected` or `reversed`"],
      ["`amount`", "Number, in INR"],
      ["`currency`", "Always `INR`"],
      ["`customer_email`", "The email sent on the original request"],
      ["`reviewed_by`", "Name of the person who took the decision"],
      ["`comment`", "The approval note, or the reason for a rejection or reversal. Can be null on an approval."],
      ["`signature`", "HMAC-SHA256 signature of the callback (see 4.3)"],
      ["`timestamp`", "UTC time the callback was sent, ISO 8601"],
      ["`nonce`", "Random value, new on every delivery attempt"])
H2("4.2 The response you return")
BULLETS(
    "Return HTTP `200` once the callback is verified and saved. The body is ignored.",
    "Only `200` counts as delivered. `201`, `204` and redirects are treated as failures.",
    "Reply within 10 seconds. Do slow work (emails, reports) after you have replied.",
    "Return `401` if the Basic auth or a signature is wrong.",
)
H2("4.3 Checks your endpoint must do, in this order")
STEPS(
    "**Basic auth.** Compare the `Authorization` header with your username and password.",
    "**RSA signature.** `X-Signature` is RSA PKCS#1 v1.5 with SHA-256 over the raw body bytes, base64 encoded. "
    "Verify it with the portal public key, which you download once from `GET <base-url>/api/v1/meta/public-key` "
    "(PEM). Verify the bytes exactly as received, before parsing the JSON.",
    "**Find your record** by `deposit_id` or `withdrawal_id`. This also tells you which PSP it belongs to, and so "
    "which `signature_salt` to use. If you do not know the ID, reply `200` and ignore it.",
    "**HMAC signature.** Build the string from these fields, sorted alphabetically, joined as "
    "`key=value&key=value`: `amount`, `currency`, `customer_email`, `deposit_id` (or `withdrawal_id`), `nonce`, "
    "`status`, `timestamp`. Compute HMAC-SHA256 with that PSP's `signature_salt` as the key, as lowercase hex, "
    "and compare it with `signature`.",
    "**Replay check.** Reject a `timestamp` older than 5 minutes, and ignore a `nonce` you have already processed.",
    "**Update your record** and reply `200`.",
)
P("Rules for the HMAC string:")
BULLETS(
    "`amount` is written without trailing zeros: `12500`, not `12500.00`; `999.5`, not `999.50`.",
    "`timestamp` is the text exactly as received, for example `2026-09-25T09:45:00Z`.",
)
P("Example string for the callback above:")
CODE("""
amount=12500&currency=INR&customer_email=rahul@example.com&deposit_id=DEP-00001
&nonce=3f9c1e0a7b2d4c5e8f6a1b2c3d4e5f60&status=approved&timestamp=2026-09-25T09:45:00Z
(one line, no line break)
""")
H2("4.4 Retries and duplicates")
BULLETS(
    "If your endpoint does not return `200`, we retry 3 more times, after about 2, 4 and 8 seconds.",
    "If all attempts fail, the PSP team re-sends the callback by hand from the portal once your endpoint is back.",
    "Every attempt has a new `timestamp`, `nonce` and `signature`.",
    "The same result can reach you more than once. If the record already has that status, reply `200` and do not "
    "apply it again (for example, do not credit the customer twice).",
    "A request can get two callbacks over its life: `approved` first and `reversed` later.",
)
H2("4.5 Example callback endpoint (Python / Flask)")
P("The same logic applies in any language.")
CODE('''
import base64, hashlib, hmac, json
from decimal import Decimal
from flask import Flask, request
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

app = Flask(__name__)
USERNAME, PASSWORD = "psp_portal", "<the password you generated>"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
# Saved once from GET <base-url>/api/v1/meta/public-key
PORTAL_KEY = serialization.load_pem_public_key(open("portal_public_key.pem", "rb").read())

def amount_text(value):                       # 12500 -> "12500", 999.5 -> "999.5"
    return format(Decimal(str(value)).normalize(), "f")

@app.post("/api/psp/callback")
def psp_callback():
    raw = request.get_data()                  # raw bytes, before any JSON parsing

    # 1. Basic auth
    if not hmac.compare_digest(request.headers.get("Authorization", ""), EXPECTED_AUTH):
        return "", 401

    # 2. RSA signature of the raw body
    try:
        PORTAL_KEY.verify(base64.b64decode(request.headers.get("X-Signature", "")),
                          raw, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return "", 401

    # 3. Find your own record (and the PSP it belongs to)
    data = json.loads(raw)
    id_field = "deposit_id" if "deposit_id" in data else "withdrawal_id"
    record = find_record(data[id_field])      # your own lookup
    if record is None:
        return "", 200                        # unknown ID: acknowledge and ignore

    # 4. HMAC signature with that PSP's signature_salt
    fields = {
        id_field: data[id_field], "status": data["status"],
        "amount": amount_text(data["amount"]), "currency": data["currency"],
        "customer_email": data["customer_email"],
        "timestamp": data["timestamp"], "nonce": data["nonce"],
    }
    message = "&".join(f"{k}={fields[k]}" for k in sorted(fields))
    expected = hmac.new(record.psp_signature_salt.encode(), message.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, data["signature"]):
        return "", 401

    # 5. Replay check, then 6. update and reply
    if too_old(data["timestamp"]):            # older than 5 minutes
        return "", 401
    if not nonce_seen_before(data["nonce"]):
        apply_result(record, data["status"], data.get("comment"))
    return "", 200
''')

H1("5. Calling the portal API")
P("Send these two headers on every call:")
CODE("""
Authorization: Bearer <api_token>
X-API-Secret: <api_secret>
""")
BULLETS(
    "HTTPS only. Plain HTTP is rejected.",
    "POST bodies are `multipart/form-data`, not JSON, because the screenshot file is sent with the request.",
    "GET calls (payment details and status) need only these two headers, with no signature.",
    "Use the token, secret, salt and account number of the PSP the request is for.",
    "Call the portal only from your CRM server, never from the customer's browser or app, so the token and "
    "secret are never exposed.",
    "INR is the only currency.",
)

H1("6. Signing a POST request")
P("Every POST carries three extra form fields:")
TABLE([1, 4.5], ["Field", "Value"],
      ["`timestamp`", "Current Unix time in seconds (UTC). Must be within 5 minutes of our server time, so keep "
                      "your server clock in sync (NTP)."],
      ["`nonce`", "A random value, new for every request: 16 to 64 characters of `A-Z a-z 0-9 _ -`. A UUID "
                  "without dashes works."],
      ["`signature`", "HMAC-SHA256 as lowercase hex, built as described below"])
P("To build the signature:")
STEPS(
    "Take the signed fields from the table below.",
    "Sort the field names alphabetically.",
    "Join them as `key=value&key=value`.",
    "Compute HMAC-SHA256 of that string with `signature_salt` as the key. Output lowercase hex.",
)
TABLE([1, 4.5], ["Request", "Signed fields (already in alphabetical order)"],
      ["Deposit", "`amount`, `bank_account_id`, `customer_email`, `method`, `nonce`, `path`, `timestamp`"],
      ["Withdrawal", "`amount`, `currency`, `customer_email`, `dest_account_number`, `method`, `nonce`, `path`, "
                     "`source_account_id`, `timestamp`"])
P("Rules that cause most signature failures:")
BULLETS(
    "`method` is `POST`.",
    "`path` is `/api/v1/deposits` or `/api/v1/withdrawals`, with no host name and no URL prefix.",
    "`amount` is written without trailing zeros: `1200`, not `1200.00`; `999.5`, not `999.50`.",
    "`currency` is `INR` in the withdrawal signature, even if you do not send the field.",
    "Send `customer_email` in lowercase, and sign exactly the value you send.",
    "`method` and `path` are only part of the signature string. Do not send them as form fields.",
)
P("Example string for a deposit:")
CODE("""
amount=1200&bank_account_id=50100123456789&customer_email=rahul@example.com&method=POST
&nonce=3f9c1e0a7b2d4c5e8f6a1b2c3d4e5f60&path=/api/v1/deposits&timestamp=1791625500
(one line, no line break)
""")
CODE('''
import hashlib, hmac, time, uuid

def sign(fields: dict, salt: str) -> str:
    message = "&".join(f"{k}={fields[k]}" for k in sorted(fields))
    return hmac.new(salt.encode(), message.encode(), hashlib.sha256).hexdigest()

timestamp, nonce = int(time.time()), uuid.uuid4().hex
signature = sign({
    "amount": "1200", "bank_account_id": "50100123456789",
    "customer_email": "rahul@example.com",
    "method": "POST", "path": "/api/v1/deposits",
    "timestamp": timestamp, "nonce": nonce,
}, SIGNATURE_SALT)
''')
P("**Optional RSA signing.** If we agree to switch it on, you also send an `X-Signature` header on every POST: RSA "
  "PKCS#1 v1.5 with SHA-256 over the exact raw request body, base64 encoded, signed with your private key (2048 "
  "bits or more). You give us the public key. It is off unless we confirm it with you.")

H1("7. Show the payment details to the customer")
P("Before a customer pays a deposit, they need to know where to send the money. This call returns the PSP's bank "
  "account and payment QR code, so your CRM can show them on its deposit screen.")
CODE("GET <base-url>/api/v1/deposits/payment-details")
P("It needs only the two headers from section 5, with no signature and no parameters. The credentials tell us "
  "which PSP is calling, and we return that PSP's details.")
CODE("""
curl https://<portal-host>/api/v1/deposits/payment-details \\
  -H "Authorization: Bearer <api_token>" -H "X-API-Secret: <api_secret>"
""")
P("Response `200`:")
CODE("""
{
  "psp_code": "PSP001",
  "psp_name": "XYZ Payments",
  "bank_account_id": "50100123456789",
  "bank_name": "HDFC Bank",
  "account_name": "XYZ Payments Pvt Ltd",
  "account_number": "50100123456789",
  "ifsc_code": "HDFC0001234",
  "branch": "Andheri East, Mumbai",
  "qr_code_url": "https://<bucket>.s3.ap-south-1.amazonaws.com/psp-qr/...&X-Amz-Signature=...",
  "payment_methods": ["imps", "rtgs", "upi"],
  "currency": "INR",
  "updated_at": "2026-11-01T10:15:00Z"
}
""")
TABLE([1.2, 4], ["Field", "Description"],
      ["`psp_code`, `psp_name`", "The PSP whose credentials you used"],
      ["`bank_account_id`", "Send this value back as `bank_account_id` when you create the deposit (section 8)"],
      ["`bank_name`, `account_name`, `account_number`, `ifsc_code`, `branch`",
       "The bank account the customer pays into. `account_name` is the account holder name. Show all of them."],
      ["`qr_code_url`", "Link to the payment QR image (for UPI). It expires after about 15 minutes, so show it "
                        "straight away and do not store it. It is `null` when the PSP has no QR code; then show "
                        "only the bank details."],
      ["`payment_methods`", "How the customer can pay: `imps`, `rtgs` or `upi`. Send the one they used as "
                            "`payment_method` on the deposit."],
      ["`currency`", "Always `INR`"],
      ["`updated_at`", "When the PSP's details last changed"])
H2("7.1 How to use it")
STEPS(
    "The customer opens the deposit screen in your CRM.",
    "Your CRM server calls `payment-details` with the credentials of the PSP you want the customer to pay.",
    "Your CRM shows the bank details and the QR code to the customer.",
    "The customer pays, then gives you the screenshot and UTR number.",
    "Your CRM creates the deposit (section 8) with the **same PSP's credentials** and the `bank_account_id` from "
    "step 2.",
)
NOTE("**Call it every time a customer opens the deposit screen. Do not cache the details.** The PSP team can change "
     "the bank account at any time. A customer who sees an old account may send money to an account that is no "
     "longer used. The response carries `Cache-Control: no-store`.")
H2("7.2 Working with several PSPs")
P("Each PSP has its own credentials, bank account and QR code. To offer more than one:")
BULLETS(
    "Call `payment-details` once for each PSP, each with that PSP's own token and secret. The calls can run in "
    "parallel.",
    "Leave out any PSP that returns an error (for example `E2001` or `E2008`).",
    "Either let the customer choose one of the remaining PSPs, or pick one yourself (for example in turn, or by "
    "amount) and show only that one.",
    "Remember which PSP the customer paid, and create the deposit with that PSP's credentials. Otherwise the "
    "request is rejected with `E2003`, or reaches the wrong PSP team.",
)
H2("7.3 Errors")
TABLE([0.7, 0.5, 2.3, 2.3], ["Code", "HTTP", "Meaning", "What to do"],
      ["`E2001`", "403", "PSP is inactive", "Do not offer deposits through this PSP"],
      ["`E2008`", "409", "The PSP has no bank details set up yet",
       "Do not offer deposits through this PSP; tell us"],
      ["`E1001` to `E1014`", "401", "Credentials missing, wrong, expired or revoked", "See section 11"])

H1("8. Create a deposit")
CODE("POST <base-url>/api/v1/deposits")
TABLE([1.3, 0.6, 3], ["Field", "Required", "Notes"],
      ["`customer_name`", "Yes", "Up to 200 characters"],
      ["`customer_email`", "Yes", "Valid email address"],
      ["`amount`", "Yes", "Greater than 0, at most 2 decimal places, in INR"],
      ["`bank_account_id`", "Yes", "The `bank_account_id` from the payment details (section 7)"],
      ["`payment_method`", "Yes", "How the customer paid: `imps`, `rtgs` or `upi`"],
      ["`screenshot`", "Yes", "File: PNG, JPEG, WEBP or PDF, up to 5 MB"],
      ["`utr_number`", "No", "Up to 100 characters"],
      ["`comment`", "No", "Up to 2000 characters"],
      ["`timestamp`, `nonce`, `signature`", "Yes", "See section 6"])
CODE("""
curl -X POST https://<portal-host>/api/v1/deposits \\
  -H "Authorization: Bearer <api_token>" -H "X-API-Secret: <api_secret>" \\
  -F customer_name="Rahul Sharma" -F customer_email=rahul@example.com \\
  -F amount=1200 -F bank_account_id=50100123456789 -F payment_method=upi \\
  -F utr_number=UTR123456 \\
  -F timestamp=1791625500 -F nonce=3f9c1e0a7b2d4c5e8f6a1b2c3d4e5f60 \\
  -F signature=<hmac hex> -F screenshot=@receipt.png
""")
P("Response `201`:")
CODE("""
{ "success": true, "deposit_id": "DEP-00001", "status": "pending",
  "message": "Deposit received and queued for review" }
""")
P("Save the `deposit_id` against your own record, together with the PSP you used. The callback identifies the "
  "request only by this ID.")

H1("9. Create a withdrawal")
CODE("POST <base-url>/api/v1/withdrawals")
TABLE([1.3, 0.6, 3], ["Field", "Required", "Notes"],
      ["`customer_name`", "Yes", "Up to 200 characters"],
      ["`customer_email`", "Yes", "Valid email address"],
      ["`amount`", "Yes", "Greater than 0, at most 2 decimal places"],
      ["`source_account_id`", "Yes", "The PSP account number we gave you"],
      ["`dest_bank_name`", "Yes", "Customer's bank name"],
      ["`dest_account_number`", "Yes", "Customer's account: 4 to 50 letters or digits, no spaces"],
      ["`dest_ifsc`", "Yes", "IFSC (11 characters) or SWIFT/BIC (8 or 11 characters)"],
      ["`dest_account_name`", "Yes", "Account holder name"],
      ["`currency`", "No", "Only `INR`. Defaults to `INR` when not sent."],
      ["`comment`", "No", "Up to 2000 characters"],
      ["`screenshot`", "No", "File: PNG, JPEG, WEBP or PDF, up to 5 MB"],
      ["`timestamp`, `nonce`, `signature`", "Yes", "See section 6"])
CODE("""
curl -X POST https://<portal-host>/api/v1/withdrawals \\
  -H "Authorization: Bearer <api_token>" -H "X-API-Secret: <api_secret>" \\
  -F customer_name="Rahul Sharma" -F customer_email=rahul@example.com \\
  -F amount=5000 -F source_account_id=50100123456789 \\
  -F dest_bank_name="HDFC Bank" -F dest_account_number=123456789012 \\
  -F dest_ifsc=HDFC0001234 -F dest_account_name="Rahul Sharma" \\
  -F timestamp=1791625500 -F nonce=7a1c9e2b4d6f4a8c9b0d1e2f3a4b5c6d \\
  -F signature=<hmac hex>
""")
P("Response `201`:")
CODE("""
{ "success": true, "withdrawal_id": "WDL-00001", "status": "pending",
  "message": "Withdrawal received and queued for review" }
""")

H1("10. Check the status")
CODE("""
GET <base-url>/api/v1/deposits/{deposit_id}
GET <base-url>/api/v1/withdrawals/{withdrawal_id}
""")
P("These need only the two headers from section 5, with no signature. Use them as a fallback when a callback has "
  "not arrived. You can read only the requests created with the same PSP's credentials.")
CODE("""
{ "deposit_id": "DEP-00001", "status": "approved", "amount": "1200.00", "currency": "INR",
  "customer_email": "rahul@example.com", "comment": "UTR verified",
  "reviewed_at": "2026-09-25T09:45:00Z", "created_at": "2026-09-25T09:30:00Z" }
""")

H1("11. Errors")
P("Every error has the same shape:")
CODE("""
{ "success": false, "error_code": "E1005", "message": "Invalid signature", "details": [] }
""")
TABLE([0.75, 0.45, 2.2, 2.1], ["Code", "HTTP", "Meaning", "What to do"],
      ["`E1000`", "422", "A field is missing or not valid", "Read `details` for the field name"],
      ["`E1001`", "401", "Token or secret header missing", "Check the two headers"],
      ["`E1002`", "401", "Token or secret wrong", "Check you are using the right PSP's pair"],
      ["`E1003`", "401", "API token expired", "Ask us for the rotated credentials"],
      ["`E1004`", "401", "`signature`, `timestamp` or `nonce` missing", "Send all three fields"],
      ["`E1005`", "401", "Signature wrong", "Check the rules in section 6"],
      ["`E1007`", "401", "RSA `X-Signature` missing or wrong", "Only when RSA signing is switched on"],
      ["`E1008`", "403", "HTTPS required", "Call the `https://` URL"],
      ["`E1012`", "401", "Timestamp outside the 5 minute window", "Sync your server clock"],
      ["`E1013`", "409", "Nonce already used", "That request was already accepted. See section 12."],
      ["`E1014`", "401", "API token revoked", "Ask us for new credentials"],
      ["`E2001`", "403", "PSP is inactive", "Contact us"],
      ["`E2003`", "422", "Account number does not belong to this PSP",
       "Check `bank_account_id` / `source_account_id`, and that you used the same PSP's credentials"],
      ["`E2008`", "409", "PSP has no bank details set up yet", "Do not offer deposits through this PSP; tell us"],
      ["`E3001`", "404", "ID not found for this PSP", "Check the ID and the credentials used"],
      ["`E3004`", "422", "Screenshot type or size not accepted", "PNG, JPEG, WEBP or PDF, up to 5 MB"],
      ["`E5000` to `E5002`", "500 / 503", "Problem on our side", "Retry later; contact us if it continues"])
P("The full list is at `GET <base-url>/api/v1/meta/error-codes`. Interactive API documentation is at "
  "`<base-url>/docs`.")

H1("12. Retries and timeouts")
BULLETS(
    "There is no idempotency key. If you send the same request again with a new nonce, a second record is created.",
    "If a POST times out and you do not know whether it reached us, send it again within 5 minutes with the same "
    "`timestamp`, `nonce` and `signature`.",
    "If you get `201`, the first attempt never arrived and this one is the record.",
    "If you get `E1013`, the first attempt was accepted. Do not send it again; contact us for the ID.",
    "After any other error, fix the problem and send the request with a new `timestamp`, `nonce` and `signature`.",
    "The payment details call (section 7) only reads data, so it is always safe to repeat.",
)

story.append(PageBreak())
H1("13. UAT tests and go-live")
TABLE([0.3, 2, 3], ["#", "Test", "Expected result"],
      ["1", "Get the payment details", "`200` with the PSP's bank details; the QR code opens from `qr_code_url`"],
      ["2", "Create a deposit", "`201` with a `deposit_id`, status `pending`"],
      ["3", "Create a withdrawal", "`201` with a `withdrawal_id`, status `pending`"],
      ["4", "We approve the deposit", "You receive `deposit.approved`, verify it and reply `200`"],
      ["5", "We reject the withdrawal", "You receive `withdrawal.rejected` with the reason in `comment`"],
      ["6", "We reverse the approved deposit", "You receive `deposit.reversed` for the same ID"],
      ["7", "We re-send a callback", "Your CRM does not apply the same result twice"],
      ["8", "We change the PSP's bank details", "Your deposit screen shows the new details straight away"],
      ["9", "Send a request with a wrong signature", "`401` `E1005`"],
      ["10", "Send the same signed request twice", "Second one gets `409` `E1013`"],
      ["11", "GET the status of a request", "Status matches the last callback"])
P("When all tests pass, we exchange the Production credentials in the same way and go live.")

H1("14. Details to send us")
P("Fill this in and return it through a secure channel.")
TABLE([2.2, 1.5, 1.5], ["Item", "UAT", "Production"],
      ["Callback URL (HTTPS)", "", ""],
      ["Callback username", "", ""],
      ["Callback password", "", ""],
      ["Your server IP(s), if we must whitelist them", "", ""],
      ["Technical contact name and email", "", ""],
      ["RSA public key (only if RSA signing is agreed)", "", ""])


# ---------------------------------------------------------------- page frame

def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(GRID)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    canvas.drawString(18 * mm, 9 * mm, f"{TITLE}  |  Confidential")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title=TITLE, author="PSP Portal", subject="Deposits, withdrawals and callback API",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "PSP_Portal_CRM_Integration_Guide.pdf")
