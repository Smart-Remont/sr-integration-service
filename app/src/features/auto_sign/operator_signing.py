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
blocks the document for good while the signature is rolled back: a signature lost. What a failed commit
leaves is in ``signature_record.py``.

To keep the loop from signing and rolling back forever: a dry run of the insert before signing, a brake
per document, and the run ends on the first unclear answer of MyNCA.
"""

from fastapi import Response
from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.repository.base import SpRow
from src.shared.cron_run import run_stop_requested
from src.shared.mynca_client import MyncaClient
from src.shared.uuid_text import is_uuid_text

from .company_key import CompanyKeys
from .failure_brake import SigningFailureBrake
from .mynca_signature import MyncaSignature, RunMustEnd, Signature
from .repo import AutoSignRepository
from .responses import empty_cron_response
from .run import read_rows_under_run_lock
from .signature_record import Recorded, SignatureRecord
from .signing_plan import signing_plan

_RUN_NAME = "auto-sign:cron-auto-sign-operator"


class OperatorSigningService:
    def __init__(
        self,
        repository: AutoSignRepository,
        mynca: MyncaClient,
        master_key: str,
        brake: SigningFailureBrake,
    ) -> None:
        self._repository = repository
        self._brake = brake
        self._keys = CompanyKeys(repository, mynca, master_key)
        self._signatures = MyncaSignature(mynca, brake)
        self._record = SignatureRecord(repository)

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
            except RunMustEnd:
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
        except RunMustEnd:
            raise
        except Exception as exc:
            logger.error(
                "Auto sign item failed | sign_id={sign_id} error={error}",
                sign_id=row.get("sign_id"),
                error=php_error_state(exc),
            )

    async def _sign_with_company_key(self, row: SpRow) -> Signature | None:
        sign_id = row["sign_id"]
        if self._brake.is_paused(sign_id):
            logger.warning("Auto sign: paused after failures | sign_id={sign_id}", sign_id=sign_id)
            return None
        plan = signing_plan(row)
        if plan is None or await self._record.would_be_refused(row, plan.target):
            return None
        key = await self._keys.active_company_key(row["company_id"], sign_id)
        if key is None or not await self._keys.certificate_is_valid(key, sign_id):
            return None
        file_content = await self._signatures.client_signed_file(plan.client_process_id, sign_id)
        if file_content is None:
            return None
        return await self._signatures.cms_sign_save(sign_id, plan, key, file_content)

    async def _finish_signature(self, row: SpRow, signature: Signature) -> None:
        sign_id = row["sign_id"]
        process_id = signature.sign_process_id
        if signature.group_id is not None and not is_uuid_text(signature.group_id):
            logger.error("Auto sign: group id is not a UUID | sign_id={sign_id}", sign_id=sign_id)
            await self._signatures.roll_back(process_id, sign_id, reason="bad group id")
            return
        recorded = await self._record.record_signature(row, signature)
        if recorded is Recorded.UNKNOWN:
            recorded = await self._record.resolve_failed_commit(process_id, sign_id)
            await self._act_on_outcome(recorded, process_id, sign_id)
            # The run's connection, and with it the lock, was terminated.
            raise RunMustEnd
        await self._act_on_outcome(recorded, process_id, sign_id)

    async def _act_on_outcome(
        self,
        recorded: Recorded,
        process_id: str,
        sign_id: object,
    ) -> None:
        match recorded:
            case Recorded.YES:
                self._brake.note_success(sign_id)
                logger.info(
                    "Auto sign: signed | sign_id={sign_id} process={process}",
                    sign_id=sign_id,
                    process=process_id,
                )
            case Recorded.NO:
                await self._signatures.roll_back(process_id, sign_id, reason="not recorded")
            case Recorded.UNKNOWN:
                logger.critical(
                    "Auto sign: signature state unknown, no rollback, check sign_tab against MyNCA "
                    "by hand | sign_id={sign_id} process={process}",
                    sign_id=sign_id,
                    process=process_id,
                )
                self._signatures.note_failure(sign_id)
