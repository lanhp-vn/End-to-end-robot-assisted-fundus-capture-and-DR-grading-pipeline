"""Arc-detection debug collector for the arm-mounted USB observation camera (read-only diagnostic).

Stages the arm + hand into the grab pose (the ONLY pose where the Aurora-screen ROI geometry is
valid), then shows the live deskewed 5:3 (800x480) screen ROI with the two alignment-arc boxes
drawn -- each RED when ``arc_detector.detect()`` classifies that arc as RED (misaligned) and GREEN
when it reads clear -- plus a raw-classification HUD (per-arc coverage vs the threshold). Use it to
collect frames where the RED/clear classification is WRONG, so ``coverage_threshold`` / ``red_bands``
/ the arc box geometry can be retuned against real evidence.

This is a DIAGNOSTIC, not a demo: it runs the same staged grab as the demos but makes NO Aurora
connection, presses NO shutter, fires NO trigger, records NO video, and grades nothing. It only
observes the camera and saves stills on demand. The arc boxes + red bands + screen ROI it draws and
records are read verbatim from ``system_camera_config.yaml`` (never written -- IL-5); re-derive them
with ``scripts/calibration/system_camera/calibrate_view.py``.

The arm + hand buses are opened/closed solely by ``run_grab_demo`` (one owner per bus -- IL-4); this
tool opens only the USB camera. All motion is the defined staged sequence; on exit torque is released
in place (or reversed only on explicit 'h' at the prompt) -- no surprise movement.

SPACE saves one "case" -- three files sharing a millisecond-timestamp stem ``arc_<ts>`` in
``--out-dir`` (default ``media_outputs/arc_debug/``, git-ignored):
  * ``arc_<ts>_clean.png``      the un-annotated 800x480 ROI -- ground truth, re-feedable to detect()
                                / calibrate_view.py --from-files.
  * ``arc_<ts>_annotated.png``  the ROI with arc boxes + verdict HUD burned in.
  * ``arc_<ts>.json``           the numbers the detector saw: per-arc coverage + verdict, the
                                threshold, the red bands, the exact ROI/arc geometry used, camera
                                metadata, and an empty ``expected_note`` for you to fill in later.

The full ``opencv-python`` wheel is required for the window (lerobot's headless build has no HighGUI;
``pyproject.toml`` drops the headless pin). Focus is locked per ``system_camera_config.yaml``
(``autofocus``/``focus``; DSHOW-only) so the ROI you check is sharp.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py
      [--camera N] [--backend auto|dshow] [--out-dir DIR]

Keys (focus the TERMINAL, not the window):
  SPACE     save one case (clean + annotated + sidecar) to --out-dir
  q / ESC   quit the loop -> arm/hand exit prompt (Enter releases in place, 'h' reverses)
  Ctrl+C    same as q
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox  # noqa: E402
from arm101_hand.system_camera import AlignmentState  # noqa: E402


def build_arc_case_sidecar(
    alignment: AlignmentState,
    cfg: AutoTriggerConfig,
    screen_roi: RoiBox,
    *,
    camera_index: int,
    backend: str,
    frame_w: int,
    frame_h: int,
    captured_at: _dt.datetime,
) -> dict[str, object]:
    """Assemble the JSON-serializable sidecar dict for one saved arc-debug case.

    Pure (no cv2, no I/O): records what ``detect()`` saw -- per-arc coverage + verdict, the
    threshold, the red bands, and the exact ROI/arc geometry that produced the classification --
    plus camera metadata and an empty operator-fillable ``expected_note``. ``captured_at`` is
    injected (not read from the clock) so the function is deterministic + unit-testable.
    """
    return {
        "tool": "usb_camera_arc_debug",
        "captured_at": captured_at.isoformat(),
        "camera": {
            "index": camera_index,
            "backend": backend,
            "frame_w": frame_w,
            "frame_h": frame_h,
        },
        "screen_roi": screen_roi.model_dump(),
        "detection": {
            "left_red": alignment.left_red,
            "left_cov": alignment.left_cov,
            "right_red": alignment.right_red,
            "right_cov": alignment.right_cov,
            "both_red": alignment.both_red,
            "both_clear": alignment.both_clear,
            "coverage_threshold": cfg.coverage_threshold,
        },
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "red_bands": [b.model_dump() for b in cfg.red_bands],
        "morph_kernel": cfg.morph_kernel,
        "expected_note": "",
    }
