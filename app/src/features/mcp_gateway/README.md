# mcp_gateway — MCP-сервер для Nexora

Через `/mcp` ассистент Nexora (Cursor Agent SDK) читает MySpace от имени сотрудника. Шлюз прав не решает и в базу за авторизацией не ходит: всё проверяет MySpace на `/ext/v1`. Данные — только через `/ext/v1`, таблицы `knb.*` отсюда не читаются: часть прав живёт в Python MySpace.

## Цепочка

```
Nexora ──Bearer srx_A (aud=sr-mcp)──▶ integrations-sr /mcp
  /mcp ── POST {MYSPACE}/ext/v1/auth/token/   Basic mcp-gateway:<secret>
          grant_type=urn:ietf:params:oauth:grant-type:token-exchange
          subject_token=srx_A, subject_token_type=urn:ietf:params:oauth:token-type:access_token
          audience=myspace-ext, scope=<области всех инструментов шлюза>
       ◀── {access_token: srx_B, expires_in, scope}
  /mcp ── GET {MYSPACE}/ext/v1/kanban/...     Bearer srx_B
```

Ответы `/mcp` до SDK:

| Что | Ответ |
|---|---|
| нет Bearer или токен не того вида, что выдаёт MySpace (`srx_` и 43 знака base64url) — в MySpace не уходит | 401, `WWW-Authenticate: Bearer error="invalid_token"` |
| обмен: `invalid_grant` (токен неизвестен, отозван, истёк, чужая аудитория) | 401 `invalid_token` |
| обмен: `invalid_scope` (у токена или сотрудника нет нужной области) | 403, `WWW-Authenticate: Bearer error="insufficient_scope"` |
| обмен: `invalid_client`, `invalid_target`, `invalid_request`, `unsupported_grant_type`, неизвестный код, 4xx без JSON — отказ самому шлюзу, наша настройка | 503 и `ERROR` в журнале |
| MySpace не ответил, 5xx, странный ответ, обмен не уложился в срок запроса | 503, `Retry-After: 30` |
| не заданы `MYSPACE_EXT_API_URL` или секрет, либо адрес не `https://` на бою | 503, остальной сервис работает |

- srx_B лежит в памяти воркера: ключ — sha256(srx_A), срок — `expires_in` минус 30 с, не больше 10 000 записей. У каждого воркера gunicorn свой кэш. Одновременные промахи одного вызывающего дают один обмен.
- Отказ `invalid_grant` или `invalid_scope` помнится 30 с (тоже не больше 10 000 записей): повторы с тем же токеном отвечаются без MySpace. Отказ самому шлюзу и недоступность MySpace не запоминаются.
- 401 на чтении (токен отозван вместе с родителем, клиент выключен): srx_B выбрасывается из кэша, повторный обмен идёт под тем же замком вызывающего и пропускается, если соседний запрос уже получил новый токен. Потом повтор чтения; не вышло — ошибка инструмента «доступ закончился».
- Срок одного запроса — 25 с на всё (обмен, чтение, повтор), чтобы уложиться в 30 с nginx. Чтение у httpx — до 10 с.
- Ответы инструментов на ошибки `/ext/v1`: 400/403/404 — `isError` с русским текстом MySpace, 429 — то же и `Retry-After`, 5xx, таймаут и срок запроса — «MySpace временно недоступен». 4xx без JSON пишется в журнал (код и путь). SDK ставит перед текстом `Error executing tool <имя>: `.
- В журнале только первые 12 знаков sha256(srx_A). Ни srx_A, ни srx_B, ни секрет не пишутся ни в loguru, ни в журналы httpx и SDK.

Что видит вызывающий после отзыва своего токена: до конца срока srx_B в кэше `initialize` и `tools/list` отвечают 200, потому что MySpace там не спрашивают. Вызов инструмента идёт в MySpace, тот проверяет всю цепочку и отвечает 401, повторный обмен отклоняется, инструмент возвращает `isError` «доступ закончился». Данные после отзыва не уходят. Следующий запрос с этим токеном — 401.

Адрес — ровно `/mcp`: `/mcp/` отвечает 404 (в приложении `redirect_slashes=False`).

Транспорт — Streamable HTTP из официального SDK (`mcp` 2.x, `MCPServer`), `stateless_http` и JSON-ответы: gunicorn держит несколько воркеров, и любой отвечает на любой запрос. Сессий MCP нет, `initialize` можно не слать. Работают обе эпохи протокола: старая с `initialize` (2025-06-18 и т.п.) и 2026-07-28 без рукопожатия. Менеджер запросов SDK и httpx-клиент к MySpace стартуют в lifespan приложения (`McpGateway.running()`).

## Области и `tools/list`

Каждый инструмент принадлежит одной области (`ScopedTools.add(..., scope=...)`, у инструмента `_meta["smartremont/scope"]`). Промежуточный слой SDK (`MCPServer.middleware`, `ScopedTools.enforce_scopes`) на каждый запрос:

- убирает из `tools/list` инструменты областей, которых нет в `scope` обмена; ответ непривычного вида — пустой список и `ERROR` в журнале;
- вызов такого инструмента отклоняет до запуска, как неизвестный: JSON-RPC `-32602 Unknown tool`;
- инструмент, добавленный мимо `ScopedTools` (прямо `server.add_tool`), области не имеет: не показывается и не вызывается.

`tools.read` проверяет область ещё раз перед запросом в MySpace. Слой SDK помечен как provisional, поэтому версия `mcp` закреплена `>=2.3.0,<2.4`; при обновлении проверить тесты `app/tests/mcp_gateway/`.

## Настройки (`app/.env`)

| Переменная | Что |
|---|---|
| `MYSPACE_EXT_API_URL` | адрес backend MySpace без `/ext/v1`. На бою только `https://` |
| `MCP_GATEWAY_CLIENT_ID` | клиент шлюза в `admin.api_client_tab`, по умолчанию `mcp-gateway` |
| `MCP_GATEWAY_CLIENT_SECRET` | его секрет. Пусто — 503 |

Клиент `mcp-gateway` в MySpace: `audiences = {myspace-ext}`, `serves_audience = sr-mcp`, области те же, что у инструментов. Клиенту `nexora` нужна аудитория `sr-mcp`. Команды — `hub/sql/nexora-ext-api/README.md`.

## Как добавить инструмент новой области

1. В MySpace: область в `admin.api_scope_tab`, ручка `/ext/v1/...` с `required_scope`, область разрешена клиентам `nexora` и `mcp-gateway` (`ext_api/README.md`).
2. Здесь: модуль `<область>_tools.py` по образцу `kanban_tools.py`. Функции с `ctx: Context` и параметрами `Annotated[..., Field(description=...)]`, тело — `await tools.read(ctx, "/ext/v1/...", params)`. Регистрация — `tools.add(server, fn, scope="requests.read", title=..., description=...)`, описание по-русски: что возвращает и когда звать.
3. Вызвать регистрацию в `gateway.build_server()`.
4. Тесты в `app/tests/mcp_gateway/`: адрес и параметры, область скрывает и отказывает.

Шлюз просит у MySpace все области своих инструментов сразу. Если у токена Nexora окажется не каждая из них, MySpace откажет в обмене целиком (`invalid_scope`). Тогда `scope` в обмене не передавать: для `access_token` MySpace выдаст области родителя (`requested_scopes` в `ext_api/ex_services/ext_token_services.py`). Одна строка в `client.py`.

## Подключение в Nexora (Cursor Agent SDK)

```ts
export default defineConnection({
  url: "https://<integrations-sr>/mcp",
  auth: async (session) => ({ headers: { Authorization: `Bearer ${await srMcpTokenFor(session)}` } }),
});
```

`srMcpTokenFor(session)` меняет пропуск сотрудника (assistant JWT от виджета MySpace) у MySpace и кэширует ответ до истечения:

```
POST https://<myspace>/ext/v1/auth/token/
Authorization: Basic nexora:<secret>
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<assistant JWT>
subject_token_type=urn:ietf:params:oauth:token-type:jwt
audience=sr-mcp
scope=kanban.read
→ {"access_token": "srx_…", "expires_in": 600, "scope": "kanban.read"}
```

Ответ 401 от `/mcp` значит, что токен истёк или отозван: обменять пропуск заново. 403 — у пропуска нет нужной области. Адрес — ровно `/mcp`, без косой черты в конце.

## Выкатка

1. Переменные из таблицы выше в `app/.env` сервера integrations-sr.
2. Клиенты в боевой базе MySpace (`hub/sql/nexora-ext-api/README.md`): `mcp-gateway` с `allowed_ips` = исходящий адрес сервера integrations-sr; `nexora` с аудиторией `sr-mcp`.
3. Сеть: integrations-sr ходит в MySpace по `https`.
4. nginx на публичном имени integrations-sr:

   ```nginx
   limit_req_zone $binary_remote_addr zone=sr_mcp:10m rate=10r/s;

   location = /mcp {
       allow <IP серверов Nexora>;
       deny  all;
       limit_req zone=sr_mcp burst=40 nodelay;
       proxy_pass http://127.0.0.1:9020/mcp;
       proxy_read_timeout 30s;
   }
   ```

   `allow/deny` обязателен. `allowed_ips` клиента `nexora` на пути через шлюз не работает: при обмене MySpace видит адрес шлюза, а не Nexora, и проверяет только список `mcp-gateway`. Поэтому адреса Nexora держит nginx. `limit_req` — от перебора токенов и лишних обменов. Неверный по виду токен отсекается без MySpace, отказ MySpace помнится 30 с, но каждый новый токен правильного вида — это обмен.
5. Лимит MySpace на клиента (`EXT_API_RATE_PER_CLIENT`, 600 в минуту) общий для всех сотрудников, которые ходят через шлюз: все обмены и чтения шлюза считаются клиенту `mcp-gateway`. При росте числа сотрудников поднять лимит этому клиенту. Лимит на сотрудника (60 в минуту) действует как обычно.

## Отступления от правил репо

- **Вход по Bearer, обмененному у MySpace, а не `basic_auth_dependency`.** Вызывающий — сотрудник через Nexora, его токен выдаёт и проверяет MySpace. Basic здесь не к чему привязать: свой секрет шлюз предъявляет MySpace при обмене. Нет настройки — 503, как требуют правила.
- **Маршрут вне `routers/` и Scalar.** `/mcp` — ASGI-приложение SDK (JSON-RPC, GET/POST/DELETE на одном адресе), а не маршрут FastAPI. Подключён `app.add_route` в `main.py`, в OpenAPI его нет: схема MCP описывается `tools/list`.
- **Выключена проверка Host и Origin из SDK** (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`). По умолчанию SDK пускает только `localhost`, за nginx пришло бы публичное имя. Проверка защищает локальные серверы от страниц в браузере, а здесь без Bearer ответа нет, и cookie не используются.
- **Нет журнала событий через `committed()`.** Шлюз только читает; каждый обмен и каждое чтение пишет MySpace в `admin.api_call_log_tab`.

## Файлы

| Файл | Что |
|---|---|
| `gateway.py` | ASGI-приложение `/mcp`: Bearer, обмен, 401/403/503, запуск SDK |
| `client.py` | HTTP к MySpace: обмен и GET, ответы → исключения, вид токена |
| `tokens.py` | кэш srx_B, память об отказах, обмен под замком вызывающего |
| `session.py` | вызывающий одного запроса, срок запроса, повторный обмен на 401 |
| `tooling.py` | области инструментов, слой SDK для `tools/list` и `tools/call`, ошибки → `isError` |
| `kanban_tools.py` | инструменты канбана (`kanban.read`) |
| `deps.py` | экземпляр шлюза на воркер |
