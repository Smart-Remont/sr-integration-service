"""``ClientSignController::signDocListAction``: the page a client opens by the hash in a link."""

from collections.abc import Awaitable, Callable

from fastapi import Response
from loguru import logger

from src.php_compat.responses import php_html_response

from .errors import ClientSignDatabaseError
from .repo import ClientSignDatabase, ClientSignRepository
from .views import OLD_SIGN_DOC_LIST_PAGE, render_sign_doc_list, render_sign_doc_list_error


class DocumentListService:
    def __init__(self, database: ClientSignDatabase) -> None:
        self._database = database

    async def sign_doc_list(self, request_hash: str) -> Response:
        """A failed read after the first one leaves its part of the page empty, as in PHP."""
        try:
            request_id = await self._database.step(
                lambda repository: repository.client_request_id_by_hash(request_hash)
            )
        except ClientSignDatabaseError:
            logger.warning("client-sign list: hash lookup failed")
            return php_html_response(render_sign_doc_list_error("Заявка не найдена"))
        client_info = await self._read_or(
            None, lambda repository: repository.client_info(request_id)
        )
        documents = await self._read_or(
            [], lambda repository: repository.request_documents(request_id)
        )
        deleted = await self._read_or(
            [], lambda repository: repository.deleted_signatures(request_id)
        )
        return php_html_response(
            render_sign_doc_list(
                request_hash=request_hash,
                client_info=client_info,
                documents=documents,
                deleted_signatures=deleted,
            )
        )

    async def _read_or[T](
        self,
        default: T,
        read: Callable[[ClientSignRepository], Awaitable[T]],
    ) -> T:
        try:
            return await self._database.step(read)
        except ClientSignDatabaseError:
            logger.warning("client-sign list: a read failed, its part of the page stays empty")
            return default


def old_sign_doc_list() -> Response:
    """``ClientController::signDocListAction`` returns at its first line: the page has no data."""
    return php_html_response(OLD_SIGN_DOC_LIST_PAGE)
