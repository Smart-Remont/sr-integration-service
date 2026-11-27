import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.repository import BaseRepository, scalar_from_sp_rows
from src.shared.advisory_lock import advisory_lock_key
from src.shared.deal_guard import fetch_deal_client_request_state

from .schemas import InstallmentApplicationResponse


@dataclass(slots=True, frozen=True)
class InstallmentProvider:
    id: int
    code: str
    base_url: str
    config: dict[str, Any]


@dataclass(slots=True, frozen=True)
class ProviderCredential:
    username: str
    password: str


@dataclass(slots=True, frozen=True)
class ProviderToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(slots=True, frozen=True)
class WebhookCredential:
    username: str
    password_hash: str


@dataclass(slots=True, frozen=True)
class WebhookApplication:
    id: int
    status: str
    approved_params: dict[str, Any] | None


@dataclass(slots=True, frozen=True)
class ApplicationRef:
    id: int
    status: str


@dataclass(slots=True, frozen=True)
class ApplicationBorrower:
    iin: str | None
    mobile_phone: str | None
    bank_id: int | None


@dataclass(slots=True, frozen=True)
class AllowedBank:
    bank_id: int
    provider_product_id: str | None
    bank_code: str
    bank_name: str
    credit_program_id: int


class InstallmentRepository(BaseRepository):
    async def get_provider_by_code(self, code: str) -> InstallmentProvider | None:
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

        return InstallmentProvider(
            id=row["id"],
            code=row["code"],
            base_url=row["base_url"],
            config=config,
        )

    async def get_active_credentials(self, provider_id: int, env: str) -> ProviderCredential | None:
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
        return ProviderCredential(
            username=row["username"],
            password=row["password"],
        )

    async def get_token(self, provider_id: int) -> ProviderToken | None:
        rows = await self.call_sp(
            "public.installment__token_get",
            provider_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return ProviderToken(
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
        iin: str,
        mobile_phone: str,
        installment_product_id: int | None = None,
        status: str = "NEW",
        initial_fee: Decimal | None = None,
        payment_day: int | None = None,
        expires_at: datetime | None = None,
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
            "iin": iin,
            "mobile_phone": mobile_phone,
            "status": status,
        }
        if installment_product_id is not None:
            payload["installment_product_id"] = installment_product_id
        if initial_fee is not None:
            payload["initial_fee"] = str(initial_fee)
        if payment_day is not None:
            payload["payment_day"] = payment_day
        if expires_at is not None:
            payload["expires_at"] = expires_at.isoformat()
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

    async def mark_apply_unknown(self, application_id: int) -> None:
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_mark_apply_unknown",
                json.dumps({"application_id": application_id}),
                module_code="MYSPACE",
            )
        )

    async def request_refund(
        self,
        *,
        application_id: int,
        refund_type: str,
        refund_amount: Decimal | None,
        requested_by: int,
    ) -> dict[str, Any]:
        payload = {
            "application_id": application_id,
            "refund_type": refund_type,
            "refund_amount": str(refund_amount) if refund_amount is not None else None,
            "requested_by": requested_by,
        }
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__application_refund_request",
                    json.dumps(payload),
                    session_user_id=requested_by,
                    module_code="MYSPACE",
                )
            )
        )

    async def clear_refund(self, application_id: int) -> None:
        scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__application_refund_clear",
                json.dumps({"application_id": application_id}),
                module_code="MYSPACE",
            )
        )

    async def refund_result(
        self,
        *,
        covlir_status: str,
        application_id: int | None = None,
        uuid: str | None = None,
        refund_amount: Decimal | None = None,
    ) -> dict[str, Any]:
        payload = {
            "application_id": application_id,
            "uuid": uuid,
            "covlir_status": covlir_status,
            "refund_amount": str(refund_amount) if refund_amount is not None else None,
        }
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__application_refund_result",
                    json.dumps(payload),
                    module_code="MYSPACE",
                )
            )
        )

    @staticmethod
    def _json_result(raw: Any) -> dict[str, Any]:
        if isinstance(raw, str):
            # Decimal, not float: money keeps its digits (900000.00, not 900000.0).
            raw = json.loads(raw, parse_float=Decimal)
        if not isinstance(raw, dict):
            raise RuntimeError("Unexpected installment function result.")
        return raw

    async def list_for_reconcile(self, *, stale_minutes: int, limit: int) -> list[dict[str, Any]]:
        rows = await self.call_sp(
            "public.installment__applications_for_reconcile",
            stale_minutes,
            limit,
            cursor=True,
            module_code="MYSPACE",
        )
        return [dict(row) for row in rows]

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

    async def get_application_by_id(
        self, application_id: int
    ) -> InstallmentApplicationResponse | None:
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
        """Sum of active/issued principal of all applications of this deal
        (public.cr_deal_committed_amount). Several hunters can work the same deal in
        parallel — their combined principal must not exceed the deal amount."""
        row = await self.fetchrow(
            "SELECT public.cr_deal_committed_amount($1::bigint, $2::bigint) AS committed",
            client_request_id,
            exclude_application_id,
        )
        return Decimal(str(row["committed"])) if row is not None else Decimal("0")

    async def get_deal_client_request_state(self, client_request_id: int) -> dict[str, Any] | None:
        return await fetch_deal_client_request_state(self, client_request_id)

    async def get_provider_webhook_credentials(self, code: str = "FF") -> WebhookCredential | None:
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

        return WebhookCredential(username=username, password_hash=password_hash)

    async def get_application_by_reference_or_uuid(
        self,
        reference_id: str | None,
        uuid: str | None,
    ) -> WebhookApplication | None:
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

        return WebhookApplication(
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
        credit_contract: str | None = None,
        issued_at: str | None = None,
    ) -> None:
        payload = {
            "application_id": application_id,
            "status": status,
            "approved_params": approved_params,
            "uuid": uuid,
            "product_id": product_id,
            "loan_type": loan_type,
            "redirect_url": redirect_url,
            "credit_contract": credit_contract,
            "issued_at": issued_at,
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
        credit_contract: str | None = None,
    ) -> None:
        payload = {
            "application_id": application_id,
            "status": status,
            "approved_params": approved_params,
            "product_id": product_id,
            "loan_type": loan_type,
            "uuid": uuid,
            "credit_contract": credit_contract,
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

    async def sync_online_banks(self, *, provider_code: str) -> dict[str, Any]:
        """One online bank_tab row per current catalog row (installment__online_banks_sync)."""
        raw = scalar_from_sp_rows(
            await self.call_sp(
                "public.installment__online_banks_sync",
                provider_code,
                module_code="MYSPACE",
            )
        )
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise RuntimeError("Unexpected online banks sync result.")
        return raw

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

    # --- BI Finance ----------------------------------------------------------------

    async def get_deal_seller(self, client_request_id: int) -> dict[str, Any] | None:
        """Legal entity of the deal's company (initiator_company_tab): BI Finance pays it."""
        rows = await self.call_sp(
            "public.installment__client_request_get_for_apply",
            client_request_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "company_id": row.get("company_id"),
            "bin": row.get("seller_bin"),
            "name": row.get("seller_name"),
            "iban": row.get("seller_iban"),
        }

    async def find_application_by_uuid(
        self, provider_code: str, uuid: str
    ) -> ApplicationRef | None:
        rows = await self.call_sp(
            "public.installment__application_find_by_uuid",
            provider_code,
            uuid,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        return ApplicationRef(id=int(rows[0]["id"]), status=str(rows[0]["status"]))

    async def count_applications_awaiting_bank_number(self, provider_code: str) -> int:
        return int(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__applications_awaiting_bank_number",
                    provider_code,
                    module_code="MYSPACE",
                )
            )
            or 0
        )

    async def get_application_borrower(self, application_id: int) -> ApplicationBorrower | None:
        rows = await self.call_sp(
            "public.installment__application_borrower_get",
            application_id,
            cursor=True,
            module_code="MYSPACE",
        )
        if not rows:
            return None
        row = rows[0]
        return ApplicationBorrower(
            iin=row["iin"], mobile_phone=row["mobile_phone"], bank_id=row["bank_id"]
        )

    async def lock_token_renewal(self, provider_id: int) -> None:
        """Until the end of the transaction: one worker renews the token, the others wait and
        then take the new one (the gateway answers 429 to frequent logins)."""
        await self.call_sp(
            "pg_catalog.pg_advisory_xact_lock",
            advisory_lock_key(f"installment-token:{provider_id}"),
            module_code="MYSPACE",
        )

    async def update_application_from_bif(
        self,
        *,
        application_id: int,
        status: str,
        approved_params: dict[str, Any] | None,
        comment: str | None,
    ) -> dict[str, Any]:
        payload = {
            "application_id": application_id,
            "status": status,
            "approved_params": approved_params,
            "comment": comment,
        }
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__application_update_from_bif",
                    json.dumps(payload),
                    module_code="MYSPACE",
                )
            )
        )

    async def expire_applications(self, *, limit: int) -> dict[str, Any]:
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__applications_expire", limit, module_code="MYSPACE"
                )
            )
        )

    async def refund_manual(
        self,
        *,
        application_id: int,
        amount: Decimal,
        comment: str,
        created_by: int,
        refunded_at: date | None,
    ) -> dict[str, Any]:
        payload = {
            "application_id": application_id,
            "amount": str(amount),
            "comment": comment,
            "created_by": created_by,
            "refunded_at": refunded_at.isoformat() if refunded_at is not None else None,
        }
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__application_refund_manual",
                    json.dumps(payload),
                    session_user_id=created_by,
                    module_code="MYSPACE",
                )
            )
        )

    # --- MySpace «Рассрочки» --------------------------------------------------------

    async def list_applications_for_oa(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await self.call_sp(
            "public.installment__oa_applications_list",
            json.dumps(filters),
            cursor=True,
            module_code="MYSPACE",
        )
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("approved_params"), str):
                item["approved_params"] = json.loads(item["approved_params"])
            result.append(item)
        return result

    async def get_oa_stats(self, filters: dict[str, Any]) -> dict[str, Any]:
        return self._json_result(
            scalar_from_sp_rows(
                await self.call_sp(
                    "public.installment__oa_stats", json.dumps(filters), module_code="MYSPACE"
                )
            )
        )
