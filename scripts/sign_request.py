"""Add timestamp + MD5 signature (and optionally an RSA X-Signature) to a request body.

Usage:
    python scripts/sign_request.py deposit    body.json SALT [crm_private_key.pem]
    python scripts/sign_request.py withdrawal body.json SALT [crm_private_key.pem]

Add --ist to stamp the request in IST (2026-09-25T15:15:00+05:30) instead of UTC.

Prints the signed JSON body to stdout. With a private key, the X-Signature header
value for exactly those bytes is printed to stderr. Send the body byte-for-byte as printed.
"""

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: E402

from app.security import compute_signature  # noqa: E402

SIGNED_FIELDS = {
    "deposit": ["amount", "currency", "bank_account_id", "customer_email"],
    "withdrawal": ["amount", "currency", "customer_email", "dest_account_number", "source_account_id"],
}


IST = timezone(timedelta(hours=5, minutes=30))


def main() -> None:
    ist = "--ist" in sys.argv
    if ist:
        sys.argv.remove("--ist")
    if len(sys.argv) not in (4, 5) or sys.argv[1] not in SIGNED_FIELDS:
        sys.exit(__doc__)
    kind, path, salt = sys.argv[1:4]
    body = json.loads(Path(path).read_text(), parse_float=Decimal)
    if ist:
        body["timestamp"] = datetime.now(IST).isoformat(timespec="seconds")
    else:
        body["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {k: body[k] for k in SIGNED_FIELDS[kind]} | {"timestamp": body["timestamp"]}
    body["signature"] = compute_signature(fields, salt)
    raw = json.dumps(body, default=lambda o: float(o) if isinstance(o, Decimal) else str(o))
    print(raw)

    if len(sys.argv) == 5:
        key = serialization.load_pem_private_key(Path(sys.argv[4]).read_bytes(), password=None)
        signature = key.sign(raw.encode(), padding.PKCS1v15(), hashes.SHA256())
        print("X-Signature: " + base64.b64encode(signature).decode(), file=sys.stderr)


if __name__ == "__main__":
    main()
