from fastapi import Response

from src.php_compat.responses import PHP_HTML_CONTENT_TYPE


def empty_cron_response() -> Response:
    """These PHP actions end with ``return 0`` and render nothing: 200 with an empty body."""
    return Response(content=b"", headers={"Content-Type": PHP_HTML_CONTENT_TYPE})
