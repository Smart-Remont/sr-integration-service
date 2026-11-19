"""The calls to MyNCA around a company signature: the file of the client, the signature, its rollback."""

import asyncio
from dataclasses import dataclass
from typing import NoReturn

from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.php_compat.text import php_string
from src.shared.mynca_client import MyncaAnswer, MyncaClient
from src.shared.uuid_text import is_uuid_text

from .company_key import CompanyKey
from .failure_brake import SigningFailureBrake
from .signing_plan import SigningPlan

_FIRST_SERVER_ERROR = 500


@dataclass(frozen=True)
class Signature:
    sign_process_id: str
    dn_name: str
    group_id: str | None


class RunMustEnd(Exception):
    """MyNCA may be flapping, or the run's connection was terminated: no further signing now."""


class MyncaSignature:
    def __init__(self, mynca: MyncaClient, brake: SigningFailureBrake) -> None:
        self._mynca = mynca
        self._brake = brake

    async def client_signed_file(self, client_process_id: str, sign_id: object) -> object | None:
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

    async def cms_sign_save(
        self,
        sign_id: object,
        plan: SigningPlan,
        key: CompanyKey,
        file_content: object,
    ) -> Signature | None:
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
        return Signature(
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
        self.note_failure(sign_id)

    def _end_run_on_unclear_answer(self, sign_id: object, code: int) -> NoReturn:
        logger.critical(
            "Auto sign: no clear answer to cms/sign-save, a signature may exist at MyNCA and the "
            "next run signs again | sign_id={sign_id} code={code}",
            sign_id=sign_id,
            code=code,
        )
        self.note_failure(sign_id)
        raise RunMustEnd

    async def roll_back(self, sign_process_id: str, sign_id: object, *, reason: str) -> None:
        self.note_failure(sign_id)
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

    def note_failure(self, sign_id: object) -> None:
        if self._brake.note_failure(sign_id):
            logger.critical(
                "Auto sign: document paused for an hour after repeated failures in this worker | "
                "sign_id={sign_id}",
                sign_id=sign_id,
            )


def _file_content(answer: MyncaAnswer) -> object | None:
    inner = answer.field("data")
    return inner.get("file_content") if isinstance(inner, dict) else None


def _optional_text(value: object) -> str | None:
    """``isset($x) ? (string) $x : null``."""
    return None if value is None else php_string(value)
