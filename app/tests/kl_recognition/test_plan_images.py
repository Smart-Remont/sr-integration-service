"""Plan images from a KL PDF: page 2 is required, the plan is cut out by its walls and comes
with four overlapping quarters."""

import cv2
import numpy as np
import pymupdf
import pytest

from src.features.kl_recognition.plan_images import (
    PlanPageMissingError,
    build_page_images,
    build_plan_images,
    find_plan_box,
)


def synthetic_plan_page() -> np.ndarray:
    """White page with a text block on the left and a walled plan on the right."""
    page = np.full((1600, 2400, 3), 255, np.uint8)
    cv2.putText(page, "LEGEND", (80, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 6)
    x1, y1, x2, y2 = 1200, 300, 2200, 1300
    cv2.rectangle(page, (x1, y1), (x2, y2), (0, 0, 0), 14)
    cv2.line(page, (1700, y1), (1700, y2), (0, 0, 0), 10)
    cv2.line(page, (x1, 800), (1700, 800), (0, 0, 0), 10)
    return page


def pdf_with_pages(*images: np.ndarray) -> bytes:
    doc = pymupdf.open()
    for image in images:
        ok, jpeg = cv2.imencode(".jpg", image)
        assert ok
        page = doc.new_page(width=image.shape[1] / 4, height=image.shape[0] / 4)
        page.insert_image(page.rect, stream=jpeg.tobytes())
    return doc.tobytes()


def test_plan_box_skips_text_and_holds_the_walls() -> None:
    box = find_plan_box(synthetic_plan_page())
    assert box is not None
    x1, y1, x2, y2 = box
    assert x1 > 1000  # the legend on the left is left out
    assert x1 <= 1200 and y1 <= 300 and x2 >= 2200 and y2 >= 1300


def test_build_plan_images_returns_plan_and_four_quarters() -> None:
    page = synthetic_plan_page()
    images = build_plan_images(pdf_with_pages(page, page))
    assert len(images) == 5
    decoded = [cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR) for image in images]
    assert max(decoded[0].shape[:2]) == 2000
    assert all(max(tile.shape[:2]) == 1600 for tile in decoded[1:])


def test_one_page_sheet_has_no_plan() -> None:
    with pytest.raises(PlanPageMissingError):
        build_plan_images(pdf_with_pages(synthetic_plan_page()))


def test_a_table_below_the_plan_is_not_joined_to_it() -> None:
    # A framed table with rows and columns scores like a second plan; joining it pulled the
    # whole «Что вы получаете» panel and the legend between them into the picture.
    page = np.full((3200, 2400, 3), 255, np.uint8)
    x1, y1, x2, y2 = 600, 200, 1800, 1400
    cv2.rectangle(page, (x1, y1), (x2, y2), (0, 0, 0), 16)
    for x in (1000, 1400):
        cv2.line(page, (x, y1), (x, y2), (0, 0, 0), 12)
    cv2.line(page, (x1, 800), (x2, 800), (0, 0, 0), 12)
    cv2.rectangle(page, (200, 2000), (2200, 3000), (0, 0, 0), 8)
    for y in range(2200, 3000, 200):
        cv2.line(page, (200, y), (2200, y), (0, 0, 0), 6)
    cv2.line(page, (1200, 2000), (1200, 3000), (0, 0, 0), 6)

    box = find_plan_box(page)
    assert box is not None
    assert box[1] < 200 and box[3] < 2000  # the plan only, the table below is left out


def test_page_images_hold_the_whole_page_and_its_quarters() -> None:
    page = synthetic_plan_page()
    images = build_page_images(pdf_with_pages(page, page))
    decoded = [cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR) for image in images]
    assert len(decoded) == 5
    assert decoded[0].shape[:2] == (round(2000 * 1600 / 2400), 2000)  # the page, not the cut


def test_a_tinted_balcony_outside_the_walls_is_kept() -> None:
    # Open balconies are often only a tint below the walls with the label inside (Biography).
    page = synthetic_plan_page()
    cv2.rectangle(page, (1300, 1310), (1900, 1500), (225, 205, 205), -1)
    box = find_plan_box(page)
    assert box is not None and box[3] >= 1500
