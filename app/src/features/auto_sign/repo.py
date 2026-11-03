"""One method, one database function, with the parameters PHP sent as text.

Rows with a company key do not go through ``pg_row``: it would turn the key bytes into text.
"""

from src.php_compat.pdo_values import NO_MODULE_CODE, pdo_text, pg_row
from src.repository.base import SpRow, SpRows, scalar_from_sp_rows
from src.shared.cron_run import CronRepository
from src.shared.sign_tab import (
    SignTabCall,
    id_by_process_call,
    insert_sign_general_call,
    sign_tab_modify_call,
)

_INT = "integer"
_BOOLEAN = "boolean"


class AutoSignRepository(CronRepository):
    async def sign_read_for_auto_sign_operator(self) -> SpRows:
        return await self._read_cursor("public.sign_read_for_auto_sign_operator")

    async def company_key_store__get_active_by_company(
        self,
        company_id: int,
        master_key: str,
    ) -> SpRow | None:
        rows = await self.call_sp(
            "nca.company_key_store__get_active_by_company",
            pdo_text(company_id),
            master_key,
            cursor=True,
            module_code=NO_MODULE_CODE,
            text_casts=(_INT, None),
        )
        return rows[0] if rows else None

    async def insert_sign_general(
        self,
        *,
        client_request_id: int | None,
        ds_id: int | None,
        client_request_document_id: int | None,
        sign_type_code: str,
        dn_name: str,
        sign_pid: int,
    ) -> object | None:
        rows = await self._run(
            insert_sign_general_call(
                client_request_id=client_request_id,
                ds_id=ds_id,
                client_request_document_id=client_request_document_id,
                sign_type_code=sign_type_code,
                dn_name=dn_name,
                source="MYNCA_AUTO",
                sign_pid=sign_pid,
            )
        )
        return scalar_from_sp_rows(rows)

    async def sign_tab__modify(
        self,
        *,
        sign_id: object | None,
        sign_process_id: str,
        sign_group_id: str | None,
    ) -> None:
        await self._run(
            sign_tab_modify_call(
                sign_id=sign_id,
                sign_process_id=sign_process_id,
                sign_group_id=sign_group_id,
            )
        )

    async def sign_tab__id_by_process(self, sign_process_id: str) -> SpRow | None:
        rows = await self._run(id_by_process_call(sign_process_id), cursor=True)
        return rows[0] if rows else None

    async def _run(self, call: SignTabCall, *, cursor: bool = False) -> SpRows:
        return await self.call_sp(
            call.function,
            *call.args,
            cursor=cursor,
            module_code=NO_MODULE_CODE,
            text_casts=call.casts,
        )

    def session_pid(self) -> int:
        return self.connection.get_server_pid()

    async def terminate_session(self, pid: int) -> None:
        await self.call_sp("pg_catalog.pg_terminate_backend", pid, module_code=NO_MODULE_CODE)

    async def session_is_alive(self, pid: int) -> bool:
        """The call must start its own transaction: the server keeps this view for one transaction."""
        rows = await self.call_sp(
            "pg_catalog.pg_stat_get_activity", pid, module_code=NO_MODULE_CODE
        )
        return bool(rows)

    async def sign_read_for_doc(self) -> SpRows:
        return await self._read_cursor("public.sign_read_for_doc")

    async def sign_group_id_get_by_client_request(self, client_request_id: int) -> SpRow | None:
        rows = await self.call_sp(
            "public.sign_group_id_get_by_client_request",
            pdo_text(client_request_id),
            cursor=True,
            module_code=NO_MODULE_CODE,
            text_casts=(_INT,),
        )
        return pg_row(rows[0]) if rows else None

    async def sign_document_upd(
        self,
        *,
        client_request_id: int,
        sign_doc_name: str,
        sign_doc_url: str,
        is_create_task: bool,
        is_update: bool,
    ) -> None:
        await self.call_sp(
            "sale.sign_document_upd",
            pdo_text(client_request_id),
            sign_doc_name,
            sign_doc_url,
            _boolean_text(is_create_task),
            _boolean_text(is_update),
            module_code=NO_MODULE_CODE,
            text_casts=(_INT, None, None, _BOOLEAN, _BOOLEAN),
        )

    async def _read_cursor(self, function: str) -> SpRows:
        rows = await self.call_sp(function, cursor=True, module_code=NO_MODULE_CODE)
        return [pg_row(row) for row in rows]


def _boolean_text(value: bool) -> str:
    """PHP ``isBool`` and the literal strings it binds."""
    return "true" if value else "false"
