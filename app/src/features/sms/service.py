"""PHP ``smsNotifyAction`` and ``smsNotifyStatusAction``, in the same order of calls.

PHP commits each function call on its own and does not look at the results, so a failed step
changes nothing in the answer. The same here, with these consequences:

- no transaction stays open while Kcell answers: the queue is read and committed first;
- each result commits right after its Kcell call, on the connection that holds the send-run lock,
  so recording does not depend on getting a second connection from the pool;
- every database step of a send run has a time limit, and going over it is a failed step;
- failures are logged by class only: the texts of database errors can quote the phone numbers and
  the message texts.

Deliberate differences:

- PHP goes on after a failed record, and the service stops the run. When the database is down, PHP
  would send every remaining batch and then send them all again.
- A status run has a lock, as a send run, and ends after a few transport failures in a row. PHP waits
  for each batch as long as ``msg.kcell.kz`` is silent, and the next run of the cron starts on top.
"""

import json
from collections.abc import Sequence
from itertools import batched

from fastapi import Response
from loguru import logger

from src.php_compat.json_codec import (
    php_json_decode,
    php_json_encode_pretty,
    php_json_encode_unescaped,
)
from src.php_compat.text import php_string, zero_to_null
from src.repository.base import SpRow
from src.shared.cron_run import database_step_limit, run_stop_requested

from .client import KcellClient, KcellRequestError
from .repo import SmsRepository
from .responses import (
    DONE_TEXT,
    NO_BATCHES_TEXT,
    NO_MESSAGES_TEXT,
    RUN_BUSY_TEXT,
    cron_text_response,
)

_MESSAGES_PER_BATCH = 200
# The address of statuses is one host: a few silent batches in a row say the same about the rest.
_STATUS_FAILURES_TO_END_RUN = 3


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
                return cron_text_response(RUN_BUSY_TEXT)
            async with database_step_limit(), self.repository.transaction():
                messages = await self.repository.sms_notify__read()
        except Exception as exc:
            # readSP gives an empty list on an error, and PHP answers as for an empty queue.
            _log_failure("sms_notify__read", exc)
            return cron_text_response(NO_MESSAGES_TEXT)
        if not messages:
            return cron_text_response(NO_MESSAGES_TEXT)
        for batch in batched(messages, _MESSAGES_PER_BATCH, strict=False):
            if run_stop_requested():
                logger.warning("SMS send run stopped between batches: the service is stopping")
                break
            if not await self._send_batch(batch):
                break
        return cron_text_response(DONE_TEXT)

    async def check_batch_statuses(self) -> Response:
        try:
            async with database_step_limit():
                locked = await self.repository.try_lock_status_run()
            if not locked:
                logger.warning("SMS status run skipped: the previous run is not finished")
                return cron_text_response(RUN_BUSY_TEXT)
            async with database_step_limit(), self.repository.transaction():
                batches = await self.repository.sms_notify_batch__read()
        except Exception as exc:
            _log_failure("sms_notify_batch__read", exc)
            return cron_text_response(NO_BATCHES_TEXT)
        if not batches:
            return cron_text_response(NO_BATCHES_TEXT)
        failures_in_a_row = 0
        for position, batch in enumerate(batches):
            if run_stop_requested():
                logger.warning("SMS status run stopped between batches: the service is stopping")
                break
            answered = await self._update_batch_status(str(batch["batch_id"]))
            failures_in_a_row = 0 if answered else failures_in_a_row + 1
            if failures_in_a_row >= _STATUS_FAILURES_TO_END_RUN:
                logger.error(
                    "SMS status run stopped: Kcell did not answer {failures} times in a row, "
                    "{left} batches wait for the next run",
                    failures=failures_in_a_row,
                    left=len(batches) - position - 1,
                )
                break
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
        fields = _send_answer_fields(answer)
        batch_id = _bound_text(fields.get("batch_id"))
        results = zero_to_null(fields.get("results"))
        if not _is_valid_utf8(batch_id):
            # A JSON string with a multibyte first letter: the server refuses the single byte PDO
            # sends, nothing is recorded, and this is not a database failure: the run goes on.
            logger.error(
                "Kcell reply gives no usable batch, {count} messages stay queued", count=len(ids)
            )
            return True
        try:
            async with database_step_limit(), self.repository.transaction():
                await self.repository.sms_notify_result__set(
                    ids=ids,
                    batch_id=batch_id,
                    created_at=_bound_text(fields.get("created_at")),
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

    async def _update_batch_status(self, batch_id: str) -> bool:
        """False when Kcell did not answer at all. Any answer, even an error page, is an answer."""
        try:
            answer = await self.kcell.read_batch_status(batch_id)
        except KcellRequestError as exc:
            logger.error(
                "Kcell status of batch {batch_id} failed: {error}",
                batch_id=batch_id,
                error=str(exc),
            )
            return False
        messages = zero_to_null(_object_fields(answer).get("messages"))
        if messages is None:
            return True
        try:
            async with database_step_limit():
                await self.repository.committed(
                    lambda repository: repository.sms_notify_status__set(
                        php_json_encode_unescaped(messages),
                    ),
                )
        except Exception as exc:
            _log_failure("sms_notify_status__set", exc)
        return True


def _log_failure(step: str, exc: Exception) -> None:
    logger.error("SMS step {step} failed: {error}", step=step, error=type(exc).__name__)


def _bound_text(value: object) -> str | None:
    """What PDO binds for a value of the answer: ``true`` is ``1`` and an array is ``Array``."""
    return None if value is None else php_string(value)


def _send_answer_fields(answer: bytes) -> SpRow:
    """``$result['batch_id']`` on a JSON string is its first byte.

    Kcell has not answered this way. An ASCII first letter records the rows as sent, as in PHP: without
    it such an answer would send the SMS again every minute.
    """
    decoded = php_json_decode(answer)
    if isinstance(decoded, str):
        first_byte = decoded.encode("utf-8", "surrogateescape")[:1].decode(
            "utf-8", "surrogateescape"
        )
        return {"batch_id": first_byte, "created_at": first_byte}
    return decoded if isinstance(decoded, dict) else {}


def _is_valid_utf8(text: str | None) -> bool:
    return text is None or not any("\udc80" <= letter <= "\udcff" for letter in text)


def _object_fields(answer: bytes) -> SpRow:
    decoded = php_json_decode(answer)
    return decoded if isinstance(decoded, dict) else {}


def _is_json(answer: bytes) -> bool:
    try:
        json.loads(answer)
    except ValueError:
        return False
    return True
