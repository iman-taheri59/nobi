"""Ed25519 request signing for the future live-execution boundary.

This module has no HTTP client and cannot place an order. It deliberately signs
an already-serialized raw body so the caller can transmit those exact bytes.
"""
from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class SigningConfigurationError(ValueError):
    """A safe, secret-free signing configuration error."""


@dataclass(frozen=True)
class SignedRequest:
    headers: dict[str, str]
    payload: bytes


def decode_private_key(encoded: str) -> bytes:
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise SigningConfigurationError("NOBITEX_API_SECRET is not valid URL-safe Base64") from exc
    if len(raw) != 32:
        raise SigningConfigurationError("NOBITEX_API_SECRET must decode to exactly 32 bytes")
    return raw


def signing_payload(timestamp: str, method: str, full_path: str, raw_body: bytes) -> bytes:
    if not timestamp.isdigit():
        raise SigningConfigurationError("timestamp must contain Unix seconds")
    if not full_path.startswith("/"):
        raise SigningConfigurationError("full_path must start with /")
    try:
        prefix = f"{timestamp}{method.upper()}{full_path}".encode("ascii")
    except UnicodeEncodeError as exc:
        raise SigningConfigurationError("method and full_path must be ASCII") from exc
    return prefix + raw_body


def sign_request(
    public_key: str,
    private_key_b64: str,
    method: str,
    full_path: str,
    raw_body: bytes = b"",
    *,
    timestamp: int | None = None,
) -> SignedRequest:
    if not public_key.strip():
        raise SigningConfigurationError("NOBITEX_API_KEY is empty")
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    payload = signing_payload(ts, method, full_path, raw_body)
    private_key = Ed25519PrivateKey.from_private_bytes(decode_private_key(private_key_b64))
    signature = base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii")
    return SignedRequest(
        headers={
            "Nobitex-Key": public_key,
            "Nobitex-Signature": signature,
            "Nobitex-Timestamp": ts,
        },
        payload=payload,
    )


__all__ = [
    "SignedRequest",
    "SigningConfigurationError",
    "decode_private_key",
    "sign_request",
    "signing_payload",
]
