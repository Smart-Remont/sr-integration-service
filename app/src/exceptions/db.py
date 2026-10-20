import re
from collections.abc import Generator
from contextlib import contextmanager

from asyncpg.exceptions import (
    CheckViolationError,
    ForeignKeyViolationError,
    IntegrityConstraintViolationError,
    PostgresError,
    UndefinedColumnError,
    UndefinedTableError,
    UniqueViolationError,
)

from .infra import (
    CheckConstraintError,
    DatabaseConfigurationError,
    DataIntegrityError,
    DuplicateKeyError,
    ForeignKeyError,
    InfrastructureError,
    StoredProcedureError,
    UnexpectedDatabaseError,
)

_PG_RAISE_MESSAGE_RE = re.compile(r"\{([^}]*)\}")


def postgres_raise_message(exc: BaseException) -> str:
    """Text inside ``{...}`` of a stored function ``RAISE EXCEPTION '{...}'``, else the raw text."""
    text = str(exc)
    match = _PG_RAISE_MESSAGE_RE.search(text)
    return match.group(1) if match else text


@contextmanager
def map_asyncpg_errors() -> Generator[None]:
    try:
        yield

    except UniqueViolationError as e:
        raise DuplicateKeyError() from e

    except ForeignKeyViolationError as e:
        raise ForeignKeyError() from e

    except CheckViolationError as e:
        raise CheckConstraintError() from e

    except IntegrityConstraintViolationError as e:
        raise DataIntegrityError() from e

    except (UndefinedColumnError, UndefinedTableError) as e:
        raise DatabaseConfigurationError() from e

    except InfrastructureError:
        raise

    except PostgresError as e:
        sqlstate = getattr(e, "sqlstate", None)
        if sqlstate == "P0001" or _PG_RAISE_MESSAGE_RE.search(str(e)):
            raise StoredProcedureError(postgres_raise_message(e).strip()) from e
        raise UnexpectedDatabaseError() from e
