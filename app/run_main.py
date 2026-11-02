from _gunicorn import GunicornApplication, get_app_options, gunicorn_config
from src.main import app

__all__ = (
    "app",
    "main",
)


def main():
    GunicornApplication(
        application=app,
        options=get_app_options(
            host=gunicorn_config.host,
            port=gunicorn_config.port,
            timeout=gunicorn_config.timeout,
            graceful_timeout=gunicorn_config.graceful_timeout,
            workers=gunicorn_config.workers,
            log_level=gunicorn_config.log_level,
        ),
    ).run()


if __name__ == "__main__":
    main()
