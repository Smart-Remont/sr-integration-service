from __future__ import annotations

import os
from typing import List

from dotenv import load_dotenv

load_dotenv()


class BigIntegrationAuthConfig:
    username: str = os.getenv("INTEGRATION_HS_BI_USER", "hs_bi")
    password: str = os.getenv("INTEGRATION_HS_BI_PASSWORD", "")


class InstallmentAuthConfig:
    username: str = os.getenv("INSTALLMENT_API_USER", "")
    password: str = os.getenv("INSTALLMENT_API_PASSWORD", "")


class FactoringAuthConfig:
    """Basic auth for /api/v1/factoring/*. Falls back to installment creds if unset."""

    username: str = os.getenv(
        "FACTORING_API_USER",
        os.getenv("INSTALLMENT_API_USER", ""),
    )
    password: str = os.getenv(
        "FACTORING_API_PASSWORD",
        os.getenv("INSTALLMENT_API_PASSWORD", ""),
    )


class IntegrationContextConfig:
    """Signed proxy context (CRM → integrations-sr). See docs/integration-context.md."""

    secret: str = os.getenv("INTEGRATION_CONTEXT_SECRET", "")
    audience: str = os.getenv("INTEGRATION_CONTEXT_AUDIENCE", "integrations-sr")
    issuer_allowlist: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("INTEGRATION_CONTEXT_ISSUERS", "sr-crm").split(",")
        if item.strip()
    )

    @property
    def is_enforced(self) -> bool:
        return bool(self.secret.strip())


class StorageAuthConfig:
    """Basic auth for /api/v1/storage/*. Falls back to installment creds if unset."""

    username: str = os.getenv(
        "STORAGE_API_USER",
        os.getenv("INSTALLMENT_API_USER", ""),
    )
    password: str = os.getenv(
        "STORAGE_API_PASSWORD",
        os.getenv("INSTALLMENT_API_PASSWORD", ""),
    )


class FactoringPrescoringConfig:
    """HTTP Basic for bank prescoring_factoring (separate from JWT apply-lead)."""

    username: str = os.getenv("FACTORING_PRESCORING_USER", "")
    password: str = os.getenv("FACTORING_PRESCORING_PASSWORD", "")


class CORSConfig:
    allow_origins: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    allow_credentials: bool = True
    allow_methods: List[str] = ["*"]
    allow_headers: List[str] = ["*"]


class MyncaConfig:
    """Client ЭЦП via existing MyNCA (same service as constructor ClientSignController)."""

    base_url: str = os.getenv("MYNCA_BASE_URL", "https://nca.smartremont.kz/api")
    token: str = os.getenv("MYNCA_TOKEN", "")
    office_public_url: str = os.getenv(
        "OFFICE_PUBLIC_URL",
        "https://office.smartremont.kz",
    )
    public_base_url: str = os.getenv(
        "PUBLIC_BASE_URL",
        "https://devintegration.smart-remont.kz",
    )
    # Company EDS keys used to sign cession (assignment) documents live encrypted
    # in nca.company_key_store_tab (same DB, managed by the `myspace` admin app —
    # see myspace-backend/nca/). We decrypt them in Postgres via
    # nca.company_key_store__get_decrypted(id, master_key) — same master key as
    # myspace's NCA_MASTER_KEY env var. Which key to use is resolved from
    # client_request_tab.company_id → nca.company_key_store__read_by_company.
    nca_master_key: str = os.getenv("NCA_MASTER_KEY", "")


class AppConfig:
    env: str = os.getenv("APP_ENV", "stage")


class MinioConfig:
    """MinIO / S3-compatible storage — same keys as smremont `application.ini` → minio.*."""

    endpoint: str = os.getenv("MINIO_ENDPOINT", "").rstrip("/")
    bucket: str = os.getenv("MINIO_BUCKET", "smartremont")
    access_key: str = os.getenv("MINIO_ACCESS_KEY", "")
    secret_key: str = os.getenv("MINIO_SECRET_KEY", "")
    region: str = os.getenv("MINIO_REGION", "us-east-1")

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.access_key and self.secret_key and self.bucket)


def _first_nonempty_env(*names: str, default: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value.rstrip("/")
    return default.rstrip("/")


class FileStoreConfig:
    """Public URLs for stored files: STORAGE_PUBLIC_URL + `/documents/...`."""

    public_base_url: str = _first_nonempty_env(
        "STORAGE_PUBLIC_URL",
        "OFFICE_PUBLIC_URL",
        default="https://office.smartremont.kz",
    )

    def file_url(self, logical_path: str) -> str:
        return f"{self.public_base_url}{logical_path}"


cors_config = CORSConfig()
big_integration_auth_config = BigIntegrationAuthConfig()
installment_auth_config = InstallmentAuthConfig()
integration_context_config = IntegrationContextConfig()
factoring_auth_config = FactoringAuthConfig()
storage_auth_config = StorageAuthConfig()
factoring_prescoring_config = FactoringPrescoringConfig()
mynca_config = MyncaConfig()
app_config = AppConfig()
minio_config = MinioConfig()
file_store_config = FileStoreConfig()
