"""Preview the fixed ROI zoom of the arm-mounted USB observation camera (read-only diagnostic).

Validates the hardcoded region of interest that zooms the live preview + recording onto just the
Optomed Aurora's screen, BEFORE wiring it into ``scripts/demos/grab_trigger_capture.py``. The
arm + hand + camera geometry is fixed, so the screen always lands in the same place -- the ROI is
a constant here (``_ROI``). Run this, eyeball the framing, and adjust ``_ROI`` if needed.

NOT the Aurora *fundus* camera (patient retinal images -- that is ``arm101_hand.fundus_camera``);
this is the host webcam in ``arm101_hand.system_camera``. No motors, no Aurora link.

Two windows open (both letterboxed, never stretched on resize):
  * "<title> -- ROI zoom"   the ROI crop upscaled to 640x480: the zoomed feed the demo will show.
  * "<title> -- full frame" the full camera frame with the ROI rectangle drawn, so you can see
                            exactly what is being cropped against the whole scene.

Single-threaded by design (like ``usb_camera_capture.py``): this tool reads + shows frames itself
and polls the TERMINAL for keys via ``msvcrt.kbhit`` -- it can't block on ``getwch`` because the
cv2 windows need ``waitKey`` pumped every loop or they freeze. Shares ``open_capture`` and the
``Roi`` crop helper with the device layer, so the crop math is identical to the demo's.

The full ``opencv-python`` wheel is required for the windows (lerobot's ``opencv-python-headless``
has no HighGUI); ``pyproject.toml`` drops the headless pin so the full build is the sole ``cv2``.

Usage:
  uv run python scripts/diagnostics/usb_camera_roi_preview.py [--camera N] [--backend auto|dshow]

Keys (focus the TERMINAL, not the windows):
  q/ESC   quit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import msvcrt
import sys
import time
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.system_camera import Roi, open_capture  # noqa: E402

# Fixed ROI: a 4:3 crop (uniform zoom, no distortion) of the Aurora screen, measured against a
# 640x480 frame -- option 1 / 2.29x from the ROI selection gallery. ``Roi`` rescales it to the
# live frame at crop time, so a different capture resolution still maps to the same region.
_ROI = Roi(x=130, y=6, w=280, h=210, ref_w=640, ref_h=480)

_ZOOM_SIZE = (640, 480)  # upscale target for the "ROI zoom" window (matches the reference frame)
_GREEN = (0, 255, 0)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` -- that would freeze the cv2 windows,
    which need ``waitKey`` pumped every loop -- so we poll ``kbhit`` each iteration instead.
    Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow / function-key prefixes.
    """
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--camera", type=int, default=0, help="USB camera index (default 0; the arm cam is usually 1)"
    )
    ap.add_argument("--backend", choices=("auto", "dshow"), default="auto", help="cv2 capture backend")
    args = ap.parse_args()

    title = f"USB cam {args.camera} (ROI)"
    zoom_title = f"{title} -- ROI zoom"
    full_title = f"{title} -- full frame"

    print(f"Opening USB camera index {args.camera} ({args.backend}) ...")
    cap = open_capture(args.camera, args.backend)
    if not cap.isOpened():
        cap.release()
        print(
            f"ERROR: could not open camera index {args.camera}. "
            "Try another --camera N (the arm cam is usually 1; index 0 is often the built-in "
            "webcam) or --backend dshow.",
            file=sys.stderr,
        )
        return 1

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    rx, ry, rw, rh = _ROI.for_frame(width, height)
    print(
        f"Window open: {width}x{height} @ {src_fps:.0f} fps (camera-reported).\n"
        f"ROI (this frame): x={rx} y={ry} w={rw} h={rh}  (constant {_ROI.w}x{_ROI.h} @ "
        f"{_ROI.ref_w}x{_ROI.ref_h}).\n"
        "Keys (focus THIS terminal): q/ESC = quit."
    )
    if (width, height) != (_ROI.ref_w, _ROI.ref_h):
        print(
            f"  NOTE: live frame {width}x{height} differs from the ROI reference "
            f"{_ROI.ref_w}x{_ROI.ref_h} -- the ROI was rescaled to fit.",
            file=sys.stderr,
        )

    # WINDOW_NORMAL | KEEPRATIO: resizable + letterboxed -- scales uniformly, never stretches
    # (same flags as WebcamPreview's window in preview.py).
    for name in (zoom_title, full_title):
        cv2.namedWindow(name, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

    started = time.monotonic()
    last_good: float | None = None  # time of the most recent successful grab
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                # MSMF can open the device yet fail every grab when the cam is held by another app
                # or was left invalidated by a prior Ctrl+C'd run. Don't spin forever flooding
                # warnings -- bail with guidance once a grace window passes (then `finally` cleans up).
                now = time.monotonic()
                if last_good is None and now - started > _OPEN_GRACE_S:
                    print(
                        f"\nERROR: camera index {args.camera} opened but delivered no frames in "
                        f"{_OPEN_GRACE_S:.0f}s.\n"
                        "  Likely in use by another app (Optomed Client / Windows Camera / Teams / "
                        "browser) or left invalidated by a previous run.\n"
                        "  Fixes: close other camera users; unplug + replug the USB cam; "
                        "or try --backend dshow.",
                        file=sys.stderr,
                    )
                    return 1
                if last_good is not None and now - last_good > _STALL_S:
                    print(
                        f"\nERROR: camera index {args.camera} stopped delivering frames "
                        f"({_STALL_S:.0f}s stall) -- it may have been unplugged or grabbed by "
                        "another app. Reconnect it and retry.",
                        file=sys.stderr,
                    )
                    return 1
                cv2.waitKey(30)  # transient hiccup -- keep pumping, still honor q/ESC
                if _poll_key() in ("q", "Q", "\x1b"):
                    break
                continue

            last_good = time.monotonic()

            # ROI zoom: crop to the region, then upscale to the reference size -- this is exactly
            # the zoomed feed the demo's preview + recording will produce.
            zoom = cv2.resize(_ROI.crop(frame), _ZOOM_SIZE, interpolation=cv2.INTER_LINEAR)
            cv2.imshow(zoom_title, zoom)

            # Full frame with the ROI rectangle drawn (on a copy so the crop above stays clean).
            x, y, w, h = _ROI.for_frame(frame.shape[1], frame.shape[0])
            overlay = frame.copy()
            cv2.rectangle(overlay, (x, y), (x + w, y + h), _GREEN, 2)
            cv2.imshow(full_title, overlay)

            cv2.waitKey(1)  # pump GUI events; required for the windows to stay responsive
            if _poll_key() in ("q", "Q", "\x1b"):  # q / ESC
                break
    except KeyboardInterrupt:
        print("\n^C")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Camera closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
