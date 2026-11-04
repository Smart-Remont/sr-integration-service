import os


class GunicornConfig:
    host: str = os.getenv("GUNICORN_HOST", "0.0.0.0")  # noqa: S104 — inside the container
    port: int = int(os.getenv("GUNICORN_PORT", "8000"))
    timeout: int = int(os.getenv("GUNICORN_TIMEOUT", "900"))
    # Below the longest waits of a cron run: 300 s of an SMS send, 120 s of cms/sign-save. A restart
    # during such a wait kills the worker, and the run loses the record of what the partner accepted.
    # So a rollout takes the cron lines off and waits for the run to end (ROLLOUT.md). A run that gets
    # SIGTERM between rows stops by itself (shared/cron_run.py). stop_grace_period in compose must be longer.
    graceful_timeout: int = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "50"))
    workers: int = int(os.getenv("GUNICORN_WORKERS", "4"))
    log_level: str = os.getenv("GUNICORN_LOG_LEVEL", "info")


gunicorn_config = GunicornConfig()
