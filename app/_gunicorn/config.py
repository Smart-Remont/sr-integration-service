import os


class GunicornConfig:
    host: str = os.getenv("GUNICORN_HOST", "0.0.0.0")  # noqa: S104 — inside the container
    port: int = int(os.getenv("GUNICORN_PORT", "8000"))
    timeout: int = int(os.getenv("GUNICORN_TIMEOUT", "900"))
    workers: int = int(os.getenv("GUNICORN_WORKERS", "4"))
    log_level: str = os.getenv("GUNICORN_LOG_LEVEL", "info")


gunicorn_config = GunicornConfig()
