"""OpenAPI metadata: service description and tag descriptions shown in Scalar."""

DESCRIPTION = (
    "HTTP-слой над функциями PostgreSQL для банковских и партнёрских интеграций Smart Remont. "
    "Бизнес-правила и валидация данных — в функциях БД (`asyncpg`, без ORM).\n\n"
    "| Тип API | Префикс | Auth | Ответ |\n"
    "|---|---|---|---|\n"
    "| Внутренний REST | `/api/v1/...` | Basic, для прокси CRM ещё JWT `X-Integration-Context` "
    "| Pydantic-схемы, обычные HTTP-коды |\n"
    "| BIG Integration | `/api/big_integration/...` | Basic | конверт `{data, response, error}` |\n"
    "| Входящие вебхуки | `.../webhook*`, `.../sign-callback` | проверка в обработчике | см. операцию |\n"
)

TAGS = [
    {
        "name": "Installment (Freedom Finance)",
        "description": (
            "Онлайн-рассрочка Freedom Finance: каталог банковских продуктов, создание и опрос "
            "заявок, входящий webhook о решении банка, автоматический и ручной apply к сделке."
        ),
    },
    {
        "name": "Factoring (Freedom Finance)",
        "description": (
            "Факторинг Freedom Finance: заявки, прескоринг, печатные формы и подпись через MyNCA, "
            "цессия, входящие webhooks о статусе и возврате."
        ),
    },
    {
        "name": "Big Integration",
        "description": (
            "API ДДУ для BI Group (боевые маршруты, nginx office → этот сервис). Тело запроса — сырой "
            "JSON без Pydantic, проверки в функции БД. Ответ — конверт "
            '`{"data", "response", "error"}`: `data` — первая строка refcursor как есть, ошибка '
            "функции БД — HTTP 500 и `error.message`. Контракт заморожен: "
            "`app/tests/big_integration/`."
        ),
    },
    {
        "name": "File Storage",
        "description": (
            "Загрузка файлов в MinIO (S3). Контракт `{filename, path, ext}` совместим с "
            "myspace `file_store()`. Неизвестный `mode` → HTTP 400."
        ),
    },
    {
        "name": "Health",
        "description": "`/health` — процесс жив (liveness), `/health/ready` — есть связь с БД.",
    },
]
