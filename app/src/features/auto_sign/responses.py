from fastapi import Response

from src.php_compat.responses import php_html_response


def empty_cron_response() -> Response:
    """These PHP actions end with ``return 0`` and render nothing: 200 with an empty body."""
    return php_html_response()
