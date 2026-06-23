import cv2
import numpy as np

from arm101_hand.system_camera.preview import (
    _fit_within,
    _letterbox,
    imshow_fit,
    resolution_mismatch_warning,
)


def _white(w: int, h: int) -> np.ndarray:
    # solid white frame so content pixels (255) are distinguishable from black bars (0)
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_output_matches_window_size():
    out = _letterbox(_white(640, 480), 1000, 500)
    assert out.shape == (500, 1000, 3)  # (h, w, c) == the window size


def test_exact_aspect_fills_no_bars():
    # 640x480 (4:3) into 800x600 (4:3) scales to fill -- no black bars anywhere.
    out = _letterbox(_white(640, 480), 800, 600)
    assert out.shape == (600, 800, 3)
    assert out.min() == 255  # every pixel is content


def test_pillarbox_when_window_wider():
    # 4:3 frame into a 2:1 window -> bars on the LEFT and RIGHT, none top/bottom.
    out = _letterbox(_white(640, 480), 1000, 500)
    assert out[:, 0].sum() == 0 and out[:, -1].sum() == 0  # left + right columns black
    assert out[250, 500].sum() > 0  # centre is content
    assert out[0, 500].sum() > 0 and out[-1, 500].sum() > 0  # full height (no top/bottom bars)


def test_letterbox_when_window_taller():
    # 4:3 frame into a square window -> bars on TOP and BOTTOM, none left/right.
    out = _letterbox(_white(640, 480), 480, 480)
    assert out[0, :].sum() == 0 and out[-1, :].sum() == 0  # top + bottom rows black
    assert out[240, 240].sum() > 0  # centre is content
    assert out[240, 0].sum() > 0 and out[240, -1].sum() > 0  # full width (no side bars)


def test_content_preserves_source_aspect():
    # The non-black content region must keep the source 4:3 ratio (no distortion).
    out = _letterbox(_white(640, 480), 1000, 500)
    nonzero = out.sum(axis=2) > 0
    rows, cols = np.where(nonzero)
    content_w = cols.max() - cols.min() + 1
    content_h = rows.max() - rows.min() + 1
    assert abs(content_w / content_h - 640 / 480) < 0.02


def test_fit_within_unchanged_when_already_fits():
    # 640x480 is under the 960x720 cap -> returned unchanged (ROI consumers unaffected).
    assert _fit_within(640, 480, 960, 720) == (640, 480)


def test_fit_within_scales_down_preserving_aspect():
    # 2592x1944 (4:3) scaled to fit 960x720 -> exactly 960x720, same aspect.
    assert _fit_within(2592, 1944, 960, 720) == (960, 720)


def test_fit_within_never_upscales():
    # A frame smaller than the cap is never enlarged.
    assert _fit_within(320, 240, 960, 720) == (320, 240)


def test_fit_within_handles_zero():
    # Degenerate size (camera not ready) returns unchanged, no divide-by-zero.
    assert _fit_within(0, 0, 960, 720) == (0, 0)


def test_resolution_mismatch_none_when_equal():
    assert resolution_mismatch_warning(2592, 1944, 2592, 1944) is None


def test_resolution_mismatch_warns_when_clamped():
    msg = resolution_mismatch_warning(2592, 1944, 1920, 1080)
    assert msg is not None
    assert "2592x1944" in msg and "1920x1080" in msg


def test_resolution_mismatch_none_when_request_unspecified():
    # width/height = None means "give me the driver max" -> nothing to compare against.
    assert resolution_mismatch_warning(None, None, 1920, 1080) is None
    assert resolution_mismatch_warning(2592, None, 2592, 1944) is None


def test_imshow_fit_survives_missing_window(monkeypatch):
    # getWindowImageRect THROWS on a never-created window (Win32) -- it doesn't just return zeros.
    # imshow_fit must fall back to a plain imshow (which creates the window) instead of crashing.
    def _raise(_title):
        raise cv2.error("NULL window")

    shown: dict[str, object] = {}
    monkeypatch.setattr(cv2, "getWindowImageRect", _raise)
    monkeypatch.setattr(cv2, "imshow", lambda title, frame: shown.update(title=title, shape=frame.shape))

    imshow_fit("never-created", _white(640, 480))  # must NOT raise

    assert shown["title"] == "never-created"
    assert shown["shape"] == (480, 640, 3)  # raw frame shown (not letterboxed to a window size)
