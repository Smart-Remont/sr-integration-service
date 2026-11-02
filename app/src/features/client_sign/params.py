from dataclasses import dataclass

from fastapi import Request

from src.php_compat.text import php_strtoupper
from src.php_compat.zend_request import PhpParam, php_param_text, read_params


@dataclass(frozen=True)
class DocumentParams:
    """``_getParam`` of the document routes. ``uuid`` is what PDO binds for an array too."""

    uuid: str
    # An array in the redirect: Zend's ltrim gives an empty string.
    redirect_uuid: str
    # strtoupper of an array gives null.
    doc_type: str
    hash: str


@dataclass(frozen=True, slots=True)
class IntegrationDownloadParams:
    uuid: str
    for_view: str
    is_base64: bool


async def read_document_params(request: Request) -> DocumentParams:
    params = await read_params(request)
    uuid = params.get("uuid")
    doc_type = params.get("doc_type")
    return DocumentParams(
        uuid=php_param_text(uuid),
        redirect_uuid="" if isinstance(uuid, dict) else php_param_text(uuid),
        doc_type="" if isinstance(doc_type, dict) else php_strtoupper(php_param_text(doc_type)),
        hash=php_param_text(params.get("hash")),
    )


async def read_integration_download_params(
    request: Request,
    *,
    default_for_view: str,
) -> IntegrationDownloadParams:
    """``downloadDsAction`` and ``downloadAgreementAction``: their defaults differ."""
    params = await read_params(request)
    return IntegrationDownloadParams(
        uuid=php_param_text(params.get("uuid")),
        for_view=php_param_text(params.get("for_view")) or default_for_view,
        is_base64=bool(php_param_text(params.get("is_base64"))),
    )


async def read_list_hash(request: Request) -> str:
    return php_param_text((await read_params(request)).get("remont"))


async def read_callback_key(request: Request) -> PhpParam | None:
    return (await read_params(request)).address.get("key")
