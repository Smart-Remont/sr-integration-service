# CLAUDE.md — integrations-sr

Единственный файл с правилами репозитория для людей и ИИ-агентов. [AGENTS.md](AGENTS.md) ссылается сюда, [README.md](README.md) — только запуск.

Сервис интеграций Smart Remont: банки, партнёры, застройщики. FastAPI + `asyncpg` поверх функций PostgreSQL, без ORM. Сейчас здесь: Freedom Finance (рассрочка и факторинг), MyNCA (подписи), BIG Integration (API ДДУ для BI Group), загрузка файлов в MinIO, распознавание квартирных листов BI (`kl_recognition`: Composer через Cursor SDK, помощник на Node в `composer_sdk/`, без БД). Интеграций будет много, поэтому каждая новая строится по одним правилам ниже. Отступление от правил — только с записью в «Технический долг».

Общие правила Smart Remont (базы, SQL, ветки других репо, агенты) — `../myspace/CLAUDE.md`. Агент для этого репо — `smart-remont:integrations-dev`. Состояние и выкатка Freedom — `../myspace/features/freedom-factoring/STATUS.md` и `../myspace/sql/freedom-factoring/PROD_APPLY_TODO.md`. Локальные папки `sql/`, `agent-memory/`, `docs/` в `.gitignore`: это ссылки на `../myspace/sql/` и `../myspace/features/`, новые файлы кладутся туда.

## Боевые маршруты BIG — контракт заморожен

nginx `office.smartremont.kz` (PHP) отправляет сюда только эти адреса. BI Group разбирает ответы программно.

| office.smartremont.kz | Сервис | Функция БД |
|---|---|---|
| `/fastapi/healthcheck` | `GET /health` | — |
| `/integration/request-create-v3` | `POST /api/big_integration/request-create-v3` | `rest.ddu__create_request_v2` → `rest.ddu__request_get` |
| `/integration/request-event-v3` | `POST /api/big_integration/request-event-v3` | `rest.ddu__request_event_v3` → `rest.ddu__request_get` |
| `/integration/repair-pack-prices` | `POST /api/big_integration/repair-pack-prices` | `rest.ddu_repair_pack_info__get` |
| `/integration/ddu-flat-info-multiple` | `POST /api/big_integration/ddu-flat-info-multiple` | `rest.ddu_flat_info_multiple` |
| `/integration/ddu-resident-agreement-status` | `POST /api/big_integration/ddu-resident-agreement-status` | `rest.ddu_resident_agreement__log_insert` |

Не меняются: HTTP-коды, ключи конверта `{data, response, error}`, тексты ошибок, типы значений (`Decimal` → число, дата → ISO-строка). Всё это закреплено в `app/tests/big_integration/test_contract.py` и `app/tests/test_app.py`; эти тесты проходят и на коде `main`. Упавший тест здесь означает поломку у BI Group, а не повод поправить тест. Новый маршрут BIG добавляется отдельной папкой и не меняет общие `responses.py`, `http.py`, `errors.py`.

## Ветки

- `main` — бой. В `main` только через PR.
- `dev` — работа и тестовый стенд `devintegration.smart-remont.kz`. Фича: ветка от `main` → PR в `main`; в `dev` то, что сейчас проверяется на стенде.
- `legacy-php-cutover` — архив: перенос остального PHP `IntegrationController` (оплаты Kaspi/Сбер/CloudPayments, SMS, подписи Aitu/MyNCA, лиды, кроны, BI). Не мержить: колбэки оплат и подписей без проверки подписи отправителя, проверка IP Kaspi по `X-Forwarded-For`, ошибки, которые молча откатывают записи. Переносить оттуда можно по одной интеграции, переписав по правилам этого файла.

## Команды

```bash
uv sync                                  # зависимости, включая dev (pytest, ruff)
cp app/.env.example app/.env             # и заполнить
cd app && uv run uvicorn src.main:app --reload   # локально, http://127.0.0.1:8000/scalar при API_DOCS_ENABLED=true
uv run pytest                            # все тесты (из корня)
uv run ruff check . && uv run ruff format --check .
docker compose up --build                # как на сервере: gunicorn, порт 127.0.0.1:9020
```

Перед каждым коммитом: `ruff check`, `ruff format --check`, `pytest` — без ошибок. То же гоняет CI (`.github/workflows/ci.yml`).

## Устройство

```text
app/
├── run_main.py, _gunicorn/      запуск gunicorn (настройки из env GUNICORN_*)
└── src/
    ├── main.py                  приложение, lifespan, /health, /health/ready, Scalar
    ├── openapi.py               описание сервиса и тегов для Scalar
    ├── config.py                всё чтение env (кроме БД)
    ├── database/                пул asyncpg, DatabaseConnectionDep (транзакция на запрос)
    ├── repository/base.py       BaseRepository: call_sp(), committed(), scalar_from_sp_rows()
    ├── exceptions/              ошибки БД → HTTP (409/422/500), текст из RAISE '{…}'
    ├── security/basic_auth.py   basic_auth_dependency() — единственная реализация Basic Auth
    ├── integration_context/     JWT X-Integration-Context от прокси CRM (сотрудник, scope, заявка)
    ├── shared/                  общая логика нескольких интеграций (deal_guard)
    ├── storage/                 MinIO: режимы, пути /documents/..., загрузка
    ├── schema/base.py           BaseSchema для Pydantic-моделей /api/v1
    ├── routers/                 сборка URL: /api + /v1 | /big_integration
    └── features/<интеграция>/   одна папка — одна интеграция
```

Путь запроса: `router` → `service` → `repository` (`call_sp`) и `client` (внешний HTTP). Ответ: Pydantic-модель или конверт BIG.

## Правила кода

### Слои
- `router.py` — только HTTP: аргументы, зависимости auth, вызов сервиса, `response_model`. Ни SQL, ни бизнес-правил, ни внешних вызовов.
- `service.py` — порядок шагов и правила, которых нет в БД. Не знает про `Request`.
- `repo.py` — только `call_sp(...)`. Один метод — одна функция БД. Без условий и форматирования ответа.
- `client.py` — внешний HTTP одного партнёра. Без доступа к БД.
- `deps.py` — сборка сервиса из соединения, клиента и конфига.
- Интеграция не импортирует другую интеграцию (`features/a` → `features/b` запрещено). Общий код — в `src/shared/` или в инфраструктурных пакетах выше.

### Данные и транзакции
- Данные — только через функции БД (`call_sp`). Новый SQL по таблицам в Python не пишется. Новая или изменённая функция — файл в `../myspace/sql/<фича>/` с номером и строкой в выкатке фичи; в этом репо `.sql` не хранится. Мутирующий SQL накатывает пользователь.
- Один запрос — одна транзакция (`DatabaseConnectionDep`). Ошибка должна уйти исключением: тогда транзакция откатывается. Нельзя ловить ошибку после записи в БД и возвращать ответ с ошибкой: такая транзакция зафиксируется наполовину, а после ошибки БД — молча откатится целиком.
- `committed()` — только для записи, которая должна пережить откат запроса: журнал событий или состояние, которое внешняя система уже знает (банк принял заявку, MyNCA создал подпись). Любое другое использование — ошибка.
- `set_config` в `call_sp` действует только внутри транзакции: сотрудник одного запроса не попадает в аудит другого.
- Вызов функции, которая возвращает значение, — `scalar_from_sp_rows(...)`; refcursor — `call_sp(..., cursor=True)`; JSON-аргумент — `json.dumps(payload)`.

### Безопасность
- У каждого маршрута есть проверка вызывающего: Basic через `basic_auth_dependency(config, api_name=...)`, для запросов из CRM ещё `IntegrationContextDep` с проверкой scope и заявки. Свою реализацию Basic Auth не писать.
- Входящий вебхук проверяет отправителя: учётку или подпись из `integration_credential_tab`, проверку подписи ЭЦП и ИИН подписанта. Колбэк, который верит телу запроса без проверки, не принимается.
- Нет учётных данных в конфиге — ответ 503, а не пропуск без проверки.
- Сравнение секретов — `secrets.compare_digest` / `bcrypt.checkpw`.
- Секреты и адреса партнёров: банки — `integration_provider_tab` / `integration_credential_tab` (отдельно stage и prod через `APP_ENV`), остальное — env через `config.py`. В коде ни паролей, ни токенов, ни боевых URL по умолчанию для секретов.
- TLS не отключать (`verify=False` запрещён). IP клиента — только `request.client.host`; заголовкам `X-Forwarded-For` не доверять.
- Отключить проверку безопасности для теста можно только с пометкой `IMPORTANT!` в коде и блокером в выкатке фичи.
- В логах нет ИИН, телефонов, токенов, тел документов.

### Внешние HTTP-вызовы
- `httpx.AsyncClient` с явным таймаутом. Больше 30 с — только с причиной в комментарии (как конвертация документа в MyNCA).
- Ошибка партнёра превращается в исключение клиента этой интеграции; исходное тело ответа сохраняется в журнале событий и в тексте ошибки.
- Каждое обращение к партнёру, которое меняет состояние, пишется в журнал событий интеграции через `committed()`.
- Повтор вызова не должен создавать дубль: идемпотентность по нашему номеру заявки (`reference_id`) или проверка состояния перед вызовом.

### Ошибки и логи
- Ошибки БД с `RAISE EXCEPTION '{текст}'` становятся 422 с этим текстом (`exceptions/handler.py`). Бизнес-отказ в сервисе — `HTTPException` с понятным текстом на русском.
- `except Exception` — только на границе (обработчик, крон) с логом и повторным выбросом или явным ответом. Молча глотать ошибки нельзя.
- Логи — `loguru` с подстановками `{}`: `logger.info("x={x}", x=x)`. `%s` в loguru не работает.

### Схемы и API
- `/api/v1`: запросы и ответы — Pydantic-модели от `BaseSchema`, ключи JSON в `snake_case`, `response_model` у каждого маршрута.
- У маршрута есть `summary`, `description` (какая функция БД, кто вызывает) и примеры (`openapi_examples.py` интеграции). Это и есть документация в Scalar.
- Статусы, коды провайдеров, типы событий — `StrEnum` или константы, не строки по коду.
- `/scalar` и `/openapi.json` работают только при `API_DOCS_ENABLED=true`. На бою — выключено.

### Стиль
- Python 3.14: `X | None`, `list[...]`, `type Alias = ...`, PEP 695 generics. Подсказки типов у всех функций.
- Имена говорят, что делают: `mark_cession_sent`, а не `process`/`handle2`. Без сокращений, кроме общепринятых (`iin`, `id`, `url`).
- Файл больше ~500 строк или функция больше ~50 строк — разбить. Модуль сервиса интеграции делится по процессам (заявка, подпись, вебхук, цессия), а не растёт в один файл.
- Комментарий объясняет «почему», а не «что». Без закомментированного кода, без `TODO` без задачи.
- Без копирования: если код нужен второй интеграции — переносится в `src/shared/` или инфраструктурный пакет.
- Без импортов внутри функций ради обхода циклических зависимостей, без `# noqa` без причины.
- Только `ruff format`; правила линтера — в `pyproject.toml`, ослаблять их нельзя без записи причины там же.

### Тесты
- Новый маршрут или правило — тест в `app/tests/<интеграция>/`. Внешний HTTP и БД подменяются (как `FakeConnection` в `tests/big_integration/test_contract.py` или `AsyncMock` репозитория).
- Тесты не ходят в настоящий банк и в базы.
- Исправление ошибки начинается с теста, который её воспроизводит.

## Новая интеграция — порядок

1. Функции БД и таблицы — в `../myspace/sql/<фича>/` (агент `sql-dev`), учётки партнёра — строки в `integration_provider_tab` / `integration_credential_tab`.
2. Папка `app/src/features/<партнёр>/`: `router.py`, `schemas.py`, `service.py`, `repo.py`, `client.py`, `deps.py`, `auth.py`, `openapi_examples.py`. Большой сервис — подпапка с модулями по процессам.
3. Префикс в `routers/config.py`, подключение в `routers/v1.py`, тег с описанием в `openapi.py`.
4. Новые переменные env — в `config.py` и `app/.env.example` с комментарием.
5. Тесты, `ruff`, `pytest`. Запись в документ фичи в `../myspace/features/<фича>/`.

## Запуск на сервере

- Docker: `Dockerfile` (uv из зафиксированной версии, пользователь без root, `HEALTHCHECK` по `/health`), `docker-compose.yaml` публикует порт только на `127.0.0.1:9020` — снаружи сервис доступен только через nginx.
- Переменные — `app/.env` через `env_file` в compose. В образ `.env` не попадает (`.dockerignore`).
- `GET /health` — процесс жив; `GET /health/ready` — есть связь с PostgreSQL.
- Пул БД: `DB_POOL_MAX_SIZE` на каждый воркер gunicorn, всего соединений = `GUNICORN_WORKERS × DB_POOL_MAX_SIZE`.

### Крон

Расписание живёт в базе: функции `cronrun__every_10_minutes`, `cronrun__every_2_hours` и т.д. (их вызывает планировщик БД, лог — `cron_log_tab`). Ручку сервиса из крона вызывает функция БД через расширение `http`. Новую задачу — сюда же.

| Что | Расписание | Как вызывается |
|---|---|---|
| Рассрочка Freedom: сверка заявок с банком — статусы без хука, запись выданных в сделку, закрытие неотправленных, итог возвратов | каждые 10 минут | `cronrun__every_10_minutes` → `cron__installment_reconcile()` → `GET /api/v1/installment/ff/reconcile` (Basic из `integration_credential_tab`, провайдер FF, `env = sr_reconcile_cron`). SQL: `../myspace/sql/freedom-installment/29_reconcile_cron.sql` |

## Технический долг

Не копировать эти приёмы в новый код; закрывать по мере работы с файлом.

1. **Блокер выкатки факторинга.** Проверка ИИН подписанта печатных форм отключена для теста с Freedom: `FactoringService._sign_callback_iin_error` (`features/factoring/ff/service.py`), три теста `IIN_CHECK_DISABLED` в `tests/factoring/test_sign_callback.py`. Вернуть до `main`.
2. `features/factoring/ff/service.py` (~3000 строк) и `features/installment/ff/service.py` (~1250) — разбить по процессам: прескоринг, заявка и подача, печатные формы и подпись, вебхуки, цессия, возвраты.
3. SQL по таблицам в Python вместо функций БД: `features/factoring/ff/repo.py` (обновление прескоринга, поиск заявки, чтения `client_request_tab`, реквизиты компании, номера договоров цессии), `features/installment/ff/repo.py` (`client_request_tab`), `shared/deal_guard.py`. Перевести на функции в `../myspace/sql/`.
4. Вызовы банка и MyNCA идут внутри транзакции запроса и держат соединение пула всё время ответа партнёра.
5. Маршруты BIG возвращают ответ с ошибкой вместо исключения (см. «Данные и транзакции»). Контракт заморожен, поэтому оставлено; в новых маршрутах так не делать.
6. Конфиг — классы с `os.getenv` при импорте; при росте числа интеграций перейти на `pydantic-settings` с проверкой обязательных значений при старте.
