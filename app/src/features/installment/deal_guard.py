"""Shared by installment and factoring: a credit product may only be opened on
the deal's main, client-confirmed client request. Anything else ends up with a
credit that CRM does not show (crm_deal_one_preset hides unconfirmed requests)
or that is later cancelled together with a non-main request."""

from typing import Any

from fastapi import HTTPException, status
from src.repository import BaseRepository

CLOSED_CLIENT_REQUEST_STATUSES = frozenset({"CANCEL", "TERMINATED"})

_DEAL_CLIENT_REQUEST_STATE_SQL = """
    SELECT
        s.status_code,
        d.crm_deal_id
    FROM client_request_tab cr
    LEFT JOIN client_request_status_tab s
        ON s.client_request_status_id = cr.client_request_status_id
    LEFT JOIN sale.crm_deal_tab d
        ON d.client_request_main_id = cr.client_request_id
    WHERE cr.client_request_id = $1
"""


async def fetch_deal_client_request_state(
    repository: BaseRepository, client_request_id: int
) -> dict[str, Any] | None:
    row = await repository.fetchrow(_DEAL_CLIENT_REQUEST_STATE_SQL, client_request_id)
    return dict(row) if row is not None else None


def check_deal_client_request_state(
    state: dict[str, Any] | None, client_request_id: int
) -> None:
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"client_request_id={client_request_id} was not found.",
        )
    if state.get("crm_deal_id") is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Заявка {client_request_id} не является основной заявкой сделки. "
                "Выберите пакет на сделке и оформляйте по нему."
            ),
        )
    status_code = state.get("status_code")
    if status_code is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Выбор клиента по заявке {client_request_id} не подтверждён "
                "(нет статуса заявки). Подтвердите выбор в конструкторе."
            ),
        )
    if status_code in CLOSED_CLIENT_REQUEST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Заявка {client_request_id} в статусе {status_code}: "
                "оформление по ней недоступно."
            ),
        )
