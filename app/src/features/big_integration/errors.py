from asyncpg.exceptions import PostgresError

from src.exceptions.db import postgres_raise_message


class BigIntegrationDatabaseError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def to_big_integration_database_error(exc: Exception) -> BigIntegrationDatabaseError:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, PostgresError):
            return BigIntegrationDatabaseError(postgres_raise_message(cause))
        cause = cause.__cause__
    return BigIntegrationDatabaseError(str(exc))
