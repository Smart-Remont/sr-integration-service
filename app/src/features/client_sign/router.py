"""PHP ``ClientSignController`` and the document routes of two other controllers.

Any HTTP method and Zend address form, as in PHP. These pages have no login: the hash or uuid in
the address, an md5 with the database secret, is the caller check (CLAUDE.md, technical debt).
"""

from fastapi import APIRouter, Request, Response

from src.php_compat.zend_request import php_route

from .deps import (
    DocumentListServiceDep,
    DownloadServiceDep,
    SignCallbackServiceDep,
    SigningServiceDep,
)
from .doc_types import DocumentType
from .document_list import old_sign_doc_list
from .openapi_examples import (
    CALLBACK_RESPONSES,
    FILE_RESPONSES,
    INTEGRATION_FILE_RESPONSES,
    PAGE_RESPONSES,
    PERFORM_SIGN_RESPONSES,
)
from .params import read_callback_key, read_document_params, read_list_hash

router = APIRouter(tags=["Client documents signing"])

_SIGN_FUNCTIONS = "Документ по uuid: `ds.ds_get_by_uuid`, `public.client_request_get_by_uuid`, `public.otbasy_statement_get_by_uuid`."


@php_route(
    router,
    "/sign-doc-list",
    method="GET",
    summary="Страница документов клиента по hash",
    description=(
        "`/client-sign/sign-doc-list/remont/{hash}`. Открывает клиент по ссылке. Функции БД: "
        "`client.sign_client_request_id_get`, `public.get_client_info`, "
        "`client.sign_request_doc_read`, `client.sign_deleted_read`."
    ),
    responses=PAGE_RESPONSES,
    response_class=Response,
)
async def sign_doc_list(request: Request, service: DocumentListServiceDep) -> Response:
    return await service.sign_doc_list(await read_list_hash(request))


@php_route(
    router,
    "/old-sign-doc-list",
    method="GET",
    summary="Старый адрес страницы документов",
    description=(
        "`/client/sign-doc-list`. В PHP действие заканчивается в первой строке, поэтому страница "
        "всегда одна и та же, без данных. БД не вызывает."
    ),
    responses=PAGE_RESPONSES,
    response_class=Response,
)
async def old_sign_doc_list_page() -> Response:
    return old_sign_doc_list()


@php_route(
    router,
    "/perform-sign",
    method="GET",
    summary="Начать подписание документа через MyNCA",
    description=(
        "`/client-sign/perform-sign/uuid/{uuid}/doc_type/{DS|AGREEMENT|OTBASY_STATEMENT}/hash/{hash}`. "
        f"Создаёт процесс в MyNCA и переводит на него. {_SIGN_FUNCTIONS}"
    ),
    responses=PERFORM_SIGN_RESPONSES,
    response_class=Response,
)
async def perform_sign(request: Request, service: SigningServiceDep) -> Response:
    return await service.perform_sign(await read_document_params(request))


@php_route(
    router,
    "/callback",
    method="POST",
    summary="Колбэк MyNCA: подпись поставлена",
    description=(
        "Вызывает MyNCA по `back_url`, в адресе ключ `key/{hmac}`, который выдал `perform-sign`. "
        "Записывает подпись один раз. Повтор по тому же документу отвечает успехом, по другому "
        "документу отказом. Функции БД: "
        "`client.insert_sign_general`, `client.sign_tab__modify`, `client.sign_tab__id_by_process`. "
        "ИИН подписанта проверяет `client.insert_sign_general`."
    ),
    responses=CALLBACK_RESPONSES,
    response_class=Response,
)
async def sign_callback(request: Request, service: SignCallbackServiceDep) -> Response:
    return await service.handle(await request.body(), await read_callback_key(request))


@php_route(
    router,
    "/download",
    method="GET",
    summary="Скачать документ",
    description=(
        "`/client-sign/download/uuid/{uuid}/doc_type/{type}`. Подписанный отдаёт как "
        f"`download-signed`, неподписанный конвертирует через MyNCA. {_SIGN_FUNCTIONS}"
    ),
    responses=FILE_RESPONSES,
    response_class=Response,
)
async def download(request: Request, service: DownloadServiceDep) -> Response:
    return await service.download(await read_document_params(request))


@php_route(
    router,
    "/download-signed",
    method="GET",
    summary="Скачать подписанный PDF",
    description=(
        "`/client-sign/download-signed/uuid/{uuid}/doc_type/{type}`. MyNCA по `sign_group_id`, "
        f"без него партнёрский бэкенд. {_SIGN_FUNCTIONS}"
    ),
    responses=FILE_RESPONSES,
    response_class=Response,
)
async def download_signed(request: Request, service: DownloadServiceDep) -> Response:
    return await service.download_signed(await read_document_params(request))


@php_route(
    router,
    "/download-cms",
    method="GET",
    summary="Скачать CMS подписи",
    description=f"`/client-sign/download-cms/uuid/{{uuid}}/doc_type/{{type}}`. {_SIGN_FUNCTIONS}",
    responses=FILE_RESPONSES,
    response_class=Response,
)
async def download_cms(request: Request, service: DownloadServiceDep) -> Response:
    return await service.download_cms(await read_document_params(request))


@php_route(
    router,
    "/download-agreement",
    method="GET",
    summary="Скачать договор, старый адрес",
    description="`/integration/download-agreement/uuid/{uuid}`. `public.client_request_get_by_uuid`.",
    responses=INTEGRATION_FILE_RESPONSES,
    response_class=Response,
)
async def download_agreement(request: Request, service: DownloadServiceDep) -> Response:
    params = await read_document_params(request)
    return await service.integration_download(DocumentType.AGREEMENT, params.uuid)


@php_route(
    router,
    "/download-ds",
    method="GET",
    summary="Скачать ДС, старый адрес",
    description="`/integration/download-ds/uuid/{uuid}`. `ds.ds_get_by_uuid`.",
    responses=INTEGRATION_FILE_RESPONSES,
    response_class=Response,
)
async def download_ds(request: Request, service: DownloadServiceDep) -> Response:
    params = await read_document_params(request)
    return await service.integration_download(DocumentType.DS, params.uuid)
