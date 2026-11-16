from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .schemas import BiBadgeErrorResponse


class BiBadgeError(Exception):
    """A refusal the form shows to the client.

    An exception and not an answer, so that the request transaction rolls back. ``field`` names the
    form field to mark, or is None for a message over the whole form.
    """

    def __init__(self, message: str, *, status_code: int, field: str | None = None) -> None:
        self.message = message
        self.status_code = status_code
        self.field = field
        super().__init__(message)


def register_bi_badge_handlers(app: FastAPI) -> None:
    async def refused(_: Request, exc: BiBadgeError) -> JSONResponse:
        body = BiBadgeErrorResponse(field=exc.field, message=exc.message)
        return JSONResponse(body.model_dump(), status_code=exc.status_code)

    app.add_exception_handler(BiBadgeError, refused)
