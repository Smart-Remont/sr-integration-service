"""Shared by the BIG tests that are not the frozen contract, and by Kaspi and SMS.

``test_contract.py`` keeps its own copy of the fake connection on purpose: it is frozen and has to
keep passing on its own, whatever happens to the code here.
"""

import base64
import re
from collections.abc import Callable
from typing import Any

from src.config import big_integration_auth_config

FLAT = "c0f17482-ca65-4e58-8446-d1b155f45945"
_FUNCTION_RE = re.compile(r"^select ([a-z0-9_.]+)\(")
_CURSOR_RE = re.compile(r'^fetch all in "(.+)"$')

type SpResponse = list[dict[str, Any]] | Exception | Callable[[tuple[Any, ...]], Any]


class FakeConnection:
    """Answers stored-function calls the way asyncpg does, from a per-test script."""

    def __init__(self, responses: dict[str, SpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._cursors: dict[str, list[dict[str, Any]]] = {}

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        self.calls.append((function, args))
        response = self.responses[function]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(args)
        return response

    async def fetchval(self, query: str, *args: Any) -> None:
        assert query.startswith("select set_config(")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        cursor = _CURSOR_RE.match(query)
        if cursor:
            return self._cursors.pop(cursor.group(1))
        function = _FUNCTION_RE.match(query).group(1)
        return self._answer(function, args)

    async def execute(self, query: str, *args: Any) -> None:
        function = _FUNCTION_RE.match(query).group(1)
        cursor_name, *sp_args = args
        self._cursors[cursor_name] = self._answer(function, tuple(sp_args))


def _auth(password: str | None = None) -> dict[str, str]:
    user = big_integration_auth_config.username
    secret = password if password is not None else big_integration_auth_config.password
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _ok(data: Any) -> dict[str, Any]:
    return {"data": data, "response": True, "error": {"message": ""}}


def _error(message: str) -> dict[str, Any]:
    return {"data": None, "response": False, "error": {"message": message}}


def _url(route: str) -> str:
    return f"/api/big_integration/{route}"
