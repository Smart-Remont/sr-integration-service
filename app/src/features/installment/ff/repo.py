import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.features.big_integration.db import scalar_from_sp_rows
from src.repository import BaseRepository

from ..schemas import InstallmentApplicationResponse


@dataclass(slots=True, frozen=True)
class FFProvider:
    id: int
    code: str
    base_url: str
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class FFCredential:
    username: str
    password: str


@dataclass(slots=True, frozen=True)
class FFToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(slots=True, frozen=True)
class FFWebhookCredential:
    username: str
    password_hash: str


@dataclass(slots=True, frozen=True)
class FFWebhookApplication:
    id: int
    status: str
    approved_params: dict[str, Any] | None


@dataclass(slots=True, frozen=True)
class FFAllowedBank:
    bank_id: int
    provider_product_id: str | None
    bank_code: str
    bank_name: str
    credit_program_id: int


class FFRepository(BaseRepository):
    async def get_provider_by_code(self, code: str) -> FFProvider | None:
        rows = await self.call_sp(
            "public.installment__provider_get",
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

        return FFProvider(
            id=row["id"],
            code=row["code"],
            base_url=row["base_url"],
            config=config,
        )

    async def get_active_credentials(self, provider_id: int, env: str) -> FFCredential | None:
        rows = await self.call_sp(
            "public.installment__credential_get_active",
            provider_id,
            env,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return FFCredential(
            username=row["username"],
            password=row["password"],
        )

    async def get_token(self, provider_id: int) -> FFToken | None:
        rows = await self.call_sp(
            "public.installment__token_get",
            provider_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return FFToken(
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
                "public.installment__token_upsert",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def insert_application(
        self,
        *,
        client_request_id: int,
        provider_id: int,
        product_id: str,
        bank_id: int,
        loan_type: str,
        principal: Decimal,
        period: int,
        created_by: int,
        installment_product_id: int | None = None,
        status: str = "NEW",
    ) -> int:
        payload = {
            "client_request_id": client_request_id,
            "provider_id": provider_id,
            "product_id": product_id,
            "bank_id": bank_id,
            "loan_type": loan_type,
            "principal": str(principal),
            "period": period,
            "created_by": created_by,
            "status": status,
        }
        if installment_product_id is not None:
            payload["installment_product_id"] = installment_product_id
        scalar_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_create",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        if scalar_result is None:
            raise RuntimeError("Failed to insert installment application.")
        return int(scalar_result)

    async def update_application_after_apply(
        self,
        *,
        application_id: int,
        uuid: str,
        redirect_url: str | None,
        status: str,
        reference_id: str,
    ) -> None:
        payload = {
            "application_id": application_id,
            "uuid": uuid,
            "redirect_url": redirect_url,
            "status": status,
            "reference_id": reference_id,
        }
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_update_after_apply",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def mark_apply_failed(self, application_id: int) -> None:
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_mark_apply_failed",
                json.dumps({"application_id": application_id}),
                module_code="MYSPACE",
            )
        )

    async def insert_event_log(
        self,
        *,
        installment_id: int | None,
        event_type: str,
        payload: dict[str, Any],
        source: str,
    ) -> None:
        request_payload: dict[str, Any] = {
            "event_type": event_type,
            "payload": payload,
            "source": source,
        }
        if installment_id is not None:
            request_payload["installment_id"] = installment_id
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__event_log_add",
                json.dumps(request_payload),
                module_code="MYSPACE",
            )
        )

    async def get_applications_by_client_request(
        self, client_request_id: int
    ) -> list[InstallmentApplicationResponse]:
        rows = await self.call_sp(
            "public.installment__applications_list_by_client_request",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        result = []
        for row in rows:
            payload = dict(row)
            if isinstance(payload.get("approved_params"), str):
                payload["approved_params"] = json.loads(payload["approved_params"])
            result.append(InstallmentApplicationResponse.model_validate(payload))
        return result

    async def get_application_by_id(self, application_id: int) -> InstallmentApplicationResponse | None:
        rows = await self.call_sp(
            "public.installment__application_get",
            application_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        payload = dict(rows[0])

        if isinstance(payload.get("approved_params"), str):
            payload["approved_params"] = json.loads(payload["approved_params"])

        return InstallmentApplicationResponse.model_validate(payload)

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
        factoring/ff/repo.py). Several hunters can work the same deal in
        parallel — their combined principal must not exceed the deal amount."""
        row = await self.fetchrow(
            "SELECT public.cr_deal_committed_amount($1::bigint, $2::bigint) AS committed",
            client_request_id,
            exclude_application_id,
        )
        return Decimal(str(row["committed"])) if row is not None else Decimal("0")

    async def client_request_exists(self, client_request_id: int) -> bool:
        rows = await self.call_sp(
            "public.installment__client_request_get_for_apply",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        return bool(rows)

    async def get_provider_webhook_credentials(self, code: str = "FF") -> FFWebhookCredential | None:
        rows = await self.call_sp(
            "public.installment__provider_webhook_credentials_get",
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

        return FFWebhookCredential(username=username, password_hash=password_hash)

    async def get_application_by_reference_or_uuid(
        self,
        reference_id: str | None,
        uuid: str | None,
    ) -> FFWebhookApplication | None:
        if reference_id is None and uuid is None:
            return None

        rows = await self.call_sp(
            "public.installment__application_get_by_reference_or_uuid",
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

        return FFWebhookApplication(
            id=row["id"],
            status=row["status"],
            approved_params=approved_params if isinstance(approved_params, dict) else None,
        )

    async def update_application_from_webhook(
        self,
        *,
        application_id: int,
        status: str,
        approved_params: dict[str, Any] | None,
        uuid: str | None,
        product_id: str | None,
        loan_type: str | None,
        redirect_url: str | None,
    ) -> None:
        payload = {
            "application_id": application_id,
            "status": status,
            "approved_params": approved_params,
            "uuid": uuid,
            "product_id": product_id,
            "loan_type": loan_type,
            "redirect_url": redirect_url,
        }
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_update_from_webhook",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def update_application_from_poll(
        self,
        *,
        application_id: int,
        status: str,
        approved_params: dict[str, Any] | None,
        product_id: str | None,
        loan_type: str | None,
        uuid: str | None = None,
    ) -> None:
        payload = {
            "application_id": application_id,
            "status": status,
            "approved_params": approved_params,
            "product_id": product_id,
            "loan_type": loan_type,
            "uuid": uuid,
        }
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_update_from_poll",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )

    async def get_allowed_banks_for_client_request(self, client_request_id: int) -> list[dict]:
        rows = await self.call_sp(
            "public.installment__allowed_banks_for_client_request",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        return [dict(row) for row in rows]

    async def sync_provider_products(self, *, provider_code: str, products: list[dict]) -> dict:
        payload = {
            "provider_code": provider_code,
            "products": products,
        }
        raw_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__provider_product_sync",
                json.dumps(payload),
                module_code="MYSPACE",
            )
        )
        if raw_result is None:
            raise RuntimeError("Failed to sync provider products.")
        if isinstance(raw_result, str):
            parsed = json.loads(raw_result)
        elif isinstance(raw_result, dict):
            parsed = raw_result
        else:
            raise RuntimeError("Unexpected sync provider products response type.")
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected sync provider products response shape.")
        return parsed

    async def list_provider_products(
        self, provider_code: str, *, current_only: bool = True
    ) -> list[dict]:
        rows = await self.call_sp(
            "public.installment__provider_product_list",
            provider_code,
            current_only,
            cursor=True,
            module_code="MYSPACE",
        )
        return [dict(row) for row in rows]

    async def apply_application_to_deal(
        self,
        *,
        application_id: int,
        created_by: int,
    ) -> int:
        payload = {
            "application_id": application_id,
            "created_by": created_by,
        }
        scalar_result = scalar_from_sp_rows(
            await self.call_sp(
                "public.cr_credit_detail__insert_from_installment",
                json.dumps(payload),
                session_user_id=created_by,
                module_code="MYSPACE",
            )
        )
        if scalar_result is None:
            raise RuntimeError("Failed to apply installment application to deal.")
        return int(scalar_result)
