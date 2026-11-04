"""What can be refused from a row alone, before the database and the company key."""

from dataclasses import dataclass

from loguru import logger

from src.php_compat.text import php_is_positive, php_string
from src.repository.base import SpRow
from src.shared.uuid_text import is_uuid_text


@dataclass(frozen=True)
class SignTarget:
    """What PHP puts in ``$ext_id`` and ``$a``. ``ext_id`` stays 0 when nothing matches, as in PHP."""

    ext_id: int
    client_request_id: int | None = None
    ds_id: int | None = None
    client_request_document_id: int | None = None


@dataclass(frozen=True)
class SigningPlan:
    client_process_id: str
    group_id: str | None
    target: SignTarget


def signing_plan(row: SpRow) -> SigningPlan | None:
    sign_id = row["sign_id"]
    if not php_is_positive(row.get("company_id")):
        logger.error("Auto sign: no company | sign_id={sign_id}", sign_id=sign_id)
        return None
    client_process_id = php_string(row.get("sign_process_id"))
    if not client_process_id:
        # PHP would sign a file from the partner backend or sign.smartremont.kz. Not ported: any
        # answer of the partner, even an error page, would get the company signature.
        logger.error(
            "Auto sign: no client process, legacy branch is not ported | "
            "sign_id={sign_id} doc_type={doc_type}",
            sign_id=sign_id,
            doc_type=row.get("doc_type"),
        )
        return None
    group_id = php_string(row.get("sign_group_id")) or None
    if not is_uuid_text(client_process_id) or (group_id is not None and not is_uuid_text(group_id)):
        logger.error(
            "Auto sign: process or group is not a UUID | sign_id={sign_id}", sign_id=sign_id
        )
        return None
    target = sign_target(row)
    if target.ext_id == 0:
        # PHP signs here, and the insert then refuses: a signature to roll back, every minute.
        logger.error(
            "Auto sign: no document id for this type | sign_id={sign_id} doc_type={doc_type}",
            sign_id=sign_id,
            doc_type=row.get("doc_type"),
        )
        return None
    return SigningPlan(client_process_id=client_process_id, group_id=group_id, target=target)


def sign_target(row: SpRow) -> SignTarget:
    doc_type = row.get("doc_type")
    ds_id = row.get("ds_id")
    agreement_id = row.get("agreement_client_request_id")
    document_id = row.get("client_request_document_id")
    if doc_type == "DS" and isinstance(ds_id, int) and ds_id:
        return SignTarget(ext_id=ds_id, ds_id=ds_id)
    if doc_type == "AGREEMENT" and isinstance(agreement_id, int) and agreement_id:
        return SignTarget(ext_id=agreement_id, client_request_id=agreement_id)
    if doc_type == "APP_CLIENT" and isinstance(document_id, int) and document_id:
        return SignTarget(ext_id=document_id, client_request_document_id=document_id)
    return SignTarget(ext_id=0)
