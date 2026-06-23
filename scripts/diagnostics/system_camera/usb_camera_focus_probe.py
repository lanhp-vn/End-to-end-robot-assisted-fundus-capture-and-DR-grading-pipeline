"""Probe + tune MANUAL focus for the arm-mounted USB observation camera (read-only diagnostic).

The IFWATER module (Sony IMX362) defaults to PDAF *auto*focus, which hunts onto the room behind the
Optomed Aurora and breathes during recording. Because the arm + camera + screen geometry is fixed,
one *manual* focus value keeps the Aurora screen permanently sharp. UVC has no per-region AF (focus
is a single global lens position), so "focus on the ROI" = the manual ``CAP_PROP_FOCUS`` value that
maximizes sharpness measured *on* the configured ``screen_roi``. This tool finds it and lets you
fine-tune.

This is the GATING check before wiring focus into ``system_camera_config.yaml`` + ``open_capture``:
it also tells us whether this unit/backend even accepts manual focus. Absolute sharpness depends on
the scene, so the probe first measures an AUTOFOCUS baseline (what AF achieves on this exact view)
and judges manual focus against it. If the sweep's best stays far below that baseline, the focus
motor isn't being driven -- retry with ``--backend dshow`` (DSHOW exposes focus controls far more
reliably than the MSMF default on Windows).

What it does:
  1. Measures the autofocus baseline: AF on, settle, record the best ROI sharpness (the self-
     calibrating "what sharp looks like" for this scene).
  2. Disables autofocus and sweeps ``CAP_PROP_FOCUS`` over ``--min..--max`` step ``--step``, scoring
     ROI sharpness (variance of Laplacian) at each, live in a window. Prints the sharpest value and
     a verdict: did manual focus reach a usable fraction of the AF baseline?
  3. Holds at the best value and drops into a manual fine-tune loop so you can nudge + confirm,
     then prints the lines to paste into ``system_camera_config.yaml``. Restores autofocus on exit
     so the camera is never left parked at a blurry manual value for the next script.

NOT the Aurora *fundus* camera (patient retinal images -- that is ``arm101_hand.fundus_camera``);
this is the host webcam in ``arm101_hand.system_camera``. No motors, no Aurora link. Single-threaded
like the other system_camera diagnostics: it reads + shows frames itself and polls the TERMINAL for
keys via ``msvcrt.kbhit`` (it can't block on ``getwch`` -- the cv2 window needs ``waitKey`` pumped).

The full ``opencv-python`` wheel is required for the window (lerobot's ``opencv-python-headless``
has no HighGUI); ``pyproject.toml`` drops the headless pin so the full build is the sole ``cv2``.

Usage:
  uv run python scripts/diagnostics/system_camera/usb_camera_focus_probe.py [--camera N]
        [--backend auto|dshow] [--min 0] [--max 1023] [--step 20] [--settle 8]
  (focus controls need --backend dshow on Windows; MSMF accepts the calls but doesn't drive the motor)

Keys (focus the TERMINAL, not the window) -- in the fine-tune loop:
  [ / ]   nudge focus down / up by --step
  a       toggle autofocus on/off
  SPACE   print current focus value + ROI sharpness
  s       re-run the automated sweep
  q/ESC   quit (Ctrl+C also works); on quit prints the config lines to save
"""

from __future__ import annotations

import argparse
import msvcrt
import sys
import time
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    Roi,
    best_sample,
    focus_reached_baseline,
    focus_steps,
    imshow_fit,
    open_capture,
    roi_from_region,
    sharpness,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
# ROI to score sharpness on. Set in main() from screen_roi in system_camera_config.yaml (re-derived
# by scripts/calibration/system_camera/calibrate_view.py); placeholder until then.
_ROI: Roi = Roi(x=0, y=0, w=1, h=1)
_ZOOM_SIZE = (640, 480)  # upscale target for the ROI window (matches the reference frame)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_GREEN = (0, 255, 0)
_YELLOW = (0, 255, 255)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_AF_SETTLE_FRAMES = 45  # frames to let autofocus hunt + settle when measuring the baseline (~1.5 s)


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting)."""
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _settle(cap: cv2.VideoCapture, frames: int):
    """Pump ``frames`` reads so a focus move + exposure settle; return the last good frame (or None).

    ``waitKey`` is pumped each read so the window stays responsive while the lens moves.
    """
    last = None
    for _ in range(frames):
        ok, f = cap.read()
        if ok and f is not None:
            last = f
        cv2.waitKey(1)
    return last


def _roi_sharpness(frame) -> float:
    return sharpness(_ROI.crop(frame))


def _show(frame, focus: int, score: float, best: tuple[int, float] | None, af: bool):
    """Render the ROI zoom upscaled to the reference size with a focus/sharpness HUD."""
    zoom = cv2.resize(_ROI.crop(frame), _ZOOM_SIZE, interpolation=cv2.INTER_LINEAR)
    lines = [
        f"AF {'ON' if af else 'off'}   focus={focus}   sharpness={score:7.1f}",
        f"best: focus={best[0]} sharpness={best[1]:.1f}" if best else "best: (sweeping...)",
    ]
    for i, text in enumerate(lines):
        cv2.putText(zoom, text, (12, 28 + 26 * i), _FONT, 0.6, _GREEN if i == 0 else _YELLOW, 2, cv2.LINE_AA)
    imshow_fit("USB cam focus probe -- ROI", zoom)


def _set_focus(cap: cv2.VideoCapture, value: int) -> None:
    cap.set(cv2.CAP_PROP_FOCUS, float(value))


def _get_focus(cap: cv2.VideoCapture) -> int:
    return int(round(cap.get(cv2.CAP_PROP_FOCUS)))


def _sweep(cap, values: list[int], settle: int) -> list[tuple[int, float]]:
    """Set each focus value, settle, score ROI sharpness. Returns samples; 'q' aborts early."""
    samples: list[tuple[int, float]] = []
    best: tuple[int, float] | None = None
    for v in values:
        _set_focus(cap, v)
        frame = _settle(cap, settle)
        if frame is None:
            continue
        score = _roi_sharpness(frame)
        samples.append((v, score))
        best = best_sample(samples)
        _show(frame, v, score, best, af=False)
        print(f"  focus={v:4d}  sharpness={score:8.1f}")
        if _poll_key() in ("q", "Q", "\x1b"):
            print("  (sweep aborted)")
            break
    return samples


def _af_baseline(cap: cv2.VideoCapture) -> float:
    """Turn autofocus ON, let it settle, and return the best ROI sharpness it achieves.

    This self-calibrates "sharp" to the current scene: absolute Laplacian variance depends on ROI
    texture, so we judge manual focus against what AF manages on the very same view, not a magic
    constant. Returns the MAX (the sharpest moment) over the settle window.
    """
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 1.0)
    best = 0.0
    for _ in range(_AF_SETTLE_FRAMES):
        ok, f = cap.read()
        if ok and f is not None:
            best = max(best, _roi_sharpness(f))
        cv2.waitKey(1)
    return best


def _report_verdict(best: tuple[int, float] | None, af_baseline: float, range_top: int) -> None:
    """Compare the sweep's best manual focus against the AF baseline and print the verdict."""
    if best is None:
        print("WARNING: no samples captured -- camera delivered no frames during the sweep.", file=sys.stderr)
        return
    pct = best[1] / af_baseline * 100 if af_baseline > 0 else 0.0
    if focus_reached_baseline(best[1], af_baseline):
        print(
            f"Manual focus WORKS: best focus={best[0]} (sharpness {best[1]:.1f} = {pct:.0f}% of the "
            f"AF baseline {af_baseline:.1f})."
        )
        return
    msg = (
        f"WARNING: manual focus did NOT reach a sharp value -- best sharpness {best[1]:.1f} is only "
        f"{pct:.0f}% of the AF baseline {af_baseline:.1f}."
    )
    if best[0] == range_top:
        # Peak pinned to the top of the swept range -> the true optimum is beyond it; extend --max.
        msg += (
            f"\n  But the best is at the TOP of the swept range ({range_top}) -- the optimum is likely\n"
            "  higher. Re-run with a larger --max to bracket the peak (the focus motor IS responding)."
        )
    else:
        msg += (
            "\n  The focus motor may not be driven on this backend (a known MSMF/UVC issue) -- try\n"
            "  --backend dshow; or widen --min/--max/--step. Else this unit locks focus to auto."
        )
    print(msg, file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="camera index (default: config camera_index)")
    ap.add_argument(
        "--backend",
        choices=("auto", "dshow"),
        default=None,
        help="cv2 capture backend (default: backend from system_camera_config.yaml; focus needs dshow)",
    )
    ap.add_argument("--min", type=int, default=0, help="lowest focus value to sweep")
    ap.add_argument(
        "--max", type=int, default=1023, help="highest focus value to sweep (UVC VCM range is often 0..1023)"
    )
    ap.add_argument("--step", type=int, default=20, help="focus sweep step")
    ap.add_argument("--settle", type=int, default=8, help="frames to discard after each focus change")
    args = ap.parse_args()

    try:
        values = focus_steps(args.min, args.max, args.step)
    except ValueError as e:
        print(f"ERROR: bad sweep range -- {e}", file=sys.stderr)
        return 2

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend
    # Score sharpness on the calibrated screen_roi (the value the focus value will be locked for).
    global _ROI
    _ROI = roi_from_region(cfg.screen_roi)

    # This probe MANAGES focus itself (it sweeps), so it never passes cfg.focus to open_capture --
    # the defaults (autofocus on, focus untouched) leave it free to measure the AF baseline first.
    print(f"Opening USB camera index {camera_index} ({backend}) ...")
    cap = open_capture(camera_index, backend, fourcc=cfg.fourcc, width=cfg.width, height=cfg.height)
    if not cap.isOpened():
        cap.release()
        print(
            f"ERROR: could not open camera index {camera_index}. Try another --camera N "
            "(arm cam is usually 1) or --backend dshow.",
            file=sys.stderr,
        )
        return 1

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rx, ry, rw, rh = _ROI.for_frame(width, height)

    # Confirm the camera is delivering frames before we start poking focus.
    started = time.monotonic()
    while _settle(cap, 1) is None:
        if time.monotonic() - started > _OPEN_GRACE_S:
            print(
                f"\nERROR: camera index {camera_index} opened but delivered no frames in "
                f"{_OPEN_GRACE_S:.0f}s. Close other camera apps, replug, or try --backend dshow.",
                file=sys.stderr,
            )
            cap.release()
            return 1

    cv2.namedWindow("USB cam focus probe -- ROI", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("USB cam focus probe -- ROI", *_ZOOM_SIZE)

    print(f"Frame {width}x{height}; ROI x={rx} y={ry} w={rw} h={rh}.")
    # Self-calibrate: what sharpness does AUTOfocus achieve on this exact scene? Manual focus is
    # judged against this, not a magic constant (a near-blank ROI scores low even when sharp).
    print("Measuring autofocus baseline (point at the Aurora screen, hold steady) ...")
    af_baseline = _af_baseline(cap)
    af_focus = _get_focus(cap)  # the lens position AF chose: best target hint + reveals the value scale
    print(f"  AF baseline ROI sharpness: {af_baseline:.1f}  (manual focus aims to get near this)")
    print(f"  AF settled at focus value {af_focus}  (bracket this with --min/--max; shows the range scale)")
    if af_baseline <= 0:
        print(
            "  WARNING: autofocus itself scored ~0 -- the camera delivered no usable frames or "
            "couldn't focus the ROI. Check the framing/lighting before trusting the sweep.",
            file=sys.stderr,
        )

    # Disable autofocus so manual CAP_PROP_FOCUS takes hold (readback is advisory on Windows).
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0.0)
    af_readback = cap.get(cv2.CAP_PROP_AUTOFOCUS)
    print(
        f"Autofocus -> off (read back {af_readback:g}; manual reads 2 on DSHOW, 0 on MSMF). "
        f"Sweeping focus {args.min}..{args.max} step {args.step}, {len(values)} values:"
    )

    autofocus = False
    try:
        samples = _sweep(cap, values, args.settle)
        best = best_sample(samples)
        _report_verdict(best, af_baseline, values[-1])
        if best is not None:
            _set_focus(cap, best[0])
            print(
                f"\nBest ROI focus: {best[0]} (sharpness {best[1]:.1f}). Holding there.\n"
                "Fine-tune: [ / ] nudge, 'a' toggle AF, SPACE print, 's' re-sweep, q/ESC quit."
            )
        focus = best[0] if best else _get_focus(cap)
        while True:
            frame = _settle(cap, 1)
            if frame is None:
                cv2.waitKey(30)
                if _poll_key() in ("q", "Q", "\x1b"):
                    break
                continue
            score = _roi_sharpness(frame)
            _show(frame, focus, score, best, autofocus)
            cv2.waitKey(1)
            key = _poll_key()
            if key in ("q", "Q", "\x1b"):
                break
            if key == "[":
                focus = max(args.min, focus - args.step)
                _set_focus(cap, focus)
            elif key == "]":
                focus = min(args.max, focus + args.step)
                _set_focus(cap, focus)
            elif key in ("a", "A"):
                autofocus = not autofocus
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1.0 if autofocus else 0.0)
                if not autofocus:
                    _set_focus(cap, focus)
                print(f"  autofocus {'ON' if autofocus else 'off'}")
            elif key in ("s", "S"):
                print("Re-sweeping ...")
                autofocus = False
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 0.0)
                samples = _sweep(cap, values, args.settle)
                best = best_sample(samples)
                _report_verdict(best, af_baseline, values[-1])
                if best is not None:
                    focus = best[0]
                    _set_focus(cap, focus)
                    print(f"Best ROI focus: {best[0]} (sharpness {best[1]:.1f}). Holding there.")
            elif key == " ":
                print(f"  focus={focus}  ROI sharpness={score:.1f}")
    except KeyboardInterrupt:
        print("\n^C")
    finally:
        # Restore autofocus before releasing: other scripts don't set focus yet (until Phase 3), so
        # leaving AF off would hand them a lens parked at our manual value -- i.e. a blurry preview.
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1.0)
        cap.release()
        cv2.destroyAllWindows()

    print(
        "\nCamera closed (autofocus restored). To lock this focus everywhere, add to "
        "src/arm101_hand/data/system_camera_config.yaml (Phase 3 wires open_capture to apply it):\n"
        "  autofocus: false\n"
        f"  focus: {focus}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
