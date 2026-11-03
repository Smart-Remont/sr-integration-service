"""Kaspi ``check`` and ``pay``, in the order of PHP ``kaspiRequestPayAction``.

PHP ran every function call on its own and looked only at some of the results. Here one request
is one transaction, and the same outcomes are kept this way:

- a refusal is an exception: the transaction rolls back and the log row goes through
  ``committed()``, since it must survive that rollback;
- a failure PHP ignored runs in a savepoint and is logged (technical debt in CLAUDE.md): the
  payment stays and Kaspi gets the same answer;
- the log row of a success commits together with the payment.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, NoReturn

from fastapi import Response
from loguru import logger

from src.php_compat.json_codec import php_json_encode_default
from src.php_compat.pdo_errors import php_error_state
from src.php_compat.responses import php_html_response
from src.repository.base import SpRow

from .errors import KaspiDatabaseError, KaspiRefusalError
from .params import KaspiLogEntry, KaspiLogStatus, KaspiParams
from .responses import kaspi_xml_response
from .xml import check_xml, error_xml, pay_xml

if TYPE_CHECKING:
    from .repo import KaspiRepository

_COMMAND_CHECK = "check"
_COMMAND_PAY = "pay"
_RESULT_DATABASE_ERROR = 5


class KaspiService:
    def __init__(self, repository: KaspiRepository) -> None:
        self.repository = repository

    async def serve(self, params: KaspiParams) -> Response:
        if params.command not in (_COMMAND_CHECK, _COMMAND_PAY):
            return php_html_response()
        if params.txn_id_is_array:
            # PHP died on this in createElement, after the payment had been recorded.
            logger.warning("Kaspi {command}: txn_id is an array", command=params.command)
            return php_html_response()
        if params.command == _COMMAND_CHECK:
            return await self._check(params)
        return await self._pay(params)

    async def _check(self, params: KaspiParams) -> Response:
        result = await self._require_accepted(params, error_txn_date=None)
        info = await self._read_info(params)
        xml = check_xml(params.txn_id, result, info)
        await self._log_success(_log_entry(params, xml, txn_date=None))
        return kaspi_xml_response(xml)

    async def _pay(self, params: KaspiParams) -> Response:
        # PHP committed each call, so a retry saw the first payment and got code 3. Under one long
        # transaction it would pass the check and fail on the unique index with code 5. The lock
        # makes the retry wait until the first attempt has finished.
        await self.repository.lock_account(params.account)
        result = await self._require_accepted(params, error_txn_date=params.txn_date)
        try:
            paid_id = await self.repository.kaspi_request_pay(
                client_request_id=params.account,
                iin=params.iin,
                amount=params.amount,
                txn_id=params.txn_id,
                txn_date=params.txn_date,
            )
        except KaspiDatabaseError as exc:
            await self._refuse_payment(params, exc)
        await self._survive_failure(
            "after_payment_operations",
            lambda: self.repository.after_payment_operations(paid_id),
        )
        info = await self._read_info(params)
        xml = pay_xml(
            txn_id=params.txn_id,
            provider_txn=paid_id,
            amount=params.amount,
            result=result,
            company_bin=(info or {}).get("company_bin"),
        )
        await self._log_success(_log_entry(params, xml, txn_date=params.txn_date))
        logger.info("Kaspi pay accepted: txn_id={txn_id}", txn_id=params.txn_id)
        return kaspi_xml_response(xml)

    async def _require_accepted(
        self,
        params: KaspiParams,
        *,
        error_txn_date: str | None,
    ) -> int | None:
        try:
            result = await self.repository.kaspi_check(
                client_request_id=params.account,
                iin=params.iin,
                command=params.command or "",
                amount=params.amount,
                txn_id=params.txn_id,
            )
        except KaspiDatabaseError as exc:
            xml = error_xml(params.txn_id, _RESULT_DATABASE_ERROR, exc.message)
            await self._refuse(_log_entry(params, xml, error_txn_date, is_error=True))
        if result:
            xml = error_xml(params.txn_id, result)
            await self._refuse(_log_entry(params, xml, None, is_error=True))
        return result

    async def _read_info(self, params: KaspiParams) -> SpRow | None:
        return await self._survive_failure(
            "kaspi_request_get_info",
            lambda: self.repository.kaspi_request_get_info(params.account, params.iin),
        )

    async def _refuse_payment(self, params: KaspiParams, error: KaspiDatabaseError) -> NoReturn:
        # PHP read the error from the wrong variable here, so the answer has no comment.
        xml = error_xml(params.txn_id, _RESULT_DATABASE_ERROR)
        text = _failed_call_json(error)
        await self._refuse(_log_entry(params, xml, params.txn_date, is_error=True, text=text))

    async def _refuse(self, entry: KaspiLogEntry) -> NoReturn:
        try:
            await self.repository.committed(lambda repo: repo.insert_kaspi_pay_log(entry))
        except Exception as exc:
            # PHP did not check the log call either: a lost log row must not change the answer.
            logger.error("Kaspi log row lost: {error}", error=php_error_state(exc))
        raise KaspiRefusalError(entry.response_xml)

    async def _log_success(self, entry: KaspiLogEntry) -> None:
        await self._survive_failure(
            "insert_kaspi_pay_log",
            lambda: self.repository.insert_kaspi_pay_log(entry),
        )

    async def _survive_failure[T](
        self,
        step: str,
        call: Callable[[], Awaitable[T]],
    ) -> T | None:
        try:
            async with self.repository.savepoint():
                return await call()
        except Exception as exc:
            logger.error("Kaspi step {step} failed: {error}", step=step, error=php_error_state(exc))
            return None


def _log_entry(
    params: KaspiParams,
    xml: bytes,
    txn_date: str | None,
    *,
    is_error: bool = False,
    text: str | None = None,
) -> KaspiLogEntry:
    return KaspiLogEntry(
        command=params.command or "",
        request_text=params.request_json if text is None else text,
        response_xml=xml,
        account=params.account,
        txn_id=params.txn_id,
        amount=params.amount,
        txn_date=txn_date,
        status=KaspiLogStatus.ERROR if is_error else KaspiLogStatus.SUCCESS,
    )


def _failed_call_json(error: KaspiDatabaseError) -> str:
    return php_json_encode_default(
        {
            "value": None,
            "notice": [],
            "error": error.message,
            "error_debug": error.pdo_message,
            "status": False,
        },
    )
