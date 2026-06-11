"""Region-of-interest crop helper for the arm-mounted USB observation camera (device layer).

A fixed ROI lets the live preview + recording zoom into just the Optomed Aurora's screen: the
arm + hand + camera geometry is fixed, so the screen always lands in the same place. The ROI is
stored in pixels against a *reference* frame size and rescaled to whatever the camera actually
delivers at crop time -- so the same constant maps to the same physical region regardless of the
negotiated capture resolution, and can never silently crop the wrong area if the camera opens
larger than expected.

Pure geometry + numpy slicing; no cv2. Shared by ``scripts/diagnostics/usb_camera_roi_preview.py``
(validation) and ``WebcamPreview`` (when an ROI is configured), so the crop math lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

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

    def for_frame(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` rescaled to ``frame_w x frame_h`` and clamped inside it."""
        sx, sy = frame_w / self.ref_w, frame_h / self.ref_h
        x = max(0, min(round(self.x * sx), frame_w - 1))
        y = max(0, min(round(self.y * sy), frame_h - 1))
        w = max(1, min(round(self.w * sx), frame_w - x))
        h = max(1, min(round(self.h * sy), frame_h - y))
        return x, y, w, h

    def crop(self, frame: np.ndarray) -> np.ndarray:
        """Return the ROI sub-image of ``frame`` (a view, rescaled + clamped to the frame)."""
        fh, fw = frame.shape[:2]
        x, y, w, h = self.for_frame(fw, fh)
        return frame[y : y + h, x : x + w]


# Canonical ROI for the arm-mounted USB observation camera: a 4:3 crop (uniform zoom, no
# distortion) of the Optomed Aurora's screen, measured against a 640x480 frame -- option 1 / 2.29x,
# validated live with scripts/diagnostics/usb_camera_roi_preview.py. The arm + hand + camera
# geometry is fixed, so the screen always lands here. SINGLE SOURCE OF TRUTH: grab_trigger_capture.py
# imports this for its preview + recording; usb_camera_roi_preview.py defaults its editable _ROI to
# it. Re-tune by validating a new value in the diagnostic, then editing it HERE.
AURORA_SCREEN_ROI = Roi(x=130, y=6, w=280, h=210, ref_w=640, ref_h=480)
