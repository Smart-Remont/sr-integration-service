import pytest
from fastapi import Request

from src.php_compat.zend_request import php_raw_body, read_param, read_params


def _request(query: bytes, *, path: str = "/route", tail: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": [],
        "path_params": {} if tail is None else {"tail": tail},
    }
    return Request(scope)


def _post_request(
    body: bytes,
    *,
    content_type: bytes = b"application/x-www-form-urlencoded",
) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/route",
        "query_string": b"",
        "headers": [(b"content-type", content_type)],
        "path_params": {},
        "app": None,
    }
    return Request(scope, receive)


async def test_json_routes_never_receive_invalid_utf8() -> None:
    assert await read_param(_request(b"a=%FF"), "a") == "\ufffd"


async def test_original_bytes_stay_available() -> None:
    params = await read_params(_request(b"a=%FF"))

    assert params.get("a") == "\udcff"


async def test_array_parameter_reads_as_php_prints_it() -> None:
    assert await read_param(_request(b"a[]=1&a[]=2"), "a") == "Array"


async def test_last_repeated_key_wins_and_dots_become_underscores() -> None:
    params = await read_params(_request(b"a=1&a=2&b.c=3"))

    assert params.query == {"a": "2", "b_c": "3"}


async def test_address_pairs_are_read_from_the_raw_path() -> None:
    request = _request(b"", path="/route/a/x+y/z/orphan", tail="a/x+y/z/orphan")
    request.scope["raw_path"] = b"/route/a/x+y%2Fz/orphan"

    params = await read_params(request)

    assert params.address == {"a": "x y/z"}


async def test_repeated_address_key_becomes_an_array() -> None:
    request = _request(b"", path="/route/a/1/a/2", tail="a/1/a/2")

    params = await read_params(request)

    assert params.address == {"a": {"0": "1", "1": "2"}}


async def test_query_keeps_the_first_100000_variables_not_counting_empty_ones() -> None:
    query = b"&&" + b"&".join(b"k%d=1" % index for index in range(100001))

    params = await read_params(_request(query))

    assert len(params.query) == 100000
    assert "k99999" in params.query
    assert "k100000" not in params.query


async def test_form_keeps_one_variable_more_and_counts_empty_ones() -> None:
    body = b"&".join(b"k%d=1" % index for index in range(100002))
    assert len((await read_params(_post_request(body))).form) == 100001

    params = await read_params(_post_request(b"&&" + body))

    assert len(params.form) == 99999


async def test_array_index_follows_the_largest_number() -> None:
    params = await read_params(_request(b"a[5]=x&a[]=y&a[b]=z&a[]=w&c[05]=x&c[]=y"))

    assert params.query["a"] == {"5": "x", "6": "y", "b": "z", "7": "w"}
    assert params.query["c"] == {"05": "x", "0": "y"}


async def test_nested_arrays_are_built() -> None:
    params = await read_params(_request(b"a[x][]=1&a[x][]=2&a[][y]=3"))

    assert params.query["a"] == {"x": {"0": "1", "1": "2"}, "0": {"y": "3"}}


async def test_long_repeated_address_key_is_built_in_place() -> None:
    tail = "/".join(["a/1"] * 20000)

    params = await read_params(_request(b"", path=f"/route/{tail}", tail=tail))

    array = params.address["a"]
    assert len(array) == 20000
    assert array["19999"] == "1"


async def test_raw_body_is_empty_for_a_multipart_request_and_the_form_still_reads() -> None:
    body = b'--b\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n--b--\r\n'
    request = _post_request(body, content_type=b"multipart/form-data; boundary=b")

    assert await php_raw_body(request) == b""
    assert (await read_params(request)).form == {"a": "1"}


async def test_multipart_without_a_boundary_keeps_the_raw_body_and_has_no_form() -> None:
    request = _post_request(b"{}", content_type=b"multipart/form-data")

    assert await php_raw_body(request) == b"{}"
    assert (await read_params(request)).form == {}


@pytest.mark.parametrize("body", [b"x", b"not a form", b"--b\r\nno headers"])
async def test_broken_multipart_is_an_empty_form(body: bytes) -> None:
    request = _post_request(body, content_type=b"multipart/form-data; boundary=b")

    assert await php_raw_body(request) == b""
    assert (await read_params(request)).form == {}


async def test_broken_multipart_can_be_read_again_without_the_raw_body_first() -> None:
    request = _post_request(b"not a form", content_type=b"multipart/form-data; boundary=b")

    assert (await read_params(request)).form == {}
    assert (await read_params(request)).form == {}


async def test_multipart_is_not_lost_over_the_limits_of_starlette() -> None:
    parts = b"".join(
        b'--b\r\nContent-Disposition: form-data; name="k%d"\r\n\r\n1\r\n' % index
        for index in range(1100)
    )
    long_part = b'--b\r\nContent-Disposition: form-data; name="long"\r\n\r\n'
    body = parts + long_part + b"x" * (2 * 1024 * 1024) + b"\r\n--b--\r\n"
    request = _post_request(body, content_type=b"multipart/form-data; boundary=b")

    form = (await read_params(request)).form

    assert len(form) == 1101
    assert len(form["long"]) == 2 * 1024 * 1024
