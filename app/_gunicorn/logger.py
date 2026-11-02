import re
from logging import Filter, Formatter, LogRecord

from gunicorn.glogging import Logger

LOG_DEFAULT_FORMAT = (
    "[%(asctime)s.%(msecs)03d] %(module)10s:%(lineno)-3d %(levelname)-7s - %(message)s"
)

_CALLBACK_KEY = re.compile(r"(/callback/key/)[^\s/?\"]+")
# Everything after the address goes: Zend finds the token in the query and in /key/value pairs,
# under an escaped name too, and the last of several values is the one that counts.
_ALBATO_TOKEN = re.compile(r"(/albato-meta-lead)[^\s\"]+")


class SecretPathFilter(Filter):
    """Keys in addresses (a MyNCA callback, the Albato token) must not reach the access log."""

    def filter(self, record: LogRecord) -> bool:
        message = record.getMessage()
        masked = _ALBATO_TOKEN.sub(r"\1***", _CALLBACK_KEY.sub(r"\1***", message))
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


class GunicornLogger(Logger):
    def setup(self, cfg) -> None:
        super().setup(cfg)

        self._set_handler(
            log=self.access_log,
            output=cfg.accesslog,
            fmt=Formatter(fmt=LOG_DEFAULT_FORMAT),
        )
        for handler in self.access_log.handlers:
            handler.addFilter(SecretPathFilter())
        self._set_handler(
            log=self.error_log,
            output=cfg.errorlog,
            fmt=Formatter(fmt=LOG_DEFAULT_FORMAT),
        )
