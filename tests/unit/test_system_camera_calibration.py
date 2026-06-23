from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import HsvBand, RoiBox
from arm101_hand.system_camera.calibration import (
    arc_bands_from_circle,
    detect_screen_rects,
    fit_camera_circle,
    sample_hsv_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
    write_calibration_values,
)

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


def test_detect_screen_rects_finds_tilted_interior_rect():
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    box = cv2.boxPoints(((400, 240), (360, 220), 6.0)).astype(np.int32)  # 5:3-ish, tilted 6deg, interior
    cv2.fillPoly(img, [box], (255, 255, 255))
    rects = detect_screen_rects(img, top_n=3)
    assert len(rects) >= 1
    (cx, cy), (w, h), angle = rects[0]
    assert 360 <= cx <= 440 and 200 <= cy <= 280
    assert abs(abs(angle) - 6.0) < 2.0  # recovered the tilt


def test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle():
    rect = ((800.0, 600.0), (400.0, 240.0), -1.0)  # in a 1600x1200 frame
    sr = screen_roi_from_rect(rect, 1600, 1200)
    assert (sr.ref_w, sr.ref_h) == (800, 480)
    assert sr.angle == -1.0
    # the stored box, scaled back to the frame, keeps ~5:3
    from arm101_hand.system_camera import roi_from_region

    x, y, w, h = roi_from_region(sr).for_frame(1600, 1200)
    assert abs((w / h) - 5 / 3) < 0.1


def test_fit_camera_circle_centres_on_disc():
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    cv2.circle(img, (400, 240), 180, (255, 255, 255), -1)
    cx, cy, r = fit_camera_circle(img)
    assert abs(cx - 400) <= 15 and abs(cy - 240) <= 15 and abs(r - 180) <= 25


def test_arc_bands_from_circle_are_symmetric():
    left, right = arc_bands_from_circle(400, 240, 200, ref_w=800, ref_h=480)
    assert left.x < 400 <= right.x
    assert (left.w, left.h) == (right.w, right.h)
    assert abs(right.x - (800 - (left.x + left.w))) <= 1  # mirror across centre


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
