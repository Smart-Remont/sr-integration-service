from pydantic import Field

from src.config import file_store_config
from src.schema import BaseSchema
from src.storage.modes import FILE_STORE_MODE_DESCRIPTIONS, FileStoreMode, path_template

from .openapi_examples import CONFIG_RESPONSE, UPLOAD_RESPONSE


class FileUploadResponse(BaseSchema):
    filename: str = Field(description="Исходное имя загруженного файла", examples=["photo.jpg"])
    path: str = Field(
        description="Относительный путь в хранилище (`/documents/...`), как в БД smremont",
        examples=[UPLOAD_RESPONSE["path"]],
    )
    ext: str = Field(
        description="Расширение файла (lowercase), как PHP `file_ext`", examples=["jpg"]
    )
    file_url: str = Field(
        description="Публичный URL: `STORAGE_PUBLIC_URL` + `path`",
        examples=[UPLOAD_RESPONSE["fileUrl"]],
    )


class FileStoreModeItem(BaseSchema):
    mode: FileStoreMode
    description: str = Field(description="Назначение режима")
    path_template: str = Field(
        description="Шаблон пути как в PHP srfileUploadAction (`{date}`, `{n}`, `{uniq}`, `{ext}`)"
    )


class FileStoreModesResponse(BaseSchema):
    public_base_url: str = Field(
        description="Базовый URL для `file_url`",
        examples=["https://office.smartremont.kz"],
    )
    items: list[FileStoreModeItem]
    total: int


class FileStoreConfigResponse(BaseSchema):
    minio_configured: bool = Field(
        description="Заданы MINIO_ENDPOINT, ACCESS_KEY, SECRET_KEY, BUCKET"
    )
    public_base_url: str = Field(examples=[CONFIG_RESPONSE["publicBaseUrl"]])
    minio_endpoint: str | None = Field(default=None, description="MINIO_ENDPOINT (без секретов)")
    minio_bucket: str | None = Field(default=None, description="MINIO_BUCKET")


def build_modes_response() -> FileStoreModesResponse:
    items = [
        FileStoreModeItem(
            mode=mode,
            description=FILE_STORE_MODE_DESCRIPTIONS[mode],
            path_template=path_template(mode),
        )
        for mode in FileStoreMode
    ]
    return FileStoreModesResponse(
        public_base_url=file_store_config.public_base_url,
        items=items,
        total=len(items),
    )
