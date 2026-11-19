from .logger import GunicornLogger


def get_app_options(
    host: str,
    port: int,
    timeout: int,
    graceful_timeout: int,
    workers: int,
    log_level: str,
) -> dict:
    return {
        "accesslog": "-",
        "errorlog": "-",
        "bind": f"{host}:{port}",
        "loglevel": log_level,
        "logger_class": GunicornLogger,
        "timeout": timeout,
        "graceful_timeout": graceful_timeout,
        "workers": workers,
        "worker_class": "uvicorn.workers.UvicornWorker",
    }
