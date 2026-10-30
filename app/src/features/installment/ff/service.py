import base64
import binascii
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from secrets import compare_digest
from typing import Any

import segno
from asyncpg.exceptions import PostgresError
from bcrypt import checkpw
from fastapi import HTTPException, status
from loguru import logger

from src.exceptions import StoredProcedureError
from src.shared.deal_guard import check_deal_client_request_state

from ..schemas import (
    AllowedBankListResponse,
    AllowedBankResponse,
    ApplyInstallmentApplicationResponse,
    CreateInstallmentApplicationRequest,
    CreateInstallmentApplicationResponse,
    CreateInstallmentRefundRequest,
    FFProductsResponse,
    InstallmentApplicationResponse,
    InstallmentRefundResponse,
    ProviderProductListResponse,
    ProviderProductResponse,
    ReconcileResponse,
    SyncProductsResponse,
    WebhookAckResponse,
)
from .client import FFClient, FFClientError
from .repo import FFProvider, FFRepository, FFWebhookCredential

_PG_RAISE_MESSAGE_RE = re.compile(r"\{([^}]*)\}")

# Statuses of a goods-loan application the bank reports in hooks and poll
# (FF docs: hooks — REJECTED/APPROVED/ALTERNATIVE/ISSUED, poll — the rest).
FF_APPLICATION_STATUSES = frozenset(
    {
        "NEW",
        "IN_PROGRESS",
        "APPROVED",
        "ALTERNATIVE",
        "ON_SIGNING",
        "STATEMENT",
        "HOLD_BEFORE_ISSUED",
        "READY_FOR_ISSUE",
        "ISSUED",
        "REJECTED",
    }
)
# REVERSED — loan fully refunded to the bank (installment__application_refund_result).
TERMINAL_STATUSES = frozenset({"ISSUED", "REJECTED", "REVERSED"})
# Poll statuses of a refund on the bank side; our own status stays ISSUED/REVERSED,
# the refund lives in refund_status.
FF_REFUND_STATUSES = frozenset({"PROCESSING_REFUND", "REFUND", "PARTIAL_REFUND"})
# The bank moves an application to PROCESSING_REFUND before it answers 200; if a
# REQUESTED refund still shows ISSUED after this long, the bank did not take it.
REFUND_DECLINE_AFTER = timedelta(minutes=10)

# Top-level fields of a hook / poll answer kept next to the loan params:
# the borrower's name and IIN go into the deal, the contract number into
# reconciliation with the bank.
_BANK_IDENTITY_FIELDS = (
    "credit_contract",
    "iin",
    "mobile_phone",
    "first_name",
    "last_name",
    "middle_name",
    "channel",
    "status_reason",
    "alternative_reason",
)

REFUND_UNKNOWN_DETAIL = (
    "Банк не ответил на запрос возврата вовремя. Запрос мог дойти: итог придёт от банка "
    "или подтянется сверкой. Повторно не отправляйте."
)

APPLY_UNKNOWN_DETAIL = (
    "Банк не ответил на подачу заявки вовремя. Заявка могла уйти в банк: "
    "статус уточнится сам в течение пары часов. Повторно не отправляйте."
)

TOKEN_FALLBACK_TTL = timedelta(minutes=55)


class FFService:
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
        ff_kwargs = self._ff_request_kwargs(provider)

        try:
            payload = await self._call_with_reauth(
                provider,
                lambda token: self.ff_client.get_partner_info(
                    access_token=token,
                    partner_id=partner_id,
                    **ff_kwargs,
                ),
            )
        except FFClientError as exc:
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
        products_payload = [
            product.model_dump(mode="json") for product in products_response.products
        ]
        result = await self.ff_repository.sync_provider_products(
            provider_code="FF",
            products=products_payload,
        )
        # Each catalog row (program × term) is an online bank the CRM offers.
        banks = await self.ff_repository.sync_online_banks(provider_code="FF")
        sync_response = SyncProductsResponse.model_validate({**result, **banks})
        await self._log_event(
            "SYNC_PRODUCTS",
            source="INSTALLMENT",
            payload={
                "provider_code": "FF",
                "products_fetched": len(products_payload),
                "inserted": sync_response.inserted,
                "unchanged": sync_response.unchanged,
                "relinked": sync_response.relinked,
                "orphaned_bank_ids": sync_response.orphaned_bank_ids,
                "ids": sync_response.ids,
                "banks_created": sync_response.banks_created,
                "banks_updated": sync_response.banks_updated,
                "banks_deactivated": sync_response.banks_deactivated,
            },
        )
        if sync_response.orphaned_bank_ids:
            logger.warning(
                "FF sync: banks lost their product | bank_ids={ids}",
                ids=sync_response.orphaned_bank_ids,
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

        # Committed before the bank call: see FFRepository.committed.
        application_id = await self.ff_repository.committed(
            lambda repo: repo.insert_application(
                client_request_id=request.client_request_id,
                provider_id=provider.id,
                product_id=request.product_id,
                bank_id=request.bank_id,
                loan_type=request.loan_type,
                principal=request.principal,
                period=request.period,
                created_by=request.created_by,
                iin=iin,
                mobile_phone=phone,
                status="NEW",
            )
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

        ff_kwargs = self._ff_request_kwargs(provider)
        try:
            response_payload = await self._call_with_reauth(
                provider,
                lambda token: self.ff_client.apply_goods_loan_lead(
                    access_token=token,
                    payload=payload,
                    **ff_kwargs,
                ),
            )
        except FFClientError as exc:
            if exc.maybe_sent:
                await self._mark_apply_unknown(application_id, payload, error=exc.detail)
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=APPLY_UNKNOWN_DETAIL,
                ) from exc
            mapped = self._map_ff_error(exc)
            await self._mark_apply_failed(application_id, payload, error=str(mapped.detail))
            raise mapped from exc
        except HTTPException as exc:
            # Login to the bank failed: the application itself never left us.
            await self._mark_apply_failed(
                application_id, payload, error=self._extract_error_message(exc)
            )
            raise

        application_uuid = response_payload.get("uuid")
        if not isinstance(application_uuid, str) or not application_uuid:
            # The bank answered 200 but without uuid: it may still have created
            # the lead, and its hooks will find it by reference_id.
            await self._mark_apply_unknown(
                application_id,
                payload,
                error="Freedom Finance did not return application uuid.",
                response=response_payload,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=APPLY_UNKNOWN_DETAIL,
            )

        redirect_url_value = response_payload.get("url")
        redirect_url = redirect_url_value if isinstance(redirect_url_value, str) else None

        await self.ff_repository.committed(
            lambda repo: repo.update_application_after_apply(
                application_id=application_id,
                uuid=application_uuid,
                redirect_url=redirect_url,
                status="IN_PROGRESS",
                reference_id=reference_id,
            )
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
            redirect_qr=self._qr_data_uri(redirect_url),
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

        if application.client_request_credit_detail_id is None and application.status != "ISSUED":
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

        ff_kwargs = self._ff_request_kwargs(provider)
        application_uuid = application.uuid
        try:
            response_payload = await self._call_with_reauth(
                provider,
                lambda token: self.ff_client.get_goods_application_info(
                    access_token=token,
                    uuid=application_uuid,
                    **ff_kwargs,
                ),
            )
        except (FFClientError, HTTPException) as exc:
            mapped = self._map_ff_error(exc) if isinstance(exc, FFClientError) else exc
            await self._log_event(
                "POLL_FAILED",
                installment_id=application_id,
                source="FF",
                payload={
                    "uuid": application.uuid,
                    "error": self._extract_error_message(mapped),
                },
            )
            raise mapped from exc

        polled_status = self._extract_poll_status(response_payload)
        bank_status = polled_status
        if polled_status in FF_REFUND_STATUSES:
            polled_status = application.status
        elif polled_status not in FF_APPLICATION_STATUSES:
            # Refund statuses and anything new: keep ours, the event log has it.
            await self._log_event(
                "POLL_UNSUPPORTED_STATUS",
                installment_id=application_id,
                source="FF",
                payload={"response": response_payload, "status_before": application.status},
            )
            polled_status = application.status
        status_value = self._keep_terminal_poll_status(application.status, polled_status)
        approved_params = self._bank_params(response_payload, params_key="credit_params")
        product_id = self._extract_string(response_payload, "product")
        loan_type = self._extract_string(response_payload, "loan_type")
        payload_uuid = self._extract_string(response_payload, "uuid")

        await self.ff_repository.update_application_from_poll(
            application_id=application_id,
            status=status_value,
            approved_params=approved_params or None,
            product_id=product_id,
            loan_type=loan_type,
            uuid=payload_uuid,
            credit_contract=self._extract_string(response_payload, "credit_contract"),
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
        if application.refund_status == "REQUESTED":
            await self._refund_result_from_poll(application, bank_status)
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

        if self._extract_string(payload, "covlir_status"):
            # The refund result was sent to the general hook URL.
            return await self._apply_refund_webhook(payload)

        status_value = self._extract_status(payload)
        uuid = self._extract_string(payload, "uuid") or self._extract_string(payload, "lead_id")
        reference_id = self._extract_string(payload, "reference_id")
        approved_params = self._bank_params(payload, params_key="approved_params")
        loan_type = self._extract_string(payload, "loan_type")
        product_id = self._extract_string(payload, "product")
        redirect_url = self._extract_string(payload, "redirect_url")
        credit_contract = self._extract_string(payload, "credit_contract")
        issued_at = self._extract_string(payload, "issued_at")

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

        if status_value not in FF_APPLICATION_STATUSES:
            # Ack so the bank does not retry; nothing to change on our side.
            await self._log_event(
                "WEBHOOK_UNSUPPORTED_STATUS",
                installment_id=application.id,
                source="FF",
                payload={"hook": payload, "status_before": application.status},
            )
            return WebhookAckResponse(ok=True, status=True)

        status_value = self._keep_terminal_poll_status(application.status, status_value)
        current_params = application.approved_params or {}
        merged_params = {**current_params, **approved_params}

        if application.status == status_value and merged_params == current_params:
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
                approved_params=approved_params or None,
                uuid=uuid,
                product_id=product_id,
                loan_type=loan_type,
                redirect_url=redirect_url,
                credit_contract=credit_contract,
                issued_at=issued_at,
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

    async def _call_with_reauth(
        self,
        provider: FFProvider,
        call: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run `call(access_token)` against the bank, logging in again once on 401.
        Bank errors stay FFClientError (the caller decides how to map them);
        a failed login is already an HTTPException."""
        access_token = await self._ensure_valid_token(provider=provider)
        try:
            return await call(access_token)
        except FFClientError as exc:
            if exc.status_code != status.HTTP_401_UNAUTHORIZED:
                raise
            logger.warning("FF request got 401, re-authenticating")
        access_token = await self._authenticate_and_store_token(provider=provider)
        return await call(access_token)

    async def reconcile(
        self,
        *,
        stale_minutes: int = 10,
        unknown_after_minutes: int = 120,
        limit: int = 50,
    ) -> ReconcileResponse:
        """Cron: ask the bank about applications that went quiet, close ones the
        bank never acknowledged, and put issued ones into the deal. Hooks stay
        the main channel; this covers lost hooks and a lost apply answer."""
        rows = await self.ff_repository.list_for_reconcile(stale_minutes=stale_minutes, limit=limit)
        now = datetime.now(UTC)
        polled = changed = applied = expired = errors = refunds = 0

        for row in rows:
            application_id = row["id"]
            try:
                if row.get("refund_status") == "REQUESTED":
                    updated = await self.poll_application(application_id)
                    polled += 1
                    if updated.refund_status != "REQUESTED":
                        refunds += 1
                    continue

                if row["status"] == "ISSUED":
                    if await self._try_auto_apply_on_issued(application_id):
                        applied += 1
                    continue

                if not row["uuid"]:
                    # NEW / APPLY_UNKNOWN without uuid: the bank's lead TTL is
                    # 1 hour, so after that a hook by reference_id would have come.
                    if row["created_at"] < now - timedelta(minutes=unknown_after_minutes):
                        await self.ff_repository.mark_apply_failed(application_id)
                        await self._log_event(
                            "APPLY_UNKNOWN_EXPIRED",
                            installment_id=application_id,
                            source="INSTALLMENT",
                            payload={
                                "status_before": row["status"],
                                "minutes": unknown_after_minutes,
                            },
                        )
                        expired += 1
                    continue

                updated = await self.poll_application(application_id)
                polled += 1
                if updated.status != row["status"]:
                    changed += 1
                if (
                    updated.status == "ISSUED"
                    and updated.client_request_credit_detail_id is not None
                ):
                    applied += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "Installment reconcile failed | application_id={id} error={error}",
                    id=application_id,
                    error=self._extract_error_message(exc),
                )

        return ReconcileResponse(
            checked=len(rows),
            polled=polled,
            changed=changed,
            applied=applied,
            expired=expired,
            errors=errors,
            refunds=refunds,
        )

    # --- refund (goods-loan-refund) -------------------------------------------

    async def create_refund(
        self,
        application_id: int,
        request: CreateInstallmentRefundRequest,
    ) -> InstallmentRefundResponse:
        """Send a refund of an issued loan to the bank (contract termination).

        installment__application_refund_request checks what the bank checks and
        marks REQUESTED before the call, committed: a second click is refused and
        a lost answer is resolved by the bank's hook or by reconcile."""
        application = await self.get_application_by_id(application_id)
        provider = await self._require_provider()
        # The result arrives only as a hook: without our hook credentials the
        # bank cannot report it.
        await self._require_webhook_credentials()

        try:
            prepared = await self.ff_repository.committed(
                lambda repo: repo.request_refund(
                    application_id=application.id,
                    refund_type=request.refund_type,
                    refund_amount=request.refund_amount,
                    requested_by=request.requested_by,
                )
            )
        except StoredProcedureError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message
            ) from exc

        amount = Decimal(str(prepared["refund_amount"]))
        bank_payload = {
            "uuid": prepared["uuid"],
            "partner_id": self._required_config_value(provider, "partner_id"),
            "refund_type": prepared["refund_type"],
            "refund_amount": float(amount),
            "hook_url": self._refund_hook_url(provider),
        }
        await self._log_event(
            "REFUND_REQUEST",
            installment_id=application.id,
            source="FF",
            payload={"request": bank_payload, "requested_by": request.requested_by},
        )

        ff_kwargs = self._ff_request_kwargs(provider)
        try:
            response_payload = await self._call_with_reauth(
                provider,
                lambda token: self.ff_client.send_goods_loan_refund(
                    access_token=token,
                    payload=bank_payload,
                    **ff_kwargs,
                ),
            )
        except FFClientError as exc:
            if exc.maybe_sent:
                await self._log_event(
                    "REFUND_UNKNOWN",
                    installment_id=application.id,
                    source="FF",
                    payload={"error": exc.detail},
                )
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=REFUND_UNKNOWN_DETAIL
                ) from exc
            mapped = self._map_ff_error(exc)
            await self._refund_rejected(application.id, error=str(mapped.detail))
            raise mapped from exc
        except HTTPException as exc:
            # Login to the bank failed: the refund never left us.
            await self._refund_rejected(application.id, error=self._extract_error_message(exc))
            raise

        message = self._extract_string(response_payload, "message")
        if message is not None and message.lower() != "success":
            description = (
                self._extract_string(response_payload, "description") or "Банк отклонил возврат."
            )
            await self._refund_rejected(application.id, error=description)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=description
            )

        await self._log_event(
            "REFUND_ACCEPTED",
            installment_id=application.id,
            source="FF",
            payload={"response": response_payload},
        )
        return InstallmentRefundResponse(
            id=application.id,
            uuid=prepared["uuid"],
            refund_type=prepared["refund_type"],
            refund_amount=amount,
            refund_status="REQUESTED",
            loan_amount=Decimal(str(prepared["loan_amount"])),
            refunded_before=Decimal(str(prepared["refunded_before"])),
            bank_message=message,
        )

    async def handle_refund_webhook(
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
                payload={"error": self._extract_error_message(exc), "hook": payload},
            )
            raise
        return await self._apply_refund_webhook(payload)

    async def _apply_refund_webhook(self, payload: dict[str, Any]) -> WebhookAckResponse:
        """Bank hook after Colvir: {uuid, partner_id, refund_type, refund_amount,
        covlir_status: SUCCESS | DECLINE} (the bank spells it «covlir»)."""
        uuid = self._extract_string(payload, "uuid")
        covlir_status = (self._extract_string(payload, "covlir_status") or "").upper()
        if not uuid or covlir_status not in {"SUCCESS", "DECLINE"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Refund hook must include uuid and covlir_status SUCCESS | DECLINE.",
            )
        refund_amount = self._decimal_or_none(payload.get("refund_amount"))
        try:
            result = await self.ff_repository.committed(
                lambda repo: repo.refund_result(
                    uuid=uuid, covlir_status=covlir_status, refund_amount=refund_amount
                )
            )
        except StoredProcedureError as exc:
            # Unknown uuid or no refund in progress: ack so the bank does not
            # retry, keep the hook in the log for a manual look.
            await self._log_event(
                "REFUND_WEBHOOK_UNEXPECTED",
                source="FF",
                payload={"error": exc.message, "hook": payload},
            )
            return WebhookAckResponse(ok=True, status=True)

        await self._log_event(
            "REFUND_RESULT" if result.get("changed") else "REFUND_WEBHOOK_DUPLICATE",
            installment_id=result.get("application_id"),
            source="FF",
            payload={"source": "webhook", "hook": payload, "result": result},
        )
        return WebhookAckResponse(ok=True, status=True)

    async def _refund_result_from_poll(
        self, application: InstallmentApplicationResponse, bank_status: str
    ) -> None:
        """A REQUESTED refund whose hook did not come: infer the result from the
        bank's application status. A repeated partial refund cannot be told
        apart from the previous one by status — left for the hook / a manual look."""
        result: str | None = None
        if (bank_status == "REFUND" and application.refund_type == "REFUND") or (
            bank_status == "PARTIAL_REFUND"
            and application.refund_type == "PARTIAL_REFUND"
            and not application.refund_amount
        ):
            result = "SUCCESS"
        elif bank_status == "ISSUED":
            requested_at = application.refund_requested_at
            if requested_at is not None and datetime.now(UTC) - requested_at > REFUND_DECLINE_AFTER:
                result = "DECLINE"
        if result is None:
            if bank_status != "PROCESSING_REFUND":
                await self._log_event(
                    "REFUND_NEEDS_CHECK",
                    installment_id=application.id,
                    source="INSTALLMENT",
                    payload={"bank_status": bank_status, "refund_type": application.refund_type},
                )
            return
        changed = await self.ff_repository.committed(
            lambda repo: repo.refund_result(application_id=application.id, covlir_status=result)
        )
        await self._log_event(
            "REFUND_RESULT",
            installment_id=application.id,
            source="INSTALLMENT",
            payload={"source": "poll", "bank_status": bank_status, "result": changed},
        )

    async def _refund_rejected(self, application_id: int, *, error: str) -> None:
        await self._log_event(
            "REFUND_REJECTED",
            installment_id=application_id,
            source="FF",
            payload={"error": error},
        )
        try:
            await self.ff_repository.committed(lambda repo: repo.clear_refund(application_id))
        except Exception as clear_exc:
            logger.warning(
                "Failed to clear REQUESTED refund | application_id={id} error={error}",
                id=application_id,
                error=clear_exc,
            )

    def _refund_hook_url(self, provider: FFProvider) -> str:
        explicit = provider.config.get("refund_hook_url")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        return f"{self._required_config_value(provider, 'hook_url').rstrip('/')}/refund"

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return Decimal(str(value))
        except ArithmeticError:
            return None

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

        now = datetime.now(UTC)
        token_exp = self.ff_client.access_token_expires_at(access_token)
        expires_at = (
            token_exp if token_exp is not None and token_exp > now else now + TOKEN_FALLBACK_TTL
        )
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
                # The bank stores the order number from here
                # (credits_universaladditionalinformation.reference_id); the
                # top-level one comes back as null in goods-application-info.
                "reference_id": reference_id,
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

        return raw_status.strip().upper()

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
        if current in TERMINAL_STATUSES and polled != current:
            return current
        return polled

    @classmethod
    def _bank_params(cls, payload: dict[str, Any], *, params_key: str) -> dict[str, Any]:
        """Loan params (`approved_params` in hooks, `credit_params` in poll) plus
        the borrower's identity and the contract number. Empty placeholders
        the bank sends ("", "+") are dropped so they never overwrite real data."""
        source = payload.get(params_key)
        params: dict[str, Any] = {}
        if isinstance(source, dict):
            for key, value in source.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                params[key] = value.strip() if isinstance(value, str) else value
        for key in _BANK_IDENTITY_FIELDS:
            value = cls._clean_identity_value(key, payload.get(key))
            if value is not None:
                params[key] = value
        return params

    @staticmethod
    def _clean_identity_value(key: str, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            return None
        digits = "".join(symbol for symbol in cleaned if symbol.isdigit())
        if key == "iin":
            return digits if len(digits) == 12 else None
        if key == "mobile_phone":
            return f"+{digits}" if len(digits) == 11 else None
        return cleaned

    @staticmethod
    def _qr_data_uri(url: str | None) -> str | None:
        if not url:
            return None
        try:
            return segno.make(url, error="m").svg_data_uri(scale=6, border=2)
        except Exception as exc:
            logger.warning("QR render failed | error={error}", error=exc)
            return None

    @staticmethod
    def _parse_basic_authorization_header(
        header_value: str | None,
    ) -> tuple[str | None, str | None]:
        if header_value is None or not header_value.startswith("Basic "):
            return None, None

        token = header_value[6:].strip()
        if not token:
            return None, None

        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except binascii.Error, UnicodeDecodeError:
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

    async def _try_auto_apply_on_issued(self, application_id: int) -> bool:
        application = await self.ff_repository.get_application_by_id(application_id)
        if application is None:
            return False
        if application.status != "ISSUED":
            return False
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
            return False
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
            return False

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
            return False

        await self._log_event(
            "AUTO_APPLY",
            installment_id=application_id,
            source="INSTALLMENT",
            payload={"client_request_credit_detail_id": credit_detail_id},
        )
        return True

    async def _mark_apply_failed(
        self,
        application_id: int,
        request_payload: dict[str, Any],
        *,
        error: str,
    ) -> None:
        await self._log_event(
            "FF_APPLY_FAILED",
            installment_id=application_id,
            source="FF",
            payload={"error": error, "request": request_payload},
        )
        try:
            await self.ff_repository.committed(lambda repo: repo.mark_apply_failed(application_id))
        except Exception as mark_exc:
            logger.warning(
                "Failed to mark APPLY_FAILED | application_id={id} error={error}",
                id=application_id,
                error=mark_exc,
            )

    async def _mark_apply_unknown(
        self,
        application_id: int,
        request_payload: dict[str, Any],
        *,
        error: str,
        response: dict[str, Any] | None = None,
    ) -> None:
        await self._log_event(
            "FF_APPLY_UNKNOWN",
            installment_id=application_id,
            source="FF",
            payload={"error": error, "request": request_payload, "response": response},
        )
        try:
            await self.ff_repository.committed(lambda repo: repo.mark_apply_unknown(application_id))
        except Exception as mark_exc:
            logger.warning(
                "Failed to mark APPLY_UNKNOWN | application_id={id} error={error}",
                id=application_id,
                error=mark_exc,
            )

    async def _log_event(
        self,
        event_type: str,
        *,
        source: str,
        installment_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        redacted = self._redact_pii(payload or {})
        try:
            # Committed on its own: an HTTP error later in the request must not
            # erase the audit of what was sent to and received from the bank.
            await self.ff_repository.committed(
                lambda repo: repo.insert_event_log(
                    installment_id=installment_id,
                    event_type=event_type,
                    payload=redacted,
                    source=source,
                )
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
                    lowered
                    in {
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
