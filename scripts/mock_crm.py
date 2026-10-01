"""A fake CRM callback endpoint, for testing callbacks on your own machine.

Usage:
    python scripts/mock_crm.py                     # listens on http://127.0.0.1:9100/cb
    python scripts/mock_crm.py 9200 crm secret123  # custom port, username, password

Use these values when creating the PSP:
    "callback_url":      "http://127.0.0.1:9100/cb"
    "callback_username": "crm"
    "callback_password": "cb-pass-123"

Every callback is printed, along with whether the Basic auth and the portal's RSA
signature were valid. It answers 200 when both are fine, so the portal marks the
callback as delivered. A real CRM endpoint has to do these same checks.
"""

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
USERNAME = sys.argv[2] if len(sys.argv) > 2 else "crm"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "cb-pass-123"
PORTAL = "http://localhost:8000"

EXPECTED_AUTH = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()


def rsa_signature_ok(body: bytes, signature_b64: str | None) -> bool | None:
    """True/False, or None when the portal's public key could not be fetched."""
    if not signature_b64:
        return False
    try:
        pem = httpx.get(f"{PORTAL}/api/v1/meta/public-key", timeout=5).content
        public_key = serialization.load_pem_public_key(pem)
    except Exception:
        return None
    try:
        public_key.verify(base64.b64decode(signature_b64), body, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        auth_ok = self.headers.get("Authorization") == EXPECTED_AUTH
        rsa_ok = rsa_signature_ok(body, self.headers.get("X-Signature"))

        print("\n=== Callback received ===")
        try:
            print(json.dumps(json.loads(body), indent=2))
        except ValueError:
            print(body.decode(errors="replace"))
        print(f"Basic auth valid:    {auth_ok}")
        print(f"RSA signature valid: {'could not reach portal to check' if rsa_ok is None else rsa_ok}")

        accepted = auth_ok and rsa_ok is not False
        print(f"Replying: {'200 OK' if accepted else '401 (portal will retry)'}")
        self.send_response(200 if accepted else 401)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Fake CRM listening on http://127.0.0.1:{PORT}/cb  (username '{USERNAME}', password '{PASSWORD}')")
    print("Press Ctrl+C to stop.")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
