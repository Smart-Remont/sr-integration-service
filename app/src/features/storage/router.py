from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from src.config import file_store_config, minio_config
from src.routers.config import api_prefix_config
from src.storage.file_store import (
    MinioNotConfiguredError,
    MinioUploadError,
    UnknownFileStoreModeError,
    file_store,
)
from src.storage.modes import FileStoreMode

from .auth import StorageBasicAuthDep
from .openapi_examples import CONFIG_RESPONSE, MODES_RESPONSE, UPLOAD_RESPONSE
from .schemas import (
    FileStoreConfigResponse,
    FileStoreModesResponse,
    FileUploadResponse,
    build_modes_response,
)

router = APIRouter(prefix=api_prefix_config.v1.storage, tags=["File Storage"])


def _allowed_modes_hint() -> str:
    return ", ".join(mode.value for mode in FileStoreMode)


@router.get(
    "/config",
    response_model=FileStoreConfigResponse,
    summary="Текущая конфигурация MinIO (без секретов)",
    responses={
        200: {
            "description": "Конфигурация MinIO",
            "content": {"application/json": {"example": CONFIG_RESPONSE}},
        },
    },
)
async def get_storage_config(_: StorageBasicAuthDep) -> FileStoreConfigResponse:
    return FileStoreConfigResponse(
        minio_configured=minio_config.is_configured,
        public_base_url=file_store_config.public_base_url,
        minio_endpoint=minio_config.endpoint or None,
        minio_bucket=minio_config.bucket if minio_config.is_configured else None,
    )


@router.get(
    "/modes",
    response_model=FileStoreModesResponse,
    summary="Список режимов upload (mode) как в PHP srfileUploadAction",
    description=(
        "Каждый `mode` определяет каталог под `/documents/...` в MinIO "
        "(object key `documents/...`). Неизвестный `mode` на upload → HTTP 400."
    ),
    responses={
        200: {
            "description": "Поддерживаемые режимы",
            "content": {"application/json": {"example": MODES_RESPONSE}},
        },
    },
)
async def list_storage_modes(_: StorageBasicAuthDep) -> FileStoreModesResponse:
    return build_modes_response()


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл в MinIO",
    description=(
        "Прямой async PUT в MinIO (`MINIO_*`, object key `documents/...`). "
        "Multipart: поле `file` + form `mode`. "
        "Ответ `{filename, path, ext, file_url}` — как myspace `file_store()`."
    ),
    responses={
        201: {
            "description": "Файл сохранён",
            "content": {"application/json": {"example": UPLOAD_RESPONSE}},
        },
        400: {
            "description": "Пустой файл или неизвестный mode",
            "content": {
                "application/json": {
                    "examples": {
                        "unknown_mode": {
                            "value": {"detail": "Unknown mode: NOPE. Allowed: CARD_FILES, ..."}
                        },
                        "empty_file": {"value": {"detail": "Empty file"}},
                    }
                }
            },
        },
        502: {
            "description": "MinIO недоступен или вернул ошибку",
            "content": {
                "application/json": {
                    "example": {"detail": "MinIO upload failed for mode=MATERIAL_PHOTO"}
                }
            },
        },
        503: {
            "description": "MinIO не сконфигурирован (MINIO_*)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "MinIO is not configured (MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY)."
                    }
                }
            },
        },
    },
)
async def upload_file(
    _: StorageBasicAuthDep,
    file: Annotated[
        UploadFile,
        File(description="Файл для загрузки"),
    ],
    mode: Annotated[
        str,
        Form(description=f"Режим каталога. Допустимые: {_allowed_modes_hint()}"),
    ],
) -> FileUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    try:
        stored = await file_store(file.filename, content, mode)
    except UnknownFileStoreModeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown mode: {mode}. Allowed: {_allowed_modes_hint()}",
        ) from None
    except MinioNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MinIO is not configured (MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY).",
        ) from None
    except MinioUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO upload failed for mode={exc.mode}",
        ) from exc

    return FileUploadResponse.model_validate(stored)
