"""Interactive view-calibration for the arm-mounted USB observation camera.

Re-derives the Aurora SCREEN ROI, the two alignment-ARC regions, and the RED/GREEN HSV bands,
then writes them into src/arm101_hand/data/system_camera_config.yaml. Run after any RESOLUTION
or LIGHTING change (the fixed-fraction ROI + colour bands drift otherwise).

Flow:
  1. Capture (or --from-files) the WHITE startup screen -> pick 1 of up to 3 ROI candidates
     (keys 1/2/3), or 'm' to drag one manually (cv2.selectROI), or 'r' to recapture.
  2. Capture the RED arcs (ROI box overlaid) -> auto-detect left/right arc regions + red band.
  3. Capture the GREEN arcs -> green band; reuse the arc regions.
  4. CONFIRM screen: ROI crops with the arc boxes + red/green masks tinted, labelled by the REAL
     arc_detector.detect(); 'y' writes, 'e' re-tune (selectROI / trackbars), 'r' redo, 'q' quit.
Clones usb_camera_capture.py's live-window + SPACE loop. Plain opencv-python only (no contrib).

Usage:
  uv run python scripts/calibration/system_camera/calibrate_view.py [--camera N] [--backend auto|dshow]
  uv run python scripts/calibration/system_camera/calibrate_view.py --from-files WHITE RED GREEN
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox  # noqa: E402
from arm101_hand.system_camera import Roi, detect, imshow_fit, open_capture, roi_from_region  # noqa: E402
from arm101_hand.system_camera.arc_detector import _band_mask, classify_band  # noqa: E402
from arm101_hand.system_camera.calibration import (  # noqa: E402
    detect_arc_regions,
    detect_screen_rect,
    sample_hsv_band,
    suggest_coverage_threshold,
    to_roi_candidate,
    write_calibration_values,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_REF_W, _REF_H = 640, 480
_MAX_WIN_W, _MAX_WIN_H = 1100, 800  # initial window cap so large frames fit the screen


def _open_window(title: str, frame_w: int, frame_h: int) -> None:
    """Create a resizable window sized to fit the screen (never upscales). MUST precede imshow_fit:
    on the Win32 backend getWindowImageRect raises a NULL-window error for a never-created window."""
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    scale = min(_MAX_WIN_W / frame_w, _MAX_WIN_H / frame_h, 1.0)
    cv2.resizeWindow(title, max(1, round(frame_w * scale)), max(1, round(frame_h * scale)))


def _crop_to_ref(frame: np.ndarray, roi: Roi) -> np.ndarray:
    """Crop ``frame`` to ``roi`` and resize to the 640x480 detection reference."""
    return cv2.resize(roi.crop(frame), (_REF_W, _REF_H), interpolation=cv2.INTER_AREA)


def _capture_frame(cap, title: str, overlay: Roi | None) -> np.ndarray | None:
    """Stream until SPACE (return the frame) or q/ESC (return None). Draws ``overlay`` if given."""
    opened = False
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                return None
            continue
        if not opened:  # create the window once we know the frame size (imshow_fit needs it to exist)
            _open_window(title, frame.shape[1], frame.shape[0])
            opened = True
        disp = frame.copy()
        if overlay is not None:
            x, y, w, h = overlay.for_frame(frame.shape[1], frame.shape[0])
            cv2.rectangle(disp, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(disp, title, (12, 28), _FONT, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            return frame
        if key in (ord("q"), 27):
            return None


def _pick_roi(white: np.ndarray) -> Roi | None:
    """Show up to 3 ROI candidates; return the chosen Roi, or None to recapture."""
    fh, fw = white.shape[:2]
    rects = detect_screen_rect(white, top_n=3)
    cands = [to_roi_candidate(b, fw, fh) for b in rects]
    # If detection found <3 distinct screens, pad with framing variants of the best (or the whole
    # frame if nothing was detected) so the operator always has options.
    if not cands:
        cands = [(0, 0, fw, fh)]
    while len(cands) < 3:
        bx, by, bw, bh = cands[0]
        m = int(0.06 * len(cands) * bw)
        cands.append(to_roi_candidate((max(0, bx - m), max(0, by - m), bw + 2 * m, bh + 2 * m), fw, fh))
    title = "Calib 1/3: pick ROI  [1/2/3 select | m manual | r recapture | q quit]"
    colors = [(0, 255, 0), (0, 200, 255), (255, 180, 0)]
    _open_window(title, fw, fh)
    while True:
        disp = white.copy()
        for i, (x, y, w, h) in enumerate(cands[:3]):
            cv2.rectangle(disp, (x, y), (x + w, y + h), colors[i], 2)
            cv2.putText(disp, str(i + 1), (x + 6, y + 26), _FONT, 0.9, colors[i], 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("1"), ord("2"), ord("3")):
            idx = key - ord("1")
            if idx < len(cands[:3]):
                x, y, w, h = cands[idx]
                cv2.destroyWindow(title)
                return Roi(x=x, y=y, w=w, h=h, ref_w=fw, ref_h=fh)
        if key == ord("m"):
            r = cv2.selectROI(title, white, showCrosshair=True, fromCenter=False)
            cv2.destroyWindow("ROI selector")
            if r[2] > 0 and r[3] > 0:
                cv2.destroyWindow(title)
                return Roi(x=int(r[0]), y=int(r[1]), w=int(r[2]), h=int(r[3]), ref_w=fw, ref_h=fh)
        if key == ord("r"):
            cv2.destroyWindow(title)
            return None
        if key in (ord("q"), 27):
            cv2.destroyWindow(title)
            raise KeyboardInterrupt


def _tint_mask(crop: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    out = crop.copy()
    out[mask > 0] = color
    return cv2.addWeighted(crop, 0.5, out, 0.5, 0.0)


def _confirm(
    red_ref: np.ndarray,
    green_ref: np.ndarray,
    trial: AutoTriggerConfig,
) -> str:
    """Show the two ROI crops with arc boxes + tinted masks + real-detector labels. Returns the
    pressed action key: 'y' (accept), 'e' (edit), 'r' (redo), 'q' (quit)."""
    title = "Calib confirm  [y write | e re-tune | r redo | q quit]"
    la = roi_from_region(trial.left_arc)
    ra = roi_from_region(trial.right_arc)
    _open_window(title, 2 * _REF_W, _REF_H)  # the confirm composite is two ROI panels side by side
    while True:
        panels = []
        for label, frame, bands, tint in (
            ("RED frame", red_ref, trial.red_bands, (0, 0, 255)),
            ("GREEN frame", green_ref, trial.green_bands, (0, 255, 0)),
        ):
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = _band_mask(hsv, bands)
            panel = _tint_mask(frame, mask, tint)
            for arc in (la, ra):
                x, y, w, h = arc.for_frame(_REF_W, _REF_H)
                cv2.rectangle(panel, (x, y), (x + w, y + h), (255, 255, 0), 1)
            state = detect(frame, trial)
            cv2.putText(
                panel,
                f"{label}: L={state.left} R={state.right} ready={state.ready}",
                (8, 24),
                _FONT,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            panels.append(panel)
        imshow_fit(title, np.hstack(panels))
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("y"), ord("e"), ord("r"), ord("q"), 27):
            cv2.destroyWindow(title)
            return "q" if key == 27 else chr(key)


def _retune(red_ref: np.ndarray, trial: AutoTriggerConfig) -> AutoTriggerConfig:
    """Trackbar HSV tuning for green + red on the red/green crops (adapted from threshold_inRange.py).
    Drag selectROI to re-set an arc box. Returns an updated AutoTriggerConfig. ESC accepts."""
    # Minimal: re-run selectROI for both arcs on the red crop; keep bands. (Full trackbar tuning is
    # a bench enhancement; the auto bands + this geometry override cover the common failure.)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="USB camera index (default: config)")
    ap.add_argument(
        "--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: config)"
    )
    ap.add_argument(
        "--from-files",
        nargs=3,
        metavar=("WHITE", "RED", "GREEN"),
        default=None,
        help="skip live capture; run detection on three saved frames",
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    cap = None
    try:
        if args.from_files:
            white, red, green = _load_frames_from_files(args.from_files)
            if (white.shape[1], white.shape[0]) != (
                cfg.width or white.shape[1],
                cfg.height or white.shape[0],
            ):
                print(
                    f"WARNING: image size {white.shape[1]}x{white.shape[0]} != config "
                    f"{cfg.width}x{cfg.height}; calibrating for a different resolution.",
                    file=sys.stderr,
                )
            roi = None
            while roi is None:
                roi = _pick_roi(white)
                if roi is None:
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
            roi = None
            while roi is None:
                white = _capture_frame(cap, "Calib 1/3: frame the WHITE startup screen, SPACE", None)
                if white is None:
                    return 1
                roi = _pick_roi(white)
            red = _capture_frame(cap, "Calib 2/3: show RED arcs, SPACE", roi)
            if red is None:
                return 1
            green = _capture_frame(cap, "Calib 3/3: show GREEN arcs, SPACE", roi)
            if green is None:
                return 1

        red_ref = _crop_to_ref(red, roi)
        green_ref = _crop_to_ref(green, roi)
        try:
            left_arc, right_arc = detect_arc_regions(red_ref)
            red_bands = sample_hsv_band(_region_crop(red_ref, left_arc), "red") or sample_hsv_band(
                _region_crop(red_ref, right_arc), "red"
            )
            green_bands = sample_hsv_band(_region_crop(green_ref, left_arc), "green") or sample_hsv_band(
                _region_crop(green_ref, right_arc), "green"
            )
        except ValueError as e:
            print(
                f"ERROR: auto-detection failed ({e}). Re-run and frame the arcs inside the ROI.",
                file=sys.stderr,
            )
            return 1

        trial = AutoTriggerConfig(
            left_arc=left_arc, right_arc=right_arc, red_bands=red_bands, green_bands=green_bands
        )
        gcov_g = classify_band(_to_hsv_region(green_ref, left_arc), trial)[1]
        gcov_r = classify_band(_to_hsv_region(red_ref, left_arc), trial)[1]
        threshold = suggest_coverage_threshold(gcov_g, gcov_r)
        trial = trial.model_copy(update={"coverage_threshold": threshold})

        while True:
            action = _confirm(red_ref, green_ref, trial)
            if action == "y":
                screen_roi = RoiBox(x=roi.x, y=roi.y, w=roi.w, h=roi.h, ref_w=roi.ref_w, ref_h=roi.ref_h)
                write_calibration_values(
                    _CONFIG_PATH,
                    screen_roi=screen_roi,
                    left_arc=trial.left_arc,
                    right_arc=trial.right_arc,
                    red_bands=trial.red_bands,
                    green_bands=trial.green_bands,
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


def _region_crop(ref_bgr: np.ndarray, region: RoiBox) -> np.ndarray:
    return roi_from_region(region).crop(ref_bgr)


def _to_hsv_region(ref_bgr: np.ndarray, region: RoiBox) -> np.ndarray:
    return cv2.cvtColor(_region_crop(ref_bgr, region), cv2.COLOR_BGR2HSV)


if __name__ == "__main__":
    sys.exit(main())
