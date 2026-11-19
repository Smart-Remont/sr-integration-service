from fastapi import Response

from src.php_compat.responses import php_html_response

NO_MESSAGES_TEXT = "No messages to send."
NO_BATCHES_TEXT = "No batches to check."
DONE_TEXT = "0"
# Not in PHP, which had no lock: the next run sent the queue a second time.
RUN_BUSY_TEXT = "Previous run is not finished."


def cron_text_response(text: str) -> Response:
    return php_html_response(text.encode())
