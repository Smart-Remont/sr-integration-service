"""Requests as Zend Framework 1 and PHP read them: addresses, parameters, any HTTP method."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Any
from urllib.parse import unquote_to_bytes

from fastapi import APIRouter, Request

from .text import PHP_ARRAY_TEXT

type PhpParam = str | dict[str, PhpParam | None]

_URLENCODED = "application/x-www-form-urlencoded"
_MULTIPART = "multipart/form-data"
_ALL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
_ARRAY_INDEX = re.compile(r"\[([^\]]*)\]")
# PHP's default max_input_vars. The rest of the input is ignored, not refused.
_MAX_INPUT_VARS = 1000
# PHP stops reading a POST body only after the variable that went over the limit.
_MAX_FORM_VARS = _MAX_INPUT_VARS + 1


class _PhpArray(dict[str, Any]):
    """A PHP array: ``[]`` takes the key after the largest number, which is kept and not searched."""

    next_index = 0

    def key_for(self, key: str) -> str:
        if not key:
            key = str(self.next_index)
        if key.isascii() and key.isdecimal() and (key == "0" or key[0] != "0"):
            self.next_index = max(self.next_index, int(key) + 1)
        return key

    def append(self, value: object) -> None:
        self[self.key_for("")] = value


@dataclass(frozen=True)
class ZendParams:
    address: dict[str, PhpParam]
    query: dict[str, PhpParam]
    form: dict[str, PhpParam]

    def get(self, name: str) -> PhpParam | None:
        """``Zend_Controller_Request_Http::getParam``: an empty value still stops the search."""
        for source in (self.address, self.query, self.form):
            if name in source:
                return source[name]
        return None

    def all_params(self, *, controller: str, action: str) -> dict[str, PhpParam]:
        """``_getAllParams``, keeping its key order and the priority of the sources."""
        params: dict[str, PhpParam] = {"controller": controller, "action": action}
        for key, value in self.address.items():
            params.setdefault(key, value)
        params.setdefault("module", "default")
        for source in (self.query, self.form):
            for key, value in source.items():
                params.setdefault(key, value)
        return params


async def read_params(request: Request) -> ZendParams:
    return ZendParams(
        address=_address_params(request),
        query=_parse_php_query(_query_variables(request.scope.get("query_string", b""))),
        form=await _form_params(request),
    )


async def read_param(request: Request, name: str) -> str:
    """``_getParam`` with an empty default: the address, the query string, then the POST form.

    Invalid UTF-8 becomes U+FFFD here, because JSON answers cannot carry it. A route that repeats
    the original bytes reads them through ``read_params``.
    """
    text = php_param_text((await read_params(request)).get(name))
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def php_param_text(value: PhpParam | None) -> str:
    if isinstance(value, dict):
        return PHP_ARRAY_TEXT
    return value or ""


def php_route(
    router: APIRouter,
    path: str,
    *,
    method: str,
    other_methods: Callable[..., Any] | None = None,
    **docs: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a route the way the Zend router served it.

    Scalar shows ``method`` on ``path``. As in PHP, the address also works with a trailing slash
    and ``/key/value`` pairs, and other HTTP methods reach ``other_methods`` (the same endpoint
    when PHP did not check the method).
    """

    def register(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        with_tail = f"{path}/{{tail:path}}"
        others = [item for item in _ALL_METHODS if item != method]
        router.add_api_route(path, endpoint, methods=[method], **docs)
        router.add_api_route(with_tail, endpoint, methods=[method], include_in_schema=False)
        for route_path in (path, with_tail):
            router.add_api_route(
                route_path,
                other_methods or endpoint,
                methods=others,
                include_in_schema=False,
            )
        return endpoint

    return register


def _address_params(request: Request) -> dict[str, PhpParam]:
    """``Zend_Controller_Router_Route_Module::match``: a repeated key becomes an array."""
    tail = request.path_params.get("tail")
    if tail is None:
        return {}
    segments = _raw_tail(request, tail).strip(b"/").split(b"/")
    if segments == [b""]:
        return {}
    params: dict[str, PhpParam | None] = {}
    for key_segment, value_segment in zip(segments[::2], [*segments[1::2], None], strict=False):
        key = _url_decode(key_segment)
        value = None if value_segment is None else _url_decode(value_segment)
        params[key] = value if params.get(key) is None else _merged(params[key], value)
    return {key: value for key, value in params.items() if value is not None}


def _raw_tail(request: Request, tail: str) -> bytes:
    # The decoded path turns %2F into a separator, and Zend splits the address before decoding.
    raw_path = request.scope.get("raw_path")
    if not raw_path:
        return tail.encode()
    path: str = request.scope["path"]
    depth = path[: len(path) - len(tail)].count("/")
    return b"/".join(raw_path.split(b"/")[depth:])


def _merged(existing: PhpParam | None, value: str | None) -> _PhpArray:
    if isinstance(existing, _PhpArray):
        array = existing
    else:
        array = _PhpArray()
        array.append(existing)
    array.append(value)
    return array


async def _form_params(request: Request) -> dict[str, PhpParam]:
    if request.method != "POST":
        return {}
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith(_URLENCODED):
        return _parse_php_query(_form_variables(await request.body()))
    if content_type.startswith(_MULTIPART):
        form = await request.form()
        return {key: value for key, value in form.items() if isinstance(value, str)}
    return {}


def _query_variables(raw: bytes) -> Iterable[bytes]:
    return islice(filter(None, raw.split(b"&")), _MAX_INPUT_VARS)


def _form_variables(raw: bytes) -> Iterable[bytes]:
    # Unlike the query string, here an empty variable counts towards the limit.
    return raw.split(b"&", _MAX_FORM_VARS)[:_MAX_FORM_VARS]


def _parse_php_query(variables: Iterable[bytes]) -> dict[str, PhpParam]:
    """``parse_str``: ``a[]=1`` is an array, the last repeated key wins, ``.`` becomes ``_``."""
    params: dict[str, Any] = {}
    for variable in variables:
        name, _, value = variable.partition(b"=")
        _assign(params, _url_decode(name).lstrip(" "), _url_decode(value))
    return params


def _assign(params: dict[str, Any], name: str, value: str) -> None:
    base, bracket, rest = name.partition("[")
    if bracket and "]" not in rest:
        base, rest = f"{base}_{rest}", ""
    base = base.replace(".", "_").replace(" ", "_")
    if not base:
        return
    indexes = _ARRAY_INDEX.findall(f"[{rest}")
    if not indexes:
        params[base] = value
        return
    if not isinstance(params.get(base), _PhpArray):
        params[base] = _PhpArray()
    array = params[base]
    for index in indexes[:-1]:
        key = array.key_for(index)
        if not isinstance(array.get(key), _PhpArray):
            array[key] = _PhpArray()
        array = array[key]
    array[array.key_for(indexes[-1])] = value


def _url_decode(raw: bytes) -> str:
    # Keeps invalid UTF-8 as PHP does: the database and the response see the original bytes.
    return unquote_to_bytes(raw.replace(b"+", b" ")).decode("utf-8", "surrogateescape")
