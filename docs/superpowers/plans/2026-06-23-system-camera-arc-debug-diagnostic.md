# System-Camera Arc-Detection Debug Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only diagnostic that stages the arm+hand into `grab`, shows the live deskewed 800×480 Aurora-screen ROI with the two alignment-arc boxes drawn and colored by their live RED/clear verdict, and saves a self-documenting "case" (clean frame + annotated frame + coverage/threshold sidecar) on SPACE — for collecting frames where `arc_detector.detect()` misclassifies.

**Architecture:** One new script `scripts/diagnostics/system_camera/usb_camera_arc_debug.py`. It calls the existing staged-grab harness `run_grab_demo(on_hold=...)`; the hold-hook opens the USB camera and runs a single-threaded `read → crop to deskewed ROI → detect() → annotate → imshow_fit → msvcrt.kbhit poll` loop (the exact pattern of `usb_camera_roi_preview.py`). It reuses the device-layer pure helpers (`open_capture`, `roi_from_region`, `Roi.crop`, `imshow_fit`, `arc_detector.detect`) so the crop+detection path is byte-identical to the demo's. **No Aurora link, no shutter/trigger, no recording, no grading.** A pure `build_arc_case_sidecar` function (unit-tested) assembles the sidecar dict.

**Tech Stack:** Python 3.12, `opencv-python` (full wheel; HighGUI), numpy, pydantic config models, `msvcrt` (Windows terminal key polling). Reuses `arm101_hand.system_camera` and `arm101_hand.scripts.grab_common`.

## Global Constraints

- **Python 3.12.** Code must pass: `ruff format --check .`, `ruff check .` (selects `E,W,F,I,B,C4,N,UP,SIM`; `E501` ignored; line-length 110), `mypy src` (CI does **not** type-check `scripts/` or `tests/`, but write clean annotations anyway), `pytest -m 'not hardware'`.
- **No Aurora / no trigger / no recording / no grading.** No `PictorClient`, no `auto_trigger`, no shutter press, no capture cycle, no `r` record toggle, no DR model.
- **No device-layer changes.** Do **not** modify `preview.py`, `arc_detector.py`, `auto_trigger.py`, `system_camera_config.py`, or any demo. Reuse only.
- **IL-2:** never import from / modify `references/`. **IL-5:** the tool **reads** `system_camera_config.yaml` and **never writes** it; its only writes are case files under `media_outputs/arc_debug/` (already gitignored via `media_outputs/`).
- **No-surprise-movement:** all motion is owned by `run_grab_demo` (defined staged sequence; releases torque in place on exit, reverses only on explicit `h`). The diagnostic adds **no** arm/hand motion.
- **Reuse, don't reinvent:** crop = `roi_from_region(screen_roi)` + `Roi.crop` + resize to `(ref_w, ref_h)`; detection = `arc_detector.detect`; window = `imshow_fit`; camera = `open_capture`.
- **SPACE output:** three files sharing a `arc_<ts>` stem in `--out-dir` (default `media_outputs/arc_debug/`): `_clean.png` (un-annotated ground truth), `_annotated.png`, `.json` (sidecar with `expected_note: ""`).

---

### Task 1: Pure sidecar-builder + unit test

Create the script file containing **only** the module docstring, the builder-required imports, and the pure `build_arc_case_sidecar` function. The interactive loop/`main()` come in Task 2. This keeps Task 1's commit ruff-clean (no unused imports) and gives a real TDD red→green on the pure function.

**Files:**
- Create: `scripts/diagnostics/system_camera/usb_camera_arc_debug.py`
- Test: `tests/unit/test_arc_debug_sidecar.py`

**Interfaces:**
- Consumes: `arm101_hand.config.system_camera_config.AutoTriggerConfig` (fields `coverage_threshold: float`, `morph_kernel: int`, `left_arc: RoiBox`, `right_arc: RoiBox`, `red_bands: list[HsvBand]`), `RoiBox` (fields `x,y,w,h,ref_w,ref_h: int`, `angle: float`; `.model_dump() -> dict`), `arm101_hand.system_camera.AlignmentState` (fields `left_red,right_red: bool`, `left_cov,right_cov: float`; properties `both_red,both_clear: bool`).
- Produces: `build_arc_case_sidecar(alignment, cfg, screen_roi, *, camera_index, backend, frame_w, frame_h, captured_at) -> dict[str, object]` — JSON-serializable; `captured_at: datetime.datetime` is injected for determinism.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_arc_debug_sidecar.py`:

```python
"""Unit test for the arc-debug diagnostic's pure sidecar-builder.

The builder lives in scripts/diagnostics/system_camera/usb_camera_arc_debug.py. scripts/ is not
an importable package here (scripts add src/ to sys.path and import arm101_hand; nothing imports a
script *module*), so we load the function via a path-based import. Safe because the script's heavy
work (run_grab_demo, camera open) is inside main()/guarded by __main__ -- module top-level only
defines functions + constants.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox
from arm101_hand.system_camera.arc_detector import AlignmentState

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "diagnostics"
    / "system_camera"
    / "usb_camera_arc_debug.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("usb_camera_arc_debug", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_arc_case_sidecar


def test_build_arc_case_sidecar_shape():
    build = _load_builder()
    alignment = AlignmentState(left_red=True, right_red=False, left_cov=0.061, right_cov=0.004)
    cfg = AutoTriggerConfig(
        left_arc=RoiBox(x=144, y=130, w=70, h=230, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=586, y=130, w=70, h=230, ref_w=800, ref_h=480),
        coverage_threshold=0.02,
    )
    screen_roi = RoiBox(x=102, y=89, w=202, h=97, ref_w=800, ref_h=480, angle=-2.045)
    ts = dt.datetime(2026, 6, 23, 14, 5, 33, tzinfo=dt.UTC)

    out = build(
        alignment,
        cfg,
        screen_roi,
        camera_index=1,
        backend="dshow",
        frame_w=1600,
        frame_h=1200,
        captured_at=ts,
    )

    assert out["tool"] == "usb_camera_arc_debug"
    assert out["captured_at"] == "2026-06-23T14:05:33+00:00"
    assert out["camera"] == {"index": 1, "backend": "dshow", "frame_w": 1600, "frame_h": 1200}

    det = out["detection"]
    assert det["left_red"] is True and det["right_red"] is False
    assert det["left_cov"] == 0.061 and det["right_cov"] == 0.004
    assert det["both_red"] is False and det["both_clear"] is False
    assert det["coverage_threshold"] == 0.02

    assert out["screen_roi"] == screen_roi.model_dump()
    assert out["left_arc"] == cfg.left_arc.model_dump()
    assert out["right_arc"] == cfg.right_arc.model_dump()
    assert out["red_bands"] == [b.model_dump() for b in cfg.red_bands]
    assert out["morph_kernel"] == cfg.morph_kernel
    assert out["expected_note"] == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_arc_debug_sidecar.py -v`
Expected: FAIL — `usb_camera_arc_debug.py` does not exist yet, so `spec_from_file_location` returns `None` and the `assert spec is not None` trips (or `FileNotFoundError` on exec).

- [ ] **Step 3: Create the script with the docstring, imports, and the builder**

Create `scripts/diagnostics/system_camera/usb_camera_arc_debug.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_arc_debug_sidecar.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Lint/format the new files**

Run: `uv run ruff format scripts/diagnostics/system_camera/usb_camera_arc_debug.py tests/unit/test_arc_debug_sidecar.py && uv run ruff check scripts/diagnostics/system_camera/usb_camera_arc_debug.py tests/unit/test_arc_debug_sidecar.py`
Expected: no changes needed / "All checks passed!". (If format rewrites anything, re-run the test — still PASS.)

- [ ] **Step 6: Commit**

```bash
git add scripts/diagnostics/system_camera/usb_camera_arc_debug.py tests/unit/test_arc_debug_sidecar.py
git commit -m "feat(system_camera): arc-debug sidecar-builder + unit test"
```

---

### Task 2: Interactive grab + live-arc-overlay loop

Extend the script with the camera/key helpers, the annotate function, the grab hold-hook loop, and `main()`. No new unit test (the window + grab + camera path is bench-only, like the other `system_camera` diagnostics); verify with ruff + an offline `--help` smoke (argparse exits before any hardware is touched).

**Files:**
- Modify: `scripts/diagnostics/system_camera/usb_camera_arc_debug.py` (add imports, helpers, `main()`)

**Interfaces:**
- Consumes: `arm101_hand.config.load_system_camera_config(path) -> SystemCameraConfig` (fields `camera_index:int`, `backend:str`, `fourcc:str`, `width/height:int|None`, `autofocus:bool`, `focus:int|None`, `screen_roi:RoiBox`, `auto_trigger:AutoTriggerConfig`); `arm101_hand.scripts.grab_common.run_grab_demo(on_hold) -> int` and `GrabHoldContext`; `arm101_hand.system_camera.{detect, imshow_fit, open_capture, resolution_mismatch_warning, roi_from_region}`; `roi_from_region(region).for_frame(w,h) -> (x,y,w,h)` and `.crop(frame)`; `roi.ref_w/ref_h`.
- Produces: `main() -> int` (the console entry); `build_arc_case_sidecar` from Task 1 is called inside the loop.

- [ ] **Step 1: Expand the import block**

Replace the Task 1 import block (everything from `import datetime as _dt` down to the two first-party `# noqa: E402` imports) with this fuller block (keep the module docstring above it unchanged):

```python
import argparse
import datetime as _dt
import json
import msvcrt
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox  # noqa: E402
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    AlignmentState,
    detect,
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
    roi_from_region,
)

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_OUT_DIR = _REPO_ROOT / "media_outputs" / "arc_debug"
_WINDOW = "Arc detect debug (Optomed view)"
_RED = (0, 0, 255)
_GREEN = (0, 255, 0)
_WHITE = (255, 255, 255)
_OPEN_GRACE_S = 5.0  # bail if the camera opens but delivers no frame within this window
_STALL_S = 3.0  # bail if a streaming camera then stops delivering frames for this long
```

- [ ] **Step 2: Add the key-poll, annotate, and save helpers**

Add these functions immediately **after** `build_arc_case_sidecar` (so `build_arc_case_sidecar` keeps its Task-1 position and the test still loads it):

```python
def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt (``""`` if none waiting).

    Single-threaded design: we can't block on ``getwch`` -- that would freeze the cv2 window, which
    needs ``waitKey`` pumped every loop. Raises ``KeyboardInterrupt`` on Ctrl+C; swallows arrow /
    function-key prefixes.
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


def _annotate(clean: np.ndarray, cfg: AutoTriggerConfig, alignment: AlignmentState) -> np.ndarray:
    """Return a copy of the ROI frame with both arc boxes (RED verdict -> red, clear -> green) and a
    raw-classification HUD drawn. This annotated frame is shown live AND saved beside the clean one;
    it carries no UI chrome (counter/help live in the terminal) so each saved case stays clean."""
    disp = clean.copy()
    h, w = disp.shape[:2]
    for region, is_red in ((cfg.left_arc, alignment.left_red), (cfg.right_arc, alignment.right_red)):
        x, y, bw, bh = roi_from_region(region).for_frame(w, h)
        cv2.rectangle(disp, (x, y), (x + bw, y + bh), _RED if is_red else _GREEN, 2)
    t = cfg.coverage_threshold
    lcmp = ">=" if alignment.left_red else "<"
    rcmp = ">=" if alignment.right_red else "<"
    hud = (
        f"L:{'RED' if alignment.left_red else 'clr'} {alignment.left_cov:.3f}{lcmp}{t:.3f}   "
        f"R:{'RED' if alignment.right_red else 'clr'} {alignment.right_cov:.3f}{rcmp}{t:.3f}   "
        f"both_red={alignment.both_red}"
    )
    cv2.putText(disp, hud, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, _WHITE, 1, cv2.LINE_AA)
    return disp


def _save_case(
    out_dir: Path,
    clean: np.ndarray,
    annotated: np.ndarray,
    sidecar: dict[str, object],
    captured_at: _dt.datetime,
) -> bool:
    """Write the three case files (clean PNG + annotated PNG + JSON sidecar) sharing one ``arc_<ts>``
    stem derived from ``captured_at`` (so the filenames match the sidecar's ``captured_at``). Returns
    True only if all three wrote."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = captured_at.strftime("%Y%m%d_%H%M%S_") + f"{captured_at.microsecond // 1000:03d}"
    clean_path = out_dir / f"arc_{stem}_clean.png"
    annot_path = out_dir / f"arc_{stem}_annotated.png"
    json_path = out_dir / f"arc_{stem}.json"
    ok_clean = cv2.imwrite(str(clean_path), clean)
    ok_annot = cv2.imwrite(str(annot_path), annotated)
    json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    if ok_clean and ok_annot:
        print(f"  saved case arc_{stem}: {clean_path.name}, {annot_path.name}, {json_path.name}")
        return True
    print(f"  WARNING: case arc_{stem} -- image write failed (clean={ok_clean}, annot={ok_annot})")
    return False
```

- [ ] **Step 3: Add `main()` with the grab hold-hook loop**

Append at the end of the file:

```python
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--camera",
        type=int,
        default=None,
        help="USB camera index (default: camera_index from system_camera_config.yaml)",
    )
    ap.add_argument(
        "--backend",
        choices=("auto", "dshow"),
        default=None,
        help="cv2 capture backend (default: backend from system_camera_config.yaml)",
    )
    ap.add_argument(
        "--out-dir",
        default=str(_OUT_DIR),
        help="folder for SPACE-saved cases (default: <repo>/media_outputs/arc_debug)",
    )
    args = ap.parse_args()

    scfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else scfg.camera_index
    backend = args.backend if args.backend is not None else scfg.backend
    out_dir = Path(args.out_dir)
    roi = roi_from_region(scfg.screen_roi)
    acfg = scfg.auto_trigger

    def _arc_debug_loop(_ctx: GrabHoldContext) -> None:
        # _ctx is unused: the arm + hand simply hold grab under torque; this hook coordinates no
        # motion. It only opens the USB camera and runs the detection-overlay loop.
        print(f"Opening USB camera index {camera_index} ({backend}) ...")
        cap = open_capture(
            camera_index,
            backend,
            fourcc=scfg.fourcc,
            width=scfg.width,
            height=scfg.height,
            autofocus=scfg.autofocus,
            focus=scfg.focus,
        )
        if not cap.isOpened():
            cap.release()
            print(
                f"ERROR: could not open camera index {camera_index}. Try another --camera N or "
                "--backend dshow. (Arm/hand will still release at the exit prompt.)",
                file=sys.stderr,
            )
            return

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        warn = resolution_mismatch_warning(scfg.width, scfg.height, width, height)
        if warn:
            print(warn, file=sys.stderr)

        cv2.namedWindow(_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(_WINDOW, roi.ref_w, roi.ref_h)
        print(
            f"Live arc overlay: {width}x{height} stream, cropped to a {roi.w}x{roi.h} deskewed ROI "
            f"(angle {roi.angle:+.2f} deg). Boxes: RED verdict=red, clear=green. coverage_threshold="
            f"{acfg.coverage_threshold:.3f}.\n"
            "Keys (focus THIS terminal): SPACE = save case, q/ESC = quit."
        )

        started = time.monotonic()
        last_good: float | None = None
        saved = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    now = time.monotonic()
                    if last_good is None and now - started > _OPEN_GRACE_S:
                        print(
                            f"\nERROR: camera index {camera_index} opened but delivered no frames in "
                            f"{_OPEN_GRACE_S:.0f}s. Likely in use by another app or invalidated by a "
                            "previous run -- close other camera users / replug / try --backend dshow.",
                            file=sys.stderr,
                        )
                        break
                    if last_good is not None and now - last_good > _STALL_S:
                        print(
                            f"\nERROR: camera index {camera_index} stopped delivering frames "
                            f"({_STALL_S:.0f}s stall) -- it may have been unplugged or grabbed by "
                            "another app.",
                            file=sys.stderr,
                        )
                        break
                    cv2.waitKey(30)  # transient hiccup -- keep pumping, still honor q/ESC
                    if _poll_key() in ("q", "Q", "\x1b"):
                        break
                    continue

                last_good = time.monotonic()
                # Crop (+ deskew) to the screen ROI and upscale to the reference size -- identical to
                # WebcamPreview, so detect() sees exactly what the demo feeds it. This is the saveable
                # ground truth.
                clean = cv2.resize(
                    roi.crop(frame), (roi.ref_w, roi.ref_h), interpolation=cv2.INTER_LINEAR
                )
                alignment = detect(clean, acfg)
                annotated = _annotate(clean, acfg, alignment)
                imshow_fit(_WINDOW, annotated)
                cv2.waitKey(1)  # pump GUI events; required for the window to stay responsive

                key = _poll_key()
                if key in ("q", "Q", "\x1b"):  # q / ESC
                    break
                if key == " ":  # SPACE -> save one case
                    captured_at = _dt.datetime.now(_dt.UTC)
                    sidecar = build_arc_case_sidecar(
                        alignment,
                        acfg,
                        scfg.screen_roi,
                        camera_index=camera_index,
                        backend=backend,
                        frame_w=width,
                        frame_h=height,
                        captured_at=captured_at,
                    )
                    if _save_case(out_dir, clean, annotated, sidecar, captured_at):
                        saved += 1
        except KeyboardInterrupt:
            print("\n^C")
        finally:
            cap.release()
            cv2.destroyAllWindows()
            print(f"Camera closed. {saved} case(s) saved to {out_dir}.")

    return run_grab_demo(_arc_debug_loop)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify the Task-1 unit test still passes (module still imports + builder unchanged)**

Run: `uv run pytest tests/unit/test_arc_debug_sidecar.py -v`
Expected: PASS — the test exec-imports the now-fuller module (it pulls in `grab_common`/`cv2`, same as the existing `test_device_setup` import chain) and still finds `build_arc_case_sidecar`.

- [ ] **Step 5: Offline `--help` smoke (no hardware touched)**

Run: `uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py --help`
Expected: argparse prints the usage/description and exits 0 **before** `run_grab_demo` is called (no arm/hand/camera access). Confirms imports resolve and the script is runnable.

- [ ] **Step 6: Lint/format**

Run: `uv run ruff format scripts/diagnostics/system_camera/usb_camera_arc_debug.py && uv run ruff check scripts/diagnostics/system_camera/usb_camera_arc_debug.py`
Expected: no changes / "All checks passed!". (Watch for `I` import-order and `F401` unused — the import block above is already ordered and every name is used: `np` in `_annotate`/`_save_case` hints, `GrabHoldContext` in the hook annotation, `RoiBox`/`AutoTriggerConfig`/`AlignmentState` in `build_arc_case_sidecar`/`_annotate`.)

- [ ] **Step 7: Commit**

```bash
git add scripts/diagnostics/system_camera/usb_camera_arc_debug.py
git commit -m "feat(system_camera): arc-debug grab + live-arc-overlay loop"
```

---

### Task 3: Documentation pointers (CLAUDE.md + README.md)

Add the new diagnostic to both docs' diagnostics lists and tree summaries (IL-7 single-source-of-truth: one canonical home, pointers elsewhere). It is a diagnostic that **moves the arm+hand** (unlike the others, which say "no motors") but makes **no Aurora** link — note that distinction.

**Files:**
- Modify: `CLAUDE.md` (tree comment line; §4 diagnostics command list; §7 CV note)
- Modify: `README.md` (diagnostics command list; tree summary line)

- [ ] **Step 1: CLAUDE.md — add the command to the §4 diagnostics list**

After the `usb_camera_focus_probe.py` line (currently line ~99), add:

```
uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py [--camera N]   # arc-detection debug: stages the arm+hand grab, draws both arc boxes (red=RED/green=clear) + a coverage HUD on the deskewed ROI; SPACE saves a clean+annotated frame + a coverage/threshold sidecar to media_outputs/arc_debug/ (motors; no Aurora, no trigger)
```

- [ ] **Step 2: CLAUDE.md — extend the tree comment (line ~50)**

In the `diagnostics/` tree-comment line, change the `system_camera/` parenthetical ending:

Find: `usb_camera_roi_preview ROI check / usb_camera_focus_probe manual-focus sweep)`
Replace with: `usb_camera_roi_preview ROI check / usb_camera_focus_probe manual-focus sweep / usb_camera_arc_debug arc-detection case collector)`

- [ ] **Step 3: CLAUDE.md — add a pointer in the §7 CV note**

At the end of the arc-auto-trigger description in the "System-camera preview + DR-grading inference" bullet (the sentence ending `... supersedes the 2026-06-19 + 2026-06-22 arc geometry/logic).`), append:

```
 To collect frames where the RED/clear classification misbehaves (for retuning `coverage_threshold` / `red_bands` / arc geometry), `scripts/diagnostics/system_camera/usb_camera_arc_debug.py` stages the grab, draws the arc boxes on the live deskewed ROI, and SPACE-saves a clean+annotated frame + a numbers sidecar to `media_outputs/arc_debug/` (no Aurora, no trigger; design/plan in `docs/superpowers/{specs,plans}/2026-06-23-system-camera-arc-debug-diagnostic*`).
```

- [ ] **Step 4: README.md — add the command to the diagnostics list**

After the `usb_camera_focus_probe.py` line (currently line ~108), add:

```
uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py  # arc-detection debug: stages the arm+hand grab, draws the arc boxes on the deskewed ROI; SPACE saves failing RED/clear cases to media_outputs/arc_debug/ (motors; no Aurora)
```

- [ ] **Step 5: README.md — extend the tree summary (line ~156)**

Find: `system_camera/ (usb_camera_probe / usb_camera_capture / usb_camera_roi_preview / usb_camera_focus_probe)`
Replace with: `system_camera/ (usb_camera_probe / usb_camera_capture / usb_camera_roi_preview / usb_camera_focus_probe / usb_camera_arc_debug)`

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(system_camera): document usb_camera_arc_debug diagnostic"
```

---

### Task 4: Full verification sweep

Confirm the whole repo is green before handing off for bench testing. No code changes unless a check fails (then fix + re-run).

- [ ] **Step 1: Format check (CI runs `--check`)**

Run: `uv run ruff format --check .`
Expected: "N files already formatted" — no diffs.

- [ ] **Step 2: Lint**

Run: `uv run ruff check .`
Expected: "All checks passed!".

- [ ] **Step 3: Type-check src (scripts/tests are not in scope, but src must stay clean)**

Run: `uv run mypy src`
Expected: "Success: no issues found".

- [ ] **Step 4: Host unit tests**

Run: `uv run pytest -m 'not hardware'`
Expected: all pass (the new `test_arc_debug_sidecar.py` included; same green count as before + 1).

- [ ] **Step 5: Confirm IL-5 / gitignore (no stray writes committed)**

Run: `git status --short`
Expected: clean working tree for tracked files (any `media_outputs/arc_debug/` output from a manual run is git-ignored and must NOT appear). Confirm `system_camera_config.yaml` was never modified by the tool.

**Bench verification (manual, not part of CI — perform on hardware):**
- `uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py` → arm+hand stage into grab; the window shows the deskewed ROI with the two arc boxes; boxes turn red on a misaligned (red-arc) Aurora screen and green when clear; the HUD coverages track the screen.
- SPACE writes exactly three files (`_clean.png`, `_annotated.png`, `.json`) under `media_outputs/arc_debug/`; the sidecar's `detection` numbers match the HUD; `expected_note` is empty.
- `q` then Enter at the prompt releases torque in place; `h` reverses the grab. Ctrl+C also exits cleanly with torque released.

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- §3 architecture (single-threaded loop, reuse helpers, local builder, importlib test) → Tasks 1–2.
- §4 behavior/data-flow (grab → hold-hook → read/crop/detect/annotate/imshow/poll → exit prompt) → Task 2 Step 3.
- §5 SPACE output (three files, stem, sidecar schema, empty `expected_note`, verbatim config copy) → Task 1 (`build_arc_case_sidecar`) + Task 2 (`_save_case`).
- §6 CLI/keys/failure-handling/no-surprise-movement → Task 2 (`main`, grace/stall guards, `_poll_key`, `run_grab_demo` exit).
- §7 testing (unit test of builder via importlib; bench manual) → Task 1 + Task 4.
- §8 Iron-Laws (IL-2/4/5/7) → Global Constraints + Task 3 + Task 4 Step 5.
- §9 files touched (script, test, CLAUDE.md, README.md) → Tasks 1–3.

**2. Placeholder scan:** none — every code/command/expected-output step is concrete.

**3. Type consistency:** `build_arc_case_sidecar(alignment, cfg, screen_roi, *, camera_index, backend, frame_w, frame_h, captured_at) -> dict[str, object]` is identical across Task 1 (def + test) and Task 2 (call site). `AlignmentState(left_red, right_red, left_cov, right_cov)` matches `arc_detector.py`. `RoiBox.model_dump()` / `.for_frame(w,h)` / `roi_from_region()` / `detect(frame, cfg)` / `run_grab_demo(on_hold) -> int` match the device layer. `_annotate`/`_save_case`/`_poll_key` signatures are consistent between definition (Task 2 Step 2) and use (Task 2 Step 3).
