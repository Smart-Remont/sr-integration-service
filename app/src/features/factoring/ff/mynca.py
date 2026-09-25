from typing import Any

import httpx
from fastapi import HTTPException, status
from loguru import logger


class MyncaClientError(Exception):
    def __init__(self, detail: str, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


_SIGNED_STATUSES = {"SUCCESS", "SIGNED", "COMPLETED", "DONE", "PROCESSED"}
_SIGNED_SESSIONS = {"processed", "completed", "signed", "success"}


class MyncaClient:
    """HTTP client for nca.smartremont.kz — same API as constructor MyNcaClient."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "SmartRemont-integrations-sr-factoring/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def require_configured(self) -> None:
        if not self.base_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MYNCA_BASE_URL is not configured.",
            )
        if not self.token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MYNCA_TOKEN is not configured.",
            )

    async def docx_to_pdf(self, docx_bytes: bytes) -> bytes:
        payload = {"docx": _b64(docx_bytes), "is_file": False}
        body = await self._request_json("POST", "/document/docx-to-pdf", json=payload)
        pdf_b64 = _nested_str(body, "pdf") or _nested_str(body, "data", "pdf")
        if not pdf_b64:
            raise MyncaClientError("MyNCA docx-to-pdf returned empty PDF.")
        try:
            import base64

            return base64.b64decode(pdf_b64)
        except Exception as exc:  # noqa: BLE001
            raise MyncaClientError("MyNCA docx-to-pdf returned invalid PDF.") from exc

    async def sign_create(
        self,
        *,
        file_name: str,
        pdf_bytes: bytes,
        ext_id: int,
        meta_data: dict[str, Any],
        back_url: str,
        return_url: str,
        exp_minutes: int = 60,
    ) -> tuple[str, str]:
        import base64

        payload = {
            "file_name": file_name,
            "file_content": base64.b64encode(pdf_bytes).decode("ascii"),
            "ext_id": ext_id,
            "meta_data": meta_data,
            "back_url": back_url,
            "return_url": return_url,
            "exp_minutes": exp_minutes,
        }
        body = await self._request_json("POST", "/sign/create", json=payload)
        data = _unwrap_data(body)
        sign_url = data.get("sign_url")
        sign_process_id = data.get("sign_process_id")
        if not isinstance(sign_url, str) or not sign_url:
            raise MyncaClientError("MyNCA sign/create did not return sign_url.")
        if not isinstance(sign_process_id, str) or not sign_process_id:
            raise MyncaClientError("MyNCA sign/create did not return sign_process_id.")
        return sign_url, sign_process_id

    async def sign_batch(
        self,
        *,
        documents: list[dict[str, Any]],
        back_url: str,
        return_url: str,
        exp_minutes: int = 60,
        atomic: bool = True,
    ) -> tuple[str, list[dict[str, str | None]]]:
        """POST /sign/batch/create — one client signature for several PDFs.

        ``atomic`` rolls every document back when the back_url rejects any of them.
        """
        import base64

        payload = {
            "documents": [
                {
                    "file_name": item["file_name"],
                    "file_content": base64.b64encode(item["pdf_bytes"]).decode("ascii"),
                    "ext_id": item["ext_id"],
                    "meta_data": item.get("meta_data") or {},
                }
                for item in documents
            ],
            "back_url": back_url,
            "return_url": return_url,
            "exp_minutes": exp_minutes,
            "atomic": atomic,
            "sign_doc_method": "cms",
        }
        body = await self._request_json("POST", "/sign/batch/create", json=payload)
        return parse_sign_batch_response(body)

    async def sign_status(self, sign_process_id: str) -> dict[str, Any]:
        body = await self._request_json(
            "GET",
            f"/sign/{sign_process_id}/status",
        )
        return _unwrap_data(body)

    def is_process_signed(self, payload: dict[str, Any]) -> bool:
        if payload.get("is_signed") is True:
            return True
        status_value = str(payload.get("status") or "").strip().upper()
        session = str(payload.get("sign_session_status") or "").strip().lower()
        return status_value in _SIGNED_STATUSES or session in _SIGNED_SESSIONS

    async def sign_download_pdf(self, sign_process_id: str) -> bytes:
        return await self._request_bytes("GET", f"/sign/{sign_process_id}/download")

    async def sign_get_file(self, sign_process_id: str) -> str:
        """GET /sign/{id}/file — returns base64 file_content (legacy MyNcaClient.signGetFile)."""
        body = await self._request_json("GET", f"/sign/{sign_process_id}/file")
        file_b64 = _nested_str(body, "data", "data", "file_content") or _nested_str(
            body, "data", "file_content"
        )
        if not file_b64:
            raise MyncaClientError("MyNCA sign/file returned no file_content.")
        return file_b64

    async def sign_group_download_pdf(self, group_id: str) -> bytes:
        return await self._request_bytes("GET", f"/sign/group/{group_id}/download")

    async def pkcs12_info(
        self,
        *,
        key_b64: str,
        password: str,
        key_alias: str | None = None,
    ) -> dict[str, Any]:
        """Reads certificate info (incl. `pubkey` / X.509 DER public key) from a PKCS#12 key."""
        payload: dict[str, Any] = {"key": key_b64, "password": password}
        if key_alias:
            payload["keyAlias"] = key_alias
        body = await self._request_json("POST", "/pkcs12/info", json=payload)
        certificate = body.get("certificate")
        if not isinstance(certificate, dict):
            raise MyncaClientError("MyNCA pkcs12/info returned no certificate.")
        return certificate

    async def cms_sign(
        self,
        *,
        data: bytes,
        key_b64: str,
        password: str,
        key_alias: str | None = None,
        detached: bool = True,
        with_tsp: bool = False,
    ) -> bytes:
        """Signs raw bytes as CMS (PKCS#7). Returns the CMS blob (DER)."""
        payload: dict[str, Any] = {
            "data": _b64(data),
            "signer": {"key": key_b64, "password": password},
            "detached": detached,
            "withTsp": with_tsp,
        }
        if key_alias:
            payload["signer"]["keyAlias"] = key_alias
        body = await self._request_json("POST", "/cms/sign", json=payload)
        cms_b64 = _nested_str(body, "cms") or _nested_str(body, "data", "cms")
        if not cms_b64:
            raise MyncaClientError("MyNCA cms/sign returned no cms.")
        try:
            import base64

            return base64.b64decode(cms_b64)
        except Exception as exc:  # noqa: BLE001
            raise MyncaClientError("MyNCA cms/sign returned invalid CMS.") from exc

    async def cms_sign_save(
        self,
        *,
        data: bytes,
        key_b64: str,
        password: str,
        key_alias: str | None = None,
        detached: bool = True,
        with_tsp: bool = False,
        file_name: str | None = None,
        ext_id: int | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Same as cms_sign, but persisted server-side (MyNCA sign_process_tab):
        returns sign_process_id/group_id for traceability. Does NOT return the
        raw CMS bytes — fetch those separately via download_cms()."""
        payload: dict[str, Any] = {
            "data": _b64(data),
            "signer": {"key": key_b64, "password": password},
            "detached": detached,
            "withTsp": with_tsp,
        }
        if key_alias:
            payload["signer"]["keyAlias"] = key_alias
        if file_name:
            payload["file_name"] = file_name
        if ext_id is not None:
            payload["ext_id"] = ext_id
        if group_id:
            payload["group_id"] = group_id
        body = await self._request_json("POST", "/cms/sign-save", json=payload)
        data = _unwrap_data(body)
        sign_process_id = data.get("sign_process_id") or body.get("sign_process_id")
        if not isinstance(sign_process_id, str) or not sign_process_id:
            raise MyncaClientError("MyNCA cms/sign-save did not return sign_process_id.")
        return {
            "sign_process_id": sign_process_id,
            "dn_name": str(data.get("dn_name") or body.get("dn_name") or ""),
            "group_id": data.get("group_id") or body.get("group_id"),
        }

    async def download_cms(self, sign_process_id: str) -> bytes:
        """Fetches the raw CMS (PKCS#7) blob for a sign_process_id created via
        cms/sign-save."""
        return await self._request_bytes("GET", f"/sign/{sign_process_id}/download-cms")

    async def sign_rollback(self, sign_process_id: str) -> None:
        """POST /sign/{id}/rollback — undo a persisted cms/sign-save on MyNCA failure."""
        await self._request_json("POST", f"/sign/{sign_process_id}/rollback")

    async def pkcs12_validate(
        self,
        *,
        key_b64: str,
        password: str,
        key_alias: str | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "key": key_b64,
            "password": password,
            "revocationCheck": ["OCSP", "CRL"],
        }
        if key_alias:
            payload["keyAlias"] = key_alias
        body = await self._request_json("POST", "/pkcs12/info", json=payload)
        data = _unwrap_data(body)
        if data.get("valid") is True:
            return True
        certificate = body.get("certificate")
        if isinstance(certificate, dict) and certificate.get("valid") is True:
            return True
        return False

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(method, path, json=json)
        try:
            body = response.json()
        except ValueError as exc:
            raise MyncaClientError(
                f"MyNCA {path} returned non-JSON (HTTP {response.status_code})."
            ) from exc
        if not isinstance(body, dict):
            raise MyncaClientError(f"MyNCA {path} returned unexpected JSON.")
        if response.status_code >= 400:
            message = (
                _nested_str(body, "message")
                or _nested_str(body, "data", "message")
                or f"MyNCA HTTP {response.status_code}"
            )
            raise MyncaClientError(message, status_code=502)
        return body

    async def _request_bytes(self, method: str, path: str) -> bytes:
        response = await self._request(method, path, json=None)
        if response.status_code >= 400:
            raise MyncaClientError(
                f"MyNCA {path} failed (HTTP {response.status_code})."
            )
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            body = response.json()
            pdf_b64 = _nested_str(body, "pdf") or _nested_str(body, "data", "pdf")
            if pdf_b64:
                import base64

                return base64.b64decode(pdf_b64)
            raise MyncaClientError(f"MyNCA {path} JSON response has no PDF.")
        if not response.content:
            raise MyncaClientError(f"MyNCA {path} returned empty body.")
        return response.content

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(timeout=60.0, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                )
        except httpx.RequestError as exc:
            logger.error("MyNCA transport error | url={url} error={error}", url=url, error=exc)
            raise MyncaClientError(f"MyNCA request failed: {exc}") from exc
        logger.info(
            "MyNCA HTTP | method={method} path={path} status={status}",
            method=method,
            path=path,
            status=response.status_code,
        )
        return response


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def parse_sign_batch_response(
    body: dict[str, Any],
) -> tuple[str, list[dict[str, str | None]]]:
    data = _unwrap_data(body)
    sign_url = data.get("sign_url") if isinstance(data.get("sign_url"), str) else None
    if not sign_url:
        sign_url = body.get("sign_url") if isinstance(body.get("sign_url"), str) else None
    if not sign_url or not sign_url.strip():
        raise MyncaClientError("MyNCA sign/batch did not return sign_url.")

    raw_documents = data.get("documents")
    if not isinstance(raw_documents, list):
        raw_documents = body.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise MyncaClientError("MyNCA sign/batch did not return documents.")

    parsed: list[dict[str, str | None]] = []
    for item in raw_documents:
        if not isinstance(item, dict):
            raise MyncaClientError("MyNCA sign/batch returned a document that is not an object.")
        sign_process_id = item.get("sign_process_id")
        if not isinstance(sign_process_id, str) or not sign_process_id.strip():
            raise MyncaClientError("MyNCA sign/batch document is missing sign_process_id.")
        file_name = item.get("file_name")
        group_id = item.get("group_id")
        parsed.append(
            {
                "file_name": file_name.strip() if isinstance(file_name, str) else None,
                "sign_process_id": sign_process_id.strip(),
                "group_id": group_id.strip() if isinstance(group_id, str) and group_id.strip() else None,
            }
        )
    return sign_url.strip(), parsed


def _unwrap_data(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            return nested
        return data
    return body


def _nested_str(payload: dict[str, Any], *keys: str) -> str | None:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, str) and current.strip():
        return current
    return None
