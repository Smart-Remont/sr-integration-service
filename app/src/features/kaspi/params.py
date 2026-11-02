from dataclasses import dataclass
from enum import IntEnum

from fastapi import Request

from src.php_compat.json_codec import php_json_encode_default
from src.php_compat.zend_request import ZendParams, php_param_text, read_params

_CONTROLLER = "client"
_ACTION = "kaspi-request-pay"
_DEFAULT_NUMBER = "0"


class KaspiLogStatus(IntEnum):
    SUCCESS = 1
    ERROR = 2


@dataclass(frozen=True)
class KaspiParams:
    command: str | None
    txn_id: str
    txn_id_is_array: bool
    txn_date: str
    iin: str
    account: str
    amount: str
    request_json: str


@dataclass(frozen=True)
class KaspiLogEntry:
    command: str
    request_text: str
    response_xml: bytes
    account: str
    txn_id: str
    amount: str
    txn_date: str | None
    status: KaspiLogStatus


async def read_kaspi_params(request: Request) -> KaspiParams:
    params = await read_params(request)
    command = params.get("command")
    return KaspiParams(
        command=command if isinstance(command, str) else None,
        txn_id=_text(params, "txn_id"),
        txn_id_is_array=isinstance(params.get("txn_id"), dict),
        txn_date=_text(params, "txn_date"),
        iin=_text(params, "iin"),
        account=_text(params, "account", default=_DEFAULT_NUMBER),
        amount=_text(params, "sum", default=_DEFAULT_NUMBER),
        request_json=php_json_encode_default(
            params.all_params(controller=_CONTROLLER, action=_ACTION),
        ),
    )


def _text(params: ZendParams, name: str, *, default: str = "") -> str:
    # ``_getParam``: an empty value counts as missing and gives the default.
    return php_param_text(params.get(name)) or default
