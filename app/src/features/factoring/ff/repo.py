import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.features.big_integration.db import scalar_from_sp_rows
from src.repository import BaseRepository

from ..schemas import FactoringApplicationResponse

PROVIDER_CODE = "FF_FACTORING"


@dataclass(slots=True, frozen=True)
class FactoringProvider:
    id: int
    code: str
    base_url: str
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class FactoringCredential:
    username: str
    password: str


@dataclass(slots=True, frozen=True)
class FactoringToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(slots=True, frozen=True)
class FactoringWebhookCredential:
    username: str
    password_hash: str


@dataclass(slots=True, frozen=True)
class FactoringWebhookApplication:
    id: int
    status: str
    approved_params: dict[str, Any] | None


@dataclass(slots=True, frozen=True)
class CessionBatchItem:
    id: int
    uuid: str | None
    credit_contract: str
    principal: Decimal
    status: str
    company_id: int | None
    period: int | None = None
    issued_at: datetime | None = None


class FactoringRepository(BaseRepository):
    async def get_provider_by_code(self, code: str = PROVIDER_CODE) -> FactoringProvider | None:
        rows = await self.call_sp(
            "public.factoring__provider_get",
            code,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]

        raw_config = row["config"]
        if isinstance(raw_config, str):
            config = json.loads(raw_config)
        elif isinstance(raw_config, dict):
            config = raw_config
        else:
            config = {}

        return FactoringProvider(
            id=row["id"],
            code=row["code"],
            base_url=row["base_url"],
            config=config,
        )

    async def get_active_credentials(self, provider_id: int, env: str) -> FactoringCredential | None:
        rows = await self.call_sp(
            "public.factoring__credential_get_active",
            provider_id,
            env,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return FactoringCredential(
            username=row["username"],
            password=row["password"],
        )

    async def get_token(self, provider_id: int) -> FactoringToken | None:
        rows = await self.call_sp(
            "public.factoring__token_get",
            provider_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return FactoringToken(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
        )

    async def upsert_token(
        self,
        provider_id: int,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime,
    ) -> None:
        payload = {
            "provider_id": provider_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
        }
        scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__token_upsert",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def insert_application(
        self,
        *,
        client_request_id: int,
        provider_id: int,
        credit_contract: str,
        product_id: str,
        principal: Decimal,
        period: int,
        created_by: int,
        partner: str | None = None,
        channel: str | None = None,
        branch_code: str | None = None,
        reference_id: str | None = None,
        prepayment_amount: Decimal | None = None,
        interest_rate: Decimal | None = None,
        print_forms: list[dict[str, Any]] | None = None,
        credit_goods: list[dict[str, Any]] | None = None,
        request_payload: dict[str, Any] | None = None,
        success_url: str | None = None,
        failure_url: str | None = None,
        hook_url: str | None = None,
        status: str = "NEW",
        prescoring_status: str | None = None,
        prescoring_score: Decimal | None = None,
        prescoring_message: str | None = None,
        prescoring_max_limit: Decimal | None = None,
        prescoring_checked_at: datetime | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "client_request_id": client_request_id,
            "provider_id": provider_id,
            "credit_contract": credit_contract,
            "product_id": product_id,
            "principal": str(principal),
            "period": period,
            "created_by": created_by,
            "status": status,
        }
        if partner is not None:
            payload["partner"] = partner
        if channel is not None:
            payload["channel"] = channel
        if branch_code is not None:
            payload["branch_code"] = branch_code
        if reference_id is not None:
            payload["reference_id"] = reference_id
        if prepayment_amount is not None:
            payload["prepayment_amount"] = str(prepayment_amount)
        if interest_rate is not None:
            payload["interest_rate"] = str(interest_rate)
        if print_forms is not None:
            payload["print_forms"] = print_forms
        if credit_goods is not None:
            payload["credit_goods"] = credit_goods
        if request_payload is not None:
            payload["request_payload"] = request_payload
        if success_url is not None:
            payload["success_url"] = success_url
        if failure_url is not None:
            payload["failure_url"] = failure_url
        if hook_url is not None:
            payload["hook_url"] = hook_url
        if prescoring_status is not None:
            payload["prescoring_status"] = prescoring_status
        if prescoring_score is not None:
            payload["prescoring_score"] = str(prescoring_score)
        if prescoring_message is not None:
            payload["prescoring_message"] = prescoring_message
        if prescoring_max_limit is not None:
            payload["prescoring_max_limit"] = str(prescoring_max_limit)
        if prescoring_checked_at is not None:
            payload["prescoring_checked_at"] = prescoring_checked_at.isoformat()

        scalar_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__application_create",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        if scalar_result is None:
            raise RuntimeError("Failed to insert factoring application.")
        return int(scalar_result)

    async def update_application_after_apply(
        self,
        *,
        application_id: int,
        uuid: str,
        redirect_url: str | None,
        status: str,
        reference_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "application_id": application_id,
            "uuid": uuid,
            "redirect_url": redirect_url,
            "status": status,
        }
        if reference_id is not None:
            payload["reference_id"] = reference_id
        scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__application_update_after_apply",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def insert_event_log(
        self,
        *,
        factoring_id: int | None,
        event_type: str,
        payload: dict[str, Any],
        source: str,
    ) -> None:
        request_payload: dict[str, Any] = {
            "event_type": event_type,
            "payload": payload,
            "source": source,
        }
        if factoring_id is not None:
            request_payload["factoring_id"] = factoring_id
        scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__event_log_add",
                json.dumps(request_payload),
                module_code="MYSPACE",
            )
        )

    async def insert_event_log_committed(
        self,
        *,
        factoring_id: int | None,
        event_type: str,
        payload: dict[str, Any],
        source: str,
    ) -> None:
        """Write audit on a separate connection so a later HTTP 422 rollback
        does not erase CESSION_REQUEST / bank error details."""
        from src.database.pool import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as connection:
            other = FactoringRepository(connection=connection)
            async with connection.transaction():
                await other.insert_event_log(
                    factoring_id=factoring_id,
                    event_type=event_type,
                    payload=payload,
                    source=source,
                )

    def _row_to_application(self, row: dict[str, Any]) -> FactoringApplicationResponse:
        payload = dict(row)
        for key in ("approved_params", "print_forms", "credit_goods", "request_payload"):
            if isinstance(payload.get(key), str):
                payload[key] = json.loads(payload[key])
        return FactoringApplicationResponse.model_validate(payload)

    async def get_application_by_id(self, application_id: int) -> FactoringApplicationResponse | None:
        rows = await self.call_sp(
            "public.factoring__application_get",
            application_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        return self._row_to_application(dict(rows[0]))

    async def get_applications_by_client_request(
        self, client_request_id: int
    ) -> list[FactoringApplicationResponse]:
        rows = await self.call_sp(
            "public.factoring__applications_list_by_client_request",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        return [self._row_to_application(dict(row)) for row in rows]

    async def get_client_request_contacts(
        self, client_request_id: int
    ) -> dict[str, Any] | None:
        rows = await self.call_sp(
            "public.factoring__client_request_get_for_apply",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        return dict(rows[0]) if rows else None

    async def client_request_exists(self, client_request_id: int) -> bool:
        return await self.get_client_request_contacts(client_request_id) is not None

    async def get_deal_total_amount(self, client_request_id: int) -> Decimal | None:
        row = await self.fetchrow(
            """
            SELECT price_total_area_material
            FROM client_request_tab
            WHERE client_request_id = $1
            """,
            client_request_id,
        )
        if row is None or row["price_total_area_material"] is None:
            return None
        return Decimal(str(row["price_total_area_material"]))

    async def get_deal_committed_amount(
        self,
        client_request_id: int,
        *,
        exclude_application_id: int | None = None,
    ) -> Decimal:
        """Sum of active/issued principal across BOTH installment and
        factoring for this deal (public.cr_deal_committed_amount, shared with
        installment/ff/repo.py). Several hunters can work the same deal in
        parallel — their combined principal must not exceed the deal amount."""
        row = await self.fetchrow(
            "SELECT public.cr_deal_committed_amount($1::bigint, $2::bigint) AS committed",
            client_request_id,
            exclude_application_id,
        )
        return Decimal(str(row["committed"])) if row is not None else Decimal("0")

    async def get_client_request_company_id(self, client_request_id: int) -> int | None:
        row = await self.fetchrow(
            """
            SELECT company_id
            FROM client_request_tab
            WHERE client_request_id = $1
            LIMIT 1
            """,
            client_request_id,
        )
        if row is None or row["company_id"] is None:
            return None
        return int(row["company_id"])

    async def get_template_by_code(self, template_code: str) -> dict[str, Any] | None:
        wanted = template_code.strip()
        rows = await self.call_sp(
            "public.template__read",
            cursor=True,
            module_code="MYSPACE",
        )
        for row in rows:
            code = str(row.get("template_code") or "").strip()
            if code == wanted:
                return dict(row)
        fallback_ids = {
            "FF_FACTORING_APPLICATION": 43,
            "FF_FACTORING_NOTIFICATION": 44,
            "FF_FACTORING_CESSION": 46,
        }
        template_id = fallback_ids.get(wanted)
        if template_id is None:
            return None
        by_id = await self.call_sp(
            "public.template__get",
            template_id,
            cursor=True,
            module_code="MYSPACE",
        )
        return dict(by_id[0]) if by_id else None

    async def get_provider_webhook_credentials(
        self, code: str = PROVIDER_CODE
    ) -> FactoringWebhookCredential | None:
        rows = await self.call_sp(
            "public.factoring__provider_webhook_credentials_get",
            code,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        username = row["webhook_username"]
        password_hash = row["webhook_password"]
        if not isinstance(username, str) or not username.strip():
            return None
        if not isinstance(password_hash, str) or not password_hash.strip():
            return None
        return FactoringWebhookCredential(username=username, password_hash=password_hash)

    async def get_application_by_reference_or_uuid(
        self,
        reference_id: str | None,
        uuid: str | None,
    ) -> FactoringWebhookApplication | None:
        if reference_id is None and uuid is None:
            return None

        rows = await self.call_sp(
            "public.factoring__application_get_by_reference_or_uuid",
            reference_id,
            uuid,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        approved_params = row["approved_params"]
        if isinstance(approved_params, str):
            approved_params = json.loads(approved_params)

        return FactoringWebhookApplication(
            id=row["id"],
            status=row["status"],
            approved_params=approved_params if isinstance(approved_params, dict) else None,
        )

    async def get_decrypted_company_key(
        self,
        company_key_store_id: int,
        master_key: str,
    ) -> tuple[bytes, str]:
        """Decrypts a company EDS key from nca.company_key_store_tab (pgcrypto,
        same DB). OUT-param function — must SELECT * FROM it to get flattened
        columns (call_sp's generic `SELECT fn(...)` would return one composite
        column instead)."""
        row = await self.fetchrow(
            "SELECT * FROM nca.company_key_store__get_decrypted($1, $2)",
            company_key_store_id,
            master_key,
        )
        if row is None:
            raise RuntimeError(f"Company key {company_key_store_id} was not found.")
        key_data = row["key_data"]
        key_password = row["key_password"]
        if not isinstance(key_data, bytes) or not key_data:
            raise RuntimeError(f"Company key {company_key_store_id} decrypted empty.")
        if not isinstance(key_password, str) or not key_password:
            raise RuntimeError(f"Company key {company_key_store_id} password decrypted empty.")
        return key_data, key_password

    async def get_active_company_key_store_id(self, company_id: int) -> int | None:
        """First active EDS key for a legal entity (nca.company_key_store_tab)."""
        rows = await self.call_sp(
            "nca.company_key_store__read_by_company",
            company_id,
            cursor=True,
            module_code="MYSPACE",
        )
        for row in rows:
            if row.get("is_active"):
                return int(row["company_key_store_id"])
        return None

    async def get_cession_company(self, company_id: int) -> dict[str, Any] | None:
        row = await self.fetchrow(
            """
            SELECT
                c.company_name_official,
                c.director_fio,
                i.bank_account
            FROM company_tab c
            LEFT JOIN initiator_company_tab i ON i.company_id = c.company_id
            WHERE c.company_id = $1
            ORDER BY
                CASE
                    WHEN i.bank_account IS NOT NULL AND btrim(i.bank_account) <> '' THEN 0
                    ELSE 1
                END,
                i.initiator_company_id DESC NULLS LAST
            LIMIT 1
            """,
            company_id,
        )
        return dict(row) if row else None

    async def list_cession_batch(self, issue_date: date) -> list[CessionBatchItem]:
        rows = await self.call_sp(
            "public.factoring__cession_batch_list",
            issue_date,
            cursor=True,
            module_code="MYSPACE",
        )
        return [
            CessionBatchItem(
                id=row["id"],
                uuid=row["uuid"],
                credit_contract=row["credit_contract"],
                principal=row["principal"],
                status=row["status"],
                company_id=row.get("company_id"),
                period=row.get("period"),
                issued_at=row.get("issued_at"),
            )
            for row in rows
        ]

    async def mark_cession_sent(
        self,
        application_ids: list[int],
        contract_number: str,
        *,
        sign_process_id: str | None = None,
        sign_group_id: str | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "application_ids": application_ids,
            "contract_number": contract_number,
        }
        if sign_process_id is not None:
            payload["sign_process_id"] = sign_process_id
        if sign_group_id is not None:
            payload["sign_group_id"] = sign_group_id
        result = scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__cession_mark_sent",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        return int(result) if result is not None else 0

    async def mark_cession_sent_committed(
        self,
        application_ids: list[int],
        contract_number: str,
        *,
        sign_process_id: str | None = None,
        sign_group_id: str | None = None,
    ) -> int:
        """Stamp cession on a separate connection so a later rollback
        does not un-mark applications the bank already accepted."""
        from src.database.pool import get_db_pool

        pool = await get_db_pool()
        async with pool.acquire() as connection:
            other = FactoringRepository(connection=connection)
            async with connection.transaction():
                return await other.mark_cession_sent(
                    application_ids,
                    contract_number,
                    sign_process_id=sign_process_id,
                    sign_group_id=sign_group_id,
                )

    async def unmark_cession_sent(
        self,
        application_ids: list[int],
        contract_number: str | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "application_ids": application_ids,
        }
        if contract_number is not None:
            payload["contract_number"] = contract_number
        result = scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__cession_unmark",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        return int(result) if result is not None else 0

    async def update_application_from_webhook(
        self,
        *,
        application_id: int,
        status: str,
        approved_params: dict[str, Any] | None = None,
        uuid: str | None = None,
        redirect_url: str | None = None,
        issued_at: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "application_id": application_id,
            "status": status,
        }
        if approved_params is not None:
            payload["approved_params"] = approved_params
        if uuid is not None:
            payload["uuid"] = uuid
        if redirect_url is not None:
            payload["redirect_url"] = redirect_url
        if issued_at is not None:
            payload["issued_at"] = issued_at
        scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__application_update_from_webhook",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def update_application_refund(
        self,
        *,
        application_id: int | None = None,
        uuid: str | None = None,
        refund_type: str | None = None,
        refund_amount: Decimal | None = None,
        covlir_status: str | None = None,
        clear: bool = False,
    ) -> int:
        payload: dict[str, Any] = {}
        if application_id is not None:
            payload["application_id"] = application_id
        if uuid is not None:
            payload["uuid"] = uuid
        if refund_type is not None:
            payload["refund_type"] = refund_type
        if refund_amount is not None:
            payload["refund_amount"] = str(refund_amount)
        if covlir_status is not None:
            payload["covlir_status"] = covlir_status
        if clear:
            payload["clear"] = True
        scalar_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__application_update_refund",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        if scalar_result is None:
            raise RuntimeError("Failed to update factoring refund.")
        return int(scalar_result)

    async def update_print_forms(
        self,
        application_id: int,
        print_forms: list,
    ) -> int:
        payload: dict[str, Any] = {
            "application_id": application_id,
            "print_forms": print_forms,
        }
        scalar_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.factoring__application_update_print_forms",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        if scalar_result is None:
            raise RuntimeError("Failed to update factoring print_forms.")
        return int(scalar_result)
