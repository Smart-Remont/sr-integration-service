import json

from src.repository import BaseRepository, scalar_from_sp_rows
from src.repository.base import SpRow

from ..errors import BigIntegrationDatabaseError, to_big_integration_database_error

_DDU_MODULE_CODE = "DDU"


class RequestCreateV3Repository(BaseRepository):
    async def ddu_create_request_v2(self, payload: dict[str, object]) -> int:
        try:
            rows = await self.call_sp(
                "rest.ddu__create_request_v2",
                json.dumps(payload),
                module_code=_DDU_MODULE_CODE,
            )
        except Exception as exc:
            raise to_big_integration_database_error(exc) from exc

        client_request_id = scalar_from_sp_rows(rows)
        if client_request_id is None:
            raise BigIntegrationDatabaseError(
                "Пустой ответ от rest.ddu__create_request_v2",
            )

        return int(client_request_id)

    async def ddu_request_get(self, client_request_id: int) -> SpRow | None:
        try:
            rows = await self.call_sp(
                "rest.ddu__request_get",
                client_request_id,
                cursor=True,
                module_code=_DDU_MODULE_CODE,
            )
        except Exception as exc:
            raise to_big_integration_database_error(exc) from exc

        if not rows:
            return None

        return rows[0]
