"""Interactive view-calibration for the arm-mounted USB observation camera.

Re-derives the Aurora SCREEN ROI (a deskewed 5:3 / 800x480 crop carrying a rotation angle), the two
symmetric alignment-ARC bands on the camera circle, the RED HSV band(s), and the coverage threshold,
then writes them into src/arm101_hand/data/system_camera_config.yaml. Run after any RESOLUTION or
LIGHTING change (the fixed-fraction ROI + colour bands drift otherwise).

Detection is RED-ONLY: each arc is RED (misaligned) or not-red (aligned); no green is sampled (the
bright/aligned screen reads greenish overall, so green coverage is unreliable).

Flow:
  1. WHITE startup screen -> pick 1 of up to 3 deskewed 5:3 ROI candidates (keys 1/2/3), or 'm' to
     drag one manually (cv2.selectROI, angle 0), or 'r' to recapture. The chosen ROI is a RoiBox at
     the 800x480 detection reference with a deskew angle.
  2. RED arcs -> deskew-crop the ROI -> sample the RED band from a misaligned arc.
  3. BRIGHT / aligned screen -> deskew-crop -> fit the camera circle -> derive symmetric arc bands +
     validate the bands read not-red on the bright frame; pick the coverage threshold.
  4. CONFIRM screen: two deskewed panels labelled by the REAL arc_detector.detect():
     RED panel (expect both arcs RED) + BRIGHT panel (expect both clear, fitted circle drawn).
     'y' writes, 'e' re-tune arc boxes (selectROI), 'r' redo, 'q' quit.
Like usb_camera_capture.py, ACTION KEYS are read from the TERMINAL (a cv2 window often has no
keyboard focus when launched from a console); the window only displays. The 'm'/'e' manual drags
use cv2.selectROI, which you interact with IN the window. Plain opencv-python only (no contrib).

Usage:
  uv run python scripts/calibration/system_camera/calibrate_view.py [--camera N] [--backend auto|dshow]
  uv run python scripts/calibration/system_camera/calibrate_view.py --from-files WHITE RED BRIGHT
"""

from __future__ import annotations

import argparse
import msvcrt
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import AutoTriggerConfig, HsvBand, RoiBox  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    imshow_fit,
    open_capture,
    roi_from_region,
)
from arm101_hand.system_camera.arc_detector import _band_mask, detect, red_coverage  # noqa: E402
from arm101_hand.system_camera.calibration import (  # noqa: E402
    arc_bands_from_circle,
    deskew_crop,
    detect_screen_rects,
    fit_camera_circle,
    sample_red_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
    write_calibration_values,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_DETECT = (800, 480)  # the deskewed 5:3 detection reference (ref_w x ref_h)
_REF_W, _REF_H = _DETECT
_MAX_WIN_W, _MAX_WIN_H = 1100, 800  # initial window cap so large frames fit the screen


def _open_window(title: str, frame_w: int, frame_h: int) -> None:
    """Create a resizable window sized to fit the screen (never upscales). MUST precede imshow_fit:
    on the Win32 backend getWindowImageRect raises a NULL-window error for a never-created window."""
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    scale = min(_MAX_WIN_W / frame_w, _MAX_WIN_H / frame_h, 1.0)
    cv2.resizeWindow(title, max(1, round(frame_w * scale)), max(1, round(frame_h * scale)))


def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL ('' if none waiting).

    The cv2 window needs ``waitKey`` pumped every loop to stay responsive, so action keys are read
    from the CONSOLE instead (via msvcrt) -- this works regardless of which window has OS focus, so
    the operator presses keys in the same terminal they launched from (a cv2 window often does NOT
    get keyboard focus when launched from a console). Mirrors usb_camera_capture.py. Ctrl+C raises
    KeyboardInterrupt; arrow / function-key prefixes are swallowed.
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


def _capture_frame(cap, title: str, overlay: RoiBox | None) -> np.ndarray | None:
    """Stream until SPACE (return the frame) or q/ESC (return None). Draws ``overlay`` if given.

    Keys are read from the TERMINAL (see :func:`_poll_key`) while ``waitKey`` only pumps the window,
    so the operator presses SPACE/q in the console regardless of which window has OS focus."""
    print(f"\n{title}\n  Focus THIS terminal: SPACE = capture this frame, q = quit.")
    opened = False
    overlay_roi = roi_from_region(overlay) if overlay is not None else None
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            cv2.waitKey(30)  # keep the window pumped during camera warm-up / hiccups
            if _poll_key() in ("q", "Q", "\x1b"):
                return None
            continue
        if not opened:  # create the window once we know the frame size (imshow_fit needs it to exist)
            _open_window(title, frame.shape[1], frame.shape[0])
            opened = True
        disp = frame.copy()
        if overlay_roi is not None:
            x, y, w, h = overlay_roi.for_frame(frame.shape[1], frame.shape[0])
            cv2.rectangle(disp, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(disp, title, (12, 28), _FONT, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        cv2.waitKey(1)  # render only; action keys come from the terminal
        key = _poll_key()
        if key == " ":
            return frame
        if key in ("q", "Q", "\x1b"):
            return None


def _pick_roi(white: np.ndarray) -> RoiBox | None:
    """Show up to 3 deskewed 5:3 ROI candidates; return the chosen RoiBox, or None to recapture.

    Candidates come from detect_screen_rects -> screen_roi_from_rect (each a RoiBox at the 800x480
    detection reference carrying a deskew angle). Each candidate's rotated box is drawn on the white
    frame so the operator sees the tilt. 'm' drags an axis-aligned box manually (angle 0)."""
    fh, fw = white.shape[:2]
    rects = detect_screen_rects(white, top_n=3)
    cands = [screen_roi_from_rect(r, fw, fh) for r in rects]
    if not cands:  # nothing detected -> offer the whole frame so the operator always has an option
        cands = [screen_roi_from_rect(((fw / 2, fh / 2), (float(fw), float(fh)), 0.0), fw, fh)]
    title = "Calib 1/3: pick ROI (press keys in the TERMINAL)"
    colors = [(0, 255, 0), (0, 200, 255), (255, 180, 0)]
    print(
        "\nPick the screen ROI -- focus THIS terminal:\n"
        "  1 / 2 / 3 = select that numbered candidate box   m = drag one manually (in the window)\n"
        "  r = recapture   q = quit"
    )
    _open_window(title, fw, fh)
    while True:
        disp = white.copy()
        for i, box in enumerate(cands[:3]):
            x, y, w, h = roi_from_region(box).for_frame(fw, fh)
            cx, cy = x + w / 2.0, y + h / 2.0
            pts = cv2.boxPoints(((cx, cy), (float(w), float(h)), -box.angle)).astype(np.int32)
            cv2.polylines(disp, [pts], True, colors[i], 2)
            cv2.putText(disp, str(i + 1), (x + 6, y + 26), _FONT, 0.9, colors[i], 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        cv2.waitKey(20)  # render only; action keys come from the terminal
        key = _poll_key()
        if key in ("1", "2", "3"):
            idx = int(key) - 1
            if idx < len(cands[:3]):
                cv2.destroyWindow(title)
                return cands[idx]
        elif key in ("m", "M"):
            print("  Drag a box in the window, then ENTER/SPACE to accept (c to cancel).")
            r = cv2.selectROI(title, white, showCrosshair=True, fromCenter=False)
            if r[2] > 0 and r[3] > 0:
                cv2.destroyWindow(title)
                # manual drag is axis-aligned -> store at the detection ref with angle 0
                rect = ((r[0] + r[2] / 2.0, r[1] + r[3] / 2.0), (float(r[2]), float(r[3])), 0.0)
                return screen_roi_from_rect(rect, fw, fh)
        elif key in ("r", "R"):
            cv2.destroyWindow(title)
            return None
        elif key in ("q", "Q", "\x1b"):
            cv2.destroyWindow(title)
            raise KeyboardInterrupt


def _tint_mask(crop: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = crop.copy()
    out[mask > 0] = color
    return cv2.addWeighted(crop, 0.5, out, 0.5, 0.0)


def _confirm(
    red_ref: np.ndarray,
    bright_ref: np.ndarray,
    circle: tuple[float, float, float],
    trial: AutoTriggerConfig,
) -> str:
    """Show the two deskewed panels with arc boxes + tinted RED masks + real-detector labels (red-
    only). RED panel expects both arcs RED; BRIGHT panel expects both clear (+ the fitted circle).
    Returns the pressed action key: 'y' (accept), 'e' (edit), 'r' (redo), 'q' (quit)."""
    title = "Calib confirm (press keys in the TERMINAL)"
    la = roi_from_region(trial.left_arc)
    ra = roi_from_region(trial.right_arc)
    cx, cy, r = circle
    print(
        "\nConfirm calibration -- focus THIS terminal:\n"
        "  y = write config   e = re-tune arc boxes   r = redo   q = quit (no write)"
    )
    _open_window(title, 2 * _REF_W, _REF_H)  # the confirm composite is two deskewed panels side by side
    while True:
        panels = []
        for label, frame, draw_circle in (("RED frame", red_ref, False), ("BRIGHT frame", bright_ref, True)):
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = _band_mask(hsv, trial.red_bands)
            panel = _tint_mask(frame, mask, (0, 0, 255))
            if draw_circle:
                cv2.circle(panel, (int(round(cx)), int(round(cy))), int(round(r)), (255, 0, 255), 1)
            for arc in (la, ra):
                ax, ay, aw, ah = arc.for_frame(_REF_W, _REF_H)
                cv2.rectangle(panel, (ax, ay), (ax + aw, ay + ah), (255, 255, 0), 1)
            state = detect(frame, trial)
            ls = "RED" if state.left_red else "clear"
            rs = "RED" if state.right_red else "clear"
            cv2.putText(
                panel,
                f"{label}: L={ls} R={rs}",
                (8, 24),
                _FONT,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            panels.append(panel)
        gate = detect(red_ref, trial).both_red
        release = detect(bright_ref, trial).both_clear
        composite = np.hstack(panels)
        cv2.putText(
            composite,
            f"gate both RED: {gate} | release both clear: {release}",
            (8, _REF_H - 12),
            _FONT,
            0.6,
            (0, 255, 0) if (gate and release) else (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        imshow_fit(title, composite)
        cv2.waitKey(20)  # render only; action keys come from the terminal
        key = _poll_key()
        if key in ("y", "Y", "e", "E", "r", "R", "q", "Q", "\x1b"):
            cv2.destroyWindow(title)
            return "q" if key in ("q", "Q", "\x1b") else key.lower()


def _retune(red_ref: np.ndarray, trial: AutoTriggerConfig) -> AutoTriggerConfig:
    """Drag selectROI to re-set each arc band on the deskewed RED crop. Returns an updated
    AutoTriggerConfig (geometry override; the sampled red bands + threshold are kept)."""
    win = "Re-tune: drag LEFT arc, then RIGHT arc (ESC to keep current)"
    lr = cv2.selectROI(win, red_ref, showCrosshair=True, fromCenter=False)
    rr = cv2.selectROI(win, red_ref, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(win)
    upd = trial.model_copy(deep=True)
    if lr[2] > 0 and lr[3] > 0:
        upd.left_arc = RoiBox(
            x=int(lr[0]), y=int(lr[1]), w=int(lr[2]), h=int(lr[3]), ref_w=_REF_W, ref_h=_REF_H
        )
    if rr[2] > 0 and rr[3] > 0:
        upd.right_arc = RoiBox(
            x=int(rr[0]), y=int(rr[1]), w=int(rr[2]), h=int(rr[3]), ref_w=_REF_W, ref_h=_REF_H
        )
    return upd


def _load_frames_from_files(paths: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    imgs = []
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            raise SystemExit(f"ERROR: could not read image {p}")
        imgs.append(img)
    return imgs[0], imgs[1], imgs[2]


def _derive_bands_and_threshold(
    red_ref: np.ndarray, bright_ref: np.ndarray, left_arc: RoiBox, right_arc: RoiBox
) -> tuple[list[HsvBand], float, AutoTriggerConfig]:
    """Sample the RED band(s) off a misaligned arc and pick a two-pass coverage threshold."""
    try:
        red_bands = sample_red_band(roi_from_region(left_arc).crop(red_ref))
    except ValueError:
        red_bands = sample_red_band(roi_from_region(right_arc).crop(red_ref))
    trial = AutoTriggerConfig(left_arc=left_arc, right_arc=right_arc, red_bands=red_bands)

    def _cov(ref: np.ndarray, cfg: AutoTriggerConfig) -> float:
        hsv = cv2.cvtColor(roi_from_region(left_arc).crop(ref), cv2.COLOR_BGR2HSV)
        return red_coverage(hsv, cfg)

    threshold = suggest_coverage_threshold(_cov(red_ref, trial), _cov(bright_ref, trial))
    trial = trial.model_copy(update={"coverage_threshold": threshold})
    # second pass: the sampled bands now drive the threshold (coverages recomputed under `trial`)
    threshold = suggest_coverage_threshold(_cov(red_ref, trial), _cov(bright_ref, trial))
    trial = trial.model_copy(update={"coverage_threshold": threshold})
    return red_bands, threshold, trial


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="USB camera index (default: config)")
    ap.add_argument(
        "--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: config)"
    )
    ap.add_argument(
        "--from-files",
        nargs=3,
        metavar=("WHITE", "RED", "BRIGHT"),
        default=None,
        help="skip live capture; run detection on three saved frames (white / red / bright-aligned)",
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    cap = None
    try:
        if args.from_files:
            white, red, bright = _load_frames_from_files(args.from_files)
            if (white.shape[1], white.shape[0]) != (
                cfg.width or white.shape[1],
                cfg.height or white.shape[0],
            ):
                print(
                    f"WARNING: image size {white.shape[1]}x{white.shape[0]} != config "
                    f"{cfg.width}x{cfg.height}; calibrating for a different resolution.",
                    file=sys.stderr,
                )
            screen_roi = None
            while screen_roi is None:
                screen_roi = _pick_roi(white)
                if screen_roi is None:
                    print("Recapture not available with --from-files; pick a candidate or 'm'.")
        else:
            index = args.camera if args.camera is not None else cfg.camera_index
            backend = args.backend if args.backend is not None else cfg.backend
            print(f"Opening USB camera {index} ({backend}) ...")
            cap = open_capture(
                index,
                backend,
                fourcc=cfg.fourcc,
                width=cfg.width,
                height=cfg.height,
                autofocus=cfg.autofocus,
                focus=cfg.focus,
            )
            if not cap.isOpened():
                print(
                    f"ERROR: could not open camera {index}. Try --camera N / --backend dshow.",
                    file=sys.stderr,
                )
                return 1
            screen_roi = None
            while screen_roi is None:
                white = _capture_frame(cap, "Calib 1/3: frame the WHITE startup screen, SPACE", None)
                if white is None:
                    return 1
                screen_roi = _pick_roi(white)
            red = _capture_frame(cap, "Calib 2/3: show RED arcs, SPACE", screen_roi)
            if red is None:
                return 1
            bright = _capture_frame(cap, "Calib 3/3: show the BRIGHT aligned screen, SPACE", screen_roi)
            if bright is None:
                return 1

        red_ref = deskew_crop(red, screen_roi, out=_DETECT)
        bright_ref = deskew_crop(bright, screen_roi, out=_DETECT)
        try:
            cx, cy, r = fit_camera_circle(bright_ref)
        except ValueError as e:
            print(
                f"WARNING: circle fit failed ({e}); falling back to fixed symmetric bands.",
                file=sys.stderr,
            )
            cx, cy, r = _DETECT[0] * 0.5, _DETECT[1] * 0.5, _DETECT[1] * 0.42
        left_arc, right_arc = arc_bands_from_circle(cx, cy, r)
        try:
            _, _, trial = _derive_bands_and_threshold(red_ref, bright_ref, left_arc, right_arc)
        except ValueError as e:
            print(
                f"ERROR: red sampling failed ({e}). Re-run and show clearly RED, misaligned arcs.",
                file=sys.stderr,
            )
            return 1

        while True:
            action = _confirm(red_ref, bright_ref, (cx, cy, r), trial)
            if action == "y":
                write_calibration_values(
                    _CONFIG_PATH,
                    screen_roi=screen_roi,
                    left_arc=trial.left_arc,
                    right_arc=trial.right_arc,
                    red_bands=trial.red_bands,
                    coverage_threshold=trial.coverage_threshold,
                )
                print(f"Wrote calibration to {_CONFIG_PATH} (backup: {_CONFIG_PATH.name}.bak).")
                return 0
            if action == "e":
                trial = _retune(red_ref, trial)
                continue
            if action == "r" and cap is not None:
                return main()  # restart the live flow
            print("Quit without writing." if action in ("q",) else "Redo unavailable in --from-files.")
            return 0
    except KeyboardInterrupt:
        print("\n^C -- nothing written.")
        return 0
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    sys.exit(main())
