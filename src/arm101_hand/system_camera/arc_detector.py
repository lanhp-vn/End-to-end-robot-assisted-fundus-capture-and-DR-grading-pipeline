"""Red-arc detection for the arm-mounted USB observation camera (device layer).

The Optomed Aurora shows two symmetric alignment arcs (left + right of its circular live view) that
are RED when misaligned. We classify each arc region by LAB a* (red-green axis) coverage: the
fraction of pixels whose a* channel is >= a cutoff (cheap, robust per-region masking; plain
opencv-python). a* stays positive for a faint red tint even where the bright fundus washes the arcs
toward white and HSV saturation collapses, so it catches pale arcs an HSV red band misses. "Aligned
/ ready" is simply NOT red -- no other colour is thresholded (the bright/aligned screen reads
greenish overall, so green coverage is unreliable). The auto-trigger gates each fire on a both-red ->
both-not-red transition. Pure functions (no cv2 window).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .roi import roi_from_region


@dataclass(frozen=True)
class AlignmentState:
    """Per-arc red classification + coverages (coverages are for the HUD/debug)."""

    left_red: bool
    right_red: bool
    left_cov: float
    right_cov: float

    @property
    def both_red(self) -> bool:
        return self.left_red and self.right_red

    @property
    def both_clear(self) -> bool:
        return not self.left_red and not self.right_red


def arc_redness_mask(lab_band: np.ndarray, a_star_min: int) -> np.ndarray:
    """Binary (0/255) mask of pixels whose LAB a* (red-green) channel is >= a_star_min.

    a* is channel index 1 of an 8-bit BGR2LAB image; 128 = neutral, higher = redder. Saturation
    collapses near white but a* stays positive for a faint red tint, so this detects pale arcs over
    the bright fundus that an HSV red band misses."""
    return ((lab_band[:, :, 1] >= a_star_min).astype(np.uint8)) * 255


def red_coverage(lab_band: np.ndarray, cfg: AutoTriggerConfig) -> float:
    """Fraction of an arc band that is red (LAB a* >= cfg.a_star_min) after a MORPH_OPEN despeckle."""
    mask = arc_redness_mask(lab_band, cfg.a_star_min)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.morph_kernel, cfg.morph_kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs as RED / not-red by a* red coverage vs ``cfg.coverage_threshold``."""
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    lc = red_coverage(roi_from_region(cfg.left_arc).crop(lab), cfg)
    rc = red_coverage(roi_from_region(cfg.right_arc).crop(lab), cfg)
    t = cfg.coverage_threshold
    return AlignmentState(left_red=lc >= t, right_red=rc >= t, left_cov=lc, right_cov=rc)
