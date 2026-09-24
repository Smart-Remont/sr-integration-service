import base64
import binascii
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from secrets import compare_digest
from typing import Any

from asyncpg.exceptions import PostgresError
from bcrypt import checkpw
from fastapi import HTTPException, status
from loguru import logger
from src.exceptions import StoredProcedureError
from src.service import BaseService

from ..deal_guard import check_deal_client_request_state
from ..schemas import (
    AllowedBankListResponse,
    AllowedBankResponse,
    ApplyInstallmentApplicationResponse,
    CreateInstallmentApplicationRequest,
    CreateInstallmentApplicationResponse,
    FFProductsResponse,
    InstallmentApplicationResponse,
    ProviderProductListResponse,
    ProviderProductResponse,
    SyncProductsResponse,
    WebhookAckResponse,
)
from .client import FFClient, FFClientError
from .repo import FFProvider, FFRepository, FFWebhookCredential

_PG_RAISE_MESSAGE_RE = re.compile(r"\{([^}]*)\}")
VALID_FF_WEBHOOK_STATUSES = {"REJECTED", "APPROVED", "ALTERNATIVE", "ISSUED"}


class FFService(BaseService):
    def __init__(self, ff_repository: FFRepository, ff_client: FFClient, app_env: str) -> None:
        self.ff_repository = ff_repository
        self.ff_client = ff_client
        self.app_env = app_env

    async def get_products(self) -> FFProductsResponse:
        provider = await self._require_provider()
        partner_id = self._required_config_value(provider, "partner_id")
        logger.info(
            "FF get_products start | base_url={base_url} partner_id={partner_id} channel={channel} resolve_ip={resolve_ip} env={env}",
            base_url=provider.base_url,
            partner_id=partner_id,
            channel=provider.config.get("channel"),
            resolve_ip=self._provider_resolve_ip(provider),
            env=self.app_env,
        )
        access_token = await self._ensure_valid_token(provider=provider)
        ff_kwargs = self._ff_request_kwargs(provider)

        try:
            payload = await self.ff_client.get_partner_info(
                access_token=access_token,
                partner_id=partner_id,
                **ff_kwargs,
            )
        except FFClientError as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                logger.warning("FF get_products got 401, re-authenticating")
                access_token = await self._authenticate_and_store_token(provider)
                payload = await self.ff_client.get_partner_info(
                    access_token=access_token,
                    partner_id=partner_id,
                    **ff_kwargs,
                )
            else:
                logger.error(
                    "FF get_products failed | status={status} detail={detail}",
                    status=exc.status_code,
                    detail=exc.detail,
                )
                raise self._map_ff_error(exc) from exc

        logger.info("FF get_products success | payload_keys={keys}", keys=sorted(payload.keys()))
        return FFProductsResponse.model_validate(payload)

    async def sync_products(self) -> SyncProductsResponse:
        products_response = await self.get_products()
        products_payload = [product.model_dump(mode="json") for product in products_response.products]
        result = await self.ff_repository.sync_provider_products(
            provider_code="FF",
            products=products_payload,
        )
        sync_response = SyncProductsResponse.model_validate(result)
        await self._log_event(
            "SYNC_PRODUCTS",
            source="INSTALLMENT",
            payload={
                "provider_code": "FF",
                "products_fetched": len(products_payload),
                "inserted": sync_response.inserted,
                "unchanged": sync_response.unchanged,
                "ids": sync_response.ids,
            },
        )
        return sync_response

    async def list_provider_products(
        self, provider_code: str, *, current_only: bool = True
    ) -> ProviderProductListResponse:
        rows = await self.ff_repository.list_provider_products(
            provider_code, current_only=current_only
        )
        items = [ProviderProductResponse.model_validate(row) for row in rows]
        return ProviderProductListResponse(items=items, total=len(items))

    async def create_application(
        self,
        request: CreateInstallmentApplicationRequest,
    ) -> CreateInstallmentApplicationResponse:
        if request.provider_code != "FF":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only provider_code=FF is supported.",
            )

        provider = await self._require_provider()
        await self._require_webhook_credentials()
        await self._require_deal_main_client_request(request.client_request_id)

        iin = self._normalize_iin(request.iin)
        phone = self._normalize_phone(request.mobile_phone)
        if iin is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid IIN: expected 12 digits.",
            )
        if phone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid mobile_phone: expected Kazakhstan number (+7...).",
            )
        if request.principal <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Сумма займа должна быть больше 0.",
            )
        await self._require_deal_budget(request.client_request_id, request.principal)

        application_id = await self.ff_repository.insert_application(
            client_request_id=request.client_request_id,
            provider_id=provider.id,
            product_id=request.product_id,
            bank_id=request.bank_id,
            loan_type=request.loan_type,
            principal=request.principal,
            period=request.period,
            created_by=request.created_by,
            status="NEW",
        )
        reference_id = str(application_id)

        init_payload = {
            "client_request_id": request.client_request_id,
            "bank_id": request.bank_id,
            "product_id": request.product_id,
            "loan_type": request.loan_type,
            "principal": str(request.principal),
            "period": request.period,
            "created_by": request.created_by,
            "reference_id": reference_id,
        }
        await self._log_event(
            "APPLICATION_INIT",
            installment_id=application_id,
            source="INSTALLMENT",
            payload=init_payload,
        )

        payload = self._build_apply_payload(
            provider=provider,
            iin=iin,
            phone=phone,
            request=request,
            reference_id=reference_id,
        )
        await self._log_event(
            "FF_APPLY_REQUEST",
            installment_id=application_id,
            source="FF",
            payload=payload,
        )

        try:
            response_payload = await self._apply_with_reauth(provider=provider, payload=payload)
        except HTTPException as exc:
            await self._log_event(
                "FF_APPLY_FAILED",
                installment_id=application_id,
                source="FF",
                payload={
                    "error": self._extract_error_message(exc),
                    "request": payload,
                },
            )
            try:
                await self.ff_repository.mark_apply_failed(application_id)
            except Exception as mark_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to mark APPLY_FAILED | application_id={id} error={error}",
                    id=application_id,
                    error=mark_exc,
                )
            raise

        application_uuid = response_payload.get("uuid")
        if not isinstance(application_uuid, str) or not application_uuid:
            await self._log_event(
                "FF_APPLY_FAILED",
                installment_id=application_id,
                source="FF",
                payload={
                    "error": "Freedom Finance did not return application uuid.",
                    "response": response_payload,
                    "request": payload,
                },
            )
            try:
                await self.ff_repository.mark_apply_failed(application_id)
            except Exception as mark_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to mark APPLY_FAILED | application_id={id} error={error}",
                    id=application_id,
                    error=mark_exc,
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Freedom Finance did not return application uuid.",
            )

        redirect_url_value = response_payload.get("url")
        redirect_url = redirect_url_value if isinstance(redirect_url_value, str) else None

        await self.ff_repository.update_application_after_apply(
            application_id=application_id,
            uuid=application_uuid,
            redirect_url=redirect_url,
            status="IN_PROGRESS",
            reference_id=reference_id,
        )
        await self._log_event(
            "CREATED",
            installment_id=application_id,
            source="FF",
            payload={
                "response": response_payload,
                "request": payload,
                "status": "IN_PROGRESS",
            },
        )

        return CreateInstallmentApplicationResponse(
            id=application_id,
            uuid=application_uuid,
            reference_id=reference_id,
            status="IN_PROGRESS",
            redirect_url=redirect_url,
            provider_code="FF",
        )

    async def get_applications_by_client_request(
        self, client_request_id: int
    ) -> list[InstallmentApplicationResponse]:
        return await self.ff_repository.get_applications_by_client_request(client_request_id)

    async def get_allowed_banks_for_client_request(
        self, client_request_id: int
    ) -> AllowedBankListResponse:
        await self._require_deal_main_client_request(client_request_id)
        rows = await self.ff_repository.get_allowed_banks_for_client_request(client_request_id)
        items = [AllowedBankResponse.model_validate(row) for row in rows]
        await self._log_event(
            "ALLOWED_BANKS_QUERY",
            source="INSTALLMENT",
            payload={
                "client_request_id": client_request_id,
                "total": len(items),
                "bank_ids": [item.bank_id for item in items],
            },
        )
        return AllowedBankListResponse(items=items, total=len(items))

    async def get_application_by_id(self, application_id: int) -> InstallmentApplicationResponse:
        application = await self.ff_repository.get_application_by_id(application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application id={application_id} was not found.",
            )
        return application

    async def apply_application_to_deal(
        self,
        application_id: int,
        *,
        created_by: int,
    ) -> ApplyInstallmentApplicationResponse:
        application = await self.ff_repository.get_application_by_id(application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application id={application_id} was not found.",
            )

        if (
            application.client_request_credit_detail_id is None
            and application.status != "ISSUED"
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Заявку можно применить к сделке только после выдачи (ISSUED)",
            )

        await self._log_event(
            "MANUAL_APPLY_STARTED",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={
                "created_by": created_by,
                "status": application.status,
                "client_request_id": application.client_request_id,
            },
        )

        try:
            credit_detail_id = await self.ff_repository.apply_application_to_deal(
                application_id=application_id,
                created_by=created_by,
            )
        except HTTPException:
            raise
        except StoredProcedureError as exc:
            await self._log_event(
                "MANUAL_APPLY_FAILED",
                installment_id=application_id,
                source="INSTALLMENT",
                payload={
                    "created_by": created_by,
                    "error": exc.message,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.message,
            ) from exc
        except Exception as exc:
            detail = self._extract_error_message(exc)
            await self._log_event(
                "MANUAL_APPLY_FAILED",
                installment_id=application_id,
                source="INSTALLMENT",
                payload={
                    "created_by": created_by,
                    "error": detail,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            ) from exc

        await self._log_event(
            "MANUAL_APPLY",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={
                "created_by": created_by,
                "client_request_credit_detail_id": credit_detail_id,
            },
        )
        return ApplyInstallmentApplicationResponse(
            application_id=application_id,
            client_request_credit_detail_id=credit_detail_id,
        )

    async def poll_application(self, application_id: int) -> InstallmentApplicationResponse:
        application = await self.ff_repository.get_application_by_id(application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application id={application_id} was not found.",
            )
        if application.uuid is None or not application.uuid.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Application does not have FF uuid yet.",
            )

        provider = await self._require_provider()
        await self._log_event(
            "POLL_STARTED",
            installment_id=application_id,
            source="FF",
            payload={
                "uuid": application.uuid,
                "status_before": application.status,
            },
        )

        try:
            response_payload = await self._poll_with_reauth(provider=provider, application_uuid=application.uuid)
        except HTTPException as exc:
            await self._log_event(
                "POLL_FAILED",
                installment_id=application_id,
                source="FF",
                payload={
                    "uuid": application.uuid,
                    "error": self._extract_error_message(exc),
                },
            )
            raise

        status_value = self._keep_terminal_poll_status(
            application.status,
            self._extract_poll_status(response_payload),
        )
        approved_params = self._extract_poll_approved_params(response_payload)
        product_id = self._extract_string(response_payload, "product")
        loan_type = self._extract_string(response_payload, "loan_type")
        payload_uuid = self._extract_string(response_payload, "uuid")

        await self.ff_repository.update_application_from_poll(
            application_id=application_id,
            status=status_value,
            approved_params=approved_params,
            product_id=product_id,
            loan_type=loan_type,
            uuid=payload_uuid,
        )
        await self._log_event(
            "STATUS_UPDATED",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={
                "source": "poll",
                "status_before": application.status,
                "status_after": status_value,
                "approved_params": approved_params,
            },
        )
        await self._log_event(
            "STATUS_POLL",
            installment_id=application_id,
            source="FF",
            payload={
                "response": response_payload,
                "status_before": application.status,
                "status_after": status_value,
            },
        )
        await self._try_auto_apply_on_issued(application_id)
        updated_application = await self.ff_repository.get_application_by_id(application_id)
        if updated_application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application id={application_id} was not found after poll update.",
            )
        return updated_application

    async def handle_webhook(
        self,
        payload: dict[str, Any],
        *,
        authorization_header: str | None,
    ) -> WebhookAckResponse:
        try:
            await self._verify_webhook_auth(authorization_header)
        except HTTPException as exc:
            await self._log_event(
                "WEBHOOK_AUTH_FAILED",
                source="FF",
                payload={
                    "error": self._extract_error_message(exc),
                    "hook": payload,
                },
            )
            raise

        status_value = self._extract_status(payload)
        uuid = self._extract_string(payload, "uuid")
        reference_id = self._extract_string(payload, "reference_id") or self._extract_string(
            payload,
            "lead_id",
        )
        approved_params = self._build_approved_params(payload)
        loan_type = self._extract_string(payload, "loan_type")
        product_id = self._extract_string(payload, "product")
        redirect_url = self._extract_string(payload, "redirect_url")

        application = await self.ff_repository.get_application_by_reference_or_uuid(
            reference_id=reference_id,
            uuid=uuid,
        )
        if application is not None:
            owned = await self.ff_repository.get_application_by_id(application.id)
            if owned is None:
                application = None
        if application is None:
            await self._log_event(
                "WEBHOOK_ORPHAN",
                source="FF",
                payload={
                    "reference_id": reference_id,
                    "uuid": uuid,
                    "status": status_value,
                    "hook": payload,
                },
            )
            return WebhookAckResponse(ok=True, status=True)

        await self._log_event(
            "HOOK_RECEIVED",
            installment_id=application.id,
            source="FF",
            payload={
                "hook": payload,
                "status_before": application.status,
                "status_after": status_value,
            },
        )

        status_value = self._keep_terminal_poll_status(application.status, status_value)

        if application.status == status_value and application.approved_params == approved_params:
            await self._log_event(
                "HOOK_DUPLICATE",
                installment_id=application.id,
                source="FF",
                payload={
                    "reason": "no_status_change",
                    "hook": payload,
                    "status": status_value,
                },
            )
            if status_value == "ISSUED":
                await self._try_auto_apply_on_issued(application.id)
            return WebhookAckResponse(ok=True, status=True)

        try:
            await self.ff_repository.update_application_from_webhook(
                application_id=application.id,
                status=status_value,
                approved_params=approved_params,
                uuid=uuid,
                product_id=product_id,
                loan_type=loan_type,
                redirect_url=redirect_url,
            )
            await self._log_event(
                "STATUS_UPDATED",
                installment_id=application.id,
                source="INSTALLMENT",
                payload={
                    "source": "webhook",
                    "status_before": application.status,
                    "status_after": status_value,
                    "approved_params": approved_params,
                },
            )
            if status_value == "ISSUED":
                await self._try_auto_apply_on_issued(application.id)
        except Exception as exc:
            await self._log_event(
                "ERROR",
                installment_id=application.id,
                source="FF",
                payload={
                    "error": self._extract_error_message(exc),
                    "hook_payload": payload,
                },
            )
            raise

        return WebhookAckResponse(ok=True, status=True)

    async def _apply_with_reauth(self, provider: FFProvider, payload: dict[str, Any]) -> dict[str, Any]:
        access_token = await self._ensure_valid_token(provider=provider)
        ff_kwargs = self._ff_request_kwargs(provider)
        try:
            return await self.ff_client.apply_goods_loan_lead(
                access_token=access_token,
                payload=payload,
                **ff_kwargs,
            )
        except FFClientError as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                refreshed_token = await self._authenticate_and_store_token(provider=provider)
                try:
                    return await self.ff_client.apply_goods_loan_lead(
                        access_token=refreshed_token,
                        payload=payload,
                        **ff_kwargs,
                    )
                except FFClientError as retry_exc:
                    raise self._map_ff_error(retry_exc) from retry_exc
            raise self._map_ff_error(exc) from exc

    async def _poll_with_reauth(self, provider: FFProvider, application_uuid: str) -> dict[str, Any]:
        access_token = await self._ensure_valid_token(provider=provider)
        ff_kwargs = self._ff_request_kwargs(provider)
        try:
            return await self.ff_client.get_goods_application_info(
                access_token=access_token,
                uuid=application_uuid,
                **ff_kwargs,
            )
        except FFClientError as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                refreshed_token = await self._authenticate_and_store_token(provider=provider)
                try:
                    return await self.ff_client.get_goods_application_info(
                        access_token=refreshed_token,
                        uuid=application_uuid,
                        **ff_kwargs,
                    )
                except FFClientError as retry_exc:
                    raise self._map_ff_error(retry_exc) from retry_exc
            raise self._map_ff_error(exc) from exc

    async def _require_provider(self) -> FFProvider:
        provider = await self.ff_repository.get_provider_by_code("FF")
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider FF is not configured.",
            )
        return provider

    async def _ensure_valid_token(self, provider: FFProvider) -> str:
        token = await self.ff_repository.get_token(provider.id)
        now = datetime.now(UTC)
        if token is not None and token.expires_at > now + timedelta(seconds=30):
            return token.access_token
        return await self._authenticate_and_store_token(provider)

    async def _authenticate_and_store_token(self, provider: FFProvider) -> str:
        credentials = await self.ff_repository.get_active_credentials(provider.id, self.app_env)
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Active FF credentials were not found for env={self.app_env}.",
            )

        logger.info(
            "FF authenticate start | base_url={base_url} username={username} resolve_ip={resolve_ip} env={env}",
            base_url=provider.base_url,
            username=credentials.username,
            resolve_ip=self._provider_resolve_ip(provider),
            env=self.app_env,
        )
        try:
            access_token, refresh_token = await self.ff_client.authenticate(
                username=credentials.username,
                password=credentials.password,
                **self._ff_request_kwargs(provider),
            )
        except FFClientError as exc:
            logger.error(
                "FF authenticate failed | status={status} detail={detail}",
                status=exc.status_code,
                detail=exc.detail,
            )
            raise self._map_ff_error(exc) from exc

        expires_at = datetime.now(UTC) + timedelta(minutes=55)
        await self.ff_repository.upsert_token(
            provider_id=provider.id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        return access_token

    async def _require_deal_main_client_request(self, client_request_id: int) -> None:
        state = await self.ff_repository.get_deal_client_request_state(client_request_id)
        check_deal_client_request_state(state, client_request_id)

    async def _require_deal_budget(
        self,
        client_request_id: int,
        principal: Decimal,
        *,
        exclude_application_id: int | None = None,
    ) -> None:
        """Несколько сотрудников могут параллельно оформлять рассрочку и/или
        факторинг на одну сделку — сумма ВСЕХ их активных заявок не должна
        превышать сумму сделки. Дешёвая проверка до вызова FF; финальный
        источник правды — SQL guard в installment__application_create /
        factoring__application_create (public.cr_deal_committed_amount)."""
        deal_total = await self.ff_repository.get_deal_total_amount(client_request_id)
        if deal_total is None or deal_total <= 0:
            return
        committed = await self.ff_repository.get_deal_committed_amount(
            client_request_id, exclude_application_id=exclude_application_id
        )
        if committed + principal > deal_total:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Сумма новой заявки {principal:,.0f} ₸ с учётом уже оформленных "
                    f"заявок по сделке ({committed:,.0f} ₸) превышает сумму сделки "
                    f"{deal_total:,.0f} ₸."
                ).replace(",", " "),
            )

    @staticmethod
    def _required_config_value(provider: FFProvider, key: str) -> str:
        value = provider.config.get(key)
        if isinstance(value, str) and value.strip():
            return value
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"FF provider config.{key} is missing.",
        )

    @staticmethod
    def _provider_resolve_ip(provider: FFProvider) -> str | None:
        value = provider.config.get("resolve_ip")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def _ff_request_kwargs(cls, provider: FFProvider) -> dict[str, str | None]:
        return {
            "base_url": provider.base_url,
            "resolve_ip": cls._provider_resolve_ip(provider),
        }

    def _build_apply_payload(
        self,
        *,
        provider: FFProvider,
        iin: str,
        phone: str,
        request: CreateInstallmentApplicationRequest,
        reference_id: str,
    ) -> dict[str, Any]:
        return {
            "iin": iin,
            "mobile_phone": phone,
            "product": request.product_id,
            "partner": self._required_config_value(provider, "partner_id"),
            "channel": self._required_config_value(provider, "channel"),
            "credit_params": {
                "period": request.period,
                "principal": float(request.principal),
                "repayment_method": request.repayment_method,
            },
            "reference_id": reference_id,
            "credit_configs": {
                "with_insurance": False,
                "is_knox": False,
            },
            "additional_information": {
                "hook_url": self._required_config_value(provider, "hook_url"),
                "failure_url": self._required_config_value(provider, "failure_url"),
                "success_url": self._required_config_value(provider, "success_url"),
            },
        }

    @staticmethod
    def _normalize_iin(iin: str | None) -> str | None:
        if iin is None:
            return None
        normalized = "".join(symbol for symbol in iin if symbol.isdigit())
        if len(normalized) != 12:
            return None
        return normalized

    @staticmethod
    def _normalize_phone(raw_phone: str | None) -> str | None:
        if raw_phone is None:
            return None
        digits = "".join(symbol for symbol in raw_phone if symbol.isdigit())
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        if len(digits) != 11 or not digits.startswith("7"):
            return None
        return f"+{digits}"

    @staticmethod
    def _map_ff_error(exc: FFClientError) -> HTTPException:
        detail = exc.detail.strip() or "Freedom Finance request failed."
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            lowered = detail.lower()
            if "временно недоступ" in lowered:
                return HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Freedom Finance service is temporarily unavailable.",
                )
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            )
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Freedom Finance authentication failed.",
            )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )

    async def _require_webhook_credentials(self) -> FFWebhookCredential:
        credentials = await self.ff_repository.get_provider_webhook_credentials(code="FF")
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Webhook credentials for FF are not configured. "
                    "Cannot submit the application until webhook_username/password are set."
                ),
            )
        return credentials

    async def _verify_webhook_auth(self, authorization_header: str | None) -> None:
        credentials = await self.ff_repository.get_provider_webhook_credentials(code="FF")
        if credentials is None:
            raise self._invalid_webhook_credentials_exception()

        username, password = self._parse_basic_authorization_header(authorization_header)
        if username is None or password is None:
            raise self._invalid_webhook_credentials_exception()

        username_ok = compare_digest(username.encode(), credentials.username.encode())
        password_ok = self._verify_webhook_password(password, credentials)
        if not (username_ok and password_ok):
            raise self._invalid_webhook_credentials_exception()

    @staticmethod
    def _extract_string(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        return None

    @staticmethod
    def _extract_status(payload: dict[str, Any]) -> str:
        raw_status = payload.get("status")
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Webhook payload must include status.",
            )

        status_value = raw_status.strip().upper()
        if status_value not in VALID_FF_WEBHOOK_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported status: {status_value}",
            )
        return status_value

    @staticmethod
    def _extract_poll_status(payload: dict[str, Any]) -> str:
        raw_status = payload.get("status")
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Poll response must include status.",
            )
        return raw_status.strip().upper()

    @staticmethod
    def _keep_terminal_poll_status(current: str | None, polled: str) -> str:
        """Do not overwrite terminal ISSUED/REJECTED with a different poll/webhook status."""
        if current in {"ISSUED", "REJECTED"} and polled != current:
            return current
        return polled

    @staticmethod
    def _extract_poll_approved_params(payload: dict[str, Any]) -> dict[str, Any] | None:
        result: dict[str, Any] = {}

        credit_params = payload.get("credit_params")
        if isinstance(credit_params, dict):
            result.update(credit_params)

        for key in (
            "status_reason",
            "credit_contract",
            "iin",
            "mobile_phone",
            "channel",
            "first_name",
            "last_name",
            "middle_name",
            "reference_id",
        ):
            value = payload.get(key)
            if value is not None and str(value).strip():
                result[key] = value

        return result or None

    def _build_approved_params(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        source = payload.get("approved_params")
        approved_params = dict(source) if isinstance(source, dict) else {}
        alternative_reason = self._extract_string(payload, "alternative_reason")
        if alternative_reason is not None:
            approved_params["alternative_reason"] = alternative_reason
        status_reason = self._extract_string(payload, "status_reason")
        if status_reason is not None:
            approved_params["status_reason"] = status_reason
        return approved_params or None

    @staticmethod
    def _parse_basic_authorization_header(header_value: str | None) -> tuple[str | None, str | None]:
        if header_value is None or not header_value.startswith("Basic "):
            return None, None

        token = header_value[6:].strip()
        if not token:
            return None, None

        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None, None

        if ":" not in decoded:
            return None, None
        username, password = decoded.split(":", 1)
        return username, password

    @staticmethod
    def _verify_webhook_password(password: str, credentials: FFWebhookCredential) -> bool:
        try:
            return checkpw(password.encode(), credentials.password_hash.encode())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid webhook password hash configuration.",
            ) from exc

    async def _try_auto_apply_on_issued(self, application_id: int) -> None:
        application = await self.ff_repository.get_application_by_id(application_id)
        if application is None:
            return
        if application.status != "ISSUED":
            return
        if application.client_request_credit_detail_id is not None:
            await self._log_event(
                "AUTO_APPLY_SKIPPED",
                installment_id=application_id,
                source="INSTALLMENT",
                payload={
                    "reason": "already_applied",
                    "client_request_credit_detail_id": application.client_request_credit_detail_id,
                },
            )
            return
        if application.created_by is None:
            logger.warning(
                "Auto-apply skipped | application_id={application_id} reason=created_by missing",
                application_id=application_id,
            )
            await self._log_event(
                "AUTO_APPLY_FAILED",
                installment_id=application_id,
                source="INSTALLMENT",
                payload={"error": "created_by is missing on application"},
            )
            return

        await self._log_event(
            "AUTO_APPLY_STARTED",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={"created_by": application.created_by},
        )

        try:
            credit_detail_id = await self.ff_repository.apply_application_to_deal(
                application_id=application_id,
                created_by=application.created_by,
            )
        except Exception as exc:
            logger.warning(
                "Auto-apply failed | application_id={application_id} error={error}",
                application_id=application_id,
                error=self._extract_error_message(exc),
            )
            await self._log_event(
                "AUTO_APPLY_FAILED",
                installment_id=application_id,
                source="INSTALLMENT",
                payload={"error": self._extract_error_message(exc)},
            )
            return

        await self._log_event(
            "AUTO_APPLY",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={"client_request_credit_detail_id": credit_detail_id},
        )

    async def _log_event(
        self,
        event_type: str,
        *,
        source: str,
        installment_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.ff_repository.insert_event_log(
                installment_id=installment_id,
                event_type=event_type,
                payload=self._redact_pii(payload or {}),
                source=source,
            )
        except Exception as exc:
            logger.warning(
                "Event log write failed | event_type={event_type} installment_id={installment_id} error={error}",
                event_type=event_type,
                installment_id=installment_id,
                error=str(exc),
            )

    @staticmethod
    def _extract_error_message(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        cause: BaseException | None = exc
        while cause is not None:
            if isinstance(cause, PostgresError):
                text = str(cause)
                match = _PG_RAISE_MESSAGE_RE.search(text)
                return match.group(1) if match else text
            cause = cause.__cause__
        return str(exc)

    @staticmethod
    def _redact_pii(value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                lowered = key.lower()
                if (
                    lowered in {
                        "iin",
                        "mobile_phone",
                        "phone",
                        "prop_iin",
                        "prop_phone",
                        "first_name",
                        "last_name",
                        "middle_name",
                        "fio",
                    }
                    or lowered.endswith("_iin")
                    or "phone" in lowered
                ):
                    redacted[key] = "***"
                else:
                    redacted[key] = FFService._redact_pii(item)
            return redacted
        if isinstance(value, list):
            return [FFService._redact_pii(item) for item in value]
        return value

    @staticmethod
    def _invalid_webhook_credentials_exception() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
