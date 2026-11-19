"""Values of ``what`` in PHP ``RestController::buhAction``.

Moved: what was called in the 14 days of logs from 19.10.2026. The others are not moved on the
owner's decision, with their dates in ``features/legacy-integrations/STATUS.md``. Any other value
was never a branch of PHP and stays an empty 200.
"""

from enum import StrEnum


class MovedWhat(StrEnum):
    LOT = "lot"
    LOT_GUID = "lot_guid"
    MATERIAL_GUID = "material_guid"
    WAREHOUSE_GUID = "warehouse_guid"


NOT_MOVED_WHAT = frozenset(
    {
        "agreement",
        "agreement_guid",
        "agreement_implemented",
        "avr",
        "avr_guid",
        "change_owner",
        "change_owner_guid",
        "cleaning",
        "cleaning_guid",
        "contractor",
        "contractor_agreement",
        "contractor_agreement_guid",
        "contractor_guid",
        "contragent",
        "contragent_blacklist",
        "contragent_blacklist_guid",
        "contragent_debt",
        "contragent_guid",
        "finance_payment",
        "finance_payment_guid",
        "invoice_guid",
        "lot_aidar",
        "material",
        "payment",
        "payment_guid",
        "payment_insert",
        "penalty",
        "penalty_guid",
        "provider",
        "provider_guid",
        "realisation",
        "realisation_guid",
        "referal",
        "referal_accrual",
        "referal_accrual_upd",
        "referal_guid",
        "release",
        "release_aidar",
        "release_guid",
        "rm_supplier",
        "rm_supplier_guid",
        "techproject_developer",
        "techproject_developer_guid",
    }
)
