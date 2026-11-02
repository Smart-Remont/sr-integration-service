import os


class GunicornConfig:
    host: str = os.getenv("GUNICORN_HOST", "0.0.0.0")  # noqa: S104 — inside the container
    port: int = int(os.getenv("GUNICORN_PORT", "8000"))
    timeout: int = int(os.getenv("GUNICORN_TIMEOUT", "900"))
    # Above the 300 s read of an SMS send: killed during that wait, a run loses the record of a batch
    # Kcell accepted, and the messages go out again. stop_grace_period in compose must be longer.
    graceful_timeout: int = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "50"))
    workers: int = int(os.getenv("GUNICORN_WORKERS", "4"))
    log_level: str = os.getenv("GUNICORN_LOG_LEVEL", "info")


gunicorn_config = GunicornConfig()
