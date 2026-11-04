"""The calls of ``sign_tab`` two integrations make with the same parameters, as PDO sent them as text.

Only the calls. How one runs and what its failure means stays in the repository of the integration:
a callback answers MyNCA with the text of the error, a cron only logs it.
"""

from dataclasses import dataclass

from src.php_compat.pdo_values import pdo_text

_INT = "integer"
_UUID = "uuid"
# The signed document itself stays in MyNCA, sign_tab keeps a marker.
_SIGN_MARKER = "SIGN_CMS"
_PROCESS_FORMAT = "CMS"


@dataclass(frozen=True)
class SignTabCall:
    function: str
    args: tuple[str | None, ...]
    casts: tuple[str | None, ...]


def insert_sign_general_call(
    *,
    client_request_id: int | None,
    ds_id: int | None,
    client_request_document_id: int | None,
    sign_type_code: str,
    dn_name: str,
    source: str,
    sign_pid: int | None,
) -> SignTabCall:
    return SignTabCall(
        "client.insert_sign_general",
        (
            pdo_text(client_request_id),
            pdo_text(ds_id),
            pdo_text(client_request_document_id),
            sign_type_code,
            _SIGN_MARKER,
            _SIGN_MARKER,
            None,
            dn_name,
            source,
            pdo_text(sign_pid),
        ),
        (_INT, _INT, _INT, None, None, None, None, None, None, _INT),
    )


def sign_tab_modify_call(
    *,
    sign_id: object | None,
    sign_process_id: str,
    sign_group_id: str | None,
) -> SignTabCall:
    return SignTabCall(
        "client.sign_tab__modify",
        (pdo_text(sign_id), sign_process_id, sign_group_id, _PROCESS_FORMAT, None),
        (_INT, _UUID, _UUID, None, None),
    )


def id_by_process_call(sign_process_id: str) -> SignTabCall:
    """Returns a cursor."""
    return SignTabCall("client.sign_tab__id_by_process", (sign_process_id,), (_UUID,))
