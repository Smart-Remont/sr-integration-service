"""Plan images from a KL PDF: page 2 is required, the plan is cut out by its walls and comes
with four overlapping quarters."""

import cv2
import numpy as np
import pymupdf
import pytest

from src.features.kl_recognition.plan_images import (
    PlanPageMissingError,
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
