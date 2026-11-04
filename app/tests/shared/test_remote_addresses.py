"""An address the HTTP library cannot even parse is a failed call, as PHP sees it, and not a 500."""

import httpx
import pytest

from src.php_compat.remote_file import file_get_contents
from src.shared.mynca_client import MyncaClient

BAD_ADDRESS = "https://host:not-a-port/x"


def test_the_address_is_one_httpx_refuses() -> None:
    with pytest.raises(httpx.InvalidURL):
        httpx.URL(BAD_ADDRESS)


async def test_file_get_contents_gives_false() -> None:
    assert await file_get_contents(BAD_ADDRESS) is None


async def test_mynca_gives_a_failed_answer() -> None:
    answer = await MyncaClient("https://host:not-a-port", "token").sign_rollback("p")

    assert answer.status is False
    assert answer.code == 0
