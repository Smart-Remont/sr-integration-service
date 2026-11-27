from enum import StrEnum


class ProviderCode(StrEnum):
    """integration_provider_tab.code of an installment bank."""

    FF = "FF"
    BIF = "BIF"


class BIFStatus(StrEnum):
    """What BI Finance sends to POST /webhook/bif (ТЗ, раздел 4)."""

    APPROVED = "APPROVED"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ApplicationStatus(StrEnum):
    """Our statuses the BI Finance flow sets itself; FF adds the bank's own (ff/service.py)."""

    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    APPLY_FAILED = "APPLY_FAILED"
    APPLY_UNKNOWN = "APPLY_UNKNOWN"
    ISSUED = "ISSUED"
    EXPIRED = "EXPIRED"


class InstallmentSource(StrEnum):
    """installment_event_log_tab.source."""

    INSTALLMENT = "INSTALLMENT"
    FF = "FF"
    BIF = "BIF"
