import base64
import binascii
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from secrets import compare_digest, token_hex
from tempfile import gettempdir
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from bcrypt import checkpw
from fastapi import HTTPException, status
from loguru import logger
from src.service import BaseService

from ..schemas import (
    CessionApplicationItem,
    CessionBatchResult,
    CreateFactoringApplicationRequest,
    CreateFactoringApplicationResponse,
    CreateFactoringRefundRequest,
    FactoringApplicationResponse,
    FactoringProviderConfigResponse,
    FactoringRefundResponse,
    FactoringSignDocument,
    PrepareFactoringDocumentsRequest,
    PrepareFactoringDocumentsResponse,
    PrescoringFactoringRequest,
    PrescoringFactoringResponse,
    PrintFormItem,
    SendCessionRequest,
    SendCessionResponse,
    SubmitFactoringApplicationRequest,
    WebhookAckResponse,
)
from .client import FactoringClient, FactoringClientError
from .docx_fill import fill_docx_bytes
from .mynca import MyncaClient, MyncaClientError
from .repo import (
    PROVIDER_CODE,
    CessionBatchItem,
    FactoringProvider,
    FactoringRepository,
    FactoringWebhookCredential,
)
from .cession_placeholders import (
    allowed_periods,
    build_cession_placeholders,
    parse_discount_by_period,
)
from .limits import (
    parse_prescoring_max_limit,
    parse_principal_limits,
    phone_for_prescoring,
)
from .templates import PRINT_FORM_SPECS, fetch_template_bytes

VALID_WEBHOOK_STATUSES = {"REJECTED", "APPROVED", "ALTERNATIVE", "ISSUED", "PENDING", "IN_PROGRESS"}
ACTIVE_PREPARE_STATUSES = {"WAITING_SIGN", "NEW", "IN_PROGRESS", "PENDING", "APPROVED", "ALTERNATIVE"}
TERMINAL_WEBHOOK_STATUSES = {"ISSUED", "REJECTED", "REVERSED"}
PRINT_FORMS_CACHE_DIR = Path(gettempdir()) / "factoring-print-forms"
PRINT_FORM_PUBLIC_URL_TTL_SEC = 72 * 3600
PRINT_FORM_CACHE_TTL_SEC = 15 * 60
ALMATY_TZ = ZoneInfo("Asia/Almaty")
CESSION_TEMPLATE_CODE = "FF_FACTORING_CESSION"


@dataclass(frozen=True, slots=True)
class _PrescoringOutcome:
    status: str | None
    score: float | None
    message: str | None
    max_limit: Decimal | None
    skipped: bool
    checked_at: datetime | None


class FactoringService(BaseService):
    def __init__(
        self,
        repository: FactoringRepository,
        client: FactoringClient,
        app_env: str,
        mynca: MyncaClient | None = None,
        office_public_url: str = "https://office.smartremont.kz",
        public_base_url: str = "https://devintegration.smart-remont.kz",
        nca_master_key: str = "",
        prescoring_username: str = "",
        prescoring_password: str = "",
    ) -> None:
        self.repository = repository
        self.client = client
        self.app_env = app_env
        self.mynca = mynca
        self.office_public_url = office_public_url.rstrip("/")
        self.public_base_url = public_base_url.rstrip("/")
        self.nca_master_key = nca_master_key
        self.prescoring_username = prescoring_username.strip()
        self.prescoring_password = prescoring_password

    async def create_application(
        self,
        request: CreateFactoringApplicationRequest,
    ) -> CreateFactoringApplicationResponse:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Use POST /applications/prepare then submit.",
        )

    async def run_prescoring(
        self,
        request: PrescoringFactoringRequest,
    ) -> PrescoringFactoringResponse:
        provider = await self._require_provider()
        if not await self.repository.client_request_exists(request.client_request_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"client_request_id={request.client_request_id} was not found.",
            )
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
        self._require_principal_limits(provider, request.principal)
        partner = await self._require_partner_for_client_request(
            provider, request.client_request_id
        )
        outcome = await self._run_prescoring(
            provider=provider,
            client_request_id=request.client_request_id,
            iin=iin,
            phone=phone,
            partner=partner,
            principal=request.principal,
        )
        return self._prescoring_response(outcome, request.principal)

    async def prepare_documents(
        self,
        request: PrepareFactoringDocumentsRequest,
    ) -> PrepareFactoringDocumentsResponse:
        self._require_mynca().require_configured()
        provider = await self._require_provider()
        await self._require_webhook_credentials()
        if not await self.repository.client_request_exists(request.client_request_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"client_request_id={request.client_request_id} was not found.",
            )

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

        self._require_principal_limits(provider, request.principal)
        await self._require_deal_budget(request.client_request_id, request.principal)

        self._require_allowed_period(provider, request.period)
        product_id = request.product_id or self._required_config_value(provider, "default_product_id")
        partner = await self._require_partner_for_client_request(
            provider, request.client_request_id
        )
        prescoring = await self._run_prescoring(
            provider=provider,
            client_request_id=request.client_request_id,
            iin=iin,
            phone=phone,
            partner=partner,
            principal=request.principal,
        )
        self._require_prescoring_outcome(
            provider,
            prescoring,
            principal=request.principal,
        )

        channel = self._required_config_value(provider, "channel")
        hook_url = self._required_config_value(provider, "hook_url")
        success_url = self._required_config_value(provider, "success_url")
        failure_url = self._required_config_value(provider, "failure_url")
        branch_code = (request.branch_code or "200000").strip()
        existing = await self.repository.get_applications_by_client_request(
            request.client_request_id
        )
        busy = [item for item in existing if item.status in ACTIVE_PREPARE_STATUSES]
        if busy:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"По сделке уже есть активная заявка id={busy[0].id} "
                    f"(status={busy[0].status}). Дождитесь завершения или отклоните её."
                ),
            )
        credit_contract = self._generate_credit_contract(
            branch_code=branch_code,
            client_request_id=request.client_request_id,
            sequence=len(existing) + 1,
        )
        placeholders = self._template_values(
            client_fio=(request.client_fio or "").strip(),
            iin=iin,
            phone=phone,
            principal=request.principal,
            period=request.period,
            credit_contract=credit_contract,
            client_request_id=request.client_request_id,
        )
        print_forms: list[dict[str, Any]] = []
        for spec in PRINT_FORM_SPECS:
            print_forms.append(
                await self._prepare_one_print_form(
                    spec=spec,
                    placeholders=placeholders,
                    client_request_id=request.client_request_id,
                )
            )

        credit_goods = (
            [item.model_dump(mode="json", exclude_none=True) for item in request.credit_goods]
            if request.credit_goods
            else None
        )
        application_id = await self.repository.insert_application(
            client_request_id=request.client_request_id,
            provider_id=provider.id,
            credit_contract=credit_contract,
            product_id=product_id,
            principal=request.principal,
            period=request.period,
            created_by=request.created_by,
            partner=partner,
            channel=channel,
            branch_code=branch_code,
            prepayment_amount=request.prepayment_amount,
            interest_rate=request.interest_rate,
            print_forms=print_forms,
            credit_goods=credit_goods,
            request_payload={"iin": iin, "mobile_phone": phone},
            success_url=success_url,
            failure_url=failure_url,
            hook_url=hook_url,
            status="WAITING_SIGN",
            prescoring_status=prescoring.status,
            prescoring_score=Decimal(str(prescoring.score)) if prescoring.score is not None else None,
            prescoring_message=prescoring.message,
            prescoring_max_limit=prescoring.max_limit,
            prescoring_checked_at=prescoring.checked_at,
        )
        await self._log_event(
            "DOCUMENTS_PREPARED",
            factoring_id=application_id,
            source="FACTORING",
            payload={
                "credit_contract": credit_contract,
                "print_forms": [
                    {
                        "name": item["name"],
                        "sign_process_id": item.get("sign_process_id"),
                    }
                    for item in print_forms
                ],
            },
        )
        documents = [
            self._to_sign_document(
                item,
                application_id=application_id,
                include_public_url=False,
            )
            for item in print_forms
        ]
        return PrepareFactoringDocumentsResponse(
            id=application_id,
            credit_contract=credit_contract,
            status="WAITING_SIGN",
            provider_code=PROVIDER_CODE,
            documents=documents,
        )

    async def refresh_sign_status(
        self,
        application_id: int,
    ) -> PrepareFactoringDocumentsResponse:
        application = await self.get_application_by_id(application_id)
        documents = await self._poll_print_form_signatures(application)
        return PrepareFactoringDocumentsResponse(
            id=application.id,
            credit_contract=application.credit_contract,
            status=application.status,
            provider_code=application.provider_code,
            documents=documents,
        )

    async def submit_application(
        self,
        application_id: int,
        request: SubmitFactoringApplicationRequest,
    ) -> CreateFactoringApplicationResponse:
        application = await self.get_application_by_id(application_id)
        provider = await self._require_provider()
        self._require_stored_prescoring(provider, application)
        await self._require_webhook_credentials()
        if application.status not in {"WAITING_SIGN", "NEW"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Application status {application.status} cannot be submitted to the bank.",
            )
        documents = await self._poll_print_form_signatures(application)
        unsigned = [item.title for item in documents if not item.signed]
        if unsigned:
            mismatches = [item.error for item in documents if item.error]
            detail = "Клиент ещё не подписал: " + ", ".join(unsigned)
            if mismatches:
                detail = "; ".join(mismatches)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )

        # Заявку на факторинг могут оформить не на владельца сделки, а на другого
        # человека — поэтому контакты берём строго из самой заявки (request_payload,
        # сохранённый на prepare с тем ИИН/телефоном, на которые готовились и
        # подписывались документы). Тело запроса игнорируем: значение из формы
        # могло не совпасть с заявителем и тихо подменить контакты в банке.
        stored_contacts = application.request_payload or {}
        iin = self._normalize_iin(stored_contacts.get("iin"))
        phone = self._normalize_phone(stored_contacts.get("mobile_phone"))
        if iin is None or phone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "У заявки нет сохранённых ИИН/телефона (request_payload). "
                    "Подготовьте документы заново (prepare), чтобы контакты сохранились."
                ),
            )

        product_id = application.product_id or self._required_config_value(
            provider, "default_product_id"
        )
        partner = application.partner or await self._require_partner_for_client_request(
            provider, application.client_request_id
        )
        channel = application.channel or self._required_config_value(provider, "channel")
        hook_url = self._required_config_value(provider, "hook_url")
        success_url = self._required_config_value(provider, "success_url")
        failure_url = self._required_config_value(provider, "failure_url")
        application = await self.get_application_by_id(application_id)
        bank_print_forms = [
            {
                "name": str(form.get("name") or ""),
                "url": self._print_form_public_url(application_id, form),
            }
            for form in self._print_forms_list(application)
            if form.get("signed")
        ]
        if not bank_print_forms:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Нет подписанных печатных форм для отправки в банк.",
            )
        apply_request = CreateFactoringApplicationRequest(
            client_request_id=application.client_request_id,
            iin=iin,
            mobile_phone=phone,
            principal=application.principal or 0,
            period=application.period or 0,
            created_by=application.created_by or 0,
            print_forms=[PrintFormItem(name=item["name"], url=item["url"]) for item in bank_print_forms],
            credit_contract=application.credit_contract,
            branch_code=application.branch_code or "200000",
            product_id=product_id,
            is_knox=request.is_knox,
        )
        reference_id = str(application.id)
        bank_payload = self._build_apply_payload(
            provider=provider,
            iin=iin,
            phone=phone,
            product_id=product_id,
            partner=partner,
            channel=channel,
            credit_contract=application.credit_contract,
            request=apply_request,
            print_forms=bank_print_forms,
            credit_goods=application.credit_goods,
            reference_id=reference_id,
            hook_url=hook_url,
            success_url=success_url,
            failure_url=failure_url,
        )
        await self._log_event(
            "FF_APPLY_REQUEST",
            factoring_id=application.id,
            source="FF_FACTORING",
            payload=bank_payload,
        )
        try:
            response_payload = await self._apply_with_reauth(provider=provider, payload=bank_payload)
        except HTTPException as exc:
            await self._log_event(
                "FF_APPLY_FAILED",
                factoring_id=application.id,
                source="FF_FACTORING",
                payload={"error": self._extract_error_message(exc), "request": bank_payload},
            )
            raise

        application_uuid = response_payload.get("uuid")
        if not isinstance(application_uuid, str) or not application_uuid:
            await self._log_event(
                "FF_APPLY_FAILED",
                factoring_id=application.id,
                source="FF_FACTORING",
                payload={"error": "Bank did not return uuid", "response": response_payload},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Freedom Factoring did not return application uuid.",
            )
        redirect_url = self._extract_redirect_url(response_payload)
        await self.repository.update_application_after_apply(
            application_id=application.id,
            uuid=application_uuid,
            redirect_url=redirect_url,
            status="IN_PROGRESS",
            reference_id=reference_id,
        )
        await self._log_event(
            "CREATED",
            factoring_id=application.id,
            source="FF_FACTORING",
            payload={
                "response": response_payload,
                "request": bank_payload,
                "status": "IN_PROGRESS",
            },
        )
        return CreateFactoringApplicationResponse(
            id=application.id,
            uuid=application_uuid,
            reference_id=reference_id,
            credit_contract=application.credit_contract,
            status="IN_PROGRESS",
            redirect_url=redirect_url,
            provider_code=PROVIDER_CODE,
        )

    async def get_print_form_file(
        self,
        application_id: int,
        name: str,
        token: str,
    ) -> bytes:
        application = await self.get_application_by_id(application_id)
        form = self._find_print_form(application, name)
        stored_token = str(form.get("file_token") or "")
        incoming = token or ""
        if (
            not stored_token
            or len(stored_token) != len(incoming)
            or not compare_digest(stored_token, incoming)
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Print form not found.")
        self._require_print_form_link_active(form)
        return await self._fetch_signed_print_form_pdf(application, form)

    async def download_print_form_authenticated(
        self,
        application_id: int,
        name: str,
    ) -> bytes:
        application = await self.get_application_by_id(application_id)
        form = self._find_print_form(application, name)
        signed, error = await self._print_form_sign_state(
            form,
            self._normalize_iin((application.request_payload or {}).get("iin")),
        )
        if not signed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error or "Документ ещё не подписан.",
            )
        return await self._fetch_signed_print_form_pdf(application, form)

    async def get_application_by_id(self, application_id: int) -> FactoringApplicationResponse:
        application = await self.repository.get_application_by_id(application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"factoring application id={application_id} was not found.",
            )
        return application

    async def get_application_for_client(self, application_id: int) -> FactoringApplicationResponse:
        return self._sanitize_application_for_client(
            await self.get_application_by_id(application_id)
        )

    async def get_applications_by_client_request(
        self, client_request_id: int
    ) -> list[FactoringApplicationResponse]:
        return await self.repository.get_applications_by_client_request(client_request_id)

    async def get_applications_for_client(
        self,
        client_request_id: int,
    ) -> list[FactoringApplicationResponse]:
        items = await self.get_applications_by_client_request(client_request_id)
        return [self._sanitize_application_for_client(item) for item in items]

    async def create_refund(
        self,
        application_id: int,
        request: CreateFactoringRefundRequest,
    ) -> FactoringRefundResponse:
        application = await self.get_application_by_id(application_id)
        await self._require_webhook_credentials()
        if (application.status or "").upper() != "ISSUED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Возврат можно вызвать только по заявке в статусе ISSUED.",
            )
        if not application.uuid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="У заявки нет uuid банка — возврат невозможен.",
            )
        if not application.partner:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="У заявки нет partner_id — возврат невозможен.",
            )
        if (application.refund_status or "").upper() == "REQUESTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Возврат уже отправлен в банк, ждём ответ Colvir.",
            )
        if (application.refund_status or "").upper() == "SUCCESS" and (
            application.refund_type or ""
        ).upper() == "REFUND":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Полный возврат по этой заявке уже проведён.",
            )

        refund_type = request.refund_type.strip().upper()
        if refund_type not in {"REFUND", "PARTIAL_REFUND"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="refund_type must be REFUND or PARTIAL_REFUND.",
            )
        principal = application.principal or Decimal("0")
        already_refunded = Decimal("0")
        refund_status = (application.refund_status or "").upper()
        if application.refund_amount and (
            refund_status == "SUCCESS" or application.refund_completed_at is not None
        ):
            already_refunded = application.refund_amount
        remaining = principal - already_refunded
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="По этой заявке уже возвращена полная сумма договора.",
            )
        amount = request.refund_amount if request.refund_amount is not None else remaining
        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="refund_amount must be greater than 0.",
            )
        if refund_type == "REFUND" and amount > remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Для REFUND сумма не должна превышать остаток по договору.",
            )
        if refund_type == "PARTIAL_REFUND" and amount >= remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Для PARTIAL_REFUND сумма должна быть строго меньше остатка по договору.",
            )

        provider = await self._require_provider()
        hook_url = provider.config.get("refund_hook_url")
        if not isinstance(hook_url, str) or not hook_url.strip():
            hook_url = f"{self.public_base_url}/api/v1/factoring/ff/webhook/refund"
        bank_payload = {
            "uuid": application.uuid,
            "partner_id": application.partner,
            "refund_type": refund_type,
            "refund_amount": float(amount),
            "hook_url": hook_url,
        }
        await self.repository.update_application_refund(
            application_id=application.id,
            refund_type=refund_type,
            refund_amount=amount,
        )
        try:
            bank_response = await self._refund_with_reauth(provider=provider, payload=bank_payload)
        except FactoringClientError as exc:
            mapped = self._map_client_error(exc)
            if mapped.status_code < 500:
                await self._clear_requested_refund(application.id)
            raise mapped from exc
        except HTTPException as exc:
            if exc.status_code < 500:
                await self._clear_requested_refund(application.id)
            raise
        await self._log_event(
            "REFUND_REQUEST",
            factoring_id=application.id,
            source="FF_FACTORING",
            committed=True,
            payload={"request": bank_payload, "response": bank_response},
        )
        return FactoringRefundResponse(
            id=application.id,
            uuid=application.uuid,
            refund_type=refund_type,
            refund_amount=amount,
            refund_status="REQUESTED",
            bank_message=str(bank_response.get("message") or "success"),
        )

    @staticmethod
    def _refund_webhook_idempotent(
        application: FactoringApplicationResponse,
        covlir_status: str,
    ) -> bool:
        current = (application.refund_status or "").upper()
        if covlir_status == "SUCCESS" and current == "SUCCESS":
            return True
        if covlir_status == "DECLINE" and current == "DECLINE":
            return True
        return False

    @staticmethod
    def _require_refund_webhook_transition(
        application: FactoringApplicationResponse,
        *,
        covlir_status: str,
        refund_amount: Decimal | None,
    ) -> None:
        current = (application.refund_status or "").upper()
        if current != "REQUESTED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Refund webhook rejected: application is not in REQUESTED state.",
            )
        if covlir_status != "SUCCESS" or refund_amount is None:
            return
        principal = application.principal or Decimal("0")
        if principal <= 0:
            return
        already_refunded = application.refund_amount or Decimal("0")
        new_total = already_refunded + refund_amount
        if new_total > principal:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Refund amount {new_total:,.0f} ₸ exceeds contract principal "
                    f"{principal:,.0f} ₸."
                ).replace(",", " "),
            )

    async def _clear_requested_refund(self, application_id: int) -> None:
        try:
            await self.repository.update_application_refund(
                application_id=application_id,
                clear=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to clear REQUESTED refund after bank error | id={id} error={error}",
                id=application_id,
                error=exc,
            )

    async def handle_webhook(
        self,
        payload: dict[str, Any],
        *,
        authorization_header: str | None,
    ) -> WebhookAckResponse:
        await self._verify_webhook_auth(authorization_header)

        if self._extract_string(payload, "covlir_status"):
            return await self.handle_refund_webhook(
                payload, authorization_header=authorization_header
            )

        status_value = self._extract_status(payload)
        reference_id = self._extract_string(payload, "reference_id")
        uuid = self._extract_string(payload, "uuid")
        application = await self.repository.get_application_by_reference_or_uuid(
            reference_id=reference_id,
            uuid=uuid,
        )
        if application is None:
            await self._log_event(
                "WEBHOOK_ORPHAN",
                source="FF_FACTORING",
                payload=payload,
            )
            return WebhookAckResponse(ok=True, status=True)

        status_value = self._keep_terminal_webhook_status(application.status, status_value)
        issued_at = self._extract_issued_at(payload)
        approved_params = self._build_approved_params(payload)
        await self.repository.update_application_from_webhook(
            application_id=application.id,
            status=status_value,
            approved_params=approved_params,
            uuid=uuid,
            issued_at=issued_at,
        )
        await self._log_event(
            "WEBHOOK",
            factoring_id=application.id,
            source="FF_FACTORING",
            payload={"status": status_value, "raw": payload},
        )
        return WebhookAckResponse(ok=True, status=True)

    async def handle_refund_webhook(
        self,
        payload: dict[str, Any],
        *,
        authorization_header: str | None,
    ) -> WebhookAckResponse:
        await self._verify_webhook_auth(authorization_header)
        uuid = self._extract_string(payload, "uuid")
        covlir_status = (self._extract_string(payload, "covlir_status") or "").upper()
        if not uuid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Refund webhook payload must include uuid.",
            )
        if covlir_status not in {"SUCCESS", "DECLINE"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Refund webhook payload must include covlir_status SUCCESS|DECLINE.",
            )
        application = await self.repository.get_application_by_reference_or_uuid(
            reference_id=None,
            uuid=uuid,
        )
        if application is None:
            await self._log_event(
                "REFUND_WEBHOOK_ORPHAN",
                source="FF_FACTORING",
                payload=payload,
            )
            return WebhookAckResponse(ok=True, status=True)

        idempotent = self._refund_webhook_idempotent(application, covlir_status)
        if idempotent:
            return WebhookAckResponse(ok=True, status=True)

        refund_type = self._extract_string(payload, "refund_type")
        refund_amount_raw = payload.get("refund_amount")
        refund_amount = None
        if refund_amount_raw is not None:
            refund_amount = Decimal(str(refund_amount_raw))
        self._require_refund_webhook_transition(
            application,
            covlir_status=covlir_status,
            refund_amount=refund_amount,
        )
        await self.repository.update_application_refund(
            application_id=application.id,
            uuid=uuid,
            refund_type=refund_type,
            refund_amount=refund_amount,
            covlir_status=covlir_status,
        )
        await self._log_event(
            "REFUND_WEBHOOK",
            factoring_id=application.id,
            source="FF_FACTORING",
            payload=payload,
        )
        return WebhookAckResponse(ok=True, status=True)

    async def send_daily_cession(self, request: SendCessionRequest) -> SendCessionResponse:
        """One cession (assignment agreement) per legal entity (company_id from
        client_request_tab), because each ТОО has its own bank `partner` and its
        own EDS key (nca.company_key_store_tab). See
        agent-memory/ff-factoring/03-bank-codes-sr.md."""
        issue_date = self._parse_issue_date(request.issue_date)
        items = await self.repository.list_cession_batch(issue_date)
        if not items:
            return SendCessionResponse(issue_date=issue_date.isoformat(), batches=[])

        provider = await self._require_provider()
        by_company: dict[int | None, list[CessionBatchItem]] = {}
        for item in items:
            by_company.setdefault(item.company_id, []).append(item)

        batches = [
            await self._send_cession_for_company(
                provider=provider,
                company_id=company_id,
                company_items=company_items,
                issue_date=issue_date,
                dry_run=request.dry_run,
            )
            for company_id, company_items in by_company.items()
        ]
        return SendCessionResponse(issue_date=issue_date.isoformat(), batches=batches)

    async def _send_cession_for_company(
        self,
        *,
        provider: FactoringProvider,
        company_id: int | None,
        company_items: list[CessionBatchItem],
        issue_date: date,
        dry_run: bool,
    ) -> CessionBatchResult:
        payment_amount = sum((item.principal for item in company_items), Decimal("0"))
        response_items = [
            CessionApplicationItem(
                id=item.id,
                uuid=item.uuid,
                credit_contract=item.credit_contract,
                principal=item.principal,
                status=item.status,
            )
            for item in company_items
        ]

        partner = self._resolve_partner_for_company(provider, company_id)
        if partner is None:
            return CessionBatchResult(
                company_id=company_id,
                partner=None,
                payment_amount=payment_amount,
                applications=response_items,
                sent=False,
                bank_message=(
                    f"company_id={company_id}: нет partner в "
                    "config.partner_by_company_id — цессия не отправлена."
                ),
            )

        if dry_run:
            return CessionBatchResult(
                company_id=company_id,
                partner=partner,
                payment_amount=payment_amount,
                applications=response_items,
                sent=False,
                bank_message="dry_run: not signed, not sent.",
            )

        try:
            key_b64, password = await self._get_cession_signer_key(company_id)
        except HTTPException as exc:
            await self._log_event(
                "CESSION_SIGN_FAILED",
                factoring_id=company_items[0].id if company_items else None,
                source="MYNCA",
                committed=True,
                payload={
                    "issue_date": issue_date.isoformat(),
                    "company_id": company_id,
                    "error": self._extract_error_message(exc),
                },
            )
            raise

        mynca = self._require_mynca()
        mynca.require_configured()

        contract_number = self._generate_cession_contract_number(issue_date, company_id)
        signing_date = datetime.now(ALMATY_TZ).date()

        company = (
            await self.repository.get_cession_company(company_id)
            if company_id is not None
            else None
        )
        pdf_bytes = await self._build_cession_document(
            partner=partner,
            contract_number=contract_number,
            issue_date=issue_date,
            signing_date=signing_date,
            payment_amount=payment_amount,
            count=len(company_items),
            applications=[
                {
                    "uuid": item.uuid,
                    "credit_contract": item.credit_contract,
                    "principal": item.principal,
                    "period": item.period,
                    "issued_at": item.issued_at,
                }
                for item in company_items
            ],
            company_name=str((company or {}).get("company_name_official") or ""),
            company_iik=str((company or {}).get("bank_account") or ""),
            client_signer=str((company or {}).get("director_fio") or ""),
            discount_by_period=parse_discount_by_period(provider.config.get("discount_by_period")),
        )

        try:
            certificate = await mynca.pkcs12_info(key_b64=key_b64, password=password)
            public_key_b64 = certificate.get("pubkey")
            if not isinstance(public_key_b64, str) or not public_key_b64:
                raise MyncaClientError("MyNCA pkcs12/info returned no pubkey.")
            sign_save_result = await mynca.cms_sign_save(
                data=pdf_bytes,
                key_b64=key_b64,
                password=password,
                detached=True,
                file_name=f"cession-{contract_number}.pdf",
                ext_id=company_items[0].id,
            )
            sign_process_id = sign_save_result["sign_process_id"]
            sign_group_id = sign_save_result.get("group_id")
            cms_bytes = await mynca.download_cms(sign_process_id)
        except MyncaClientError as exc:
            await self._log_event(
                "CESSION_SIGN_FAILED",
                factoring_id=company_items[0].id,
                source="MYNCA",
                committed=True,
                payload={
                    "issue_date": issue_date.isoformat(),
                    "company_id": company_id,
                    "error": exc.detail,
                },
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail) from exc

        bank_payload = {
            "partner": partner,
            "document": base64.b64encode(pdf_bytes).decode("ascii"),
            "contract_number": contract_number,
            "signing_date": signing_date.isoformat(),
            "issue_date": issue_date.isoformat(),
            "digital_signature": base64.b64encode(cms_bytes).decode("ascii"),
            "payment_amount": str(payment_amount),
            "public_key": public_key_b64,
        }
        subject = certificate.get("subject") if isinstance(certificate.get("subject"), dict) else {}
        await self._log_event(
            "CESSION_REQUEST",
            factoring_id=company_items[0].id,
            source="FF_FACTORING",
            committed=True,
            payload={
                "issue_date": issue_date.isoformat(),
                "signing_date": signing_date.isoformat(),
                "company_id": company_id,
                "partner": partner,
                "contract_number": contract_number,
                "payment_amount": str(payment_amount),
                "application_ids": [item.id for item in company_items],
                "signer_cn": subject.get("commonName"),
                "signer_iin": subject.get("iin"),
                "signer_serial": certificate.get("serialNumber"),
                "document_bytes": len(pdf_bytes),
                "digital_signature_bytes": len(cms_bytes),
                "sign_process_id": sign_process_id,
                "sign_group_id": sign_group_id,
            },
        )

        cession_path = provider.config.get("cession_path")
        if not isinstance(cession_path, str) or not cession_path.strip():
            cession_path = "/ffc-api-public/custom/partner-document/assignment-agreement/"

        application_ids = [item.id for item in company_items]
        mark_sent = getattr(
            self.repository, "mark_cession_sent_committed", self.repository.mark_cession_sent
        )
        await mark_sent(
            application_ids,
            contract_number,
            sign_process_id=sign_process_id,
            sign_group_id=sign_group_id,
        )

        try:
            access_token = await self._ensure_valid_token(provider)
            try:
                bank_response = await self.client.send_cession(
                    access_token=access_token,
                    payload=bank_payload,
                    cession_path=cession_path,
                    **self._request_kwargs(provider),
                )
            except FactoringClientError as exc:
                if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                    access_token = await self._authenticate_and_store_token(provider)
                    bank_response = await self.client.send_cession(
                        access_token=access_token,
                        payload=bank_payload,
                        cession_path=cession_path,
                        **self._request_kwargs(provider),
                    )
                else:
                    raise
        except FactoringClientError as exc:
            await self._log_event(
                "CESSION_FAILED",
                factoring_id=company_items[0].id,
                source="FF_FACTORING",
                committed=True,
                payload={
                    "issue_date": issue_date.isoformat(),
                    "company_id": company_id,
                    "contract_number": contract_number,
                    "http_status": exc.status_code,
                    "error": exc.detail,
                },
            )
            mapped = self._map_client_error(exc)
            if mapped.status_code < 500:
                await self.repository.unmark_cession_sent(application_ids, contract_number)
            raise mapped from exc
        except HTTPException as exc:
            if exc.status_code < 500:
                await self.repository.unmark_cession_sent(application_ids, contract_number)
            raise

        await self._log_event(
            "CESSION_SENT",
            factoring_id=company_items[0].id,
            source="FF_FACTORING",
            committed=True,
            payload={
                "issue_date": issue_date.isoformat(),
                "company_id": company_id,
                "contract_number": contract_number,
                "sign_process_id": sign_process_id,
                "sign_group_id": sign_group_id,
                "response": bank_response,
                "application_ids": [item.id for item in company_items],
            },
        )

        return CessionBatchResult(
            company_id=company_id,
            partner=partner,
            contract_number=contract_number,
            payment_amount=payment_amount,
            applications=response_items,
            sent=True,
            bank_message=str(bank_response.get("message") or ""),
            sign_process_id=sign_process_id,
            sign_group_id=sign_group_id,
        )

    @staticmethod
    def _resolve_partner_for_company(
        provider: FactoringProvider, company_id: int | None
    ) -> str | None:
        mapping = provider.config.get("partner_by_company_id")
        if isinstance(mapping, dict) and company_id is not None:
            partner = mapping.get(str(company_id))
            if isinstance(partner, str) and partner.strip():
                return partner.strip()
        return None

    async def _require_partner_for_client_request(
        self, provider: FactoringProvider, client_request_id: int
    ) -> str:
        company_id = await self.repository.get_client_request_company_id(client_request_id)
        partner = self._resolve_partner_for_company(provider, company_id)
        if partner is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"company_id={company_id}: нет partner в config.partner_by_company_id "
                    "— заявку в банк не отправляем."
                ),
            )
        return partner

    async def _get_cession_signer_key(self, company_id: int | None) -> tuple[str, str]:
        """Returns (key_b64, password) for the ТОО's EDS used to sign cession,
        resolved from client_request_tab.company_id and decrypted on demand
        from nca.company_key_store_tab — never stored in our own config/env."""
        if not self.nca_master_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="NCA_MASTER_KEY is not configured (needed to decrypt company EDS key).",
            )
        if company_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Заявка без company_id (client_request_tab) — не знаем, чьим ключом подписывать.",
            )
        key_store_id = await self.repository.get_active_company_key_store_id(company_id)
        if key_store_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Нет активного ключа ЭЦП в nca.company_key_store_tab для company_id={company_id}."
                ),
            )
        key_data, password = await self.repository.get_decrypted_company_key(
            key_store_id, self.nca_master_key
        )
        return base64.b64encode(key_data).decode("ascii"), password

    @staticmethod
    def _parse_issue_date(raw: str | None) -> date:
        if raw is None or not raw.strip():
            return datetime.now(ALMATY_TZ).date()
        try:
            return date.fromisoformat(raw.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="issue_date must be YYYY-MM-DD.",
            ) from exc

    @staticmethod
    def _generate_cession_contract_number(issue_date: date, company_id: int | None) -> str:
        # TODO: replace with a proper sequence once the bank confirms the exact
        # numbering scheme (example in docs: ЦЕС-2025-001847). Company suffix
        # keeps numbers unique when we send several batches (one per ТОО) for
        # the same issue_date.
        suffix = f"-{company_id}" if company_id is not None else ""
        return f"ЦЕС-{issue_date.year}-{issue_date:%m%d}{suffix}"

    async def _build_cession_document(
        self,
        *,
        partner: str,
        contract_number: str,
        issue_date: date,
        signing_date: date,
        payment_amount: Decimal,
        count: int,
        applications: list[dict[str, Any]] | None = None,
        company_name: str = "",
        company_iik: str = "",
        client_signer: str = "",
        discount_by_period: dict[int, Decimal] | None = None,
    ) -> bytes:
        template = await self.repository.get_template_by_code(CESSION_TEMPLATE_CODE)
        path = str((template or {}).get("template_path") or "").strip()
        if not path:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Шаблон {CESSION_TEMPLATE_CODE} не найден в template_tab. "
                    "Нужно загрузить .docx договора цессии и добавить template_path."
                ),
            )
        docx_bytes = await fetch_template_bytes(path, self.office_public_url)
        items = applications or []
        placeholders, row_values = build_cession_placeholders(
            contract_number=contract_number,
            issue_date=issue_date,
            company_name=company_name,
            company_iik=company_iik,
            client_signer=client_signer,
            applications=items,
            discount_by_period=discount_by_period,
        )
        placeholders.setdefault("partner", partner)
        placeholders.setdefault("signing_date", signing_date.strftime("%d.%m.%Y"))
        placeholders.setdefault(
            "payment_amount",
            f"{payment_amount:,.0f}".replace(",", " "),
        )
        placeholders.setdefault("applications_count", str(count))
        filled = fill_docx_bytes(docx_bytes, placeholders, row_values=row_values)
        try:
            return await self._require_mynca().docx_to_pdf(filled)
        except MyncaClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail) from exc

    async def _require_provider(self) -> FactoringProvider:
        provider = await self.repository.get_provider_by_code(PROVIDER_CODE)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Provider {PROVIDER_CODE} is not configured.",
            )
        return provider

    async def _ensure_valid_token(self, provider: FactoringProvider) -> str:
        token = await self.repository.get_token(provider.id)
        if token is not None and token.expires_at > datetime.now(UTC) + timedelta(minutes=1):
            return token.access_token
        return await self._authenticate_and_store_token(provider)

    async def _authenticate_and_store_token(self, provider: FactoringProvider) -> str:
        credentials = await self.repository.get_active_credentials(provider.id, self.app_env)
        if credentials is None:
            # fallback: try stage if APP_ENV differs
            credentials = await self.repository.get_active_credentials(provider.id, "stage")
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Active factoring credentials were not found for env={self.app_env}.",
            )

        auth_path = provider.config.get("auth_path")
        if not isinstance(auth_path, str) or not auth_path.strip():
            auth_path = "/ffc-api-auth/"

        try:
            access_token, refresh_token = await self.client.authenticate(
                username=credentials.username,
                password=credentials.password,
                auth_path=auth_path,
                **self._request_kwargs(provider),
            )
        except FactoringClientError as exc:
            raise self._map_client_error(exc) from exc

        expires_at = datetime.now(UTC) + timedelta(minutes=55)
        await self.repository.upsert_token(
            provider_id=provider.id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        return access_token

    async def _apply_with_reauth(
        self,
        *,
        provider: FactoringProvider,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        access_token = await self._ensure_valid_token(provider)
        apply_path = provider.config.get("apply_path")
        if not isinstance(apply_path, str) or not apply_path.strip():
            apply_path = "/ffc-api-public/universal/apply/apply-lead-factoring"

        try:
            return await self.client.apply_lead_factoring(
                access_token=access_token,
                payload=payload,
                apply_path=apply_path,
                **self._request_kwargs(provider),
            )
        except FactoringClientError as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                access_token = await self._authenticate_and_store_token(provider)
                try:
                    return await self.client.apply_lead_factoring(
                        access_token=access_token,
                        payload=payload,
                        apply_path=apply_path,
                        **self._request_kwargs(provider),
                    )
                except FactoringClientError as retry_exc:
                    raise self._map_client_error(retry_exc) from retry_exc
            raise self._map_client_error(exc) from exc

    async def _refund_with_reauth(
        self,
        *,
        provider: FactoringProvider,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        access_token = await self._ensure_valid_token(provider)
        refund_path = provider.config.get("refund_path")
        if not isinstance(refund_path, str) or not refund_path.strip():
            refund_path = "/ffc-api-public/custom/refund/factoring-refund/"
        try:
            return await self.client.send_refund(
                access_token=access_token,
                payload=payload,
                refund_path=refund_path,
                **self._request_kwargs(provider),
            )
        except FactoringClientError as exc:
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                access_token = await self._authenticate_and_store_token(provider)
                try:
                    return await self.client.send_refund(
                        access_token=access_token,
                        payload=payload,
                        refund_path=refund_path,
                        **self._request_kwargs(provider),
                    )
                except FactoringClientError as retry_exc:
                    raise self._map_client_error(retry_exc) from retry_exc
            raise self._map_client_error(exc) from exc

    def _build_apply_payload(
        self,
        *,
        provider: FactoringProvider,
        iin: str,
        phone: str,
        product_id: str,
        partner: str,
        channel: str,
        credit_contract: str,
        request: CreateFactoringApplicationRequest,
        print_forms: list[dict[str, Any]],
        credit_goods: list[dict[str, Any]] | None,
        reference_id: str,
        hook_url: str,
        success_url: str,
        failure_url: str,
    ) -> dict[str, Any]:
        credit_params: dict[str, Any] = {
            "period": request.period,
            "principal": float(request.principal),
        }
        if request.prepayment_amount is not None:
            credit_params["prepayment_amount"] = float(request.prepayment_amount)
        if request.interest_rate is not None:
            credit_params["interest_rate"] = float(request.interest_rate)

        payload: dict[str, Any] = {
            "iin": iin,
            "mobile_phone": phone,
            "product": product_id,
            "partner": partner,
            "channel": channel,
            "credit_contract": credit_contract,
            "credit_params": credit_params,
            "print_forms": print_forms,
            "credit_configs": {"is_knox": request.is_knox},
            "additional_information": {
                "hook_url": hook_url,
                "failure_url": failure_url,
                "success_url": success_url,
                "reference_id": reference_id,
            },
            "reference_id": reference_id,
        }
        if credit_goods:
            payload["credit_goods"] = credit_goods
        return payload

    @staticmethod
    def _generate_credit_contract(
        *,
        branch_code: str,
        client_request_id: int,
        sequence: int = 1,
    ) -> str:
        yy = datetime.now(UTC).strftime("%y")
        seq = str(client_request_id) if sequence <= 1 else f"{client_request_id}-{sequence}"
        return f"FCT{yy}-{branch_code}-{seq}"

    def _require_mynca(self) -> MyncaClient:
        if self.mynca is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MyNCA client is not configured.",
            )
        return self.mynca

    @staticmethod
    def _template_values(
        *,
        client_fio: str,
        iin: str,
        phone: str,
        principal: Any,
        period: int,
        credit_contract: str,
        client_request_id: int,
    ) -> dict[str, str]:
        amount = f"{principal:,.0f}".replace(",", " ")
        now = datetime.now(ALMATY_TZ)
        created_at = now.strftime("%d.%m.%Y")
        return {
            "borrower_full_name": client_fio,
            "borrower_iin": iin,
            "borrower_number": phone,
            "borrower_otp": "",
            "principal": amount,
            "principal_kz": amount,
            "principal_ru": amount,
            "period": str(period),
            "created_at": created_at,
            "contract_number": credit_contract,
            "quantity": "1",
            "total_amount": amount,
            "client_request_id": str(client_request_id),
            "product_full_name": "Ремонт",
            "day_of_month": str(now.day),
            "jur_agreement_number": "",
        }

    async def _prepare_one_print_form(
        self,
        *,
        spec: dict[str, str],
        placeholders: dict[str, str],
        client_request_id: int,
    ) -> dict[str, Any]:
        template = await self.repository.get_template_by_code(spec["template_code"])
        path = str((template or {}).get("template_path") or "").strip()
        if not path:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Шаблон {spec['template_code']} не найден в template_tab (пустой template_path).",
            )
        docx_bytes = await fetch_template_bytes(path, self.office_public_url)
        filled = fill_docx_bytes(docx_bytes, placeholders)
        mynca = self._require_mynca()
        try:
            pdf_bytes = await mynca.docx_to_pdf(filled)
            sign_url, sign_process_id = await mynca.sign_create(
                file_name=spec["file_name"],
                pdf_bytes=pdf_bytes,
                ext_id=client_request_id,
                meta_data={
                    "type_code": "FACTORING_PRINT_FORM",
                    "doc_type": spec["name"].upper(),
                    "client_request_id": client_request_id,
                    "print_form": spec["name"],
                },
                back_url=f"{self.public_base_url}/api/v1/factoring/ff/sign-callback",
                return_url=f"{self.public_base_url}/api/v1/factoring/ff/sign-callback",
            )
        except MyncaClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=exc.detail,
            ) from exc
        expires_at = datetime.now(UTC) + timedelta(seconds=PRINT_FORM_PUBLIC_URL_TTL_SEC)
        return {
            "name": spec["name"],
            "title": spec["title"],
            "url": "",
            "file_token": token_hex(16),
            "url_expires_at": expires_at.isoformat(),
            "sign_url": sign_url,
            "sign_process_id": sign_process_id,
            "signed": False,
        }

    def _print_form_public_url(self, application_id: int, form: dict[str, Any]) -> str:
        name = str(form.get("name") or "")
        token = str(form.get("file_token") or "")
        return (
            f"{self.public_base_url}/api/v1/factoring/ff/print-forms/"
            f"{application_id}/{name}?t={token}"
        )

    def _to_sign_document(
        self,
        form: dict[str, Any],
        *,
        application_id: int,
        signed: bool | None = None,
        include_public_url: bool = False,
    ) -> FactoringSignDocument:
        is_signed = bool(form.get("signed")) if signed is None else signed
        url = None
        if include_public_url and is_signed:
            url = self._print_form_public_url(application_id, form)
        return FactoringSignDocument(
            name=str(form.get("name") or ""),
            title=str(form.get("title") or form.get("name") or ""),
            sign_url=str(form.get("sign_url") or "") or None,
            signed=is_signed,
            url=url,
            error=str(form.get("error") or "") or None,
        )

    def _sanitize_application_for_client(
        self,
        application: FactoringApplicationResponse,
    ) -> FactoringApplicationResponse:
        if not application.print_forms:
            return application
        sanitized_forms = [
            self._sanitize_print_form_for_client(dict(item))
            for item in application.print_forms
            if isinstance(item, dict)
        ]
        return application.model_copy(update={"print_forms": sanitized_forms})

    @staticmethod
    def _sanitize_print_form_for_client(form: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(form)
        cleaned.pop("file_token", None)
        cleaned.pop("url_expires_at", None)
        if "url" in cleaned:
            cleaned["url"] = ""
        return cleaned

    def _require_print_form_link_active(self, form: dict[str, Any]) -> None:
        expires_raw = form.get("url_expires_at")
        if not expires_raw:
            return
        try:
            expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
        except ValueError:
            return
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Ссылка на печатную форму истекла.",
            )

    async def _fetch_signed_print_form_pdf(
        self,
        application: FactoringApplicationResponse,
        form: dict[str, Any],
    ) -> bytes:
        signed, error = await self._print_form_sign_state(
            form,
            self._normalize_iin((application.request_payload or {}).get("iin")),
        )
        if not signed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error or "Документ ещё не подписан.",
            )

        name = str(form.get("name") or "")
        cached = self._print_form_cache_path(application.id, name)
        if cached.exists():
            age_sec = datetime.now(UTC).timestamp() - cached.stat().st_mtime
            if age_sec <= PRINT_FORM_CACHE_TTL_SEC:
                return cached.read_bytes()
            cached.unlink(missing_ok=True)

        sign_process_id = str(form.get("sign_process_id") or "")
        try:
            pdf = await self._require_mynca().sign_download_pdf(sign_process_id)
        except MyncaClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=exc.detail,
            ) from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(pdf)
        return pdf

    @staticmethod
    def _print_forms_list(application: FactoringApplicationResponse) -> list[dict[str, Any]]:
        raw = application.print_forms or []
        return [dict(item) for item in raw if isinstance(item, dict)]

    def _find_print_form(
        self,
        application: FactoringApplicationResponse,
        name: str,
    ) -> dict[str, Any]:
        for item in self._print_forms_list(application):
            if item.get("name") == name:
                return item
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Print form not found.")

    async def _print_form_sign_state(
        self,
        form: dict[str, Any],
        expected_iin: str | None,
    ) -> tuple[bool, str | None]:
        if form.get("signed") and not expected_iin:
            return True, None
        sign_process_id = str(form.get("sign_process_id") or "")
        if not sign_process_id:
            return bool(form.get("signed")), None
        try:
            payload = await self._require_mynca().sign_status(sign_process_id)
        except MyncaClientError as exc:
            logger.warning(
                "MyNCA sign status failed | sign_process_id={id} error={error}",
                id=sign_process_id,
                error=exc.detail,
            )
            return False, None
        if not self._require_mynca().is_process_signed(payload):
            return False, None
        if expected_iin:
            signer_iin = self._extract_signer_iin(payload)
            if not signer_iin:
                return False, "В подписи ЭЦП нет ИИН (dn_name / signer.subject.iin)"
            if signer_iin != expected_iin:
                return False, (
                    f"ИИН ЭЦП {signer_iin} не совпадает с ИИН заявки {expected_iin}"
                )
        return True, None

    async def _poll_print_form_signatures(
        self,
        application: FactoringApplicationResponse,
    ) -> list[FactoringSignDocument]:
        expected_iin = self._normalize_iin((application.request_payload or {}).get("iin"))
        documents: list[FactoringSignDocument] = []
        updated_list: list[dict[str, Any]] = []
        changed = False
        for form in self._print_forms_list(application):
            signed, error = await self._print_form_sign_state(form, expected_iin)
            form_copy = dict(form)
            form_copy["signed"] = signed
            if error:
                form_copy["error"] = error
            if form_copy.get("signed") != form.get("signed") or form_copy.get("error") != form.get(
                "error"
            ):
                changed = True
            updated_list.append(form_copy)
            documents.append(
                self._to_sign_document(
                    form_copy,
                    application_id=application.id,
                    signed=signed,
                    include_public_url=False,
                )
            )
        if changed:
            update_print_forms = getattr(self.repository, "update_print_forms", None)
            if update_print_forms is not None:
                await update_print_forms(application.id, updated_list)
        return documents

    @staticmethod
    def _print_form_cache_path(application_id: int, name: str) -> Path:
        safe_name = "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_"}) or "doc"
        return PRINT_FORMS_CACHE_DIR / str(application_id) / f"{safe_name}.pdf"

    @staticmethod
    def _extract_redirect_url(payload: dict[str, Any]) -> str | None:
        for key in ("redirect_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def get_provider_config(self) -> FactoringProviderConfigResponse:
        provider = await self._require_provider()
        periods = allowed_periods(provider.config)
        rates = parse_discount_by_period(provider.config.get("discount_by_period"))
        principal_min, principal_max = parse_principal_limits(provider.config)
        return FactoringProviderConfigResponse(
            periods=periods,
            discount_by_period={str(period): rates[period] for period in periods if period in rates},
            principal_min=principal_min,
            principal_max=principal_max,
            prescoring_enabled=self._is_prescoring_configured(provider),
        )

    def _require_principal_limits(
        self,
        provider: FactoringProvider,
        principal: Decimal,
    ) -> None:
        principal_min, principal_max = parse_principal_limits(provider.config)
        if principal < principal_min:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Сумма займа {principal:,.0f} ₸ меньше минимальной "
                    f"{principal_min:,.0f} ₸."
                ).replace(",", " "),
            )
        if principal > principal_max:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Сумма займа {principal:,.0f} ₸ больше максимальной "
                    f"{principal_max:,.0f} ₸."
                ).replace(",", " "),
            )

    def _is_prescoring_configured(self, provider: FactoringProvider) -> bool:
        base_url = provider.config.get("prescoring_base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            return False
        return bool(self.prescoring_username and self.prescoring_password)

    @staticmethod
    def _prescoring_required(provider: FactoringProvider) -> bool:
        value = provider.config.get("prescoring_required")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return True

    @staticmethod
    def _prescoring_validity_sec(provider: FactoringProvider) -> int:
        raw = provider.config.get("prescoring_validity_sec", 3600)
        try:
            validity = int(raw)
        except (TypeError, ValueError):
            return 3600
        return max(60, min(validity, 86400))

    def _require_prescoring_outcome(
        self,
        provider: FactoringProvider,
        outcome: _PrescoringOutcome,
        *,
        principal: Decimal | None = None,
    ) -> None:
        if not self._prescoring_required(provider):
            if not outcome.skipped and outcome.status == "REJECTED":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=outcome.message or "Клиент не прошёл прескоринг (REJECTED).",
                )
            if (
                not outcome.skipped
                and outcome.max_limit is not None
                and principal is not None
                and principal > outcome.max_limit
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Сумма {principal:,.0f} ₸ превышает лимит прескоринга "
                        f"{outcome.max_limit:,.0f} ₸."
                    ).replace(",", " "),
                )
            return

        if outcome.skipped:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Prescoring обязателен, но не был выполнен.",
            )
        status_value = (outcome.status or "").upper()
        if status_value != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=outcome.message or f"Клиент не прошёл прескоринг ({status_value or 'UNKNOWN'}).",
            )
        if (
            outcome.max_limit is not None
            and principal is not None
            and principal > outcome.max_limit
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Сумма {principal:,.0f} ₸ превышает лимит прескоринга "
                    f"{outcome.max_limit:,.0f} ₸."
                ).replace(",", " "),
            )

    def _require_stored_prescoring(
        self,
        provider: FactoringProvider,
        application: FactoringApplicationResponse,
    ) -> None:
        if not self._prescoring_required(provider):
            return

        status_value = (application.prescoring_status or "").upper()
        if status_value != "APPROVED":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    application.prescoring_message
                    or "Заявка создана без успешного прескоринга (APPROVED)."
                ),
            )

        checked_at = application.prescoring_checked_at
        if checked_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="У заявки нет времени проверки прескоринга. Подготовьте документы заново.",
            )
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        age_sec = (datetime.now(UTC) - checked_at).total_seconds()
        if age_sec > self._prescoring_validity_sec(provider):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Прескоринг устарел. Подготовьте документы заново (prepare).",
            )

    @staticmethod
    def _prescoring_timeout_sec(provider: FactoringProvider) -> float:
        raw = provider.config.get("prescoring_timeout_sec", 15)
        try:
            timeout = float(raw)
        except (TypeError, ValueError):
            return 15.0
        return max(3.0, min(timeout, 60.0))

    def _prescoring_response(
        self,
        outcome: _PrescoringOutcome,
        principal: Decimal,
    ) -> PrescoringFactoringResponse:
        if outcome.skipped or outcome.status is None:
            return PrescoringFactoringResponse(
                status=outcome.status or "SKIPPED",
                score=outcome.score,
                message=outcome.message,
                max_limit=outcome.max_limit,
                allowed=True,
                skipped=True,
            )
        allowed = outcome.status == "APPROVED"
        if allowed and outcome.max_limit is not None and principal > outcome.max_limit:
            allowed = False
        return PrescoringFactoringResponse(
            status=outcome.status,
            score=outcome.score,
            message=outcome.message,
            max_limit=outcome.max_limit,
            allowed=allowed,
            skipped=False,
        )

    async def _run_prescoring(
        self,
        *,
        provider: FactoringProvider,
        client_request_id: int,
        iin: str,
        phone: str,
        partner: str,
        principal: Decimal,
    ) -> _PrescoringOutcome:
        if not self._is_prescoring_configured(provider):
            if self._prescoring_required(provider):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Prescoring не настроен: задайте config.prescoring_base_url и "
                        "FACTORING_PRESCORING_USER/PASSWORD."
                    ),
                )
            await self._log_event(
                "PRESCORING_SKIPPED",
                source="FF_FACTORING",
                payload={"reason": "not_configured", "client_request_id": client_request_id},
            )
            return _PrescoringOutcome(None, None, None, None, True, None)

        prescoring_phone = phone_for_prescoring(phone)
        if prescoring_phone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid mobile_phone for prescoring: expected 10 digits without +7.",
            )

        base_url = str(provider.config.get("prescoring_base_url", "")).strip().rstrip("/")
        path = str(provider.config.get("prescoring_path", "/prescoring_factoring")).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        request_uuid = str(uuid4())
        payload = {
            "iin": iin,
            "phone": prescoring_phone,
            "uuid": request_uuid,
            "partner": partner,
            "principal": int(principal),
        }
        await self._log_event(
            "PRESCORING_REQUEST",
            source="FF_FACTORING",
            payload={
                "client_request_id": client_request_id,
                "uuid": request_uuid,
                "partner": partner,
                "principal": str(principal),
                "iin": "***",
                "phone": "***",
            },
        )
        try:
            response_payload = await self.client.prescoring(
                base_url=base_url,
                path=path,
                username=self.prescoring_username,
                password=self.prescoring_password,
                payload=payload,
                timeout_sec=self._prescoring_timeout_sec(provider),
            )
        except FactoringClientError as exc:
            await self._log_event(
                "PRESCORING_FAILED",
                source="FF_FACTORING",
                payload={
                    "client_request_id": client_request_id,
                    "uuid": request_uuid,
                    "error": exc.detail,
                    "status_code": exc.status_code,
                },
            )
            if self._prescoring_required(provider):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Prescoring недоступен: {exc.detail}",
                ) from exc
            return _PrescoringOutcome(None, None, exc.detail, None, True, None)

        status_value = str(response_payload.get("status") or "").upper()
        score_raw = response_payload.get("score")
        score = float(score_raw) if isinstance(score_raw, (int, float)) else None
        message = response_payload.get("message")
        message_text = message.strip() if isinstance(message, str) and message.strip() else None
        max_limit = parse_prescoring_max_limit(response_payload.get("max_limit"))
        checked_at = datetime.now(UTC)
        outcome = _PrescoringOutcome(
            status=status_value or None,
            score=score,
            message=message_text,
            max_limit=max_limit,
            skipped=False,
            checked_at=checked_at,
        )
        await self._log_event(
            "PRESCORING_RESULT",
            source="FF_FACTORING",
            payload={
                "client_request_id": client_request_id,
                "uuid": request_uuid,
                "status": outcome.status,
                "score": outcome.score,
                "message": outcome.message,
                "max_limit": str(outcome.max_limit) if outcome.max_limit is not None else None,
            },
        )
        return outcome

    async def _require_deal_budget(
        self,
        client_request_id: int,
        principal: Decimal,
        *,
        exclude_application_id: int | None = None,
    ) -> None:
        """Несколько сотрудников могут параллельно оформлять рассрочку и/или
        факторинг на одну сделку — сумма ВСЕХ их активных заявок не должна
        превышать сумму сделки. Дешёвая проверка до MyNCA/банка; финальный
        источник правды — SQL guard в factoring__application_create /
        installment__application_create (public.cr_deal_committed_amount)."""
        deal_total = await self.repository.get_deal_total_amount(client_request_id)
        if deal_total is None or deal_total <= 0:
            return
        committed = await self.repository.get_deal_committed_amount(
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
    def _require_allowed_period(provider: FactoringProvider, period: int) -> None:
        allowed = allowed_periods(provider.config)
        if period not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"period={period} is not allowed. Allowed: {allowed}.",
            )

    @staticmethod
    def _required_config_value(provider: FactoringProvider, key: str) -> str:
        value = provider.config.get(key)
        if isinstance(value, str) and value.strip():
            return value
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"FF_FACTORING provider config.{key} is missing.",
        )

    @staticmethod
    def _provider_resolve_ip(provider: FactoringProvider) -> str | None:
        value = provider.config.get("resolve_ip")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def _request_kwargs(cls, provider: FactoringProvider) -> dict[str, str | None]:
        return {
            "base_url": provider.base_url,
            "resolve_ip": cls._provider_resolve_ip(provider),
        }

    @staticmethod
    def _normalize_iin(iin: str | None) -> str | None:
        if not isinstance(iin, str):
            return None
        normalized = "".join(symbol for symbol in iin if symbol.isdigit())
        if len(normalized) != 12:
            return None
        return normalized

    @staticmethod
    def _extract_signer_iin(payload: dict[str, Any]) -> str | None:
        signer = payload.get("signer") or {}
        if not isinstance(signer, dict):
            signer = {}
        subject = signer.get("subject") or {}
        if not isinstance(subject, dict):
            subject = {}
        top_subject = payload.get("subject") or {}
        if not isinstance(top_subject, dict):
            top_subject = {}
        candidates: list[Any] = [
            payload.get("dn_name"),
            payload.get("dn"),
            subject.get("iin"),
            signer.get("iin"),
            payload.get("iin"),
            top_subject.get("iin"),
        ]
        for raw in candidates:
            if not isinstance(raw, str) or not raw.strip():
                continue
            match = re.search(r"(?:SERIALNUMBER=IIN|IIN=)?(\d{12})", raw.replace(" ", ""), re.I)
            if match:
                return match.group(1)
            digits = "".join(ch for ch in raw if ch.isdigit())
            if len(digits) == 12:
                return digits
        return None

    @classmethod
    def _keep_terminal_webhook_status(cls, current: str | None, incoming: str) -> str:
        current_upper = (current or "").upper()
        if current_upper in TERMINAL_WEBHOOK_STATUSES and incoming.upper() != current_upper:
            return current or incoming
        return incoming

    @classmethod
    def _extract_issued_at(cls, payload: dict[str, Any]) -> str | None:
        for key in ("issued_at", "issue_date", "issued_date"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _redact_pii(value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                lowered = key.lower()
                if (
                    lowered in {"iin", "mobile_phone", "phone", "prop_iin", "prop_phone", "first_name", "last_name", "middle_name", "fio"}
                    or lowered.endswith("_iin")
                    or "phone" in lowered
                ):
                    redacted[key] = "***"
                else:
                    redacted[key] = FactoringService._redact_pii(item)
            return redacted
        if isinstance(value, list):
            return [FactoringService._redact_pii(item) for item in value]
        return value

    @staticmethod
    def _normalize_phone(raw_phone: str | None) -> str | None:
        if not isinstance(raw_phone, str):
            return None
        digits = "".join(symbol for symbol in raw_phone if symbol.isdigit())
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        if len(digits) != 11 or not digits.startswith("7"):
            return None
        return f"+{digits}"

    @staticmethod
    def _map_client_error(exc: FactoringClientError) -> HTTPException:
        detail = exc.detail.strip() or "Freedom Factoring request failed."
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            )
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Freedom Factoring authentication failed.",
            )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )

    @staticmethod
    def _extract_error_message(exc: HTTPException) -> str:
        detail = exc.detail
        if isinstance(detail, str):
            return detail
        return str(detail)

    async def _log_event(
        self,
        event_type: str,
        *,
        factoring_id: int | None = None,
        source: str,
        payload: dict[str, Any],
        committed: bool = False,
    ) -> None:
        try:
            writer = (
                self.repository.insert_event_log_committed
                if committed
                else self.repository.insert_event_log
            )
            await writer(
                factoring_id=factoring_id,
                event_type=event_type,
                payload=self._redact_pii(payload),
                source=source,
            )
        except Exception as exc:  # noqa: BLE001 — audit must not break main flow
            logger.warning(
                "Factoring event log write failed | event_type={event_type} "
                "factoring_id={factoring_id} error={error}",
                event_type=event_type,
                factoring_id=factoring_id,
                error=exc,
            )

    async def _require_webhook_credentials(self) -> FactoringWebhookCredential:
        credentials = await self.repository.get_provider_webhook_credentials(code=PROVIDER_CODE)
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Webhook credentials for FF_FACTORING are not configured. "
                    "Cannot submit the application until webhook_username/password are set."
                ),
            )
        return credentials

    async def _verify_webhook_auth(self, authorization_header: str | None) -> None:
        credentials = await self.repository.get_provider_webhook_credentials(code=PROVIDER_CODE)
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
    def _invalid_webhook_credentials_exception() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

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
    def _verify_webhook_password(password: str, credentials: FactoringWebhookCredential) -> bool:
        try:
            return checkpw(password.encode(), credentials.password_hash.encode())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid webhook password hash configuration.",
            ) from exc

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
        if status_value not in VALID_WEBHOOK_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported status: {status_value}",
            )
        return status_value

    def _build_approved_params(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        source = payload.get("approved_params")
        approved_params = dict(source) if isinstance(source, dict) else {}
        for key in ("alternative_reason", "status_reason"):
            value = self._extract_string(payload, key)
            if value is not None:
                approved_params[key] = value
        return approved_params or None
