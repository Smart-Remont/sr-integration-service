from fastapi import Response

from src.php_compat.responses import PHP_HTML_CONTENT_TYPE

NO_MESSAGES_TEXT = "No messages to send."
NO_BATCHES_TEXT = "No batches to check."
DONE_TEXT = "0"
# Not in PHP, which had no lock and sent the queue a second time.
SEND_RUN_BUSY_TEXT = "Previous run is not finished."


def cron_text_response(text: str) -> Response:
    return Response(content=text.encode(), headers={"Content-Type": PHP_HTML_CONTENT_TYPE})
