from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from src.schema import BaseSchema

from .openapi_examples import (
    APPLICATION_RESPONSE,
    CREATE_APPLICATION_REQUEST,
    CREATE_APPLICATION_RESPONSE,
    FF_PRODUCTS_RESPONSE,
    PROVIDER_PRODUCTS_LIST_RESPONSE,
    SYNC_PRODUCTS_RESPONSE,
    WEBHOOK_ACK_RESPONSE,
    WEBHOOK_APPROVED,
    WEBHOOK_ISSUED,
    WEBHOOK_REJECTED,
)


class FFPrincipalLimits(BaseSchema):
    min: Decimal
    max: Decimal


class FFRepaymentCondition(BaseSchema):
    periods: list[int]
    principal_limits: FFPrincipalLimits

    @model_validator(mode="before")
    @classmethod
    def _flat_limits(cls, data: Any) -> Any:
        # The bank's docs show both {"principal_limits": {"min", "max"}} and
        # {"min", "max"} right in the condition; we always answer with the first.
        if (
            isinstance(data, dict)
            and "principal_limits" not in data
            and "min" in data
            and "max" in data
        ):
            data = {**data, "principal_limits": {"min": data["min"], "max": data["max"]}}
        return data


class FFProduct(BaseSchema):
    product_id: str
    repayment_method: str
    repayment: list[FFRepaymentCondition]

    @model_validator(mode="after")
    def _normalize_method(self) -> FFProduct:
        # The bank's examples spell installment as INSTALMENT; CRM and the
        # catalog use INSTALLMENT.
        method = self.repayment_method.strip().upper()
        self.repayment_method = "INSTALLMENT" if method == "INSTALMENT" else method
        return self


class SyncProductsResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [SYNC_PRODUCTS_RESPONSE]},
    )

    inserted: int
    closed: int
    unchanged: int
    relinked: int = Field(
        default=0, description="Сколько банков перенесено на новую строку продукта"
    )
    orphaned_bank_ids: list[int] = Field(
        default_factory=list,
        description="Банки, чей продукт банк убрал: в рассрочке их не будет, пока не перепривязать",
    )
    ids: list[int]


class ProviderProductResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    id: int
    provider_code: str
    provider_product_id: str
    period: int
    principal_min: Decimal
    principal_max: Decimal
    repayment_method: str
    tier_index: int
    content_hash: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    created_at: datetime


class ProviderProductListResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [PROVIDER_PRODUCTS_LIST_RESPONSE]},
    )

    items: list[ProviderProductResponse]
    total: int


class FFProductsResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [FF_PRODUCTS_RESPONSE]},
    )

    partner_id: str
    name: str
    products: list[FFProduct]


class InstallmentApplicationListResponse(BaseSchema):
    items: list[InstallmentApplicationResponse]
    total: int


class CreateInstallmentApplicationRequest(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [CREATE_APPLICATION_REQUEST]},
    )

    client_request_id: int = Field(
        description="ID ремонта в SR (client_request_tab). ИИН/телефон — отдельно.",
        examples=[2916069],
    )
    provider_code: Literal["FF"] = Field(description="Провайдер рассрочки", examples=["FF"])
    iin: str = Field(
        description="ИИН заёмщика (12 цифр). Может отличаться от владельца заявки на ремонт.",
        examples=["891026301046"],
    )
    mobile_phone: str = Field(
        description="Телефон заёмщика в формате +7...",
        examples=["+77066078118"],
    )
    product_id: str = Field(
        description="product_id из GET /ff/products",
        examples=["SR_MOCK_FACT_12"],
    )
    bank_id: int = Field(
        description="ID online bank_tab, разрешённого для client_request (credit program)",
        examples=[42],
    )
    repayment_method: str = Field(
        description="repayment_method из продукта FF: ANNUITY | EQUAL_INSTALMENTS | INSTALLMENT",
        examples=["INSTALLMENT"],
    )
    loan_type: Literal["installment", "credit"] = Field(
        default="installment",
        description="installment — рассрочка 0%, credit — кредит",
        examples=["installment"],
    )
    principal: Decimal = Field(description="Сумма займа", examples=[1500000])
    period: int = Field(description="Срок, месяцев", examples=[12])
    created_by: int = Field(description="ID пользователя (hunter)", examples=[42])


class CreateInstallmentApplicationResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [CREATE_APPLICATION_RESPONSE]},
    )

    id: int
    uuid: str
    reference_id: str
    status: str
    redirect_url: str | None = None
    redirect_qr: str | None = Field(
        default=None,
        description="QR-код redirect_url как data:image/svg+xml — рисуется у нас, ссылка не уходит наружу",
    )
    provider_code: str


class InstallmentApplicationResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [APPLICATION_RESPONSE]},
    )

    id: int
    client_request_id: int
    provider_code: str
    uuid: str | None = None
    reference_id: str | None = None
    product_id: str | None = None
    loan_type: str | None = None
    principal: Decimal | None = None
    period: int | None = None
    status: str
    approved_params: dict[str, Any] | None = None
    redirect_url: str | None = None
    created_by: int | None = None
    bank_id: int | None = None
    bank_name: str | None = None
    installment_product_id: int | None = None
    client_request_credit_detail_id: int | None = None
    credit_contract: str | None = None
    issued_at: datetime | None = None
    loan_amount: Decimal | None = Field(
        default=None, description="Сумма займа: одобренная банком, иначе запрошенная"
    )
    refund_type: str | None = Field(default=None, description="REFUND | PARTIAL_REFUND")
    refund_status: str | None = Field(
        default=None, description="REQUESTED — ждём банк; SUCCESS | DECLINE — ответ банка"
    )
    refund_amount: Decimal | None = Field(
        default=None, description="Сколько всего успешно возвращено в банк"
    )
    refund_requested_amount: Decimal | None = None
    refund_requested_by: int | None = None
    refund_requested_at: datetime | None = None
    refund_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApplyInstallmentApplicationRequest(BaseSchema):
    created_by: int = Field(description="ID пользователя (hunter)", examples=[42])


class ApplyInstallmentApplicationResponse(BaseSchema):
    application_id: int
    client_request_credit_detail_id: int


class AllowedBankResponse(BaseSchema):
    bank_id: int
    provider_product_id: str | None = None
    installment_product_id: int | None = None
    period: int | None = None
    principal_min: Decimal | None = None
    principal_max: Decimal | None = None
    repayment_method: str | None = None
    bank_code: str
    bank_name: str
    credit_program_id: int


class AllowedBankListResponse(BaseSchema):
    items: list[AllowedBankResponse]
    total: int


class FFWebhookPayload(BaseSchema):
    model_config = ConfigDict(
        extra="allow",
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={
            "examples": [WEBHOOK_APPROVED, WEBHOOK_REJECTED, WEBHOOK_ISSUED],
        },
    )

    status: str | None = Field(
        default=None,
        description="REJECTED | APPROVED | ALTERNATIVE | ISSUED (нет у хука о возврате)",
        examples=["APPROVED"],
    )
    uuid: str | None = Field(default=None, examples=["fc58a802-ce40-4c8b-9cff-9f11930e1702"])
    reference_id: str | None = Field(default=None, description="Наш id заявки", examples=["1"])
    lead_id: str | None = None
    approved_params: dict[str, Any] | None = None
    loan_type: str | None = Field(default=None, examples=["installment"])
    product: str | None = Field(default=None, examples=["SR_MOCK_FACT_12"])
    redirect_url: str | None = None
    alternative_reason: str | None = Field(default=None, examples=[""])


class CreateInstallmentRefundRequest(BaseSchema):
    refund_type: Literal["REFUND", "PARTIAL_REFUND"] = Field(
        default="REFUND",
        description="REFUND — весь остаток займа (расторжение); PARTIAL_REFUND — часть",
    )
    refund_amount: Decimal | None = Field(
        default=None,
        description="Сумма частичного возврата. Для REFUND не нужна — берётся остаток займа",
    )
    requested_by: int = Field(description="employee_id, кто запускает возврат", examples=[42])


class InstallmentRefundResponse(BaseSchema):
    id: int
    uuid: str
    refund_type: str
    refund_amount: Decimal
    refund_status: str = Field(
        description="REQUESTED — банк принял запрос; итог придёт хуком или сверкой"
    )
    loan_amount: Decimal
    refunded_before: Decimal
    bank_message: str | None = None


class InstallmentRefundWebhookPayload(BaseSchema):
    model_config = ConfigDict(extra="allow")

    uuid: str | None = None
    partner_id: str | None = None
    refund_type: str | None = None
    refund_amount: Decimal | None = None
    covlir_status: str | None = Field(default=None, description="SUCCESS | DECLINE")


class ReconcileResponse(BaseSchema):
    checked: int = Field(description="Сколько заявок взято на сверку")
    polled: int = Field(description="Сколько статусов запрошено у банка")
    changed: int = Field(description="У скольких заявок изменился статус")
    applied: int = Field(description="Сколько выданных заявок записано в сделку")
    expired: int = Field(description="Сколько заявок без ответа банка закрыто как неотправленные")
    refunds: int = Field(default=0, description="По скольким возвратам получен итог из банка")
    errors: int = Field(description="Сколько заявок не удалось сверить")


class WebhookAckResponse(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_alias=True,
        validate_by_name=True,
        json_schema_extra={"examples": [WEBHOOK_ACK_RESPONSE]},
    )

    ok: bool = Field(examples=[True])
    status: bool = Field(default=True, examples=[True])
