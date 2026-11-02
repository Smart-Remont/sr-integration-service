from fastapi import FastAPI, Request, Response

from src.shared.php_db_errors import php_clean_error, php_pdo_message

from .responses import kaspi_xml_response


class KaspiRefusalError(Exception):
    """A refusal Kaspi receives as XML with code 200.

    It is an exception so that the request transaction rolls back before the answer goes out.
    """

    def __init__(self, response_xml: bytes) -> None:
        self.response_xml = response_xml
        super().__init__("Kaspi request refused")


class KaspiDatabaseError(Exception):
    """What ``execSP`` and ``getSP`` put into ``error`` and ``error_debug``."""

    def __init__(self, pdo_message: str) -> None:
        self.pdo_message = pdo_message
        self.message = php_clean_error(pdo_message)
        super().__init__(self.message)

    @classmethod
    def from_exception(cls, exc: BaseException) -> KaspiDatabaseError:
        return cls(php_pdo_message(exc))


def register_kaspi_handlers(app: FastAPI) -> None:
    async def refusal(_: Request, exc: KaspiRefusalError) -> Response:
        return kaspi_xml_response(exc.response_xml)

    app.add_exception_handler(KaspiRefusalError, refusal)
