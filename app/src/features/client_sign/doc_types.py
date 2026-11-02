from enum import StrEnum


class DocumentType(StrEnum):
    """``doc_type`` of the routes."""

    DS = "DS"
    AGREEMENT = "AGREEMENT"
    OTBASY_STATEMENT = "OTBASY_STATEMENT"


class SignTypeCode(StrEnum):
    """``sign_type_tab.sign_type_code`` of a signature made through MyNCA."""

    AGREEMENT = "CLIENT_SIGN"
    DS = "CLIENT_DS_SIGN"
    OTBASY_STATEMENT = "OTBASY_STATEMENT_SIGN"


AGREEMENT_MANUAL = "AGREEMENT_MANUAL"

SIGN_TYPE_BY_DOCUMENT = {
    DocumentType.DS: SignTypeCode.DS,
    DocumentType.AGREEMENT: SignTypeCode.AGREEMENT,
    DocumentType.OTBASY_STATEMENT: SignTypeCode.OTBASY_STATEMENT,
}
ID_COLUMN_BY_DOCUMENT = {
    DocumentType.DS: "ds_id",
    DocumentType.AGREEMENT: "client_request_id",
    DocumentType.OTBASY_STATEMENT: "client_request_document_id",
}
FILE_NAME_PREFIX_BY_DOCUMENT = {
    DocumentType.DS: "Доп_соглашение_",
    DocumentType.AGREEMENT: "Договор_оказания_услуг_",
    DocumentType.OTBASY_STATEMENT: "Заявление_по_ОТБАСЫ_",
}
