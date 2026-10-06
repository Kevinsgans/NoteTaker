import cv2
import numpy as np
import pytest

from notetaker.config import Config
from notetaker.inference import inputs
from notetaker.vision import OpenCVProcessor, StabilityGate, warp


def test_detect_rectify_and_reject_blur(page_image):
    processor = OpenCVProcessor(Config())
    result = processor.process(page_image)
    assert result["quality"]["acceptable"]
    assert result["corrected"].shape[0] > 500
    assert result["corrected"].shape[1] > 550
    with pytest.raises(ValueError):
        processor.process(cv2.GaussianBlur(page_image, (51, 51), 15))


def test_manual_capture_and_corner_validation(page_image):
    processor = OpenCVProcessor(Config())
    empty = np.zeros_like(page_image)
    with pytest.raises(ValueError, match="No page"):
        processor.process(empty)
    assert processor.process(empty, force=True)["quality"]["acceptable"] is False
    with pytest.raises(ValueError):
        warp(page_image, [[-1, 0], [700, 0], [700, 500], [0, 500]])


def test_gate_stability_rearming_and_cumulative_drift(page_image):
    corners = np.float32([[120, 40], [690, 40], [690, 560], [120, 560]])
    gate = StabilityGate(0.75)
    assert not gate.update(corners, True, page_image, now=0)
    assert not gate.update(corners, True, page_image, now=0.74)
    assert gate.update(corners, True, page_image, now=0.75)
    gate.captured(page_image)
    assert not gate.update(corners, True, page_image, now=1.8)
    assert not gate.update(None, False, page_image, now=2)
    assert not gate.update(None, False, page_image, now=2.6)
    assert not gate.latched
    assert not gate.update(corners, True, page_image, now=3)
    assert not gate.update(corners + 30, True, page_image, now=3.8)


def test_model_inputs_preserve_entire_page_and_tiles(page_image):
    frames = inputs(page_image, "tiles", 336)
    assert len(frames) == 5
    assert all(f.shape == (336, 336, 3) and f.dtype == np.uint8 for f in frames)
    assert (frames[0][0] == 255).all()
