import hashlib

import pytest

from src.features.client_sign.repo import sign_process_lock_key
from src.features.kaspi.repo import account_lock_key
from src.features.sms.repo import _SEND_RUN_LOCK_KEY
from src.shared.advisory_lock import advisory_lock_key


def test_key_is_the_first_eight_bytes_of_sha256_as_a_signed_bigint() -> None:
    digest = hashlib.sha256(b"auto-sign:run").digest()

    assert advisory_lock_key("auto-sign:run") == int.from_bytes(digest[:8], "big", signed=True)


@pytest.mark.parametrize(
    ("key", "literal"),
    [
        (_SEND_RUN_LOCK_KEY, 3165429436431270518),
        (account_lock_key("77"), -6293096633168283247),
        (sign_process_lock_key("abc"), 3684406432042435101),
        (advisory_lock_key("auto-sign:cron-auto-sign-operator"), 1678123067878273961),
        (advisory_lock_key("auto-sign:cron-auto-upload-sign-doc"), -8439220147524191705),
        (advisory_lock_key("auto-sign:pdf-find-sum"), 3439227168681252398),
    ],
)
def test_lock_keys_stay_the_same(key: int, literal: int) -> None:
    """A changed key would let an old and a new process hold the "same" lock at the same time."""
    assert key == literal


def test_broken_utf8_of_a_request_does_not_fail() -> None:
    assert isinstance(advisory_lock_key("kaspi:\udcff"), int)
