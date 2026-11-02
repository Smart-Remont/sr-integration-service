"""One database step of a cron, as PHP ``readSP`` and ``scalarSP`` see it.

PHP commits each call on its own and takes a failed call for an empty result. The connection can be
gone or stuck: then every next step would also wait for its limit, so such a failure ends the run.
"""

from collections.abc import Awaitable, Callable

from asyncpg import InterfaceError
from loguru import logger

from src.php_compat.pdo_errors import php_error_state
from src.shared.cron_run import database_step_limit

from .repo import MailingRepository


class DatabaseGoneError(Exception):
    """No connection or no answer in time: the run stops here."""


class MailingSteps:
    def __init__(self, repository: MailingRepository) -> None:
        self.repository = repository

    async def take_lock(self, cron_name: str) -> bool:
        async with database_step_limit():
            locked = await self.repository.try_lock_run(cron_name)
        if not locked:
            logger.warning("{cron} skipped: the previous run is not finished", cron=cron_name)
        return locked

    async def write[T](self, name: str, call: Callable[[MailingRepository], Awaitable[T]]) -> T:
        """A step that raises: the caller decides. Its error text can quote a letter, not logged."""
        try:
            async with database_step_limit(), self.repository.transaction():
                return await call(self.repository)
        except (InterfaceError, OSError, TimeoutError) as exc:
            logger.error("{step} failed: {error}", step=name, error=type(exc).__name__)
            raise DatabaseGoneError(name) from exc

    async def value_or[T](
        self,
        name: str,
        default: T,
        call: Callable[[MailingRepository], Awaitable[T]],
    ) -> T:
        """A step that gives ``default`` when the function fails, as PHP does."""
        try:
            return await self.write(name, call)
        except DatabaseGoneError:
            raise
        except Exception as exc:
            logger.error("{step} failed: {error}", step=name, error=php_error_state(exc))
            return default
