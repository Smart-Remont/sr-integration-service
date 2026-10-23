import httpx
from loguru import logger

from src.config import KlRecognitionConfig

# BI renders the PDF on every request: usually 3–8 s, the generator sometimes needs more.
LOAD_SHEET_TIMEOUT_SECONDS = 60.0


class KlSheetNotFoundError(Exception):
    """BI has no KL for the flat (old residential complexes answer 500 "No images found")."""


class KlSheetDownloadError(Exception):
    """BI is unreachable or returned something that is not a PDF."""


class BiLoadSheetClient:
    """Downloads the apartment sheet (KL) PDF from the public BI loadSheet endpoint."""

    def __init__(self, config: KlRecognitionConfig) -> None:
        self._config = config

    async def download(self, *, flat_guid: str | None, pdf_url: str | None) -> bytes:
        if pdf_url:
            url, params = pdf_url, None
        else:
            url = self._config.load_sheet_url
            params = {"isMortgage": "false", "placementId": flat_guid}
        headers = {"Accept": "*/*", "Origin": "https://bi.group", "Referer": "https://bi.group/"}
        try:
            async with httpx.AsyncClient(timeout=LOAD_SHEET_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise KlSheetDownloadError(f"BI недоступен: {exc}") from exc

        if response.status_code != 200:
            logger.info(
                "BI loadSheet {flat}: HTTP {code} {body}",
                flat=flat_guid or pdf_url,
                code=response.status_code,
                body=response.text[:300],
            )
            raise KlSheetNotFoundError(f"BI не отдал КЛ (HTTP {response.status_code})")
        if not response.content.startswith(b"%PDF"):
            raise KlSheetDownloadError("BI вернул не PDF")
        return response.content
