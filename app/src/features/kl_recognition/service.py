import asyncio
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from .client import BiLoadSheetClient, KlSheetDownloadError, KlSheetNotFoundError
from .composer import ComposerAnswer, ComposerClient, ComposerError
from .plan_images import PlanPageMissingError, build_page_images
from .result import build_result, first_json_object, not_recognized_result
from .schemas import (
    KlRecognitionMeta,
    KlRecognitionResult,
    KlRecognizeRequest,
    RecognitionStatus,
)
from .storage_recheck import (
    RECHECK_PROMPT_PATH,
    apply_recheck,
    recheck_prompt,
    storages_to_recheck,
)

PROMPT_PATH = Path(__file__).parent / "prompt.md"
IMAGE_BUILDS_AT_ONCE = 2
CUT_PLAN_IMAGES = (
    "к сообщению приложены 5 картинок: 1 — план квартиры, вырезанный со стр. 2 квартирного "
    "листа; 2–5 — его четверти крупно, с перекрытием (верх-лево, верх-право, низ-лево, низ-право)."
)
WHOLE_PAGE_IMAGES = (
    "к сообщению приложены 5 картинок: 1 — стр. 2 квартирного листа целиком; 2–5 — её четверти "
    "крупно, с перекрытием (верх-лево, верх-право, низ-лево, низ-право). Кроме плана квартиры на "
    "странице бывают легенда, схема высоты окон, таблицы и тексты — их не читай; если планов два "
    "(два этажа), выпиши помещения обоих."
)
# The cut plan is suspect when the rooms read from it miss the sheet total by more than this
# share, or none are found: part of the plan was cut off (a plan split into two regions, a
# second floor) or the cut is not the plan at all (walls drawn as colour fills). Smaller misses
# are misprints on the sheet itself (seen up to 1.4 %) and the whole page would not help.
CUT_PLAN_MISS = 0.03

_image_semaphore: asyncio.Semaphore | None = None


def _plus(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)


def _add(meta: KlRecognitionMeta, answer: ComposerAnswer) -> KlRecognitionMeta:
    """Time and tokens of all Composer calls for one sheet."""
    return KlRecognitionMeta(
        model=meta.model,
        seconds=_plus(meta.seconds, answer.seconds),
        input_tokens=_plus(meta.input_tokens, answer.input_tokens),
        output_tokens=_plus(meta.output_tokens, answer.output_tokens),
    )


def _cut_plan_failed(result: KlRecognitionResult, bi_area: float | None) -> bool:
    if result.status is not RecognitionStatus.NOT_RECOGNIZED:
        return False
    rooms_sum = result.checks.rooms_sum
    target = result.checks.sheet_total if result.checks.sheet_total is not None else bi_area
    if not rooms_sum:
        return True
    return target is not None and abs(rooms_sum - target) > target * CUT_PLAN_MISS


def _image_slots() -> asyncio.Semaphore:
    global _image_semaphore
    if _image_semaphore is None:
        _image_semaphore = asyncio.Semaphore(IMAGE_BUILDS_AT_ONCE)
    return _image_semaphore


class KlRecognitionService:
    """KL by flat GUID → plan images → Composer → rooms and areas with a checked status.

    The plan cut from page 2 is read first; when that reading fails badly (no plan found, the
    rooms far from the sheet total) the whole page 2 is read instead.

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
        build_page_images: Callable[[bytes], list[bytes]] = build_page_images,
    ) -> None:
        self._sheet_client = sheet_client
        self._composer = composer
        self._build_images = build_images
        self._build_page_images = build_page_images
        self._model = model
        self._prompt_template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")
        self._recheck_template = RECHECK_PROMPT_PATH.read_text(encoding="utf-8")

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

        result, answered = await self._read(source, request, images, CUT_PLAN_IMAGES, meta)
        if not answered or not _cut_plan_failed(result, request.bi_area):
            return result

        logger.info(
            "KL {source}: cut plan failed ({reason}), reading the whole page",
            source=source,
            reason=result.reason,
        )
        try:
            async with _image_slots():
                page_images = await asyncio.to_thread(self._build_page_images, pdf)
        except PlanPageMissingError:
            return result
        whole, _ = await self._read(source, request, page_images, WHOLE_PAGE_IMAGES, result.meta)
        if whole.status is not RecognitionStatus.RECOGNIZED:
            whole.reason = f"{whole.reason}; по вырезке плана: {result.reason}"
        return whole

    async def _read(
        self,
        source: str,
        request: KlRecognizeRequest,
        images: list[bytes],
        images_text: str,
        meta: KlRecognitionMeta,
    ) -> tuple[KlRecognitionResult, bool]:
        """One reading of the images; False when the agent gave no usable answer."""
        bi_text = f"{request.bi_area:.2f}" if request.bi_area is not None else ""
        prompt = self._prompt_template.replace("{BI_AREA}", bi_text).replace(
            "{IMAGES}", images_text
        )
        try:
            answer = await self._composer.read_plan(prompt, images)
        except ComposerError as exc:
            logger.warning("KL {source}: Composer failed: {reason}", source=source, reason=exc)
            return not_recognized_result(str(exc), request.bi_area, meta), False

        meta = _add(meta, answer)
        data = first_json_object(answer.text)
        if data is None:
            logger.warning(
                "KL {source}: answer without JSON: {text}", source=source, text=answer.text[:500]
            )
            return not_recognized_result("ответ агента без JSON", request.bi_area, meta), False

        meta = await self._recheck_storages(source, data, images, meta)
        result = build_result(data, request.bi_area, meta)
        logger.info(
            "KL {source}: {status} ({reason}), {seconds} s",
            source=source,
            status=result.status,
            reason=result.reason,
            seconds=meta.seconds,
        )
        return result, True

    async def _recheck_storages(
        self, source: str, data: dict, images: list[bytes], meta: KlRecognitionMeta
    ) -> KlRecognitionMeta:
        """Asks once more about wardrobes and storerooms not tied to a room (see storage_recheck)."""
        storages = storages_to_recheck(data)
        if not storages:
            return meta
        try:
            answer = await self._composer.read_plan(
                recheck_prompt(self._recheck_template, data, storages), images
            )
        except ComposerError as exc:
            # The first answer stands on its own: without the second look nothing is merged.
            logger.warning(
                "KL {source}: storage recheck failed: {reason}", source=source, reason=exc
            )
            return meta
        found = apply_recheck(data, first_json_object(answer.text))
        logger.info(
            "KL {source}: storage recheck {found} of {asked}",
            source=source,
            found=found,
            asked=len(storages),
        )
        return _add(meta, answer)
