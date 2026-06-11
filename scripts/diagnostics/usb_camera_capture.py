"""Capture a still from the arm-mounted USB observation camera (read-only diagnostic).

Opens a live cv2 preview of the USB camera that films the Optomed Aurora's screen so you can
frame the shot, then writes the CURRENT frame to disk on SPACE. NOT the Aurora *fundus* camera
(patient retinal images -- that is ``arm101_hand.fundus_camera``); this is the host webcam in
``arm101_hand.system_camera``.

Single-threaded by design: unlike ``usb_camera_probe.py`` (which exercises the daemon-threaded
``WebcamPreview``), this tool reads + shows frames itself. Keys are read from the TERMINAL via a
non-blocking ``msvcrt.kbhit()`` poll -- we can't block on ``getwch`` like the probe does, because
the cv2 window needs ``waitKey`` pumped every loop or it freezes. It shares only ``open_capture``
with the device layer: ``open_capture`` (the dshow-quirk handling) and ``imshow_fit`` (manual
letterbox -- KEEPRATIO is Qt-only, a no-op on the Win32 backend; see ``preview.py``).

The full ``opencv-python`` wheel is required for the window (lerobot's ``opencv-python-headless``
has no HighGUI); ``pyproject.toml`` drops the headless pin so the full build is the sole ``cv2``.

Usage:
  uv run python scripts/diagnostics/usb_camera_capture.py [--camera N] [--backend auto|dshow]
                                                          [--out-dir DIR]

Keys (focus the TERMINAL, not the window):
  SPACE   save the current frame to --out-dir as usb_cam_<timestamp>.jpg
  q/ESC   quit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import msvcrt
import sys
import time
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.system_camera import imshow_fit, open_capture  # noqa: E402

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_GREEN = (0, 255, 0)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` like the probe does -- that would freeze
    the cv2 window, which needs ``waitKey`` pumped every loop -- so we poll ``kbhit`` each
    iteration instead. Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow / function prefixes.
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
    ap.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "media_outputs" / "camera_captures"),
        help="folder for saved stills (default: <repo>/media_outputs/camera_captures)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    title = f"USB cam {args.camera} (capture)"

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
    print(
        f"Window open: {width}x{height} @ {src_fps:.0f} fps (camera-reported).\n"
        "Keys (focus THIS terminal): SPACE = capture, q/ESC = quit."
    )

    # WINDOW_NORMAL + manual letterbox via imshow_fit: keep the aspect ratio ourselves. KEEPRATIO
    # is Qt-only -- a no-op on this wheel's Win32 backend (see preview.py). resizeWindow sets a sane
    # initial size.
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    if width > 0 and height > 0:
        cv2.resizeWindow(title, width, height)
    saved = 0
    started = time.monotonic()
    last_good: float | None = None  # time of the most recent successful grab
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                # MSMF can open the device yet fail every grab when the cam is held by another
                # app or was left invalidated by a prior Ctrl+C'd run. Don't spin forever flooding
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

            disp = frame
            if saved:  # overlay the saved-count on a COPY so the saved frame stays clean
                disp = frame.copy()
                cv2.putText(
                    disp, f"saved: {saved}", (12, disp.shape[0] - 14), _FONT, 0.7, _GREEN, 2, cv2.LINE_AA
                )
            imshow_fit(title, disp)
            cv2.waitKey(1)  # pump GUI events; required for the window to stay responsive

            key = _poll_key()
            if key in ("q", "Q", "\x1b"):  # q / ESC
                break
            if key == " ":  # SPACE -> save the CLEAN current frame
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ms -> no same-second clobber
                path = out_dir / f"usb_cam_{ts}.jpg"
                if cv2.imwrite(str(path), frame):
                    saved += 1
                    print(f"  saved -> {path}")
                else:
                    print(f"  WARNING: could not write {path}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n^C")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"Camera closed. {saved} still(s) saved to {out_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
