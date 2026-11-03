"""PHP ``cronAutoSignOperatorAction``: the company signs what the client has signed.

A signature lives in two places, MyNCA and ``sign_tab``, with no transaction around them, and nothing
records an attempt. The next run signs every document that has no ``sign_tab`` row pointing at it through
``sign_pid``. So a signature left at MyNCA without that row turns into a second signature. This risk is
kept as in PHP by decision. Where a second signature can still appear:

- ``cms/sign-save`` gives no clear answer: no id to roll back. The run ends, the next one signs again.
- The worker dies between ``cms/sign-save`` and the commit, for example in a deploy. The "starts" log
  line without its "answered" line, or a signed line missing after it, is the only trace.
- ``signRollback`` fails.
- The commit fails and its outcome stays unknown. Deliberately different from PHP, which rolled back:
  here a possible second signature is taken over a possibly lost one.
- The list is a snapshot: a signature added by hand during a run is not seen.

Insert and modify share one transaction. In PHP each commits alone, so a failed modify keeps a row that
blocks the document for good while the signature is rolled back: a signature lost. After a failed commit
one empty read proves nothing, since the old session may still be committing: see
``_resolve_failed_commit``. That step ends the old session and with it the run lock, so a new run can
start during the read-back. It then signs only if the row is still missing, and the outcome is still one
signature once this run rolls its own back, or a second one if this run ends in UNKNOWN.

To keep the loop from signing and rolling back forever: a dry run of the insert before signing, a brake
per document, and the run ends on the first unclear answer of MyNCA.
"""

import asyncio
import base64
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import NoReturn

from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.php_compat.text import php_string
from src.repository.base import SpRow
from src.shared.cron_run import database_step_limit, run_stop_requested
from src.shared.mynca_client import MyncaAnswer, MyncaClient
from src.shared.uuid_text import is_uuid_text

from .failure_brake import SigningFailureBrake
from .repo import AutoSignRepository
from .responses import empty_cron_response
from .run import read_rows_under_run_lock
from .signing_plan import SigningPlan, SignTarget, sign_target, signing_plan

_RUN_NAME = "auto-sign:cron-auto-sign-operator"
_FIRST_SERVER_ERROR = 500
# The real name is known only after signing. client.insert_sign_general needs digits after IIN, and
# for company signature types it compares them with nothing today.
_DRY_RUN_DN_NAME = "IIN000000000000"
READ_BACK_SECONDS = 120.0
READ_BACK_PAUSE_SECONDS = 15.0


@dataclass(frozen=True, repr=False)
class _CompanyKey:
    """Lives only until the item is signed. Python cannot wipe it, so it is dropped with the call."""

    key_base64: str
    password: str

    def __repr__(self) -> str:
        return "_CompanyKey(hidden)"


@dataclass(frozen=True)
class _Signature:
    sign_process_id: str
    dn_name: str
    group_id: str | None


class _Recorded(Enum):
    YES = auto()
    NO = auto()
    UNKNOWN = auto()


class _RunMustEnd(Exception):
    """MyNCA may be flapping, or the run's connection was terminated: no further signing now."""


class _DryRunFinished(Exception):
    """Leaves the dry-run transaction, which therefore always rolls back."""


class OperatorSigningService:
    def __init__(
        self,
        repository: AutoSignRepository,
        mynca: MyncaClient,
        master_key: str,
        brake: SigningFailureBrake,
    ) -> None:
        self._repository = repository
        self._mynca = mynca
        self._master_key = master_key
        self._brake = brake

    async def sign_waiting_documents(self) -> Response:
        rows = await read_rows_under_run_lock(
            self._repository,
            _RUN_NAME,
            AutoSignRepository.sign_read_for_auto_sign_operator,
        )
        for position, row in enumerate(rows, start=1):
            if run_stop_requested():
                logger.warning(
                    "Auto sign: stopped between documents, the service is stopping | left={left}",
                    left=len(rows) - position + 1,
                )
                break
            try:
                await self._sign_row(row)
            except _RunMustEnd:
                logger.critical(
                    "Auto sign: run ended early | sign_id={sign_id} left={left}",
                    sign_id=row.get("sign_id"),
                    left=len(rows) - position,
                )
                break
        return empty_cron_response()

    async def _sign_row(self, row: SpRow) -> None:
        """Boundary of an item: a failure is logged by class and the loop goes on, as in PHP."""
        try:
            signature = await self._sign_with_company_key(row)
            if signature is not None:
                await self._finish_signature(row, signature)
        except _RunMustEnd:
            raise
        except Exception as exc:
            logger.error(
                "Auto sign item failed | sign_id={sign_id} error={error}",
                sign_id=row.get("sign_id"),
                error=php_error_state(exc),
            )

    async def _sign_with_company_key(self, row: SpRow) -> _Signature | None:
        sign_id = row["sign_id"]
        if self._brake.is_paused(sign_id):
            logger.warning("Auto sign: paused after failures | sign_id={sign_id}", sign_id=sign_id)
            return None
        plan = signing_plan(row)
        if plan is None or await self._record_would_be_refused(row, plan.target):
            return None
        key = await self._active_company_key(row["company_id"], sign_id)
        if key is None or not await self._certificate_is_valid(key, sign_id):
            return None
        file_content = await self._client_signed_file(plan.client_process_id, sign_id)
        if file_content is None:
            return None
        return await self._cms_sign_save(sign_id, plan, key, file_content)

    async def _record_would_be_refused(self, row: SpRow, target: SignTarget) -> bool:
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

    async def _active_company_key(self, company_id: object, sign_id: object) -> _CompanyKey | None:
        try:
            async with self._repository.step():
                key_row = await self._repository.company_key_store__get_active_by_company(
                    int(str(company_id)),
                    self._master_key,
                )
            if key_row is None:
                logger.error("Auto sign: no company key | sign_id={sign_id}", sign_id=sign_id)
                return None
            return _company_key(key_row)
        except Exception as exc:
            logger.error(
                "Auto sign: company key read failed | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            return None

    async def _certificate_is_valid(self, key: _CompanyKey, sign_id: object) -> bool:
        answer = await self._mynca.pkcs12_info(
            {
                "key": key.key_base64,
                "password": key.password,
                "revocationCheck": ["OCSP", "CRL"],
            },
        )
        if answer.status and answer.field("valid") is True:
            return True
        logger.error(
            "Auto sign: certificate invalid or revoked | sign_id={sign_id} code={code}",
            sign_id=sign_id,
            code=answer.code,
        )
        return False

    async def _client_signed_file(self, client_process_id: str, sign_id: object) -> object | None:
        answer = await self._mynca.sign_get_file(client_process_id)
        file_content = _file_content(answer) if answer.status else None
        # PHP checks "" apart from a missing file, and both end the item.
        if file_content is None or file_content == "":
            logger.error(
                "Auto sign: client file not read | sign_id={sign_id} code={code}",
                sign_id=sign_id,
                code=answer.code,
            )
            return None
        return file_content

    async def _cms_sign_save(
        self,
        sign_id: object,
        plan: SigningPlan,
        key: _CompanyKey,
        file_content: object,
    ) -> _Signature | None:
        payload: dict[str, object] = {
            "data": file_content,
            "signer": {"key": key.key_base64, "password": key.password},
            "withTsp": True,
            "detached": False,
            "ext_id": plan.target.ext_id,
        }
        if plan.group_id is not None:
            payload["group_id"] = plan.group_id
        answer = await self._post_cms_sign_save(sign_id, plan.target.ext_id, payload)
        if not answer.status:
            return self._refused_signature(sign_id, answer)
        sign_process_id = _optional_text(answer.field("sign_process_id"))
        if sign_process_id is None or not is_uuid_text(sign_process_id):
            self._end_run_on_unclear_answer(sign_id, answer.code)
        logger.info(
            "Auto sign: cms/sign-save answered | sign_id={sign_id} process={process}",
            sign_id=sign_id,
            process=sign_process_id,
        )
        return _Signature(
            sign_process_id=sign_process_id,
            dn_name=_optional_text(answer.field("dn_name")) or "",
            group_id=_optional_text(answer.field("group_id")) or None,
        )

    async def _post_cms_sign_save(
        self,
        sign_id: object,
        ext_id: int,
        payload: dict[str, object],
    ) -> MyncaAnswer:
        logger.info(
            "Auto sign: cms/sign-save starts | sign_id={sign_id} ext_id={ext_id}",
            sign_id=sign_id,
            ext_id=ext_id,
        )
        try:
            return await self._mynca.cms_sign_save(payload)
        except asyncio.CancelledError:
            logger.critical(
                "Auto sign: cancelled during cms/sign-save, a signature may exist at MyNCA and the "
                "next run signs again | sign_id={sign_id}",
                sign_id=sign_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "Auto sign: cms/sign-save failed | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            self._end_run_on_unclear_answer(sign_id, 0)

    def _refused_signature(self, sign_id: object, answer: MyncaAnswer) -> None:
        if answer.code == 0 or answer.code >= _FIRST_SERVER_ERROR:
            self._end_run_on_unclear_answer(sign_id, answer.code)
        logger.error(
            "Auto sign: MyNCA refused to sign | sign_id={sign_id} code={code}",
            sign_id=sign_id,
            code=answer.code,
        )
        self._note_failure(sign_id)

    def _end_run_on_unclear_answer(self, sign_id: object, code: int) -> NoReturn:
        logger.critical(
            "Auto sign: no clear answer to cms/sign-save, a signature may exist at MyNCA and the "
            "next run signs again | sign_id={sign_id} code={code}",
            sign_id=sign_id,
            code=code,
        )
        self._note_failure(sign_id)
        raise _RunMustEnd

    async def _finish_signature(self, row: SpRow, signature: _Signature) -> None:
        sign_id = row["sign_id"]
        process_id = signature.sign_process_id
        if signature.group_id is not None and not is_uuid_text(signature.group_id):
            logger.error("Auto sign: group id is not a UUID | sign_id={sign_id}", sign_id=sign_id)
            await self._roll_back(process_id, sign_id, reason="bad group id")
            return
        recorded = await self._record_signature(row, signature)
        if recorded is _Recorded.UNKNOWN:
            recorded = await self._resolve_failed_commit(process_id, sign_id)
            await self._act_on_outcome(recorded, process_id, sign_id)
            # The run's connection, and with it the lock, was terminated.
            raise _RunMustEnd
        await self._act_on_outcome(recorded, process_id, sign_id)

    async def _act_on_outcome(
        self,
        recorded: _Recorded,
        process_id: str,
        sign_id: object,
    ) -> None:
        match recorded:
            case _Recorded.YES:
                self._brake.note_success(sign_id)
                logger.info(
                    "Auto sign: signed | sign_id={sign_id} process={process}",
                    sign_id=sign_id,
                    process=process_id,
                )
            case _Recorded.NO:
                await self._roll_back(process_id, sign_id, reason="not recorded")
            case _Recorded.UNKNOWN:
                logger.critical(
                    "Auto sign: signature state unknown, no rollback, check sign_tab against MyNCA "
                    "by hand | sign_id={sign_id} process={process}",
                    sign_id=sign_id,
                    process=process_id,
                )
                self._note_failure(sign_id)

    async def _record_signature(self, row: SpRow, signature: _Signature) -> _Recorded:
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
            return _Recorded.UNKNOWN if reached_commit else _Recorded.NO
        return _Recorded.YES

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

    async def _resolve_failed_commit(self, sign_process_id: str, sign_id: object) -> _Recorded:
        """The commit may still go through on the old session, so an empty read alone proves nothing.

        NO only when the old session is confirmed gone, its transaction is then decided, and a read
        made after that finds no row. Everything else stays UNKNOWN, and nothing is rolled back: a
        rollback here could remove a committed signature.
        """
        try:
            old_session = self._repository.session_pid()
        except Exception as exc:
            logger.error(
                "Auto sign: old session unknown | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            return _Recorded.UNKNOWN
        deadline = time.monotonic() + READ_BACK_SECONDS
        terminated = False
        session_gone = False
        row_absent = False
        while True:
            try:
                async with database_step_limit():
                    if not terminated:
                        await self._repository.committed(
                            lambda repository: repository.terminate_session(old_session),
                        )
                        terminated = True
                    session_gone = session_gone or not await self._repository.committed(
                        lambda repository: repository.session_is_alive(old_session),
                    )
                    found = await self._repository.committed(
                        lambda repository: repository.sign_tab__id_by_process(sign_process_id),
                    )
                if found:
                    return _Recorded.YES
                row_absent = True
            except Exception as exc:
                row_absent = False
                logger.warning(
                    "Auto sign: read-back failed | sign_id={sign_id} error={error}",
                    sign_id=sign_id,
                    error=php_error_state(exc),
                )
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(READ_BACK_PAUSE_SECONDS)
        return _Recorded.NO if session_gone and row_absent else _Recorded.UNKNOWN

    async def _roll_back(self, sign_process_id: str, sign_id: object, *, reason: str) -> None:
        self._note_failure(sign_id)
        try:
            answer = await self._mynca.sign_rollback(sign_process_id)
            rolled_back = answer.status
        except Exception as exc:
            logger.error(
                "Auto sign: rollback call failed | sign_id={sign_id} error={error}",
                sign_id=sign_id,
                error=php_error_state(exc),
            )
            rolled_back = False
        if rolled_back:
            logger.warning(
                "Auto sign: signature rolled back | sign_id={sign_id} process={process} "
                "reason={reason}",
                sign_id=sign_id,
                process=sign_process_id,
                reason=reason,
            )
            return
        logger.critical(
            "Auto sign: ROLLBACK FAILED, the signature stays at MyNCA and the next run signs again | "
            "sign_id={sign_id} process={process} reason={reason}",
            sign_id=sign_id,
            process=sign_process_id,
            reason=reason,
        )

    def _note_failure(self, sign_id: object) -> None:
        if self._brake.note_failure(sign_id):
            logger.critical(
                "Auto sign: document paused for an hour after repeated failures in this worker | "
                "sign_id={sign_id}",
                sign_id=sign_id,
            )


def _company_key(row: SpRow) -> _CompanyKey:
    """asyncpg gives a bytea as ``bytes``, where PDO gave a stream. No value in the error text."""
    key_data = row["key_data"]
    password = row["key_password"]
    if not isinstance(key_data, bytes) or not isinstance(password, str):
        raise TypeError("Unexpected types of the company key columns")
    return _CompanyKey(key_base64=base64.b64encode(key_data).decode("ascii"), password=password)


def _file_content(answer: MyncaAnswer) -> object | None:
    inner = answer.field("data")
    return inner.get("file_content") if isinstance(inner, dict) else None


def _optional_text(value: object) -> str | None:
    """``isset($x) ? (string) $x : null``."""
    return None if value is None else php_string(value)
