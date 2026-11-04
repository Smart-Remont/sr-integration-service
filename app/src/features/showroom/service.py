"""PHP ``showroomInfoListAction``: the list goes to BI Group, and its answer back to the caller.

The answer is 200 whatever happened, as in PHP. Failures are seen only in the log of the service.
No record of the sending is kept: there is no table for it and PHP kept none.
"""

from fastapi import Response
from loguru import logger

from src.php_compat.responses import php_html_response

from .client import ShowroomClient, ShowroomRequestError
from .repo import ShowroomDatabase, ShowroomFunctionError


class ShowroomService:
    def __init__(self, database: ShowroomDatabase, client: ShowroomClient) -> None:
        self.database = database
        self.client = client

    async def send_list_to_bi_group(self) -> Response:
        body = await self._list_body()
        try:
            answer = await self.client.send_list(body)
        except ShowroomRequestError as exc:
            logger.error("showroom-info-list: no answer from BI Group: {error}", error=exc)
            return php_html_response()
        return php_html_response(answer)

    async def _list_body(self) -> bytes:
        try:
            value = await self.database.read_list()
        except ShowroomFunctionError as exc:
            # PHP does not look at the error and posts an empty body. BI Group sees this too.
            logger.error("showroom-info-list: list not read: {error}", error=exc)
            return b""
        return b"" if value is None else value.encode()
