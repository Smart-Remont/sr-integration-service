"""Answers as PHP ``buhAction`` and ``response_json`` gave them."""

from fastapi import FastAPI, Request, Response, status

from src.php_compat.json_codec import php_json_encode_default, php_json_encode_pretty
from src.php_compat.pdo_errors import php_clean_error
from src.php_compat.responses import PHP_REST_CONTENT_TYPE, PHP_WWW_AUTHENTICATE

# ``response_json`` replaces the type of the controller, the other answers keep it.
RESPONSE_JSON_CONTENT_TYPE = "application/json"
_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate, max-age=0",
    # The session of ``ParentController::init``.
    "Expires": "Thu, 19 Nov 1981 08:52:00 GMT",
    "Pragma": "no-cache",
}


class Buh1cUnauthorizedError(Exception):
    pass


class Buh1cFunctionError(Exception):
    """A failed call, the case when PHP ``scalarSP`` gives ``status`` false.

    Holds the PDO message: it can quote the body of the request, so it does not go to the log.
    """

    def __init__(self, pdo_message: str, *, state: str) -> None:
        self.pdo_message = pdo_message
        self.state = state
        self.text = php_clean_error(pdo_message)
        super().__init__(state)

    def php_result_json(self) -> str:
        """``json_encode`` of the result array of ``scalarSP``, which PHP writes to the journal."""
        return php_json_encode_default(
            {
                "value": None,
                "error": self.text,
                "error_debug": self.pdo_message,
                "status": False,
            }
        )


def action_response(content: bytes = b"", *, status_code: int = status.HTTP_200_OK) -> Response:
    return Response(
        content=content,
        status_code=status_code,
        headers={"Content-Type": PHP_REST_CONTENT_TYPE, **_HEADERS},
    )


def value_response(value: str | None) -> Response:
    """``response_json`` on a success: the value as it is, nothing for null."""
    return Response(
        content=b"" if value is None else value.encode(),
        headers={"Content-Type": RESPONSE_JSON_CONTENT_TYPE, **_HEADERS},
    )


def function_error_response(exc: Buh1cFunctionError) -> Response:
    """``response_json`` on an error."""
    return Response(
        content=php_json_encode_pretty({"error": exc.text}),
        status_code=status.HTTP_400_BAD_REQUEST,
        headers={"Content-Type": RESPONSE_JSON_CONTENT_TYPE, **_HEADERS},
    )


def not_moved_response(what: str) -> Response:
    """No PHP behind this one: the caller must see that nothing was done, not an empty success."""
    message = f"what={what} не перенесён в сервис, вызов ничего не сделал"
    return Response(
        content=php_json_encode_pretty({"error": message}),
        status_code=status.HTTP_410_GONE,
        headers={"Content-Type": RESPONSE_JSON_CONTENT_TYPE, **_HEADERS},
    )


def register_buh_1c_handlers(app: FastAPI) -> None:
    async def unauthorized(_: Request, __: Buh1cUnauthorizedError) -> Response:
        response = action_response(status_code=status.HTTP_401_UNAUTHORIZED)
        response.headers["WWW-Authenticate"] = PHP_WWW_AUTHENTICATE
        return response

    app.add_exception_handler(Buh1cUnauthorizedError, unauthorized)
