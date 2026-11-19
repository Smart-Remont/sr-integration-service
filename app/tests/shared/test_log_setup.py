import sys
from collections.abc import Iterator

import pytest
from loguru import logger

from src.log_setup import configure_logging


@pytest.fixture
def restored_sink() -> Iterator[None]:
    yield
    # The sink set in the test writes to a capture that is about to close.
    logger.remove()
    logger.add(sys.__stderr__, diagnose=False)


@pytest.mark.usefixtures("restored_sink")
def test_a_logged_traceback_does_not_print_local_values(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    company_key_password = "local-value-secret"

    try:
        raise ValueError(len(company_key_password))
    except ValueError:
        logger.exception("failed")

    captured = capsys.readouterr()
    assert "failed" in captured.err
    assert "ValueError" in captured.err
    assert "local-value-secret" not in captured.err
