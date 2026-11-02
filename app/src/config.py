from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Self

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _list_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _first_nonempty_env(*names: str, default: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value.rstrip("/")
    return default.rstrip("/")


def _url_env(name: str) -> str:
    return os.getenv(name, "").strip().rstrip("/")


class BigIntegrationAuthConfig:
    username: str = os.getenv("INTEGRATION_HS_BI_USER", "hs_bi")
    password: str = os.getenv("INTEGRATION_HS_BI_PASSWORD", "")


class KaspiProxyConfig:
    """Shared with nginx, which alone may reach the Kaspi route. Empty secret: 503."""

    secret: str = os.getenv("KASPI_PROXY_SECRET", "")


class SmsCronAuthConfig:
    """Basic auth of the crontab wget lines for /api/sms/*. Empty: 503."""

    username: str = os.getenv("SMS_CRON_USER", "")
    password: str = os.getenv("SMS_CRON_PASSWORD", "")


class AutoSignCronAuthConfig:
    """Basic auth of the crontab wget lines for /api/auto_sign/*. Empty: 503."""

    username: str = os.getenv("AUTO_SIGN_CRON_USER", "")
    password: str = os.getenv("AUTO_SIGN_CRON_PASSWORD", "")


class Buh1cAuthConfig:
    """Basic auth of 1C for /api/buh_1c/*, the login written in PHP ``buhAction``. Empty: 503."""

    username: str = os.getenv("BUH_1C_USER", "")
    password: str = os.getenv("BUH_1C_PASSWORD", "")


class ShowroomCronAuthConfig:
    """Basic auth of the crontab wget line for /api/showroom/*. Empty: 503."""

    username: str = os.getenv("SHOWROOM_CRON_USER", "")
    password: str = os.getenv("SHOWROOM_CRON_PASSWORD", "")


class ShowroomConfig:
    """Where the list of showrooms goes. Taken as it is: the slash at its end is part of the address."""

    bi_url: str = os.getenv("SHOWROOM_BI_URL", "").strip()


class AutoSignConfig:
    """Crons of company signing. Empty by default, so a stand without them signs nothing: 503.

    Its own variables: MYNCA_BASE_URL has a production default, and NCA_MASTER_KEY is set wherever
    cession signing is, which must not arm this cron by itself.
    """

    mynca_base_url: str = _url_env("AUTO_SIGN_MYNCA_BASE_URL")
    mynca_token: str = os.getenv("AUTO_SIGN_MYNCA_TOKEN", "").strip()
    # Decrypts nca.company_key_store_tab. In PHP: constants.master_key.
    master_key: str = os.getenv("AUTO_SIGN_MASTER_KEY", "")

    @property
    def signing_is_configured(self) -> bool:
        # A stand given real values by mistake must not put the company signature on anything.
        return app_config.env == "prod" and self.upload_is_configured and bool(self.master_key)

    @property
    def upload_is_configured(self) -> bool:
        # The token and the company key travel to this address.
        return self.mynca_base_url.startswith("https://") and bool(self.mynca_token)


class KcellConfig:
    """Sending and status reads are separate Kcell addresses with separate passwords. Empty: 503."""

    send_url: str = os.getenv("KCELL_SEND_URL", "")
    send_username: str = os.getenv("KCELL_SEND_USER", "")
    send_password: str = os.getenv("KCELL_SEND_PASSWORD", "")
    status_url: str = os.getenv("KCELL_BATCH_STATUS_URL", "").rstrip("/")
    status_username: str = os.getenv("KCELL_BATCH_STATUS_USER", "")
    status_password: str = os.getenv("KCELL_BATCH_STATUS_PASSWORD", "")

    @property
    def send_is_configured(self) -> bool:
        return bool(self.send_url and self.send_username and self.send_password)

    @property
    def status_is_configured(self) -> bool:
        return bool(self.status_url and self.status_username and self.status_password)


@dataclass(frozen=True)
class ClientSignConfig:
    """client-sign pages moved from PHP. Empty by default: the step that needs a value answers 503."""

    mynca_base_url: str = ""
    mynca_token: str = ""
    # The address clients and MyNCA see; PHP took it from the request (getHttpHost).
    public_url: str = ""
    # Serves /dict/report, /dict/ds-download and the /documents/ files.
    office_url: str = ""
    myspace_api_url: str = ""
    # Partner backend with /get_signed_file, for documents without a MyNCA group.
    partner_url: str = ""
    # Keys the back_url given to MyNCA. It is how a callback proves where it came from.
    callback_secret: str = ""

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            mynca_base_url=_url_env("CLIENT_SIGN_MYNCA_BASE_URL"),
            mynca_token=os.getenv("CLIENT_SIGN_MYNCA_TOKEN", "").strip(),
            public_url=_url_env("CLIENT_SIGN_PUBLIC_URL"),
            office_url=_url_env("CLIENT_SIGN_OFFICE_URL"),
            myspace_api_url=_url_env("CLIENT_SIGN_MYSPACE_API_URL"),
            partner_url=_url_env("CLIENT_SIGN_PARTNER_URL"),
            callback_secret=os.getenv("CLIENT_SIGN_CALLBACK_SECRET", ""),
        )


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
    issuer_allowlist: tuple[str, ...] = _list_env("INTEGRATION_CONTEXT_ISSUERS", "sr-crm")

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
    allow_origins: tuple[str, ...] = _list_env(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    allow_credentials: bool = True
    allow_methods: tuple[str, ...] = ("*",)
    allow_headers: tuple[str, ...] = ("*",)


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
    version: str = os.getenv("APP_VERSION", "0.1.0")
    # /scalar and /openapi.json. Off unless enabled: the schema lists every webhook
    # and internal route, so production keeps it closed.
    api_docs_enabled: bool = _bool_env("API_DOCS_ENABLED")


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


class FileStoreConfig:
    """Public URLs for stored files: STORAGE_PUBLIC_URL + `/documents/...`."""

    public_base_url: str = _first_nonempty_env(
        "STORAGE_PUBLIC_URL",
        "OFFICE_PUBLIC_URL",
        default="https://office.smartremont.kz",
    )

    def file_url(self, logical_path: str) -> str:
        return f"{self.public_base_url}{logical_path}"


class KlRecognitionAuthConfig:
    """Basic auth for /api/v1/kl_recognition/*. No fallback: empty → 503."""

    username: str = os.getenv("KL_RECOGNITION_API_USER", "")
    password: str = os.getenv("KL_RECOGNITION_API_PASSWORD", "")


class KlRecognitionConfig:
    """Apartment sheet (KL) recognition: BI loadSheet + Composer through the Cursor SDK."""

    # Public BI endpoint rendering the KL PDF by flat GUID; no BI credentials needed.
    load_sheet_url: str = os.getenv(
        "BI_LOAD_SHEET_URL",
        "https://apigw.bi.group/crm-document/api/placement/public/loadSheet",
    )
    # Cursor account key the agent runs under (Cursor dashboard → Integrations → API Keys).
    cursor_api_key: str = os.getenv("CURSOR_API_KEY", "")
    cursor_model: str = os.getenv("CURSOR_MODEL", "composer-2.5")
    node_bin: str = os.getenv("KL_RECOGNITION_NODE_BIN", "node")
    # One recognition takes 15–30 s; the limit guards against a hung agent run.
    agent_timeout_seconds: int = int(os.getenv("KL_RECOGNITION_AGENT_TIMEOUT", "300"))
    max_parallel: int = int(os.getenv("KL_RECOGNITION_MAX_PARALLEL", "4"))


cors_config = CORSConfig()
big_integration_auth_config = BigIntegrationAuthConfig()
kaspi_proxy_config = KaspiProxyConfig()
sms_cron_auth_config = SmsCronAuthConfig()
auto_sign_cron_auth_config = AutoSignCronAuthConfig()
buh_1c_auth_config = Buh1cAuthConfig()
showroom_cron_auth_config = ShowroomCronAuthConfig()
showroom_config = ShowroomConfig()
auto_sign_config = AutoSignConfig()
kcell_config = KcellConfig()
client_sign_config = ClientSignConfig.from_env()
installment_auth_config = InstallmentAuthConfig()
integration_context_config = IntegrationContextConfig()
factoring_auth_config = FactoringAuthConfig()
storage_auth_config = StorageAuthConfig()
factoring_prescoring_config = FactoringPrescoringConfig()
mynca_config = MyncaConfig()
app_config = AppConfig()
minio_config = MinioConfig()
file_store_config = FileStoreConfig()
kl_recognition_auth_config = KlRecognitionAuthConfig()
kl_recognition_config = KlRecognitionConfig()
