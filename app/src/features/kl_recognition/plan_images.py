"""Images Composer reads: the apartment plan cut from KL page 2 plus its four enlarged quarters.

Cropping finds the walls — long straight lines in two perpendicular directions — with no
reliance on colour, so it works across BI sheet styles (checked on 48 sheets of 32 complexes).
Quarters overlap so a label or door on a seam stays whole in at least one of them.
"""

import cv2
import numpy as np
import pymupdf

# Pages larger than this are decoded reduced: a 15000 px page exhausts memory.
MAX_PAGE_SIDE = 3200
PLAN_SIDE = 2000
TILE_SIDE = 1600
TILE_OVERLAP = 0.15
JPEG_QUALITY = 92
ANGLES = list(range(0, 180, 10))
# A second region counts as another plan (two floors on one page) only above these shares.
SECOND_PLAN_SCORE_RATIO = 0.45
SECOND_PLAN_AREA_RATIO = 0.45


class PlanPageMissingError(Exception):
    """The KL has no page 2 or it holds no image."""


def page_two_image(pdf_bytes: bytes) -> np.ndarray:
    """Page 2 as stored in the PDF (each KL page is one JPEG), without re-rendering."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if len(doc) < 2 or not doc[1].get_images(full=True):
            raise PlanPageMissingError("в КЛ нет страницы 2 с планом")
        info = doc.extract_image(doc[1].get_images(full=True)[0][0])
    long_side = max(info["width"], info["height"])
    flag = cv2.IMREAD_COLOR
    for divider, reduced in (
        (8, cv2.IMREAD_REDUCED_COLOR_8),
        (4, cv2.IMREAD_REDUCED_COLOR_4),
        (2, cv2.IMREAD_REDUCED_COLOR_2),
    ):
        if long_side / divider >= MAX_PAGE_SIDE:
            flag = reduced
            break
    image = cv2.imdecode(np.frombuffer(info["image"], np.uint8), flag)
    if image is None:
        raise PlanPageMissingError("картинку страницы 2 не удалось прочитать")
    return _fit(image, MAX_PAGE_SIDE) if max(image.shape[:2]) > MAX_PAGE_SIDE else image


def _line_kernel(length: int, angle: int) -> np.ndarray:
    kernel = np.zeros((length, length), np.uint8)
    centre = (length - 1) / 2
    dx, dy = np.cos(np.radians(angle)) * centre, -np.sin(np.radians(angle)) * centre
    start = (round(centre - dx), round(centre - dy))
    end = (round(centre + dx), round(centre + dy))
    cv2.line(kernel, start, end, 1, 1)
    return kernel


def _wall_lines(gray: np.ndarray, long_side: int) -> tuple[np.ndarray, list[np.ndarray]]:
    """Dark pixels without solid fills, and the long straight lines per angle."""
    dark = (gray < 170).astype(np.uint8) * 255
    margin = max(2, int(long_side * 0.006))  # a frame along the page edge is not a wall
    dark[:margin, :] = 0
    dark[-margin:, :] = 0
    dark[:, :margin] = 0
    dark[:, -margin:] = 0
    solid_side = int(long_side * 0.012)  # title bars and QR codes are thicker than any wall
    solid = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (solid_side, solid_side))
    )
    dark = cv2.bitwise_and(dark, cv2.bitwise_not(cv2.dilate(solid, np.ones((9, 9), np.uint8))))
    length = int(long_side * 0.025) | 1  # letters and digits are shorter and vanish
    by_angle = [
        cv2.morphologyEx(dark, cv2.MORPH_OPEN, _line_kernel(length, angle)) for angle in ANGLES
    ]
    return dark, by_angle


def find_plan_box(page: np.ndarray) -> tuple[int, int, int, int] | None:
    """Frame of the apartment plan(s) on the page; None when no walls are found."""
    height, width = page.shape[:2]
    long_side = max(height, width)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    dark, by_angle = _wall_lines(gray, long_side)
    lines = by_angle[0].copy()
    for band in by_angle[1:]:
        cv2.bitwise_or(lines, band, dst=lines)

    gap = int(long_side * 0.03)  # glues the walls of one plan across door openings
    merged = cv2.morphologyEx(
        lines, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (gap, gap))
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(merged)
    if count < 2:
        return None

    # A plan has many walls in two perpendicular directions; text panels — in one.
    edge = cv2.bitwise_and(dark, cv2.bitwise_not(cv2.erode(dark, np.ones((3, 3), np.uint8))))
    counts = np.stack(
        [np.bincount(labels[(band > 0) & (edge > 0)], minlength=count) for band in by_angle]
    )
    score = np.minimum(counts, np.roll(counts, len(ANGLES) // 2, axis=0)).max(axis=0)
    score[0] = 0

    touch = cv2.dilate(dark, np.ones((7, 7), np.uint8))
    _, touch_labels, touch_stats, _ = cv2.connectedComponentsWithStats(touch)
    boxes: list[tuple[int, int, int, int]] = []
    best_score, best_area = int(score.max()), 0
    for region in np.argsort(-score):
        region_score = int(score[region])
        if region_score == 0 or region_score < best_score * SECOND_PLAN_SCORE_RATIO:
            break
        # everything dark physically connected to the region's walls is part of the plan
        parts = np.unique(touch_labels[(labels == region) & (lines > 0)])
        parts = parts[parts > 0]
        x, y, w, h = stats[region][:4]
        x1 = min([x, *(touch_stats[p][0] for p in parts)])
        y1 = min([y, *(touch_stats[p][1] for p in parts)])
        x2 = max([x + w, *(touch_stats[p][0] + touch_stats[p][2] for p in parts)])
        y2 = max([y + h, *(touch_stats[p][1] + touch_stats[p][3] for p in parts)])
        area = (x2 - x1) * (y2 - y1)
        if boxes and area < best_area * SECOND_PLAN_AREA_RATIO:
            continue
        best_area = best_area or area
        boxes.append((x1, y1, x2, y2))

    pad = int(long_side * 0.02)
    return (
        max(0, min(b[0] for b in boxes) - pad),
        max(0, min(b[1] for b in boxes) - pad),
        min(width, max(b[2] for b in boxes) + pad),
        min(height, max(b[3] for b in boxes) + pad),
    )


def _fit(image: np.ndarray, side: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = side / max(height, width)
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=interpolation
    )


def _jpeg(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return encoded.tobytes()


def build_plan_images(pdf_bytes: bytes) -> list[bytes]:
    """JPEGs for Composer: the whole plan first, then quarters (top-left, top-right, bottom-left, bottom-right)."""
    page = page_two_image(pdf_bytes)
    box = find_plan_box(page)
    plan = page if box is None else page[box[1] : box[3], box[0] : box[2]]

    height, width = plan.shape[:2]
    tile_h = int(height * (0.5 + TILE_OVERLAP / 2))
    tile_w = int(width * (0.5 + TILE_OVERLAP / 2))
    corners = [(0, 0), (0, width - tile_w), (height - tile_h, 0), (height - tile_h, width - tile_w)]
    tiles = [plan[y : y + tile_h, x : x + tile_w] for y, x in corners]
    return [_jpeg(_fit(plan, PLAN_SIDE)), *(_jpeg(_fit(tile, TILE_SIDE)) for tile in tiles)]
