from fastapi import Response

NO_MESSAGES_TEXT = "No messages to send."
NO_BATCHES_TEXT = "No batches to check."
DONE_TEXT = "0"
# Not in PHP, which had no lock and sent the queue a second time.
SEND_RUN_BUSY_TEXT = "Previous run is not finished."
_CONTENT_TYPE = "text/html; charset=UTF-8"


def cron_text_response(text: str) -> Response:
    return Response(content=text.encode(), headers={"Content-Type": _CONTENT_TYPE})
