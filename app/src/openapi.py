"""OpenAPI metadata: service description and tag descriptions shown in Scalar."""

DESCRIPTION = (
    "HTTP-слой над функциями PostgreSQL для банковских и партнёрских интеграций Smart Remont. "
    "Бизнес-правила и валидация данных — в функциях БД (`asyncpg`, без ORM).\n\n"
    "| Тип API | Префикс | Auth | Ответ |\n"
    "|---|---|---|---|\n"
    "| Внутренний REST | `/api/v1/...` | Basic, для прокси CRM ещё JWT `X-Integration-Context` "
    "| Pydantic-схемы, обычные HTTP-коды |\n"
    "| BIG Integration | `/api/big_integration/...` | Basic | конверт `{data, response, error}` |\n"
    "| Kaspi (перенос из PHP) | `/api/kaspi/...` | заголовок от nginx, адреса Kaspi в nginx "
    "| XML как в PHP |\n"
    "| SMS Kcell (перенос из PHP) | `/api/sms/...` | Basic, вызывает крон | текст как в PHP |\n"
    "| Подпись документов клиентом (перенос из PHP) | `/api/client_sign/...` "
    "| hash или uuid в ссылке, колбэк по ключу в адресе | HTML, файл или JSON в формате PHP |\n"
    "| Входящие вебхуки | `.../webhook*`, `.../sign-callback` | проверка в обработчике | см. операцию |\n"
)

TAGS = [
    {
        "name": "Client documents signing",
        "description": (
            "Страницы подписи договора и ДС клиентом, колбэк MyNCA и скачивание документов. "
            "Перенос из PHP `ClientSignController`. Побайтно сверены с PHP на стенде: страница со "
            "списком и неподписанными карточками, старый адрес, ошибки скачивания, переход "
            "`AGREEMENT_MANUAL`, ненайденные документы `download-agreement` и `download-ds`. "
            "Вживую не сверялись, только тесты: карточки с подписями и отозванные, `perform-sign`, "
            "колбэк, файлы через MyNCA и партнёра. Намеренно отличается от PHP: колбэку нужен ключ "
            "в адресе, а неподписанный документ в `download-agreement` и `download-ds` отвечает "
            "503, потому что в сервисе нет LibreOffice."
        ),
    },
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
            "`app/tests/big_integration/`. Маршруты, перенесённые из PHP позже, отдают numeric и "
            "даты текстом, как PHP."
        ),
    },
    {
        "name": "Kaspi",
        "description": (
            "Оплата заявок через Kaspi, перенос из PHP `ClientController`. Ответ совпадает с PHP побайтно, "
            "включая ошибки в XML с кодом 200. Kaspi запросы не подписывает: маршрут "
            "доступен только через nginx: там список адресов Kaspi и заголовок "
            "`X-Kaspi-Proxy-Secret`."
        ),
    },
    {
        "name": "SMS (Kcell)",
        "description": (
            "Кроны отправки SMS и чтения статусов, перенос из PHP `IntegrationController`. Запросы к "
            "Kcell и ответы совпадают с PHP. Вызов этих адресов отправляет настоящие SMS из очереди."
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
        "name": "KL Recognition (BI Group)",
        "description": (
            "Распознавание квартирных листов BI Group: по GUID квартиры — помещения с площадями "
            "и статус `auto`/`manual` по сверке суммы с листом и площадью BI. Читает Composer "
            "через Cursor SDK; без БД."
        ),
    },
    {
        "name": "Health",
        "description": "`/health` — процесс жив (liveness), `/health/ready` — есть связь с БД.",
    },
]
