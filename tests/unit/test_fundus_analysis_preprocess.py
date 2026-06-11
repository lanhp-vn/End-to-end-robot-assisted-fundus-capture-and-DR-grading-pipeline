import numpy as np
import torch

from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)


def _disc_on_black(h=1776, w=2368, cx=1184, cy=888, r=700) -> np.ndarray:
    """A bright fundus-like disc on a near-black background (BGR uint8)."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    yy, xx = np.ogrid[:h, :w]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    img[mask] = (180, 170, 160)
    return img


def test_circle_crop_returns_square_box_around_disc():
    img = _disc_on_black()
    cropped, info = circle_crop(img)
    assert isinstance(info, CropInfo)
    assert info.method == "circle"
    assert info.fallback is False
    h, w = cropped.shape[:2]
    assert abs(h - w) <= 2  # square
    x0, y0, x1, y1 = info.box
    assert x0 < 1184 < x1 and y0 < 888 < y1  # disc center inside box


def test_circle_crop_falls_back_on_all_black():
    img = np.zeros((1000, 1200, 3), dtype=np.uint8)
    cropped, info = circle_crop(img)
    assert info.fallback is True
    assert info.method == "center"
    h, w = cropped.shape[:2]
    assert h == w == 1000  # central square of the shorter side


def test_eval_transform_output_shape():
    t = build_eval_transform(input_size=224)
    from PIL import Image

    pil = Image.fromarray(np.full((300, 300, 3), 128, dtype=np.uint8))
    out = t(pil)
    assert isinstance(out, torch.Tensor)
    assert out.shape == (3, 224, 224)


def test_confidence_band_boundaries():
    assert confidence_band(0.80, high=0.80, medium=0.65) == "HIGH"
    assert confidence_band(0.79, high=0.80, medium=0.65) == "MEDIUM"
    assert confidence_band(0.65, high=0.80, medium=0.65) == "MEDIUM"
    assert confidence_band(0.64, high=0.80, medium=0.65) == "LOW"
