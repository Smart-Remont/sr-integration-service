from dataclasses import dataclass

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from loguru import logger

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


@dataclass(slots=True, frozen=True)
class ErrorResponse:
    status_code: int
    detail: str


_INFRASTRUCTURE_MESSAGES: dict[
    type[InfrastructureError],
    ErrorResponse,
] = {
    DuplicateKeyError: ErrorResponse(
        status_code=status.HTTP_409_CONFLICT,
        detail="A record matching the supplied data already exists.",
    ),
    ForeignKeyError: ErrorResponse(
        status_code=status.HTTP_409_CONFLICT,
        detail="A foreign key is missing or invalid.",
    ),
    CheckConstraintError: ErrorResponse(
        status_code=status.HTTP_409_CONFLICT,
        detail="The data failed a validation check.",
    ),
    DataIntegrityError: ErrorResponse(
        status_code=status.HTTP_409_CONFLICT,
        detail="There was a data validation error during processing.",
    ),
    DatabaseConfigurationError: ErrorResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Something went wrong while processing your request.",
    ),
    UnexpectedDatabaseError: ErrorResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Something went wrong while processing your request.",
    ),
}


def _json_error(
    status_code: int,
    detail: str | dict,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )


def register_infrastructure_handlers(app: FastAPI) -> None:
    @app.exception_handler(StoredProcedureError)
    async def stored_procedure_handler(
        _request: Request,
        exc: StoredProcedureError,
    ) -> JSONResponse:
        return _json_error(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        )

    for exc_type, response in _INFRASTRUCTURE_MESSAGES.items():

        @app.exception_handler(exc_type)
        async def handler(
            request: Request,
            exc: InfrastructureError,
            response: ErrorResponse = response,
        ) -> JSONResponse:
            logger.opt(exception=exc).error(
                "{error_type} on {method} {path}",
                error_type=type(exc).__name__,
                method=request.method,
                path=request.url.path,
            )

            return _json_error(
                status_code=response.status_code,
                detail=response.detail,
            )
