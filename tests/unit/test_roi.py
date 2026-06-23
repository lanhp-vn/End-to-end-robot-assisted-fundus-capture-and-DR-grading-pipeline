import numpy as np

from arm101_hand.system_camera import AURORA_SCREEN_ROI, Roi


def _frame(w: int, h: int) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_identity_at_reference_size():
    # At the reference resolution the ROI passes through unchanged.
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(640, 480) == (130, 6, 280, 210)


def test_uniform_scale_same_aspect():
    # A 2x-larger frame of the same aspect scales every component by 2.
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(1280, 960) == (260, 12, 560, 420)


def test_nonuniform_scale_per_axis():
    # Different aspect: x/w scale by the width ratio, y/h by the height ratio (independently).
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.for_frame(1280, 720) == (260, 9, 560, 315)


def test_clamps_inside_frame():
    # An ROI that would overflow the bottom-right is clamped to stay in-bounds.
    roi = Roi(600, 440, 100, 100, ref_w=640, ref_h=480)
    x, y, w, h = roi.for_frame(640, 480)
    assert (x, y, w, h) == (600, 440, 40, 40)
    assert x + w <= 640 and y + h <= 480


def test_crop_shape_matches_for_frame():
    roi = Roi(130, 6, 280, 210, ref_w=640, ref_h=480)
    assert roi.crop(_frame(640, 480)).shape == (210, 280, 3)
    # Scaled frame: crop shape tracks the rescaled ROI (h, w from for_frame).
    assert roi.crop(_frame(1280, 960)).shape == (420, 560, 3)


def test_aurora_roi_at_1600x1200():
    # The operating stream is 1600x1200 (system_camera_config.yaml), chosen for ~44 fps over USB 2.0.
    # At this size the ROI crop (~490x368) is SMALLER than the 640x480 reference, so resize-to-
    # reference is a mild (~1.3x) UPSCALE -- a deliberate trade of the no-upscale goal for frame rate.
    # Locks the for_frame numbers the config relies on.
    x, y, w, h = AURORA_SCREEN_ROI.for_frame(1600, 1200)
    assert (x, y, w, h) == (150, 188, 490, 368)
    assert w < AURORA_SCREEN_ROI.ref_w and h < AURORA_SCREEN_ROI.ref_h  # mild upscale (fps trade)


def test_aurora_roi_no_upscale_at_2592x1944():
    # Reference point: 2592x1944 is the no-upscale alternative (crop LARGER than the 640x480 ref ->
    # downscale). Kept so the trade-off the config comment describes stays test-documented.
    x, y, w, h = AURORA_SCREEN_ROI.for_frame(2592, 1944)
    assert (x, y, w, h) == (243, 304, 794, 595)
    assert w >= AURORA_SCREEN_ROI.ref_w and h >= AURORA_SCREEN_ROI.ref_h


def test_roi_from_region_builds_roi():
    from arm101_hand.config.system_camera_config import RoiBox
    from arm101_hand.system_camera import roi_from_region

    r = roi_from_region(RoiBox(x=10, y=20, w=30, h=40, ref_w=1600, ref_h=1200))
    assert (r.x, r.y, r.w, r.h, r.ref_w, r.ref_h) == (10, 20, 30, 40, 1600, 1200)
    assert isinstance(r, Roi)


def test_screen_roi_builds_roi_from_data_config():
    from pathlib import Path

    from arm101_hand.config import load_system_camera_config
    from arm101_hand.system_camera import roi_from_region

    data = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
    r = roi_from_region(load_system_camera_config(data).screen_roi)
    assert isinstance(r, Roi) and r.w >= 1 and r.h >= 1
