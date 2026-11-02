"""PHP ``smsNotifyAction`` and ``smsNotifyStatusAction``, in the same order of calls.

PHP commits each function call on its own and does not look at the results, so a failed step
changes nothing in the answer. The same here, with these consequences:

- no transaction stays open while Kcell answers: the queue is read and committed first;
- each result commits right after its Kcell call, on the connection that holds the send-run lock,
  so recording does not depend on getting a second connection from the pool;
- every database step of a send run has a time limit, and going over it is a failed step;
- failures are logged by class only: the texts of database errors can quote the phone numbers and
  the message texts.

One deliberate difference: PHP goes on after a failed record, and the service stops the run. When
the database is down, PHP would send every remaining batch and then send them all again.
"""

import json
from collections.abc import Sequence
from itertools import batched

from fastapi import Response
from loguru import logger

from src.repository.base import SpRow
from src.shared.php_json import php_json_decode, php_json_encode_pretty, php_json_encode_unescaped

from .client import KcellClient, KcellRequestError
from .limits import database_step_limit
from .repo import SmsRepository
from .responses import (
    DONE_TEXT,
    NO_BATCHES_TEXT,
    NO_MESSAGES_TEXT,
    SEND_RUN_BUSY_TEXT,
    cron_text_response,
)

_MESSAGES_PER_BATCH = 200


class SmsService:
    def __init__(self, repository: SmsRepository, kcell: KcellClient) -> None:
        self.repository = repository
        self.kcell = kcell

    async def send_queued_messages(self) -> Response:
        # Cron fires every minute and a run can last longer. Without the lock the next run would
        # read the same messages and send them again, since a result is recorded only after Kcell.
        try:
            async with database_step_limit():
                locked = await self.repository.try_lock_send_run()
            if not locked:
                logger.warning("SMS send run skipped: the previous run is not finished")
                return cron_text_response(SEND_RUN_BUSY_TEXT)
            async with database_step_limit(), self.repository.transaction():
                messages = await self.repository.sms_notify__read()
        except Exception as exc:
            # readSP gives an empty list on an error, and PHP answers as for an empty queue.
            _log_failure("sms_notify__read", exc)
            return cron_text_response(NO_MESSAGES_TEXT)
        if not messages:
            return cron_text_response(NO_MESSAGES_TEXT)
        for batch in batched(messages, _MESSAGES_PER_BATCH, strict=False):
            if not await self._send_batch(batch):
                break
        return cron_text_response(DONE_TEXT)

    async def check_batch_statuses(self) -> Response:
        try:
            async with self.repository.transaction():
                batches = await self.repository.sms_notify_batch__read()
        except Exception as exc:
            _log_failure("sms_notify_batch__read", exc)
            return cron_text_response(NO_BATCHES_TEXT)
        if not batches:
            return cron_text_response(NO_BATCHES_TEXT)
        for batch in batches:
            await self._update_batch_status(str(batch["batch_id"]))
        return cron_text_response(DONE_TEXT)

    async def _send_batch(self, messages: Sequence[SpRow]) -> bool:
        """False when Kcell may have the batch and the record failed: the run must stop."""
        ids = [int(str(message["client_message_id"])) for message in messages]
        body = php_json_encode_pretty({"messages": list(messages), "fail-fast": False})
        try:
            answer = await self.kcell.send_batch(body)
        except KcellRequestError as exc:
            # PHP passes false, PDO sends '', and the jsonb argument refuses it before the function
            # runs. So nothing is recorded and the messages stay in the queue for the next run.
            logger.error(
                "Kcell send failed, {count} messages stay queued: {error}",
                count=len(ids),
                error=str(exc),
            )
            return True
        if not _is_json(answer):
            # Same outcome in PHP: the jsonb argument refuses the reply, nothing is recorded, and the
            # next batches are still tried. This is not a database failure, so the run goes on.
            logger.error("Kcell reply is not JSON, {count} messages stay queued", count=len(ids))
            return True
        fields = _object_fields(answer)
        batch_id = _text_or_null(fields.get("batch_id"))
        results = _non_empty(fields.get("results"))
        try:
            async with database_step_limit(), self.repository.transaction():
                await self.repository.sms_notify_result__set(
                    ids=ids,
                    batch_id=batch_id,
                    created_at=_text_or_null(fields.get("created_at")),
                    response=answer.decode("utf-8", "surrogateescape"),
                    results=None if results is None else php_json_encode_unescaped(results),
                )
        except Exception as exc:
            # The next run sends these messages again, as in PHP.
            logger.error(
                "SMS result not recorded, run stopped, {count} messages stay queued, "
                "batch {batch_id}: {error}",
                count=len(ids),
                batch_id=batch_id,
                error=type(exc).__name__,
            )
            return False
        logger.info("SMS batch {batch_id}: {count} messages", batch_id=batch_id, count=len(ids))
        return True

    async def _update_batch_status(self, batch_id: str) -> None:
        try:
            answer = await self.kcell.read_batch_status(batch_id)
        except KcellRequestError as exc:
            logger.error(
                "Kcell status of batch {batch_id} failed: {error}",
                batch_id=batch_id,
                error=str(exc),
            )
            return
        messages = _non_empty(_object_fields(answer).get("messages"))
        if messages is None:
            return
        try:
            await self.repository.committed(
                lambda repository: repository.sms_notify_status__set(
                    php_json_encode_unescaped(messages),
                ),
            )
        except Exception as exc:
            _log_failure("sms_notify_status__set", exc)


def _log_failure(step: str, exc: Exception) -> None:
    logger.error("SMS step {step} failed: {error}", step=step, error=type(exc).__name__)


def _object_fields(answer: bytes) -> SpRow:
    decoded = php_json_decode(answer)
    return decoded if isinstance(decoded, dict) else {}


def _non_empty(value: object) -> object | None:
    """``isset($x) && $x !== null && !empty($x) ? $x : null``."""
    return None if not value or value == "0" else value


def _text_or_null(value: object) -> str | None:
    """PDO sends every value as a string and null as NULL. Kcell's fields here are strings."""
    return None if value is None else str(value)


def _is_json(answer: bytes) -> bool:
    try:
        json.loads(answer)
    except ValueError:
        return False
    return True
