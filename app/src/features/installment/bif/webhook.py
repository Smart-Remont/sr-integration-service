"""Statuses BI Finance sends to POST /webhook/bif (ТЗ, раздел 4).

Answers the bank reads: 200 — taken (a repeat too), 404 {"code": "APP_NOT_FOUND"} — unknown appId,
400 — bad body, 401 — bad login, 503 — our login is not configured (the bank repeats 5xx).
"""

import json
from typing import Any

from fastapi import HTTPException, status
from loguru import logger
from pydantic import ValidationError

from ..common import check_webhook_login, extract_error_message, log_event
from ..constants import BIFStatus, InstallmentSource, ProviderCode
from ..repo import InstallmentRepository
from ..schemas import BIFWebhookPayload, WebhookAckResponse


class BIFApplicationNotFoundError(Exception):
    """appId we never got: the bank does not repeat after 404."""


class BIFWebhookService:
    def __init__(self, repository: InstallmentRepository) -> None:
        self.repository = repository

    async def handle_status(
        self, body: bytes, *, authorization_header: str | None
    ) -> WebhookAckResponse:
        await self._check_sender(authorization_header)
        hook = await self._parse(body)

        application = await self.repository.find_application_by_uuid(ProviderCode.BIF, hook.app_id)
        if application is None:
            await log_event(
                self.repository,
                "WEBHOOK_ORPHAN",
                source=InstallmentSource.BIF,
                payload={"app_id": hook.app_id, "status": hook.status},
            )
            raise BIFApplicationNotFoundError(hook.app_id)

        result = await self.repository.committed(
            lambda repo: repo.update_application_from_bif(
                application_id=application.id,
                status=hook.status,
                approved_params=approved_params(hook),
                comment=hook.comment,
            )
        )
        await log_event(
            self.repository,
            "STATUS_UPDATED" if result.get("changed") else "HOOK_DUPLICATE",
            installment_id=application.id,
            source=InstallmentSource.BIF,
            payload={"hook": hook.model_dump(mode="json", by_alias=True), "result": result},
        )
        if result.get("status") == BIFStatus.ISSUED:
            await self._apply_to_deal(application.id)
        return WebhookAckResponse(ok=True, status=True)

    async def _check_sender(self, authorization_header: str | None) -> None:
        credentials = await self.repository.get_provider_webhook_credentials(code=ProviderCode.BIF)
        if credentials is None:
            await log_event(
                self.repository,
                "WEBHOOK_AUTH_FAILED",
                source=InstallmentSource.BIF,
                payload={"error": "webhook credentials are not configured"},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="BI Finance webhook credentials are not configured.",
            )
        try:
            check_webhook_login(credentials, authorization_header)
        except HTTPException as exc:
            await log_event(
                self.repository,
                "WEBHOOK_AUTH_FAILED",
                source=InstallmentSource.BIF,
                payload={"error": extract_error_message(exc)},
            )
            raise

    async def _parse(self, body: bytes) -> BIFWebhookPayload:
        try:
            return BIFWebhookPayload.model_validate(json.loads(body))
        except (ValueError, ValidationError) as exc:
            error = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
            await log_event(
                self.repository,
                "WEBHOOK_BAD_REQUEST",
                source=InstallmentSource.BIF,
                payload={"error": error, "body": body[:2000].decode("utf-8", "replace")},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Ожидается JSON {appId, status: APPROVED|ISSUED|REJECTED|CANCELLED, "
                    "approvedConditions?, comment?}."
                ),
            ) from exc

    async def _apply_to_deal(self, application_id: int) -> None:
        """Put the issued credit into the deal, as for Freedom. Own transaction: a refusal of the
        deal (signed documents, status) must not undo the bank's status. A refusal is logged and
        the manager applies it by hand (POST /applications/{id}/apply)."""
        application = await self.repository.get_application_by_id(application_id)
        if (
            application is None
            or application.status != BIFStatus.ISSUED
            or application.client_request_credit_detail_id is not None
            or application.created_by is None
        ):
            return
        created_by = application.created_by
        try:
            credit_detail_id = await self.repository.committed(
                lambda repo: repo.apply_application_to_deal(
                    application_id=application_id, created_by=created_by
                )
            )
        except Exception as exc:
            logger.warning(
                "BIF auto-apply failed | application_id={id} error={error}",
                id=application_id,
                error=extract_error_message(exc),
            )
            await log_event(
                self.repository,
                "AUTO_APPLY_FAILED",
                installment_id=application_id,
                source=InstallmentSource.INSTALLMENT,
                payload={"error": extract_error_message(exc)},
            )
            return
        await log_event(
            self.repository,
            "AUTO_APPLY",
            installment_id=application_id,
            source=InstallmentSource.INSTALLMENT,
            payload={"client_request_credit_detail_id": credit_detail_id},
        )


def approved_params(hook: BIFWebhookPayload) -> dict[str, Any] | None:
    """approvedConditions as the bank sends them (amount, termMonths, rate, monthlyPayment) and the
    comment; ``principal`` / ``period`` repeat amount / termMonths for the deal and the loan amount
    (installment__loan_amount reads ``principal``)."""
    params: dict[str, Any] = dict(hook.approved_conditions or {})
    amount = params.get("amount")
    if isinstance(amount, int | float) and not isinstance(amount, bool) and amount > 0:
        params["principal"] = amount
    term = params.get("termMonths")
    if isinstance(term, int) and not isinstance(term, bool) and term > 0:
        params["period"] = term
    if hook.comment is not None and hook.comment.strip():
        params["comment"] = hook.comment.strip()
    return params or None
