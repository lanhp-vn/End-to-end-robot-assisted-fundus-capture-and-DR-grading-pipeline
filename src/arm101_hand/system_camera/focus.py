"""Focus-probe core for the arm-mounted USB observation camera (device layer).

The IFWATER module (Sony IMX362) ships with PDAF *auto*focus, which hunts onto the background
(the room behind the Optomed Aurora) and breathes during recording. Because the arm + camera +
screen geometry is fixed, a single *manual* focus value keeps the Aurora screen permanently sharp.
UVC exposes focus as one global lens position (no per-region AF), so "focus on the ROI" means
picking the manual value that maximizes sharpness measured *on the ROI crop* -- which is what these
helpers compute. The cv2 window + camera control (``CAP_PROP_AUTOFOCUS`` / ``CAP_PROP_FOCUS``) lives
in ``scripts/diagnostics/system_camera/usb_camera_focus_probe.py``; the pure, testable math lives here.
"""

from __future__ import annotations

import cv2
import numpy as np


def sharpness(frame: np.ndarray) -> float:
    """Focus sharpness score = variance of the Laplacian (higher is sharper).

    A sharp image has strong edges -> a wide spread of Laplacian responses -> high variance; a
    blurred or flat image has weak edges -> low variance. Color frames are converted to gray first.
    Crop to the ROI before calling to score only the region you care about.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def focus_steps(lo: int, hi: int, step: int) -> list[int]:
    """Inclusive sweep values ``lo, lo+step, ... hi`` (``hi`` is always probed, even off-boundary).

    Raises ``ValueError`` for ``step < 1`` or ``lo > hi``.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    if lo > hi:
        raise ValueError(f"lo ({lo}) must be <= hi ({hi})")
    vals = list(range(lo, hi + 1, step))
    if vals[-1] != hi:
        vals.append(hi)
    return vals


def best_sample(samples: list[tuple[int, float]]) -> tuple[int, float] | None:
    """Return the ``(focus_value, sharpness)`` with the highest sharpness, or ``None`` if empty.

    On a tie the first (lowest-index) maximal sample wins.
    """
    if not samples:
        return None
    return max(samples, key=lambda s: s[1])


def focus_reached_baseline(best_sharpness: float | None, af_baseline: float, frac: float = 0.6) -> bool:
    """True if the best MANUAL-focus sharpness reached ``frac`` of the AUTOFOCUS baseline.

    Absolute sharpness depends on the scene's texture, so a fixed threshold is meaningless. The
    autofocus baseline self-calibrates "sharp" to the current ROI: if manual focus gets within
    ``frac`` of what AF achieves on the same scene, the focus motor is genuinely being driven to
    the focal plane. A best far below the baseline (the MSMF symptom: best ~10 vs baseline ~950)
    means the control isn't taking effect -- try a different backend. ``None`` best (no samples) or
    a non-positive baseline (AF itself never focused) both return False.
    """
    if best_sharpness is None or af_baseline <= 0:
        return False
    return best_sharpness >= frac * af_baseline
