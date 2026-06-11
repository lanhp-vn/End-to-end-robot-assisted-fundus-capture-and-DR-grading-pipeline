"""Diabetic-retinopathy grading from fundus photos (device layer).

Local, offline inference only — no network calls. Research/educational use;
not a medical device.
"""

from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

__all__ = ["CropInfo", "build_eval_transform", "circle_crop", "confidence_band"]
