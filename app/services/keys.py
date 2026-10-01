"""RSA public/private key layer (checklist items 7 and 8).

* Inbound: a CRM that has registered its public key must sign the raw request body with
  RSA-SHA256 (PKCS#1 v1.5) and send it base64-encoded in the X-Signature header.
* Outbound: every callback body is signed with the portal's private key, also in X-Signature.
  The CRM verifies it with the key from GET /api/v1/meta/public-key.
"""

import base64
import logging
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import get_settings

log = logging.getLogger(__name__)


def load_public_key(pem: str) -> rsa.RSAPublicKey:
    """Parse a PEM public key and enforce RSA with the minimum key length."""
    try:
        key = serialization.load_pem_public_key(pem.encode())
    except ValueError as exc:
        raise ValueError(f"Not a valid PEM public key: {exc}") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("Only RSA public keys are supported")
    min_bits = get_settings().min_rsa_key_bits
    if key.key_size < min_bits:
        raise ValueError(f"RSA key is {key.key_size} bits; minimum is {min_bits}")
    return key


def verify_signature(public_key_pem: str, body: bytes, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        load_public_key(public_key_pem).verify(signature, body, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError):
        return False


@lru_cache
def portal_private_key() -> rsa.RSAPrivateKey:
    """Load the portal key pair, generating it on first start if the file does not exist."""
    settings = get_settings()
    path = Path(settings.portal_private_key_path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < settings.min_rsa_key_bits:
            raise RuntimeError(f"{path} must be an RSA key of at least {settings.min_rsa_key_bits} bits")
        return key

    key = rsa.generate_private_key(public_exponent=65537, key_size=max(settings.min_rsa_key_bits, 2048))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    log.warning("Generated new portal RSA key pair at %s. Back it up; CRMs pin its public key.", path)
    return key


def portal_public_key_pem() -> str:
    return (
        portal_private_key()
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def sign_with_portal_key(body: bytes) -> str:
    signature = portal_private_key().sign(body, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()
