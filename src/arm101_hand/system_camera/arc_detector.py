"""Red-arc detection for the arm-mounted USB observation camera (device layer).

The Optomed Aurora shows two symmetric alignment arcs (left + right of its circular live view) that
are RED when misaligned. We classify each arc region by HSV RED coverage (cheap, robust per-region
masking; plain opencv-python). "Aligned / ready" is simply NOT red -- no other colour is thresholded
(the bright/aligned screen reads greenish overall, so green coverage is unreliable). The auto-trigger
gates each fire on a both-red -> both-not-red transition. Pure functions (no cv2 window).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig, HsvBand

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


def _band_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR together the inRange masks for every HSV band (red needs two for the 0/180 wrap)."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    return mask


def red_coverage(hsv_band: np.ndarray, cfg: AutoTriggerConfig) -> float:
    """Fraction of an arc band matching the red bands after a MORPH_OPEN despeckle."""
    mask = _band_mask(hsv_band, cfg.red_bands)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.morph_kernel, cfg.morph_kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs as RED / not-red by red coverage vs ``cfg.coverage_threshold``."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    lc = red_coverage(roi_from_region(cfg.left_arc).crop(hsv), cfg)
    rc = red_coverage(roi_from_region(cfg.right_arc).crop(hsv), cfg)
    t = cfg.coverage_threshold
    return AlignmentState(left_red=lc >= t, right_red=rc >= t, left_cov=lc, right_cov=rc)
