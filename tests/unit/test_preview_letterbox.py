import numpy as np

from arm101_hand.system_camera.preview import _letterbox


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
