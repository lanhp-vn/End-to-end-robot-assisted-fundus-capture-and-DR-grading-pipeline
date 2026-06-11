import numpy as np

from arm101_hand.fundus_analysis.grader import GradeResult
from arm101_hand.fundus_analysis.render import (
    GradedShot,
    _bar_width,
    _most_severe,
    _scale_to_height,
    compose_summary_panel,
    decode_bgr,
    encode_png,
)


def _result(grade: int, label: str, top: float, name: str = "x.JPG") -> GradeResult:
    probs = {"No DR": 0.1, "Mild": 0.1, "Moderate": 0.1, "Severe": 0.1, "Proliferative": 0.1}
    probs[label] = top
    return GradeResult(
        source_image=name,
        grade=grade,
        label=label,
        confidence="MEDIUM",
        probabilities=probs,
        crop={"method": "circle", "box": [0, 0, 1, 1], "fallback": False},
        model={"checkpoint": "w", "sha256_8": "abc", "arch": "vit"},
        preprocess_version="1",
        graded_at_utc="2026-06-11T00:00:00Z",
    )


def _img(h: int, w: int, fill: int) -> np.ndarray:
    return np.full((h, w, 3), fill, np.uint8)


def test_bar_width_clamps_and_scales():
    assert _bar_width(0.0, 100) == 0
    assert _bar_width(1.0, 100) == 100
    assert _bar_width(0.5, 100) == 50
    assert _bar_width(2.0, 100) == 100
    assert _bar_width(-1.0, 100) == 0


def test_most_severe_prefers_higher_grade():
    rs = [_result(0, "No DR", 0.9), _result(3, "Severe", 0.4), _result(1, "Mild", 0.8)]
    assert _most_severe(rs) == 1


def test_most_severe_tie_breaks_on_top_prob_then_latest():
    assert _most_severe([_result(2, "Moderate", 0.6), _result(2, "Moderate", 0.9)]) == 1
    assert _most_severe([_result(2, "Moderate", 0.7), _result(2, "Moderate", 0.7)]) == 1


def test_compose_single_shot_shape_and_image_preserved():
    img = _img(30, 40, 100)
    shot = GradedShot(image_bgr=img, result=_result(2, "Moderate", 0.71), source_name="x.JPG")
    out = compose_summary_panel([shot], panel_width=160, display_height=120)
    assert out.dtype == np.uint8 and out.shape[0] == 120 and out.shape[2] == 3
    left = _scale_to_height(img, 120)
    assert out.shape[1] == left.shape[1] + 160
    assert np.array_equal(out[:, : left.shape[1]], left)  # image pixels never overdrawn
    panel = out[:, left.shape[1] :]
    assert np.any(panel != panel[0, 0])  # text/bars drawn on the panel


def test_compose_multishot_leads_with_most_severe_image():
    mild = GradedShot(image_bgr=_img(20, 20, 50), result=_result(0, "No DR", 0.9), source_name="a.JPG")
    severe = GradedShot(image_bgr=_img(20, 20, 200), result=_result(3, "Severe", 0.7), source_name="b.JPG")
    out = compose_summary_panel([mild, severe], panel_width=160, display_height=120)
    left = _scale_to_height(_img(20, 20, 200), 120)
    assert np.array_equal(out[:, : left.shape[1]], left)


def test_compose_unavailable_returns_valid_frame():
    shot = GradedShot(image_bgr=_img(20, 20, 80), result=None, source_name="a.JPG")
    out = compose_summary_panel(
        [shot],
        unavailable_reason="grading unavailable -- run export_weights.py",
        panel_width=160,
        display_height=120,
    )
    assert out.dtype == np.uint8 and out.shape[0] == 120
    assert out.shape[1] == _scale_to_height(_img(20, 20, 80), 120).shape[1] + 160


def test_encode_decode_round_trip():
    frame = _img(40, 50, 123)
    out = decode_bgr(encode_png(frame))
    assert out is not None and out.shape == frame.shape and np.array_equal(out, frame)
