from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import HsvBand, RoiBox
from arm101_hand.system_camera.calibration import (
    detect_arc_regions,
    detect_screen_rect,
    sample_hsv_band,
    suggest_coverage_threshold,
    to_roi_candidate,
    write_calibration_values,
)

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


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


def test_detect_screen_rect_prefers_bright_interior_over_dim_border_region():
    # The real-frame failure mode: a dim-but-large bright region flooding in from the border (sunlit
    # walls / a daylit doorway) competes with the backlit screen -- which is smaller, near-saturated,
    # and INTERIOR. The high-threshold sweep + interior scoring must pick the screen, not the slab.
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (380, 479), (170, 170, 170), -1)  # dim wall slab, touches the border
    cv2.rectangle(img, (180, 150), (440, 330), (255, 255, 255), -1)  # backlit screen, interior island
    x, y, w, h = detect_screen_rect(img)[0]
    assert 160 <= x <= 200 and 130 <= y <= 170  # the interior screen, not the (0,0) border slab


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
    # Tight ranges: left bbox tracks cols 40-100 (pad 4), right tracks cols 540-600 (x_off 320).
    assert 30 <= left.x <= 60 and left.x + left.w <= 120
    assert 520 <= right.x <= 560 and right.x + right.w <= 620
    assert left.w >= 1 and right.h >= 1


def test_detect_arc_regions_mirrors_when_one_half_is_empty():
    # Real captures often show only ONE arc strongly (the other faint / off-screen). The arcs are
    # left/right symmetric, so the populated half is mirrored to fill the empty one (no crash).
    roi = np.zeros((480, 640, 3), dtype=np.uint8)
    roi[200:280, 40:100] = _red()  # only the LEFT half has red
    left, right = detect_arc_regions(roi)
    assert left.x < 320 <= right.x  # right was mirrored into the right half
    assert abs(right.x - (640 - (left.x + left.w))) <= 1  # reflected across the vertical centre


def test_detect_arc_regions_raises_when_both_halves_empty():
    roi = np.zeros((480, 640, 3), dtype=np.uint8)  # no red anywhere
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
    # The two bands must actually bracket the 0/180 hue wrap: one anchored at 0, one at 180.
    assert any(b.h_lo == 0 for b in bands)
    assert any(b.h_hi == 180 for b in bands)


def test_suggest_coverage_threshold_midpoint_and_floor():
    assert suggest_coverage_threshold(0.20, 0.0) == 0.10
    # Non-zero "off" coverage: catches a max()-instead-of-midpoint regression (0.15, not 0.20/0.10).
    assert suggest_coverage_threshold(0.20, 0.10) == 0.15
    assert suggest_coverage_threshold(0.02, 0.0) == 0.02  # midpoint 0.01 floored to 0.02


def test_write_calibration_preserves_comments_and_updates(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")

    write_calibration_values(
        dst,
        screen_roi=RoiBox(x=150, y=188, w=490, h=368, ref_w=1600, ref_h=1200),
        left_arc=RoiBox(x=50, y=100, w=80, h=200),
        right_arc=RoiBox(x=410, y=100, w=80, h=200),
        red_bands=[HsvBand(h_lo=0, s_lo=90, v_lo=90, h_hi=8, s_hi=255, v_hi=255)],
        green_bands=[HsvBand(h_lo=45, s_lo=60, v_lo=70, h_hi=85, s_hi=255, v_hi=255)],
        coverage_threshold=0.09,
    )

    text = dst.read_text(encoding="utf-8")
    assert "Automated trigger" in text  # a block comment survived the round-trip
    assert dst.with_suffix(".yaml.bak").exists()  # backup written

    cfg = load_system_camera_config(dst)
    assert (cfg.screen_roi.x, cfg.screen_roi.ref_w) == (150, 1600)
    assert (cfg.auto_trigger.left_arc.x, cfg.auto_trigger.right_arc.x) == (50, 410)
    assert cfg.auto_trigger.coverage_threshold == 0.09
    assert len(cfg.auto_trigger.red_bands) == 1 and cfg.auto_trigger.green_bands[0].h_lo == 45


def test_write_calibration_rejects_invalid_without_writing(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    before = dst.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        write_calibration_values(
            dst,
            screen_roi=RoiBox(x=0, y=0, w=1, h=1),
            left_arc=RoiBox(x=0, y=0, w=1, h=1),
            right_arc=RoiBox(x=0, y=0, w=1, h=1),
            red_bands=[],  # empty -> rejected by the min_length=1 guard (a degenerate config)
            green_bands=[],  # empty -> rejected too
            coverage_threshold=0.5,  # VALID: empty bands are the sole cause of rejection here
        )
    assert dst.read_text(encoding="utf-8") == before  # original untouched on failure
