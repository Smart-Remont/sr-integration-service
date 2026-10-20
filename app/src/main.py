from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from scalar_fastapi import get_scalar_api_reference

from src.config import app_config, cors_config
from src.database import close_db_pool, get_db_pool, init_db_pool
from src.exceptions import register_infrastructure_handlers
from src.openapi import DESCRIPTION, TAGS
from src.routers import main_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    await init_db_pool()
    yield
    await close_db_pool()


app = FastAPI(
    title="Smart Remont — Integrations",
    description=DESCRIPTION,
    version=app_config.version,
    lifespan=lifespan,
    redirect_slashes=False,
    openapi_tags=TAGS,
    openapi_url="/openapi.json" if app_config.api_docs_enabled else None,
    docs_url=None,
    redoc_url=None,
)
register_infrastructure_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_config.allow_origins,
    allow_credentials=cors_config.allow_credentials,
    allow_methods=cors_config.allow_methods,
    allow_headers=cors_config.allow_headers,
)


@app.get("/health", tags=["Health"], summary="Liveness: процесс отвечает")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["Health"], summary="Readiness: есть связь с PostgreSQL")
async def health_ready() -> JSONResponse:
    try:
        pool = await get_db_pool()
        async with pool.acquire() as connection:
            await connection.fetchval("select 1")
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable"},
        )
    return JSONResponse({"status": "ok"})


if app_config.api_docs_enabled:

    @app.get("/scalar", include_in_schema=False)
    async def scalar_docs() -> HTMLResponse:
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
            telemetry=False,
        )


app.include_router(main_router)
