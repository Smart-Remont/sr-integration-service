import re
from logging import Filter, Formatter, LogRecord

from gunicorn.glogging import Logger

LOG_DEFAULT_FORMAT = (
    "[%(asctime)s.%(msecs)03d] %(module)10s:%(lineno)-3d %(levelname)-7s - %(message)s"
)

# Everything after the address goes: Zend finds a value in the query and in /key/value pairs, under
# an escaped name too, and the last of several values is the one that counts. Up to the next space:
# a raw query can hold a quote, and what follows it would stay.
_SECRET_ADDRESS_TAIL = re.compile(
    r"("
    r"/albato-meta-lead"  # the token of Albato
    r"|/kaspi-request-pay"  # IIN and account of the client, the amount
    # A hash or uuid is the only pass to the documents, and a callback carries its key.
    r"|/api/client_sign(?:/(?:sign-doc-list|old-sign-doc-list|perform-sign|callback|download"
    r"|download-signed|download-cms|download-agreement|download-ds)(?![\w-]))?+"
    r")\S+",
    re.IGNORECASE,
)


class SecretPathFilter(Filter):
    """IINs and keys in addresses must not reach the access log."""

    def filter(self, record: LogRecord) -> bool:
        message = record.getMessage()
        masked = _SECRET_ADDRESS_TAIL.sub(r"\1***", message)
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
