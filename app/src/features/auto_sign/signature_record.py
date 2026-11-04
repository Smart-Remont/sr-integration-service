"""The row of a company signature in ``sign_tab``: the dry run, the record and a failed commit.

After a failed commit one empty read proves nothing, since the old session may still be committing:
see ``SignatureRecord.resolve_failed_commit``. That step ends the old session and with it the run lock,
so a new run can start during the read-back. It then signs only if the row is still missing, and the
outcome is still one signature once this run rolls its own back, or a second one if this run ends in
UNKNOWN.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum, auto

from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.repository.base import SpRow
from src.shared.cron_run import database_step_limit

from .mynca_signature import Signature
from .repo import AutoSignRepository
from .signing_plan import SignTarget, sign_target

# The real name is known only after signing. client.insert_sign_general needs digits after IIN, and
# for company signature types it compares them with nothing today.
_DRY_RUN_DN_NAME = "IIN000000000000"
READ_BACK_SECONDS = 120.0
READ_BACK_PAUSE_SECONDS = 15.0


class Recorded(Enum):
    YES = auto()
    NO = auto()
    UNKNOWN = auto()


class _DryRunFinished(Exception):
    """Leaves the dry-run transaction, which therefore always rolls back."""


@dataclass
class _ReadBack:
    old_session: int
    terminated: bool = False
    session_gone: bool = False
    row_absent: bool = False


class SignatureRecord:
    def __init__(self, repository: AutoSignRepository) -> None:
        self._repository = repository

    async def would_be_refused(self, row: SpRow, target: SignTarget) -> bool:
        """Not in PHP. A refusal found after signing means a signature to roll back, every minute.

        On the functions and triggers read on dev_prod this path stays inside the transaction: no
        dblink, no http. It uses up sequence values of ``sign_tab`` and ``stack_tab``.
        """
        try:
            async with self._repository.step():
                await self._insert_signature(row, target, _DRY_RUN_DN_NAME)
                raise _DryRunFinished
        except _DryRunFinished:
            return False
        except Exception as exc:
            logger.error(
                "Auto sign: the record would be refused, not signing | sign_id={sign_id} error={error}",
                sign_id=row["sign_id"],
                error=php_error_state(exc),
            )
            return True

    async def record_signature(self, row: SpRow, signature: Signature) -> Recorded:
        """One transaction, so both calls stay or go together and a rollback at MyNCA stays true."""
        reached_commit = False
        try:
            async with self._repository.step():
                new_sign_id = await self._insert_signature(
                    row,
                    sign_target(row),
                    signature.dn_name,
                )
                await self._repository.sign_tab__modify(
                    sign_id=new_sign_id,
                    sign_process_id=signature.sign_process_id,
                    sign_group_id=signature.group_id,
                )
                reached_commit = True
        except Exception as exc:
            logger.error(
                "Auto sign: signature not recorded | sign_id={sign_id} commit={commit} error={error}",
                sign_id=row["sign_id"],
                commit=reached_commit,
                error=php_error_state(exc),
            )
            # Only a failed commit leaves the outcome open: nothing before it can have been saved.
            return Recorded.UNKNOWN if reached_commit else Recorded.NO
        return Recorded.YES

    async def _insert_signature(
        self,
        row: SpRow,
        target: SignTarget,
        dn_name: str,
    ) -> object | None:
        return await self._repository.insert_sign_general(
            client_request_id=target.client_request_id,
            ds_id=target.ds_id,
            client_request_document_id=target.client_request_document_id,
            sign_type_code=str(row["sign_type_code"]),
            dn_name=dn_name,
            sign_pid=int(str(row["sign_id"])),
        )

    async def resolve_failed_commit(self, sign_process_id: str, sign_id: object) -> Recorded:
        """The commit may still go through on the old session, so an empty read alone proves nothing.

        NO only when the old session is confirmed gone, its transaction is then decided, and a read
        made after that finds no row. Everything else stays UNKNOWN, and nothing is rolled back: a
        rollback here could remove a committed signature.
        """
        try:
            read_back = _ReadBack(old_session=self._repository.session_pid())
        except Exception as exc:
            logger.error(
                "Auto sign: old session unknown | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            return Recorded.UNKNOWN
        deadline = time.monotonic() + READ_BACK_SECONDS
        while True:
            if await self._row_found(read_back, sign_process_id, sign_id):
                return Recorded.YES
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(READ_BACK_PAUSE_SECONDS)
        return Recorded.NO if read_back.session_gone and read_back.row_absent else Recorded.UNKNOWN

    async def _row_found(
        self,
        read_back: _ReadBack,
        sign_process_id: str,
        sign_id: object,
    ) -> bool:
        """One pass of the read-back. A failed pass says nothing about the row."""
        try:
            async with database_step_limit():
                await self._end_old_session(read_back)
                read_back.session_gone = read_back.session_gone or not (
                    await self._repository.committed(
                        lambda repository: repository.session_is_alive(read_back.old_session),
                    )
                )
                found = await self._repository.committed(
                    lambda repository: repository.sign_tab__id_by_process(sign_process_id),
                )
            if found:
                return True
            read_back.row_absent = True
        except Exception as exc:
            read_back.row_absent = False
            logger.warning(
                "Auto sign: read-back failed | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
        return False

    async def _end_old_session(self, read_back: _ReadBack) -> None:
        """Once: its transaction is decided only after the session is gone."""
        if read_back.terminated:
            return
        await self._repository.committed(
            lambda repository: repository.terminate_session(read_back.old_session),
        )
        read_back.terminated = True
