"""Read-only USB-camera smoke test: live cv2 preview window + record toggle.

Exercises the shared ``arm101_hand.system_camera.WebcamPreview`` (the same class the
``grab_trigger_capture.py`` demo uses) in isolation -- no motors, no Aurora camera --
so you can confirm a camera index opens, the window streams, and a clip records + plays.

The full ``opencv-python`` wheel is required for the window (lerobot's
``opencv-python-headless`` has no HighGUI); ``pyproject.toml`` drops the headless pin so
the full build is the sole ``cv2`` (``cv2.getBuildInformation()`` -> ``GUI: WIN32UI``).

PASS BAR (judge on all three, not just "a window appeared"):
  1. window stays responsive for 30+ seconds,
  2. the recorded clip plays back,
  3. teardown is clean on BOTH 'q' AND Ctrl+C.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_probe.py [--camera N] [--backend auto|dshow]
                                                        [--record-dir DIR] [--fps F]

Keys (focus the TERMINAL, not the window):
  r   start / stop recording (clip saved under --record-dir)
  q   quit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import msvcrt
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import WebcamPreview  # noqa: E402

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


def _read_key() -> str:
    """Blocking single keypress (mirrors the demos' msvcrt handling)."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume + ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--camera",
        type=int,
        default=None,
        help="USB camera index (default: camera_index from system_camera_config.yaml)",
    )
    ap.add_argument("--backend", choices=("auto", "dshow"), default="auto", help="cv2 capture backend")
    ap.add_argument(
        "--record-dir",
        default=str(_REPO_ROOT / "media_outputs" / "camera_recordings"),
        help="folder for recorded clips (default: <repo>/media_outputs/camera_recordings)",
    )
    ap.add_argument("--fps", type=float, default=None, help="force writer fps (default: camera-reported)")
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    preview = WebcamPreview(
        index=camera_index,
        window_title=f"USB cam {camera_index} (smoke test)",
        record_dir=Path(args.record_dir),
        fps=args.fps,
        backend=args.backend,
        fourcc=cfg.fourcc,
        width=cfg.width,
        height=cfg.height,
    )
    print(f"Opening USB camera index {camera_index} ({args.backend}) ...")
    if not preview.start():
        print(
            f"ERROR: could not open camera index {camera_index}. "
            "Try another --camera N (built-in webcam is often 0) or --backend dshow.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Window open: {preview.width}x{preview.height} @ {preview.src_fps:.0f} fps (camera-reported).\n"
        "Keys (focus this terminal): r = start/stop recording, q = quit."
    )

    try:
        while True:
            ch = _read_key()
            if ch in ("q", "Q"):
                break
            if ch in ("r", "R"):
                path = preview.toggle_record()
                print(f"  recording -> {path}" if path else "  recording stopped.")
    except KeyboardInterrupt:
        print("\n^C")
    finally:
        preview.stop()
        print("Camera closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
