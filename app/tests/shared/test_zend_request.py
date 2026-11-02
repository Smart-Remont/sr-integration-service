from fastapi import Request

from src.shared.zend_request import read_param, read_params


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


def _post_request(body: bytes) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/route",
        "query_string": b"",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "path_params": {},
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


async def test_query_keeps_the_first_1000_variables_not_counting_empty_ones() -> None:
    query = b"&&" + b"&".join(b"k%d=1" % index for index in range(1001))

    params = await read_params(_request(query))

    assert len(params.query) == 1000
    assert "k999" in params.query
    assert "k1000" not in params.query


async def test_form_keeps_one_variable_more_and_counts_empty_ones() -> None:
    body = b"&".join(b"k%d=1" % index for index in range(1002))
    assert len((await read_params(_post_request(body))).form) == 1001

    params = await read_params(_post_request(b"&&" + body))

    assert len(params.form) == 999


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
