import sys

from loguru import logger


def configure_logging() -> None:
    """The default sink and format, without ``diagnose``.

    With it, a logged traceback prints the values of local variables, and in these services those can
    be a company key, a password or a document. ``logger.add`` defaults keep the format and level.
    """
    logger.remove()
    logger.add(sys.stderr, diagnose=False)
