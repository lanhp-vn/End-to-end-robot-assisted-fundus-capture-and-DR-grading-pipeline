"""Pure fundus preprocessing: circle-crop + eval transform + confidence band.

No model, no disk I/O beyond the array passed in. cv2 uses BGR; the transform
expects an RGB PIL image, so callers convert before transforming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from torchvision import transforms

# timm.data.constants IMAGENET_DEFAULT_MEAN / STD
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class CropInfo:
    """Where and how the fundus disc was cropped."""

    method: Literal["circle", "center"]
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in source pixels
    fallback: bool


def _center_square(img: np.ndarray) -> tuple[np.ndarray, CropInfo]:
    h, w = img.shape[:2]
    side = min(h, w)
    x0 = (w - side) // 2
    y0 = (h - side) // 2
    box = (x0, y0, x0 + side, y0 + side)
    return img[y0 : y0 + side, x0 : x0 + side], CropInfo("center", box, fallback=True)


def circle_crop(
    img_bgr: np.ndarray,
    *,
    background_threshold: int = 12,
    margin_frac: float = 0.02,
    min_disc_area_frac: float = 0.05,
) -> tuple[np.ndarray, CropInfo]:
    """Crop a tight square around the fundus disc.

    Threshold the near-black background, take the bounding box of the largest
    bright contour, expand by ``margin_frac`` of the disc size, and square it.
    Falls back to a center square if no disc large enough is found.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, background_threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _center_square(img_bgr)

    h, w = img_bgr.shape[:2]
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < min_disc_area_frac * h * w:
        return _center_square(img_bgr)

    x, y, bw, bh = cv2.boundingRect(largest)
    margin = int(margin_frac * max(bw, bh))
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(w, x + bw + margin)
    y1 = min(h, y + bh + margin)

    # square the box around its center, clamped to image bounds
    side = min(max(x1 - x0, y1 - y0), min(h, w))
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    sx0 = max(0, min(cx - side // 2, w - side))
    sy0 = max(0, min(cy - side // 2, h - side))
    box = (sx0, sy0, sx0 + side, sy0 + side)
    return img_bgr[sy0 : sy0 + side, sx0 : sx0 + side], CropInfo("circle", box, fallback=False)


def build_eval_transform(input_size: int = 224) -> transforms.Compose:
    """The exact RETFound eval transform (util/datasets.py:50-58)."""
    crop_pct = 224 / 256 if input_size <= 224 else 1.0
    resize = int(input_size / crop_pct)
    return transforms.Compose(
        [
            transforms.Resize(resize, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(input_size),
            transforms.ToTensor(),
            transforms.Normalize(_IMAGENET_MEAN, _IMAGENET_STD),
        ]
    )


def confidence_band(
    top_prob: float, *, high: float, medium: float
) -> Literal["HIGH", "MEDIUM", "LOW"]:
    """Map the top softmax probability to a confidence label."""
    if top_prob >= high:
        return "HIGH"
    if top_prob >= medium:
        return "MEDIUM"
    return "LOW"
