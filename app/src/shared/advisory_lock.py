import hashlib


def advisory_lock_key(name: str) -> int:
    """The signed 64-bit key the ``pg_advisory_*`` functions take, derived from a readable name."""
    digest = hashlib.sha256(name.encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)
