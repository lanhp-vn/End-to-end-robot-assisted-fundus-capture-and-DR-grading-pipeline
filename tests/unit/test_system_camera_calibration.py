import cv2
import numpy as np
import pytest

from arm101_hand.system_camera.calibration import (
    detect_arc_regions,
    detect_screen_rect,
    sample_hsv_band,
    suggest_coverage_threshold,
    to_roi_candidate,
)


def test_detect_screen_rect_ranks_screen_above_distractor():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (100, 80), (340, 260), (255, 255, 255), -1)  # the screen (240x180)
    cv2.rectangle(img, (500, 400), (560, 440), (255, 255, 255), -1)  # small bright distractor
    rects = detect_screen_rect(img)
    assert len(rects) >= 1
    x, y, w, h = rects[0]
    assert 90 <= x <= 110 and 70 <= y <= 90  # the screen, not the distractor
    assert w > h  # landscape


def test_detect_screen_rect_returns_at_most_top_n():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(5):
        cv2.rectangle(img, (10 + i * 110, 10), (90 + i * 110, 120), (255, 255, 255), -1)
    assert len(detect_screen_rect(img, top_n=3)) <= 3


def test_to_roi_candidate_normalizes_to_4_3_within_bounds():
    x, y, w, h = to_roi_candidate((100, 100, 200, 100), 640, 480)  # 2:1 -> expand height
    assert abs((w / h) - 4 / 3) < 0.05
    assert x >= 0 and y >= 0 and x + w <= 640 and y + h <= 480


def _red() -> tuple[int, int, int]:
    return (0, 0, 255)  # BGR red -> HSV hue 0


def test_detect_arc_regions_finds_left_and_right():
    roi = np.zeros((480, 640, 3), dtype=np.uint8)
    roi[200:280, 40:100] = _red()  # left arc
    roi[200:280, 540:600] = _red()  # right arc
    left, right = detect_arc_regions(roi)
    assert left.x < 320 <= right.x
    assert left.w >= 1 and right.h >= 1


def test_detect_arc_regions_raises_when_a_half_is_empty():
    roi = np.zeros((480, 640, 3), dtype=np.uint8)
    roi[200:280, 40:100] = _red()  # only the left half has red
    with pytest.raises(ValueError):
        detect_arc_regions(roi)


def test_sample_hsv_band_green_brackets_the_hue():
    region = np.zeros((40, 40, 3), dtype=np.uint8)
    region[:] = (0, 255, 0)  # BGR green -> HSV hue 60
    bands = sample_hsv_band(region, "green")
    assert len(bands) == 1
    assert bands[0].h_lo <= 60 <= bands[0].h_hi


def test_sample_hsv_band_red_splits_on_hue_wrap():
    hsv = np.zeros((20, 20, 3), dtype=np.uint8)
    hsv[:, :10] = (2, 200, 200)  # near hue 0
    hsv[:, 10:] = (178, 200, 200)  # near hue 180
    region = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    bands = sample_hsv_band(region, "red")
    assert len(bands) == 2


def test_suggest_coverage_threshold_midpoint_and_floor():
    assert suggest_coverage_threshold(0.20, 0.0) == 0.10
    assert suggest_coverage_threshold(0.02, 0.0) == 0.02  # midpoint 0.01 floored to 0.02
