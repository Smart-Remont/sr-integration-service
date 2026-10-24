"""Images Composer reads: the apartment plan cut from KL page 2 plus its four enlarged quarters,
or — when reading the cut plan fails — the whole page 2 and its quarters.

Cropping finds the walls — long straight lines in two perpendicular directions — with no
reliance on colour. The best region is grown only by regions that nearly touch it — pieces of the
same plan split by wide openings. Joining every strong region (meant for two floors on one page)
pulled legends and «Что вы получаете» panels into the picture and shrank the plan on 11 % of
sheets. Sheet styles where the walls are colour fills, or a second floor apart from the first,
are read from the whole page instead (see the service).
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
# Regions this close to the plan frame (share of the long page side) and scoring at least this
# share of the best one are pieces of the same plan: walls broken by wide openings.
PLAN_PART_REACH = 0.03
PLAN_PART_MIN_SCORE = 0.1
# A tinted area bigger than this share of the page is a panel or the background, not a balcony;
# a tinted area joins the plan only if it comes this close (share of the long side) to its walls.
FILL_MAX_PAGE_SHARE = 0.25
FILL_TOUCH = 0.005


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
    """Frame of the best plan region on the page; None when no walls are found."""
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
    best = int(score.max())
    if best == 0:
        return None

    def region_box(region: int) -> tuple[int, int, int, int]:
        # everything dark physically connected to the region's walls is part of the plan
        parts = np.unique(touch_labels[(labels == region) & (lines > 0)])
        parts = parts[parts > 0]
        x, y, w, h = stats[region][:4]
        return (
            min([x, *(touch_stats[p][0] for p in parts)]),
            min([y, *(touch_stats[p][1] for p in parts)]),
            max([x + w, *(touch_stats[p][0] + touch_stats[p][2] for p in parts)]),
            max([y + h, *(touch_stats[p][1] + touch_stats[p][3] for p in parts)]),
        )

    order = [int(r) for r in np.argsort(-score) if score[r] >= best * PLAN_PART_MIN_SCORE]
    chosen = [order[0]]
    box = region_box(order[0])
    others = [(r, region_box(r)) for r in order[1:]]
    # A plan with wide openings falls apart into regions that nearly touch; legends, window
    # schemes and «Что вы получаете» panels stand apart. Grow the frame by touching regions only.
    reach = int(long_side * PLAN_PART_REACH)
    grown = True
    while grown:
        grown = False
        for other in list(others):
            region, other_box = other
            gap_x = max(other_box[0] - box[2], box[0] - other_box[2], 0)
            gap_y = max(other_box[1] - box[3], box[1] - other_box[3], 0)
            if gap_x <= reach and gap_y <= reach:
                box = (
                    min(box[0], other_box[0]),
                    min(box[1], other_box[1]),
                    max(box[2], other_box[2]),
                    max(box[3], other_box[3]),
                )
                chosen.append(region)
                others.remove(other)
                grown = True

    walls = (np.isin(labels, chosen) & (lines > 0)).astype(np.uint8)
    box = _with_fills(gray, walls, box, long_side)
    pad = int(long_side * 0.02)
    return (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(width, box[2] + pad),
        min(height, box[3] + pad),
    )


def _with_fills(
    gray: np.ndarray, walls: np.ndarray, box: tuple[int, int, int, int], long_side: int
) -> tuple[int, int, int, int]:
    """Adds light-filled areas without a dark outline that lean against the plan's walls: open
    balconies and terraces are often drawn as a tint outside the walls, with their label inside.
    Only fills touching the walls count — a panel next to the plan («Что вы получаете») does not."""
    height, width = gray.shape
    fill = ((gray < 243) & (gray >= 170)).astype(np.uint8) * 255
    fill = cv2.morphologyEx(fill, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    _, fill_labels, stats, _ = cv2.connectedComponentsWithStats(fill)
    touch = max(3, round(long_side * FILL_TOUCH))
    near = cv2.dilate(walls, np.ones((touch, touch), np.uint8)) > 0
    x1, y1, x2, y2 = box
    for label in np.unique(fill_labels[near]):
        x, y, w, h, area = stats[label]
        # the page background or a large panel is not a balcony
        if (
            label == 0
            or w * h > FILL_MAX_PAGE_SHARE * height * width
            or area < (long_side * 0.03) ** 2
        ):
            continue
        x1, y1, x2, y2 = min(x1, x), min(y1, y), max(x2, x + w), max(y2, y + h)
    return x1, y1, x2, y2


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


def _with_quarters(image: np.ndarray, side: int) -> list[bytes]:
    """The image itself (long side `side`), then its quarters: top-left, top-right, bottom-left,
    bottom-right."""
    height, width = image.shape[:2]
    tile_h = int(height * (0.5 + TILE_OVERLAP / 2))
    tile_w = int(width * (0.5 + TILE_OVERLAP / 2))
    corners = [(0, 0), (0, width - tile_w), (height - tile_h, 0), (height - tile_h, width - tile_w)]
    tiles = [image[y : y + tile_h, x : x + tile_w] for y, x in corners]
    return [_jpeg(_fit(image, side)), *(_jpeg(_fit(tile, TILE_SIDE)) for tile in tiles)]


def build_plan_images(pdf_bytes: bytes) -> list[bytes]:
    """The plan cut from page 2 and its quarters."""
    page = page_two_image(pdf_bytes)
    box = find_plan_box(page)
    plan = page if box is None else page[box[1] : box[3], box[0] : box[2]]
    return _with_quarters(plan, PLAN_SIDE)


def build_page_images(pdf_bytes: bytes) -> list[bytes]:
    """The whole page 2 and its quarters — for sheets where the cut plan could not be read."""
    return _with_quarters(page_two_image(pdf_bytes), PLAN_SIDE)
