"""The auto-sign crons: the caller check, missing configuration, and what must not reach the logs."""

import asyncio
from pathlib import Path

import httpx
import pytest
from asyncpg.exceptions import DataError, RaiseError
from httpx import AsyncClient

from src.config import app_config, auto_sign_config, auto_sign_cron_auth_config, minio_config
from src.features.auto_sign.operator_signing import _CompanyKey
from src.shared.mynca_client import MyncaAnswer
from tests.auto_sign.fakes import (
    CLIENT_FILE_PATH,
    CRON_USER,
    DN_NAME,
    FILE_BASE64,
    IIN,
    INSERT_SIGN,
    KEY_BASE64,
    KEY_BYTES,
    KEY_INFO_PATH,
    KEY_PASSWORD,
    MASTER_KEY,
    MODIFY_SIGN,
    MYNCA_TOKEN,
    OPERATOR_ROUTE,
    PDF_BYTES,
    READ_KEY,
    READ_ROWS,
    ROLLBACK_PATH,
    SIGN_SAVE_PATH,
    UPLOAD_ROUTE,
    Database,
    Mynca,
    assert_empty,
    basic,
    fail_after_dry_run,
)

ROUTES = [OPERATOR_ROUTE, UPLOAD_ROUTE]
SECRETS = [
    MASTER_KEY,
    KEY_BASE64,
    KEY_PASSWORD,
    MYNCA_TOKEN,
    IIN,
    DN_NAME,
    FILE_BASE64,
    "PKCS12-key-bytes-secret",
    str(KEY_BYTES),
]
ECHO = {"error": KEY_PASSWORD, "key": KEY_BASE64, "dn": DN_NAME, "master": MASTER_KEY}


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": basic(CRON_USER, "wrong")}, {"Authorization": basic("wrong", "x")}],
)
async def test_wrong_caller_is_refused_before_anything_runs(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    route: str,
    headers: dict[str, str],
) -> None:
    client.headers.pop("Authorization")

    response = await client.get(route, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert db.events == []
    assert mynca.requests == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("field", ["username", "password"])
async def test_missing_cron_credentials_are_503(
    client: AsyncClient,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    field: str,
) -> None:
    monkeypatch.setattr(auto_sign_cron_auth_config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []


@pytest.mark.parametrize(
    ("route", "config", "field"),
    [
        (OPERATOR_ROUTE, auto_sign_config, "mynca_base_url"),
        (OPERATOR_ROUTE, auto_sign_config, "mynca_token"),
        (OPERATOR_ROUTE, auto_sign_config, "master_key"),
        (UPLOAD_ROUTE, auto_sign_config, "mynca_base_url"),
        (UPLOAD_ROUTE, auto_sign_config, "mynca_token"),
        (UPLOAD_ROUTE, minio_config, "endpoint"),
        (UPLOAD_ROUTE, minio_config, "access_key"),
    ],
)
async def test_missing_configuration_is_503_and_runs_nothing(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    config: object,
    field: str,
) -> None:
    monkeypatch.setattr(config, field, "")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert mynca.requests == []
    assert not any(secret in response.text for secret in SECRETS)


def test_env_example_leaves_every_auto_sign_value_empty() -> None:
    example = Path(__file__).parents[2] / ".env.example"
    lines = [line for line in example.read_text().splitlines() if line.startswith("AUTO_SIGN_")]

    assert len(lines) == 5
    assert all(line.endswith("=") for line in lines)


@pytest.mark.parametrize(
    ("route", "other_config"),
    [
        (OPERATOR_ROUTE, (minio_config, "endpoint")),
        (UPLOAD_ROUTE, (auto_sign_config, "master_key")),
    ],
)
async def test_a_route_needs_only_its_own_configuration(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    other_config: tuple[object, str],
) -> None:
    monkeypatch.setattr(*other_config, "")

    assert_empty(await client.get(route))


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("tail", ["", "/", "/key/value"])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_zend_address_forms_and_methods_work_as_in_php(
    client: AsyncClient,
    route: str,
    tail: str,
    method: str,
) -> None:
    assert_empty(await client.request(method, f"{route}{tail}"))


@pytest.mark.usefixtures("short_limits")
async def test_no_free_connection_in_time_is_503(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
) -> None:
    db.acquire_hangs = True

    response = await asyncio.wait_for(client.get(OPERATOR_ROUTE), timeout=5)

    assert response.status_code == 503
    assert mynca.requests == []


def test_company_key_repr_hides_the_key() -> None:
    assert KEY_PASSWORD not in repr(_CompanyKey(KEY_BASE64, KEY_PASSWORD))
    assert KEY_BASE64 not in repr(_CompanyKey(KEY_BASE64, KEY_PASSWORD))


def test_mynca_answer_repr_hides_the_document() -> None:
    answer = MyncaAnswer(status=True, code=200, data={"file": FILE_BASE64}, raw=PDF_BYTES)
    assert FILE_BASE64 not in repr(answer)
    assert "SIGNED-BODY" not in repr(answer)


def _key_error_with_argument(db: Database, _: Mynca) -> None:
    # asyncpg puts the argument into the text of the error it raises for a bad one.
    db.responses[READ_KEY] = DataError(f"invalid input for query argument $3: '{MASTER_KEY}'")


def _key_raise_with_master_key(db: Database, _: Mynca) -> None:
    db.responses[READ_KEY] = RaiseError(f"{{wrong key {MASTER_KEY}}}")


def _key_unexpected_column_type(db: Database, _: Mynca) -> None:
    db.responses[READ_KEY] = [{"key_data": f"\\x{KEY_BYTES.hex()}", "key_password": KEY_PASSWORD}]


def _key_info_transport_error(_: Database, mynca: Mynca) -> None:
    mynca.answers[KEY_INFO_PATH] = httpx.ConnectError(f"failed {KEY_PASSWORD} {KEY_BASE64}")


def _key_info_other_error(_: Database, mynca: Mynca) -> None:
    mynca.answers[KEY_INFO_PATH] = ValueError(f"bad {KEY_PASSWORD} {KEY_BASE64}")


def _key_info_echoes_the_request(_: Database, mynca: Mynca) -> None:
    mynca.answers[KEY_INFO_PATH] = httpx.Response(400, json=ECHO)


def _key_info_invalid_with_certificate(_: Database, mynca: Mynca) -> None:
    mynca.answers[KEY_INFO_PATH] = httpx.Response(200, json={"valid": False, **ECHO})


def _client_file_echoes(_: Database, mynca: Mynca) -> None:
    mynca.answers[CLIENT_FILE_PATH] = httpx.Response(500, json={**ECHO, "body": FILE_BASE64})


def _sign_save_transport_error(_: Database, mynca: Mynca) -> None:
    mynca.answers[SIGN_SAVE_PATH] = httpx.ReadTimeout(f"timeout {KEY_PASSWORD} {FILE_BASE64}")


def _sign_save_other_error(_: Database, mynca: Mynca) -> None:
    mynca.answers[SIGN_SAVE_PATH] = RuntimeError(f"bad {KEY_BASE64}")


def _sign_save_echoes(_: Database, mynca: Mynca) -> None:
    mynca.answers[SIGN_SAVE_PATH] = httpx.Response(500, json=ECHO)


def _dry_run_names_the_iin(db: Database, _: Mynca) -> None:
    db.responses[INSERT_SIGN] = RaiseError(f"{{Идентификатор ИИН:{IIN} не соответствует}}")


def _insert_names_the_iin(db: Database, _: Mynca) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError(f"{{ИИН:{IIN}}}"))


def _insert_error_carries_arguments(db: Database, _: Mynca) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(
        DataError(f"invalid input for query argument $8: '{DN_NAME}'"),
    )


def _modify_names_the_iin(db: Database, _: Mynca) -> None:
    db.responses[MODIFY_SIGN] = RaiseError(f"{{{DN_NAME}}}")


def _rollback_echoes(db: Database, mynca: Mynca) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError(f"{{{DN_NAME}}}"))
    mynca.answers[ROLLBACK_PATH] = httpx.Response(500, json=ECHO)


def _rollback_other_error(db: Database, mynca: Mynca) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError(f"{{{DN_NAME}}}"))
    mynca.answers[ROLLBACK_PATH] = RuntimeError(f"bad {KEY_BASE64}")


def _commit_lost_with_text(db: Database, _: Mynca) -> None:
    db.commit_error = OSError(f"lost {DN_NAME}")


def _read_error_names_the_iin(db: Database, _: Mynca) -> None:
    db.responses[READ_ROWS] = RaiseError(f"{{{IIN}}}")


def _everything_works(_: Database, __: Mynca) -> None:
    return None


@pytest.mark.usefixtures("short_read_back")
@pytest.mark.parametrize(
    "scenario",
    [
        _key_error_with_argument,
        _key_raise_with_master_key,
        _key_unexpected_column_type,
        _key_info_transport_error,
        _key_info_other_error,
        _key_info_echoes_the_request,
        _key_info_invalid_with_certificate,
        _client_file_echoes,
        _sign_save_transport_error,
        _sign_save_other_error,
        _sign_save_echoes,
        _dry_run_names_the_iin,
        _insert_names_the_iin,
        _insert_error_carries_arguments,
        _modify_names_the_iin,
        _rollback_echoes,
        _rollback_other_error,
        _commit_lost_with_text,
        _read_error_names_the_iin,
        _everything_works,
    ],
)
async def test_no_secret_reaches_a_log_or_the_response(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    log_lines: list[str],
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    scenario: object,
) -> None:
    scenario(db, mynca)

    with caplog.at_level("DEBUG"):
        response = await client.get(OPERATOR_ROUTE)

    assert_empty(response)
    logged = "".join(log_lines) + caplog.text + "".join(capfd.readouterr())
    assert logged
    for secret in SECRETS:
        assert secret not in logged


async def test_mynca_paths_in_logs_hide_process_and_group_ids(
    client: AsyncClient,
    db: Database,
    log_lines: list[str],
) -> None:
    db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError("{refused}"))

    await client.get(OPERATOR_ROUTE)

    http_lines = [line for line in log_lines if "MyNCA HTTP" in line]
    assert "path=/sign/*/file" in "".join(http_lines)
    assert "path=/sign/*/rollback" in "".join(http_lines)
    assert "11111111-1111" not in "".join(http_lines)
    assert "22222222-2222" not in "".join(http_lines)


async def test_signing_needs_app_env_prod(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_config, "env", "stage")

    response = await client.get(OPERATOR_ROUTE)

    assert response.status_code == 503
    assert db.events == []
    assert mynca.requests == []


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize("route", [OPERATOR_ROUTE, UPLOAD_ROUTE])
async def test_plain_http_mynca_address_is_503(
    client: AsyncClient,
    db: Database,
    mynca: Mynca,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    monkeypatch.setattr(auto_sign_config, "mynca_base_url", "http://mynca.test/api")

    response = await client.get(route)

    assert response.status_code == 503
    assert db.events == []
    assert mynca.requests == []
