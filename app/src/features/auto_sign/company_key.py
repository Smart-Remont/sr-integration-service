"""The key of the company: read for one document, its certificate checked before signing."""

import base64
from dataclasses import dataclass

from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.repository.base import SpRow
from src.shared.mynca_client import MyncaClient

from .repo import AutoSignRepository


@dataclass(frozen=True, repr=False)
class CompanyKey:
    """Lives only until the item is signed. Python cannot wipe it, so it is dropped with the call."""

    key_base64: str
    password: str

    def __repr__(self) -> str:
        return "CompanyKey(hidden)"


class CompanyKeys:
    def __init__(
        self,
        repository: AutoSignRepository,
        mynca: MyncaClient,
        master_key: str,
    ) -> None:
        self._repository = repository
        self._mynca = mynca
        self._master_key = master_key

    async def active_company_key(self, company_id: object, sign_id: object) -> CompanyKey | None:
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

    async def certificate_is_valid(self, key: CompanyKey, sign_id: object) -> bool:
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


def _company_key(row: SpRow) -> CompanyKey:
    """asyncpg gives a bytea as ``bytes``, where PDO gave a stream. No value in the error text."""
    key_data = row["key_data"]
    password = row["key_password"]
    if not isinstance(key_data, bytes) or not isinstance(password, str):
        raise TypeError("Unexpected types of the company key columns")
    return CompanyKey(key_base64=base64.b64encode(key_data).decode("ascii"), password=password)
