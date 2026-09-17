from __future__ import annotations

import secrets
import time
from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath


class FileStoreMode(StrEnum):
    """Modes supported by `KanbanController::srfileUploadAction` (smremont)."""

    CARD_FILES = "CARD_FILES"
    MASTER_FILES = "MASTER_FILES"
    CHAT_FILES = "CHAT_FILES"
    CHAT_FILES_MINI = "CHAT_FILES_MINI"
    RECIPIENT_FILES = "RECIPIENT_FILES"
    CLIENT_AGREEMENT = "CLIENT_AGREEMENT"
    FLAT_LIST = "FLAT_LIST"
    REMONT_INDICATOR = "REMONT_INDICATOR"
    REQUEST_DOCS = "REQUEST_DOCS"
    MATERIAL_DOCS = "MATERIAL_DOCS"
    PLANIROVKA_PHOTOS = "PLANIROVKA_PHOTOS"
    FINANCE_PAYMENT = "FINANCE_PAYMENT"
    TEAM_MASTER = "TEAM_MASTER"
    PROJECT_REMONT = "PROJECT_REMONT"
    ORG_FILES = "ORG_FILES"
    ACCESSION_CONTRACT_FILES = "ACCESSION_CONTRACT_FILES"
    PROVIDER_DOCS = "PROVIDER_DOCS"
    PARTNER_PROJECT_PHOTO = "PARTNER_PROJECT_PHOTO"
    PARTNER_PROJECT_PHOTO_MINI = "PARTNER_PROJECT_PHOTO_MINI"
    CONTRACTOR_LOGO = "CONTRACTOR_LOGO"
    SHOW_ROOM_PHOTO = "SHOW_ROOM_PHOTO"
    SHOW_ROOM_PHOTO_MINI = "SHOW_ROOM_PHOTO_MINI"
    MATERIAL_PHOTO = "MATERIAL_PHOTO"
    MATERIAL_PHOTO_MINI = "MATERIAL_PHOTO_MINI"
    ROOM_PHOTO = "ROOM_PHOTO"
    KITCHEN_SCHEM = "KITCHEN_SCHEM"
    PRESET_KIT_PHOTO = "PRESET_KIT_PHOTO"
    PRESET_KIT_PHOTO_MINI = "PRESET_KIT_PHOTO_MINI"
    PRESET_KIT_PRESENTATION = "PRESET_KIT_PRESENTATION"
    DDU_COMMERCIAL_OFFER = "DDU_COMMERCIAL_OFFER"
    IT_SUPPORT_FILES = "IT_SUPPORT_FILES"
    REVIT_FILES = "REVIT_FILES"
    CLIENT_REQUEST_DOC = "CLIENT_REQUEST_DOC"
    LAYER = "LAYER"
    DESIGN_ROOM = "DESIGN_ROOM"
    CHECK_LIST = "CHECK_LIST"
    REMONT_CHECK = "REMONT_CHECK"
    PM_CHECK = "PM_CHECK"
    CLIENT_REQUEST_CHECK = "CLIENT_REQUEST_CHECK"
    RESIDENT_CHECK_DRAFT = "RESIDENT_CHECK_DRAFT"
    CLIENT_REQUEST_DRAFT = "CLIENT_REQUEST_DRAFT"


FILE_STORE_MODE_DESCRIPTIONS: dict[FileStoreMode, str] = {
    FileStoreMode.CARD_FILES: "Файлы карточки канбана",
    FileStoreMode.MASTER_FILES: "Файлы мастера",
    FileStoreMode.CHAT_FILES: "Вложения чата (оригинал)",
    FileStoreMode.CHAT_FILES_MINI: "Вложения чата (миниатюра)",
    FileStoreMode.RECIPIENT_FILES: "Файлы получателя",
    FileStoreMode.CLIENT_AGREEMENT: "Соглашение клиента",
    FileStoreMode.FLAT_LIST: "Список квартир",
    FileStoreMode.REMONT_INDICATOR: "Показатели ремонта",
    FileStoreMode.REQUEST_DOCS: "Документы заявки",
    FileStoreMode.MATERIAL_DOCS: "Документы материала",
    FileStoreMode.PLANIROVKA_PHOTOS: "Фото планировки",
    FileStoreMode.FINANCE_PAYMENT: "Файлы финансового платежа",
    FileStoreMode.TEAM_MASTER: "Файлы бригады/мастера",
    FileStoreMode.PROJECT_REMONT: "Файлы проекта ремонта",
    FileStoreMode.ORG_FILES: "Файлы организации",
    FileStoreMode.ACCESSION_CONTRACT_FILES: "Договор присоединения",
    FileStoreMode.PROVIDER_DOCS: "Документы поставщика",
    FileStoreMode.PARTNER_PROJECT_PHOTO: "Фото проекта партнёра",
    FileStoreMode.PARTNER_PROJECT_PHOTO_MINI: "Фото проекта партнёра (мини)",
    FileStoreMode.CONTRACTOR_LOGO: "Логотип подрядчика",
    FileStoreMode.SHOW_ROOM_PHOTO: "Фото шоурума",
    FileStoreMode.SHOW_ROOM_PHOTO_MINI: "Фото шоурума (мини)",
    FileStoreMode.MATERIAL_PHOTO: "Фото материала (оригинал)",
    FileStoreMode.MATERIAL_PHOTO_MINI: "Фото материала (мини)",
    FileStoreMode.ROOM_PHOTO: "Фото комнаты (design_room)",
    FileStoreMode.KITCHEN_SCHEM: "Схема кухни",
    FileStoreMode.PRESET_KIT_PHOTO: "Фото комплекта (оригинал)",
    FileStoreMode.PRESET_KIT_PHOTO_MINI: "Фото комплекта (мини)",
    FileStoreMode.PRESET_KIT_PRESENTATION: "Презентация комплекта",
    FileStoreMode.DDU_COMMERCIAL_OFFER: "Коммерческое предложение ДДУ",
    FileStoreMode.IT_SUPPORT_FILES: "Файлы IT-поддержки",
    FileStoreMode.REVIT_FILES: "Файлы Revit",
    FileStoreMode.CLIENT_REQUEST_DOC: "Документ заявки (дефекты)",
    FileStoreMode.LAYER: "Слой пресета (constructor)",
    FileStoreMode.DESIGN_ROOM: "Базовое фото комнаты (design_room)",
    FileStoreMode.CHECK_LIST: "Файлы пункта чек-листа ОКК (фото/аудио)",
    FileStoreMode.REMONT_CHECK: "Дефекты проверки ремонта (фото/видео/аудио)",
    FileStoreMode.PM_CHECK: "Файлы проверки менеджера",
    FileStoreMode.CLIENT_REQUEST_CHECK: "Черновая проверка заявки (фото/видео)",
    FileStoreMode.RESIDENT_CHECK_DRAFT: "Черновая приёмка ЖК",
    FileStoreMode.CLIENT_REQUEST_DRAFT: "Файлы черновой проверки заявки",
}

# Same folders/prefixes as KanbanController::srfileUploadAction.
# Placeholders: {date} Y.m.d, {n} 1-based file index, {uniq} PHP uniqid(..., true), {ext}.
MODE_PATH_TEMPLATES: dict[FileStoreMode, str] = {
    FileStoreMode.CARD_FILES: "/documents/{date}/card_files/card_file_{n}_{uniq}.{ext}",
    FileStoreMode.MASTER_FILES: "/documents/{date}/master_files/master_files{n}_{uniq}.{ext}",
    FileStoreMode.CHAT_FILES: "/documents/{date}/chat_files/chat_files{n}_{uniq}.{ext}",
    FileStoreMode.CHAT_FILES_MINI: "/documents/{date}/chat_files/mini/chat_files{n}_{uniq}.{ext}",
    FileStoreMode.RECIPIENT_FILES: "/documents/{date}/recipient_files/recipient_files{n}_{uniq}.{ext}",
    FileStoreMode.CLIENT_AGREEMENT: "/documents/{date}/client_agreement/client_agreement{n}_{uniq}.{ext}",
    FileStoreMode.FLAT_LIST: "/documents/{date}/flat_list/flat_list{n}_{uniq}.{ext}",
    FileStoreMode.REMONT_INDICATOR: "/documents/{date}/remont_indicator/remont_indicator{n}_{uniq}.{ext}",
    FileStoreMode.REQUEST_DOCS: "/documents/{date}/request_docs/request_docs{n}_{uniq}.{ext}",
    FileStoreMode.MATERIAL_DOCS: "/documents/{date}/material_docs/material_docs{n}_{uniq}.{ext}",
    FileStoreMode.PLANIROVKA_PHOTOS: "/documents/{date}/planirovka_photos/planirovka_photos{n}_{uniq}.{ext}",
    FileStoreMode.FINANCE_PAYMENT: "/documents/{date}/finance_payment_files/finance_payment_files{n}_{uniq}.{ext}",
    FileStoreMode.TEAM_MASTER: "/documents/{date}/team_master_files/team_master_files{n}_{uniq}.{ext}",
    FileStoreMode.PROJECT_REMONT: "/documents/{date}/project_remont_files/project_remont_file_{uniq}.{ext}",
    FileStoreMode.ORG_FILES: "/documents/{date}/org_files/org_files{n}_{uniq}.{ext}",
    FileStoreMode.ACCESSION_CONTRACT_FILES: "/documents/{date}/accession_contract/accession_contract{n}_{uniq}.{ext}",
    FileStoreMode.PROVIDER_DOCS: "/documents/{date}/provider_docs/doc_{uniq}.{ext}",
    FileStoreMode.PARTNER_PROJECT_PHOTO: "/documents/{date}/partner_project_photo/photo_{uniq}.{ext}",
    FileStoreMode.PARTNER_PROJECT_PHOTO_MINI: "/documents/{date}/partner_project_photo/mini/photo_mini_{uniq}.{ext}",
    FileStoreMode.CONTRACTOR_LOGO: "/documents/contractor_logo/contractor_logo_{uniq}.{ext}",
    FileStoreMode.SHOW_ROOM_PHOTO: "/documents/{date}/showroom_photos/show_room_photo_{uniq}.{ext}",
    FileStoreMode.SHOW_ROOM_PHOTO_MINI: "/documents/{date}/showroom_photos/mini/show_room_photo_mini_{uniq}.{ext}",
    FileStoreMode.MATERIAL_PHOTO: "/documents/{date}/material_photo/material_photo_orig_{uniq}.{ext}",
    FileStoreMode.MATERIAL_PHOTO_MINI: "/documents/{date}/material_photo/mini/material_photo_{uniq}.{ext}",
    FileStoreMode.ROOM_PHOTO: "/documents/{date}/design_room/room_photo_{uniq}.{ext}",
    FileStoreMode.KITCHEN_SCHEM: "/documents/{date}/kitchen_schem/schem_{uniq}.{ext}",
    FileStoreMode.PRESET_KIT_PHOTO: "/documents/{date}/preset_kit_photos/original/preset_kit_photo{uniq}.{ext}",
    FileStoreMode.PRESET_KIT_PHOTO_MINI: "/documents/{date}/preset_kit_photos/preset_kit_photo{uniq}.{ext}",
    FileStoreMode.PRESET_KIT_PRESENTATION: "/documents/{date}/preset_kit_presentation_files/preset_kit_presentation{uniq}.{ext}",
    FileStoreMode.DDU_COMMERCIAL_OFFER: "/documents/{date}/ddu_commercial_offer/ddu_commercial_offer_{n}_{uniq}.{ext}",
    FileStoreMode.IT_SUPPORT_FILES: "/documents/{date}/it_support_files/it_support_file_{n}_{uniq}.{ext}",
    FileStoreMode.REVIT_FILES: "/documents/{date}/revit_files/revit_file_{n}_{uniq}.{ext}",
    FileStoreMode.CLIENT_REQUEST_DOC: "/documents/{date}/client_request_docs/client_request_doc_{n}_{uniq}.{ext}",
    FileStoreMode.LAYER: "/documents/{date}/layer/layer_{uniq}.{ext}",
    FileStoreMode.DESIGN_ROOM: "/documents/{date}/design_room/design_room_{uniq}.{ext}",
    FileStoreMode.CHECK_LIST: "/documents/{date}/check_list/check_list_{n}_{uniq}.{ext}",
    FileStoreMode.REMONT_CHECK: "/documents/{date}/remont_check/remont_check_{n}_{uniq}.{ext}",
    FileStoreMode.PM_CHECK: "/documents/{date}/pm_check_files/pm_check_file_{n}_{uniq}.{ext}",
    FileStoreMode.CLIENT_REQUEST_CHECK: "/documents/{date}/client_request_check/client_request_check_{n}_{uniq}.{ext}",
    FileStoreMode.RESIDENT_CHECK_DRAFT: "/documents/{date}/resident_check_draft/resident_check_draft_{n}_{uniq}.{ext}",
    FileStoreMode.CLIENT_REQUEST_DRAFT: "/documents/{date}/client_request_draft/client_request_draft_{n}_{uniq}.{ext}",
}


def php_uniqid(prefix: str = "") -> str:
    """PHP `uniqid($prefix, true)`: 13 hex timestamp + `.` + 8 digits."""
    now = time.time()
    seconds = int(now)
    microseconds = int((now - seconds) * 1_000_000)
    entropy = secrets.randbelow(100_000_000)
    return f"{prefix}{seconds:08x}{microseconds:05x}.{entropy:08d}"


def filename_extension(filename: str) -> str:
    """Same as PHP `pathinfo(..., PATHINFO_EXTENSION)` — case preserved."""
    return PurePosixPath(filename).suffix.lstrip(".")


def path_template(mode: FileStoreMode) -> str:
    return MODE_PATH_TEMPLATES[mode]


def build_logical_path(mode: FileStoreMode, ext: str, *, index: int = 1) -> str:
    """Build `/documents/...` path like `KanbanController::srfileUploadAction`."""
    return MODE_PATH_TEMPLATES[mode].format(
        date=date.today().strftime("%Y.%m.%d"),
        n=index,
        uniq=php_uniqid(),
        ext=ext,
    )


def logical_path_to_object_key(logical_path: str) -> str:
    """Same as `Api_MinioStorage::absolutePathToKey` for `/documents/...` paths."""
    normalized = logical_path.replace("\\", "/")
    if "/documents/" in normalized:
        pos = normalized.index("/documents/")
        return normalized[pos + 1 :]
    return normalized.lstrip("/")
