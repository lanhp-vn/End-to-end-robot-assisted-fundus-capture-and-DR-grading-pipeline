"""Device-layer tests for the focus-probe core (no hardware): the ROI sharpness metric, the
sweep-value planner, and the best-sample selector. The cv2 window + camera I/O in
``usb_camera_focus_probe.py`` is the untestable glue around these pure functions."""

import cv2
import numpy as np
import pytest

from arm101_hand.system_camera import (
    best_sample,
    focus_reached_baseline,
    focus_steps,
    sharpness,
)


def _checkerboard(size: int = 128, cell: int = 8) -> np.ndarray:
    """High-frequency gray checkerboard -> lots of edges -> high Laplacian variance."""
    img = np.zeros((size, size), dtype=np.uint8)
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            if (x // cell + y // cell) % 2 == 0:
                img[y : y + cell, x : x + cell] = 255
    return img


def test_sharpness_higher_for_sharper_image():
    sharp = _checkerboard()
    blurred = cv2.GaussianBlur(sharp, (11, 11), 0)
    assert sharpness(sharp) > sharpness(blurred)


def test_sharpness_zero_for_flat_image():
    flat = np.full((64, 64), 128, dtype=np.uint8)
    assert sharpness(flat) == 0.0


def test_sharpness_color_matches_gray():
    gray = _checkerboard()
    color = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)  # 3-channel input is converted internally
    assert sharpness(color) == pytest.approx(sharpness(gray))


def test_focus_steps_inclusive_endpoints():
    assert focus_steps(0, 10, 5) == [0, 5, 10]


def test_focus_steps_appends_hi_off_boundary():
    # hi is always probed even when it doesn't fall on a step boundary.
    assert focus_steps(0, 9, 5) == [0, 5, 9]


def test_focus_steps_single_value():
    assert focus_steps(7, 7, 5) == [7]


def test_focus_steps_rejects_bad_args():
    with pytest.raises(ValueError):
        focus_steps(0, 10, 0)  # step < 1
    with pytest.raises(ValueError):
        focus_steps(10, 0, 5)  # lo > hi


def test_best_sample_picks_max_sharpness():
    assert best_sample([(0, 1.0), (5, 9.0), (10, 3.0)]) == (5, 9.0)


def test_best_sample_first_on_tie():
    assert best_sample([(0, 5.0), (5, 5.0)]) == (0, 5.0)


def test_best_sample_empty_is_none():
    assert best_sample([]) is None


def test_focus_reached_baseline_true_near_af():
    # Manual best ~ the autofocus baseline -> manual focus genuinely reached a sharp value.
    assert focus_reached_baseline(900.0, 950.0, frac=0.6) is True


def test_focus_reached_baseline_false_far_below():
    # The observed failure: manual best (~10) is nowhere near the AF baseline (~950).
    assert focus_reached_baseline(10.0, 950.0, frac=0.6) is False


def test_focus_reached_baseline_at_threshold():
    assert focus_reached_baseline(570.0, 950.0, frac=0.6) is True  # 570 == 0.6 * 950


def test_focus_reached_baseline_none_best_is_false():
    assert focus_reached_baseline(None, 950.0) is False


def test_focus_reached_baseline_nonpositive_baseline_is_false():
    assert focus_reached_baseline(100.0, 0.0) is False
