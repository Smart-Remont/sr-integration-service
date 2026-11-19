"""Logins that Scalar shows where the route checks the caller in its own way, unseen by OpenAPI.

Nothing is checked here: the routes moved from PHP keep their checks and their bytes. A dependency with a
``SecurityBase`` is the one way FastAPI lists a scheme on a route, so this one does nothing when called.
"""

from typing import Any

from fastapi import Depends
from fastapi.openapi.models import APIKey, APIKeyIn, HTTPBase
from fastapi.security.base import SecurityBase


class _ShownLogin(SecurityBase):
    def __init__(self, model: APIKey | HTTPBase, scheme_name: str) -> None:
        self.model = model
        self.scheme_name = scheme_name

    async def __call__(self) -> None:
        return None


def shown_basic(scheme_name: str, description: str) -> Any:
    return Depends(_ShownLogin(HTTPBase(scheme="basic", description=description), scheme_name))


def shown_header(scheme_name: str, header: str, description: str) -> Any:
    model = APIKey(**{"in": APIKeyIn.header}, name=header, description=description)
    return Depends(_ShownLogin(model, scheme_name))


def shown_address_value(scheme_name: str, name: str, description: str) -> Any:
    model = APIKey(**{"in": APIKeyIn.query}, name=name, description=description)
    return Depends(_ShownLogin(model, scheme_name))
