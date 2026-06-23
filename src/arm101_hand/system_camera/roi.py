"""Region-of-interest crop helper for the arm-mounted USB observation camera (device layer).

A fixed ROI lets the live preview + recording zoom into just the Optomed Aurora's screen: the
arm + hand + camera geometry is fixed, so the screen always lands in the same place. The ROI is
stored in pixels against a *reference* frame size and rescaled to whatever the camera actually
delivers at crop time -- so the same constant maps to the same physical region regardless of the
negotiated capture resolution, and can never silently crop the wrong area if the camera opens
larger than expected.

Pure geometry + numpy slicing; no cv2. Shared by ``scripts/diagnostics/system_camera/usb_camera_roi_preview.py``
(validation) and ``WebcamPreview`` (when an ROI is configured), so the crop math lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Roi:
    """A pixel rectangle measured against a reference frame size (default 640x480).

    ``x, y`` is the top-left corner and ``w, h`` the size, all in reference-frame pixels.
    :meth:`for_frame` rescales those to the actual frame and clamps them inside its bounds;
    :meth:`crop` returns the corresponding sub-image.
    """

    x: int
    y: int
    w: int
    h: int
    ref_w: int = 640
    ref_h: int = 480
    angle: float = 0.0  # deskew rotation in degrees; 0 = plain axis-aligned slice (fast path)

    def for_frame(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` rescaled to ``frame_w x frame_h`` and clamped inside it."""
        sx, sy = frame_w / self.ref_w, frame_h / self.ref_h
        x = max(0, min(round(self.x * sx), frame_w - 1))
        y = max(0, min(round(self.y * sy), frame_h - 1))
        w = max(1, min(round(self.w * sx), frame_w - x))
        h = max(1, min(round(self.h * sy), frame_h - y))
        return x, y, w, h

    def crop(self, frame: np.ndarray) -> np.ndarray:
        """ROI sub-image of ``frame`` (rescaled + clamped). If ``angle`` != 0, the region is rotated
        by ``angle`` about the box centre first (deskew), so a slightly-tilted screen comes out
        upright. ``angle`` == 0 takes a plain-slice fast path (unchanged behaviour/perf)."""
        import cv2  # local import: roi.py stays cv2-free on the angle==0 path

        fh, fw = frame.shape[:2]
        x, y, w, h = self.for_frame(fw, fh)
        if self.angle == 0.0:
            return frame[y : y + h, x : x + w]
        cx, cy = x + w / 2.0, y + h / 2.0
        pad = int(round(0.2 * max(w, h))) + 2  # cover the rotated corners
        px0, py0 = max(0, x - pad), max(0, y - pad)
        px1, py1 = min(fw, x + w + pad), min(fh, y + h + pad)
        region = frame[py0:py1, px0:px1]
        m = cv2.getRotationMatrix2D((cx - px0, cy - py0), self.angle, 1.0)
        rot = cv2.warpAffine(region, m, (region.shape[1], region.shape[0]))
        rx, ry = int(round(cx - px0 - w / 2.0)), int(round(cy - py0 - h / 2.0))
        return rot[max(0, ry) : ry + h, max(0, rx) : rx + w]


class _RegionLike(Protocol):
    """Structural type for a config RoiBox/ArcRegion (avoids importing the pydantic model here)."""

    x: int
    y: int
    w: int
    h: int
    ref_w: int
    ref_h: int
    angle: float


def roi_from_region(region: _RegionLike) -> Roi:
    """Build a :class:`Roi` from a config ``RoiBox`` (carries the deskew ``angle``)."""
    return Roi(
        x=region.x,
        y=region.y,
        w=region.w,
        h=region.h,
        ref_w=region.ref_w,
        ref_h=region.ref_h,
        angle=region.angle,
    )


# Canonical ROI for the arm-mounted USB observation camera: a 4:3 crop (uniform zoom, no
# distortion) of the Optomed Aurora's screen, measured against a 640x480 frame -- ~3.3x zoom.
# Re-measured 2026-06-19 for the IFWATER 12 MP cam: the screen was marked with a red rectangle and
# the box detected in both the 640x480 and 4000x3000 captures (they agreed), then expanded from the
# screen's ~16:10 to 4:3 about its center so the crop->reference upscale stays distortion-free (the
# preview/recording resize the crop to ref_w x ref_h), then nudged x right 10px (50->60) to recenter
# the live screen content in the crop (validated live). The arm + hand + camera geometry is fixed, so
# the screen always lands here. SINGLE SOURCE OF TRUTH: grab_trigger_capture.py imports this for its
# preview + recording; usb_camera_roi_preview.py defaults its editable _ROI to it. Re-tune by
# validating a new value in the diagnostic, then editing it HERE.
AURORA_SCREEN_ROI = Roi(x=60, y=75, w=196, h=147, ref_w=640, ref_h=480)
