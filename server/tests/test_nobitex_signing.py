"""The live boundary starts with deterministic, secret-safe signing."""
from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nobitex_signing import SigningConfigurationError, sign_request


def _keypair() -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(raw).decode("ascii"), key


def test_signature_matches_exact_documented_payload():
    encoded, private_key = _keypair()
    body = b'{"type":"buy","amount":"0.01"}'
    signed = sign_request("public-key", encoded, "post", "/market/orders/add?x=1", body,
                          timestamp=1700000000)
    assert signed.payload == b"1700000000POST/market/orders/add?x=1" + body
    signature = base64.urlsafe_b64decode(signed.headers["Nobitex-Signature"])
    private_key.public_key().verify(signature, signed.payload)
    assert signed.headers["Nobitex-Key"] == "public-key"
    assert signed.headers["Nobitex-Timestamp"] == "1700000000"


def test_changing_any_transmitted_body_byte_breaks_verification():
    encoded, private_key = _keypair()
    signed = sign_request("key", encoded, "POST", "/market/orders/add", b'{"amount":"1"}',
                          timestamp=1700000000)
    signature = base64.urlsafe_b64decode(signed.headers["Nobitex-Signature"])
    with pytest.raises(InvalidSignature):
        private_key.public_key().verify(signature, signed.payload + b" ")


@pytest.mark.parametrize("secret", ["not-base64!", base64.urlsafe_b64encode(b"short").decode()])
def test_bad_secret_is_refused_without_echoing_it(secret):
    with pytest.raises(SigningConfigurationError) as caught:
        sign_request("key", secret, "GET", "/market/orders/list", timestamp=1700000000)
    assert secret not in str(caught.value)


def test_empty_public_key_and_non_path_are_refused():
    encoded, _ = _keypair()
    with pytest.raises(SigningConfigurationError, match="API_KEY is empty"):
        sign_request("", encoded, "GET", "/market/orders/list", timestamp=1700000000)
    with pytest.raises(SigningConfigurationError, match="start with"):
        sign_request("key", encoded, "GET", "market/orders/list", timestamp=1700000000)
