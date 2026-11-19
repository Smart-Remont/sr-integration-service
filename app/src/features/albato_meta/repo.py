from src.php_compat.pdo_values import NO_MODULE_CODE, pdo_text
from src.repository import BaseRepository, scalar_from_sp_rows

_SETTING_CODE = "ALBATO_META_TOKEN"


class AlbatoMetaRepository(BaseRepository):
    async def access_token(self) -> str | None:
        rows = await self.call_sp(
            "public.get_setting_value_by_code",
            _SETTING_CODE,
            module_code=NO_MODULE_CODE,
        )
        return pdo_text(scalar_from_sp_rows(rows))

    async def request_from_albato_meta(self, payload: str) -> object | None:
        rows = await self.call_sp(
            "crm.request_from_albato_meta",
            payload,
            module_code=NO_MODULE_CODE,
        )
        return scalar_from_sp_rows(rows)
