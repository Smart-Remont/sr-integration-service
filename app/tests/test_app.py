from httpx import ASGITransport, AsyncClient

from src.main import app


async def _get(path: str) -> tuple[int, str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(path)
    return response.status_code, response.text


async def test_health_contract() -> None:
    # Production nginx: office.smartremont.kz/fastapi/healthcheck → /health.
    assert await _get("/health") == (200, '{"status":"ok"}')


async def test_api_docs_are_closed_by_default() -> None:
    for path in ("/scalar", "/openapi.json", "/docs", "/redoc"):
        status_code, _ = await _get(path)
        assert status_code == 404, path
