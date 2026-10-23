# integrations-sr

Сервис интеграций Smart Remont: банки, партнёры, застройщики. FastAPI и `asyncpg` поверх функций PostgreSQL.

Правила, устройство кода и боевые маршруты — в [CLAUDE.md](CLAUDE.md).

## Запуск

Нужны Python 3.14 и [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp app/.env.example app/.env      # заполнить
cd app && uv run uvicorn src.main:app --reload
```

Документация API — `http://127.0.0.1:8000/scalar`, если в `app/.env` стоит `API_DOCS_ENABLED=true`.

## Проверка

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Docker

```bash
docker compose up --build
```

Сервис слушает `127.0.0.1:9020`; снаружи к нему ходят только через nginx. Переменные берутся из `app/.env`.
