"""How a MyNCA callback proves its sender. MyNCA signs nothing, so the proof is in our back_url."""

import hashlib
import hmac
import secrets


def callback_key(secret: str, sign_type_code: str, document_id: int | None) -> str:
    """Bound to one document: a key leaked from one back_url cannot record a signature on another."""
    message = f"{sign_type_code}:{document_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def is_callback_key_valid(
    secret: str,
    sign_type_code: str,
    document_id: int | None,
    key: str,
) -> bool:
    expected = callback_key(secret, sign_type_code, document_id)
    return secrets.compare_digest(expected.encode(), key.encode("utf-8", "surrogateescape"))
