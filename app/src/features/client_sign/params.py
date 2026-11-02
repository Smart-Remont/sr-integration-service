from dataclasses import dataclass

from fastapi import Request

from src.shared.php_text import php_strtoupper
from src.shared.zend_request import PhpParam, php_param_text, read_params


@dataclass(frozen=True)
class DocumentParams:
    """``_getParam`` of the document routes. ``uuid`` is what PDO binds for an array too."""

    uuid: str
    # An array in the redirect: Zend's ltrim gives an empty string.
    redirect_uuid: str
    # strtoupper of an array gives null.
    doc_type: str
    hash: str


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


async def read_list_hash(request: Request) -> str:
    return php_param_text((await read_params(request)).get("remont"))


async def read_callback_key(request: Request) -> PhpParam | None:
    return (await read_params(request)).address.get("key")
