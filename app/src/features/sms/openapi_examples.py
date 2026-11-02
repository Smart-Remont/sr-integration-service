"""Scalar examples. The texts are the real PHP answers."""

from .responses import DONE_TEXT, NO_BATCHES_TEXT, NO_MESSAGES_TEXT, SEND_RUN_BUSY_TEXT


def _text_examples(**texts: str) -> dict[str, object]:
    return {"text/html": {"examples": {name: {"value": text} for name, text in texts.items()}}}


_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"description": "Нет учётки Basic или она неверная"},
    503: {
        "description": (
            "Не заданы `SMS_CRON_*` или адрес и учётка Kcell для этого шага, либо нет соединения с БД"
        ),
    },
}
SMS_NOTIFY_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "`0` — запуск окончен, в том числе при сбое Kcell на пачке. Сбой записи результата "
            "останавливает запуск. Сообщения без записанного `batch_id` уйдут при следующем запуске."
        ),
        "content": _text_examples(
            done=DONE_TEXT,
            empty=NO_MESSAGES_TEXT,
            busy=SEND_RUN_BUSY_TEXT,
        ),
    },
    **_AUTH_RESPONSES,
}
SMS_NOTIFY_STATUS_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "`0` — статусы запрошены, в том числе при сбоях на отдельных пачках.",
        "content": _text_examples(done=DONE_TEXT, empty=NO_BATCHES_TEXT),
    },
    **_AUTH_RESPONSES,
}
