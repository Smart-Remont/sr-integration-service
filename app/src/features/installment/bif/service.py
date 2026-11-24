"""BI Finance: a new application, the manual refund mark and the expiry of silent applications.

The bank's statuses come to ``webhook.py``. There is no poll and no refund through the bank.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status

from ..common import log_event, normalize_iin, require_deal_main_client_request
from ..constants import ApplicationStatus, InstallmentSource, ProviderCode
from ..repo import InstallmentProvider, InstallmentRepository
from ..schemas import (
    CreateBIFApplicationRequest,
    CreateInstallmentApplicationResponse,
    ExpireResponse,
    InstallmentApplicationResponse,
    ManualRefundRequest,
)
from .client import BIFClient, BIFClientError
from .token import BIFTokenSource, timeout_seconds
from .working_days import add_working_days

DEFAULT_EXPIRE_WORKING_DAYS = 3
# 502/503/timeout: the ТЗ says repeat with the same partnerId. One repeat right away, the next
# one is the manager's click (the same application goes again, see _unanswered_application).
REPEAT_DELAY_SECONDS = 1.0

APPLY_UNKNOWN_DETAIL = (
    "BI Finance не ответил на заявку вовремя. Заявка могла дойти до банка: нажмите «Отправить» "
    "ещё раз — уйдёт та же заявка с тем же номером, дубля не будет."
)


@dataclass(slots=True, frozen=True)
class _Order:
    """What goes to the bank: from the request, or from the stored application on a repeat."""

    application_id: int
    reference_id: str
    principal: int
    period: int
    initial_fee: Decimal
    payment_day: int


class BIFService:
    def __init__(
        self,
        repository: InstallmentRepository,
        client: BIFClient,
        app_env: str,
        *,
        repeat_delay_seconds: float = REPEAT_DELAY_SECONDS,
    ) -> None:
        self.repository = repository
        self.client = client
        self.tokens = BIFTokenSource(repository, client, app_env)
        self.repeat_delay_seconds = repeat_delay_seconds

    # --- new application ------------------------------------------------------------

    async def create_application(
        self, request: CreateBIFApplicationRequest
    ) -> CreateInstallmentApplicationResponse:
        provider = await self._require_provider()
        await self._require_webhook_credentials()
        await require_deal_main_client_request(self.repository, request.client_request_id)
        iin = normalize_iin(request.iin)
        if iin is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ИИН — 12 цифр.",
            )
        phone = bif_phone(request.mobile_phone)
        if phone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Телефон — мобильный номер Казахстана: +77XXXXXXXXX.",
            )
        seller = await self._seller(provider, request.client_request_id)

        repeated = await self._unanswered_application(request)
        order = repeated or await self._insert_application(provider, request, iin, phone)
        payload = build_order_payload(provider, order, seller=seller, iin=iin, phone=phone)
        await log_event(
            self.repository,
            "BIF_APPLY_REPEAT" if repeated else "APPLICATION_INIT",
            installment_id=order.application_id,
            source=InstallmentSource.INSTALLMENT,
            payload={
                "client_request_id": request.client_request_id,
                "created_by": request.created_by,
            },
        )
        await log_event(
            self.repository,
            "BIF_APPLY_REQUEST",
            installment_id=order.application_id,
            source=InstallmentSource.BIF,
            payload=payload,
        )

        answer = await self._send_order(provider, order, payload, repeated=repeated is not None)
        app_id = answer.get("appId")
        if isinstance(app_id, int):
            app_id = str(app_id)
        if not isinstance(app_id, str) or not app_id.strip():
            await self._mark_unknown(order, error="BI Finance не вернул appId.", answer=answer)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=APPLY_UNKNOWN_DETAIL
            )

        # Committed: the bank already has the application under this appId.
        await self.repository.committed(
            lambda repo: repo.update_application_after_apply(
                application_id=order.application_id,
                uuid=app_id.strip(),
                redirect_url=None,
                status=ApplicationStatus.IN_PROGRESS,
                reference_id=order.reference_id,
            )
        )
        await log_event(
            self.repository,
            "CREATED",
            installment_id=order.application_id,
            source=InstallmentSource.BIF,
            payload={"response": answer, "status": ApplicationStatus.IN_PROGRESS},
        )
        return CreateInstallmentApplicationResponse(
            id=order.application_id,
            uuid=app_id.strip(),
            reference_id=order.reference_id,
            status=ApplicationStatus.IN_PROGRESS,
            redirect_url=None,
            redirect_qr=None,
            provider_code=ProviderCode.BIF,
        )

    async def _unanswered_application(self, request: CreateBIFApplicationRequest) -> _Order | None:
        """An application of this deal the bank did not answer (APPLY_UNKNOWN) goes again with its
        partnerId: the bank does not create a second one for it (ТЗ, раздел 5)."""
        applications = await self.repository.get_applications_by_client_request(
            request.client_request_id
        )
        for application in applications:
            if (
                application.provider_code != ProviderCode.BIF
                or application.status != ApplicationStatus.APPLY_UNKNOWN
            ):
                continue
            same_terms = (
                application.principal == Decimal(request.principal)
                and application.period == request.period
                and (application.initial_fee or Decimal(0)) == request.initial_fee
                and application.payment_day == request.payment_day
            )
            if not same_terms:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"По сделке есть заявка BI Finance № {application.id} без ответа банка. "
                        "Повторно её можно отправить только на тех же условиях "
                        f"({application.principal:.0f} ₸, {application.period} мес., "
                        f"взнос {application.initial_fee or 0:.0f} ₸, "
                        f"день платежа {application.payment_day})."
                    ),
                )
            return _Order(
                application_id=application.id,
                reference_id=application.reference_id or f"SR-{application.id}",
                principal=request.principal,
                period=request.period,
                initial_fee=request.initial_fee,
                payment_day=request.payment_day,
            )
        return None

    async def _insert_application(
        self,
        provider: InstallmentProvider,
        request: CreateBIFApplicationRequest,
        iin: str,
        phone: str,
    ) -> _Order:
        days = provider.config.get("expire_working_days", DEFAULT_EXPIRE_WORKING_DAYS)
        expires_at = add_working_days(datetime.now(UTC), int(days))
        # Committed before the bank call: the partnerId the bank gets must stay ours even if
        # this request fails later.
        application_id = await self.repository.committed(
            lambda repo: repo.insert_application(
                client_request_id=request.client_request_id,
                provider_id=provider.id,
                product_id=_config_text(provider, "product_code"),
                bank_id=request.bank_id,
                loan_type="credit",
                principal=Decimal(request.principal),
                period=request.period,
                created_by=request.created_by,
                iin=iin,
                mobile_phone=phone,
                status=ApplicationStatus.NEW,
                initial_fee=request.initial_fee,
                payment_day=request.payment_day,
                expires_at=expires_at,
            )
        )
        return _Order(
            application_id=application_id,
            reference_id=f"SR-{application_id}",
            principal=request.principal,
            period=request.period,
            initial_fee=request.initial_fee,
            payment_day=request.payment_day,
        )

    async def _send_order(
        self,
        provider: InstallmentProvider,
        order: _Order,
        payload: dict[str, Any],
        *,
        repeated: bool,
    ) -> dict[str, Any]:
        """``repeated`` — the bank may already hold this partnerId (a repeat or a lost answer)."""
        attempts = 1 + int(provider.config.get("repeat_attempts", 1))
        renewed = False
        attempt = 0
        while True:
            attempt += 1
            try:
                token = await self.tokens.access_token(provider, renew=renewed)
                return await self.client.create_order(
                    provider.base_url, token, payload, timeout_seconds=timeout_seconds(provider)
                )
            except HTTPException as exc:
                # Login failed: this attempt never reached the order endpoint. If an earlier one
                # may have, the application stays unanswered and can go again.
                if repeated:
                    await self._mark_unknown(order, error=str(exc.detail))
                else:
                    await self._mark_failed(order, error=str(exc.detail))
                raise
            except BIFClientError as exc:
                if exc.status_code == status.HTTP_401_UNAUTHORIZED and not renewed:
                    renewed = True
                    attempt -= 1
                    continue
                if (
                    exc.status_code == status.HTTP_409_CONFLICT
                    and isinstance(exc.body, dict)
                    and exc.body.get("appId")
                ):
                    # A repeat of a partnerId the bank already took, answered with its appId.
                    return exc.body
                if exc.maybe_sent:
                    # From here on the bank may hold this partnerId.
                    repeated = True
                if exc.maybe_sent and attempt < attempts:
                    await log_event(
                        self.repository,
                        "BIF_APPLY_REPEAT",
                        installment_id=order.application_id,
                        source=InstallmentSource.BIF,
                        payload={"status": exc.status_code, "error": exc.message},
                    )
                    await asyncio.sleep(self.repeat_delay_seconds)
                    continue
                raise await self._order_refused(order, exc, repeated=repeated) from exc

    async def _order_refused(
        self, order: _Order, exc: BIFClientError, *, repeated: bool
    ) -> HTTPException:
        """What the manager sees. BI Finance's ``message`` goes to ``detail`` as is."""
        if exc.maybe_sent:
            await self._mark_unknown(order, error=exc.message, answer=exc.body)
            return HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=APPLY_UNKNOWN_DETAIL
            )
        if exc.status_code == status.HTTP_409_CONFLICT and repeated:
            # The bank already has this partnerId but did not tell its appId: the application
            # stays APPLY_UNKNOWN until the bank's statuses can be matched (PLAN.md, вопрос 4).
            await self._mark_unknown(order, error=exc.message, answer=exc.body)
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message)
        await self._mark_failed(order, error=exc.message, answer=exc.body)
        return bank_error(exc)

    async def _mark_failed(self, order: _Order, *, error: str, answer: Any = None) -> None:
        await log_event(
            self.repository,
            "BIF_APPLY_FAILED",
            installment_id=order.application_id,
            source=InstallmentSource.BIF,
            payload={"error": error, "response": answer},
        )
        await self.repository.committed(lambda repo: repo.mark_apply_failed(order.application_id))

    async def _mark_unknown(self, order: _Order, *, error: str, answer: Any = None) -> None:
        await log_event(
            self.repository,
            "BIF_APPLY_UNKNOWN",
            installment_id=order.application_id,
            source=InstallmentSource.BIF,
            payload={"error": error, "response": answer},
        )
        await self.repository.committed(lambda repo: repo.mark_apply_unknown(order.application_id))

    async def _seller(
        self, provider: InstallmentProvider, client_request_id: int
    ) -> dict[str, str]:
        """Who BI Finance pays: the deal's company (config.seller_by_company_id), else the default."""
        company_id = await self.repository.get_deal_company_id(client_request_id)
        by_company = provider.config.get("seller_by_company_id")
        seller = None
        if isinstance(by_company, dict) and company_id is not None:
            seller = by_company.get(str(company_id))
        if seller is None:
            seller = provider.config.get("seller_default")
        if not isinstance(seller, dict) or not all(
            isinstance(seller.get(key), str) and seller[key].strip()
            for key in ("bin", "name", "iban")
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"BI Finance: не настроен продавец для ТОО сделки (company_id={company_id}).",
            )
        return {key: seller[key].strip() for key in ("bin", "name", "iban")}

    # --- manual refund and expiry ---------------------------------------------------

    async def mark_manual_refund(
        self, application_id: int, request: ManualRefundRequest
    ) -> InstallmentApplicationResponse:
        application = await self._require_application(application_id)
        if application.provider_code != ProviderCode.BIF:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ручная отметка возврата — только BI Finance; возврат Freedom идёт через банк.",
            )
        await self.repository.refund_manual(
            application_id=application_id,
            amount=request.amount,
            comment=request.comment,
            created_by=request.created_by,
            refunded_at=request.refunded_at,
        )
        return await self._require_application(application_id)

    async def expire_applications(self, *, limit: int) -> ExpireResponse:
        result = await self.repository.expire_applications(limit=limit)
        return ExpireResponse.model_validate(result)

    # --- shared checks --------------------------------------------------------------

    async def _require_application(self, application_id: int) -> InstallmentApplicationResponse:
        application = await self.repository.get_application_by_id(application_id)
        if application is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application id={application_id} was not found.",
            )
        return application

    async def _require_provider(self) -> InstallmentProvider:
        provider = await self.repository.get_provider_by_code(ProviderCode.BIF)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Провайдер BI Finance не настроен или выключен.",
            )
        return provider

    async def _require_webhook_credentials(self) -> None:
        # Without our hook login the bank cannot report statuses: do not send applications.
        if await self.repository.get_provider_webhook_credentials(code=ProviderCode.BIF) is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не настроен вход BI Finance на наш метод статусов (webhook_username/password).",
            )


def bif_phone(raw_phone: str) -> str | None:
    """77XXXXXXXXX — the only format BI Finance takes; 8701… and +7 are converted."""
    digits = "".join(symbol for symbol in raw_phone if symbol.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits.startswith("7"):
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("77"):
        return None
    return digits


def build_order_payload(
    provider: InstallmentProvider,
    order: _Order,
    *,
    seller: dict[str, str],
    iin: str,
    phone: str,
) -> dict[str, Any]:
    """Body of POST /api/callbacks/mortgage/orders (ТЗ, раздел 3)."""
    initial_fee: int | float = (
        int(order.initial_fee)
        if order.initial_fee == order.initial_fee.to_integral()
        else float(order.initial_fee)
    )
    return {
        "partnerId": order.reference_id,
        "orderData": {
            "productCode": _config_text(provider, "product_code"),
            "dealerId": _config_text(provider, "dealer_id"),
            "sellerBin": seller["bin"],
            "sellerCompany": seller["name"],
            "sellerIban": seller["iban"],
            "person": {"iin": iin, "mobilePhone": phone},
            "loanInfo": {
                "creditAmount": order.principal,
                "creditTerm": order.period,
                "initialFee": initial_fee,
                "paymentDate": str(order.payment_day),
                "repaymentType": str(provider.config.get("repayment_type", "0")),
            },
            "schemeDefPayment": str(provider.config.get("scheme_def_payment", "0")),
            "isDeferredPrincipal": bool(provider.config.get("is_deferred_principal", False)),
        },
    }


def bank_error(exc: BIFClientError) -> HTTPException:
    message = exc.message or "BI Finance отклонил заявку."
    match exc.status_code:
        case status.HTTP_400_BAD_REQUEST:
            return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)
        case status.HTTP_409_CONFLICT:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"У клиента уже есть активная заявка в BI Finance. {message}".strip(),
            )
        case status.HTTP_403_FORBIDDEN:
            return HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"BI Finance: у нашей учётки нет права создавать заявки. {message}".strip(),
            )
        case status.HTTP_429_TOO_MANY_REQUESTS:
            return HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="BI Finance: слишком частые запросы, повторите через минуту.",
            )
        case _:
            return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)


def _config_text(provider: InstallmentProvider, key: str) -> str:
    value = provider.config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"BI Finance: не задан config.{key} провайдера.",
    )
