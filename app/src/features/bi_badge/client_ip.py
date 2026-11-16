"""The IP of a form client, for the per-IP limit of ``crm.request_from_bi_badge``.

Behind nginx and the Docker port mapping ``request.client.host`` is the address of the proxy, the
same for everyone, so the addresses nginx sets come first. ``X-Real-IP`` is set by nginx alone.
In ``X-Forwarded-For`` only the last address is added by our nginx: the ones before it come from
the client when nginx appends to the header, and a bot would change them at will to get past the
IP limit. The limit per device stays, and the IP limit only slows down bots. See the `bi_badge` item of technical debt in CLAUDE.md.
"""

import hashlib
import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, Request

# Hashed in place of an address that no source gave: such requests share one limit.
UNKNOWN_IP = "unknown"


def client_ip_from_request(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    candidates = (
        request.headers.get("x-real-ip", ""),
        forwarded_for.rsplit(",", 1)[-1],
        request.client.host if request.client else "",
    )
    for candidate in candidates:
        address = _valid_address(candidate)
        if address is not None:
            return address
    return None


def hash_client_ip(address: str | None, secret: str) -> str:
    """HMAC-SHA256: without the secret the hash cannot be turned back into an IP by brute force."""
    message = (address or UNKNOWN_IP).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _valid_address(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


ClientIpDep = Annotated[str | None, Depends(client_ip_from_request)]
