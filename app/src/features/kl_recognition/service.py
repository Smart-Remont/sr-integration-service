import asyncio
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from .client import BiLoadSheetClient, KlSheetDownloadError, KlSheetNotFoundError
from .composer import ComposerClient, ComposerError
from .plan_images import PlanPageMissingError
from .result import build_result, first_json_object, not_recognized_result
from .schemas import KlRecognitionMeta, KlRecognitionResult, KlRecognizeRequest

PROMPT_PATH = Path(__file__).parent / "prompt.md"
IMAGE_BUILDS_AT_ONCE = 2

_image_semaphore: asyncio.Semaphore | None = None


def _image_slots() -> asyncio.Semaphore:
    global _image_semaphore
    if _image_semaphore is None:
        _image_semaphore = asyncio.Semaphore(IMAGE_BUILDS_AT_ONCE)
    return _image_semaphore


class KlRecognitionService:
    """KL by flat GUID → plan images → Composer → rooms and areas with a checked status.

    Every failure that is about the sheet (no KL, no plan, agent gave no answer) becomes a
    `not_recognized` result: the caller always gets an answer with the reason.
    """

    def __init__(
        self,
        sheet_client: BiLoadSheetClient,
        composer: ComposerClient,
        build_images: Callable[[bytes], list[bytes]],
        model: str,
        prompt_template: str | None = None,
    ) -> None:
        self._sheet_client = sheet_client
        self._composer = composer
        self._build_images = build_images
        self._model = model
        self._prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")

    async def recognize(self, request: KlRecognizeRequest) -> KlRecognitionResult:
        source = str(request.flat_guid or request.pdf_url)
        meta = KlRecognitionMeta(model=self._model)
        try:
            pdf = await self._sheet_client.download(
                flat_guid=str(request.flat_guid) if request.flat_guid else None,
                pdf_url=str(request.pdf_url) if request.pdf_url else None,
            )
            # Cropping is CPU-bound and takes hundreds of MB per page (18 line maps): run it off
            # the event loop and only a couple at a time, or a burst of requests runs out of memory.
            async with _image_slots():
                images = await asyncio.to_thread(self._build_images, pdf)
        except (KlSheetNotFoundError, KlSheetDownloadError, PlanPageMissingError) as exc:
            logger.info("KL {source}: {reason}", source=source, reason=exc)
            return not_recognized_result(str(exc), request.bi_area, meta)

        bi_text = f"{request.bi_area:.2f}" if request.bi_area is not None else ""
        try:
            answer = await self._composer.read_plan(
                self._prompt_template.replace("{BI_AREA}", bi_text), images
            )
        except ComposerError as exc:
            logger.warning("KL {source}: Composer failed: {reason}", source=source, reason=exc)
            return not_recognized_result(str(exc), request.bi_area, meta)

        meta = KlRecognitionMeta(
            model=self._model,
            seconds=answer.seconds,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
        )
        data = first_json_object(answer.text)
        if data is None:
            logger.warning(
                "KL {source}: answer without JSON: {text}", source=source, text=answer.text[:500]
            )
            return not_recognized_result("ответ агента без JSON", request.bi_area, meta)

        result = build_result(data, request.bi_area, meta)
        logger.info(
            "KL {source}: {status} ({reason}), {seconds} s",
            source=source,
            status=result.status,
            reason=result.reason,
            seconds=answer.seconds,
        )
        return result
