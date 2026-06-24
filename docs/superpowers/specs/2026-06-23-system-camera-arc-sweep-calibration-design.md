# System-camera arc calibration — grab-staged manual arcs + red-detection auto-sweep + test loop

**Date:** 2026-06-23
**Status:** Design approved; pending implementation plan.
**Builds on:** `docs/superpowers/specs/2026-06-23-system-camera-calibrate-view-manual-roi-design.md` (Calib 1/3 drag + arrow-rotate ROI, already implemented on `feat/calibrate-view-manual-roi`).
**Touches:** `scripts/calibration/system_camera/calibrate_view.py`, `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py` (+ doc touch-ups).

## Problem

`calibrate_view.py` now places the **screen ROI** by manual drag + rotate (the prior spec). But the two **alignment-arc bands** are still auto-derived by fitting the camera circle (`fit_camera_circle` → `arc_bands_from_circle`), and the **red HSV band + coverage threshold** are derived from a single red frame + a single bright frame (`sample_red_band` + `suggest_coverage_threshold` midpoint). In practice:

1. The circle-fit arc geometry is fragile and the operator prefers placing the two arc boxes by hand.
2. A single red/bright pair is a thin basis for the red/clear detection; there is no way to *verify* the tuned detector against more frames, nor to feed observed misclassifications back into the tuning.
3. The calibrator opens the camera with no robot motion, so the operator must separately stage the arm to point the camera at the Aurora screen.

## Goal

Rework `calibrate_view.py` into a guided, grab-staged calibration that places the arcs manually and tunes the red/clear detection by an auto-sweep that is verified and refined against operator-labelled frames:

- Stage the robot **grab sequence** first (reuse the demos' `run_grab_demo`) so the arm-mounted USB camera is brought to view the Aurora screen; calibrate inside the hold.
- Keep Calib 1/3 (screen ROI drag + arrow-rotate) unchanged.
- Place the **two arc boxes by manual drag** (left, then right with the left box shown).
- Capture a **RED** reference panel and a **CLEAR/aligned** reference panel.
- **Auto-sweep** the red HSV band + coverage threshold so both arcs read RED on the red panel and CLEAR on the aligned panel, picking the threshold by a principled 1-D separator.
- **Test loop** (arc-debug style): the operator drives the device through states and, on any frame, tags the ground truth (`1` both-red / `2` both-clear) plus an optional note; each becomes a labelled case.
- **Re-sweep** over all labelled cases until the operator is satisfied; report any case the sweep cannot reconcile (best-effort, never silently drop).
- Write `screen_roi + left_arc + right_arc + red_bands + coverage_threshold` to `system_camera_config.yaml`.

### Non-goals

- No change to runtime detection: `arc_detector.detect`, `red_coverage`, `_band_mask`, `auto_trigger`, and the demos stay exactly as-is (the calibrator tunes the *same* pipeline so calibration and runtime stay in lockstep).
- Red-only classification is retained (an arc is RED or CLEAR; no positive green band — the bright/aligned screen reads greenish/unreliable; see `arc_detector.py` docstring + the 2026-06-23 deskew-circle-redgate spec).
- No `--from-files` offline mode (the flow needs the live grab + a live test loop).
- No live HSV trackbar tuner and no white-balance/illumination normalization (deferred — see "OpenCV research" below).
- No per-arc ground-truth labelling: the two on-screen arcs are always the same colour physically, so the operator tags one state per frame (`red`/`clear`); per-arc *detector* disagreement is what the auto-generated comment surfaces.

## Approach (chosen: A — interactive shell in `calibrate_view.py`, pure tuning logic in `calibration.py`)

The interactive flow (grab hook, drags, captures, test loop, confirm) lives in `calibrate_view.py` and reuses existing pieces (`_drag_box`, `_deskew_preview`, `arc_detector.detect`, `imshow_fit`, `run_grab_demo`). The two genuinely new *algorithms* are pure, unit-tested functions in `calibration.py`. The circle-fit arc derivation is deleted.

Rejected: (B) extracting the whole session into a new module — the stages are all cv2+msvcrt GUI, not unit-testable, so the split adds indirection over one linear script; (C) keeping semi-automatic arc geometry — the operator explicitly wants manual arc placement.

## OpenCV research leveraged (read-only; reimplemented in `src/`, never imported — IL-2)

Findings from the vendored `references/computer-vision/{opencv,opencv_contrib,opencv-python}` trees:

- **Principled threshold selection.** OpenCV's `THRESH_OTSU`/`THRESH_TRIANGLE` (`opencv/modules/imgproc/include/opencv2/imgproc.hpp:3116`; tutorial `opencv/doc/py_tutorials/py_imgproc/py_thresholding/py_thresholding.markdown:113-233`) solve exactly the two-cluster separation our coverage threshold faces. We reimplement the 1-D two-class criterion directly in numpy (`pick_threshold`) rather than abusing `cv2.threshold` on a quantized histogram: max-margin midpoint when the clusters separate, min-misclassification point when they overlap. This *is* the "report + best-effort" behavior, made principled.
- **HSV `inRange` + two-band red wrap + `MORPH_OPEN`** (`opencv/doc/tutorials/imgproc/threshold_inRange/threshold_inRange.markdown`; `py_colorspaces.markdown`; `py_morphological_ops.markdown:64-73`): confirmed canonical and identical to the existing `arc_detector` pipeline. The sweep varies the S/V floors (the discriminating knobs) with hue anchored from `sample_red_band`; coverage uses the unchanged `red_coverage`.
- **`cv2.selectROIs` (multi-ROI) — rejected.** It exists (`opencv/modules/highgui/src/roiSelector.cpp:88-107,216`) and the multi-select loops single `selectROI` until ESC, but its confirm keys are read through the cv2 window's own `waitKey`, which a console-launched window never gets focus for on Windows (the documented reason `_drag_box` was written; `selectROI` hangs GIL-held, un-interruptible). We keep `_drag_box` and extend it for the two arcs. Documented so nobody "simplifies" back to `selectROIs`.
- **Deferred:** the live trackbar HSV tuner (`opencv/samples/python/tutorial_code/imgProc/threshold_inRange/threshold_inRange.py`) is redundant given the auto-sweep; gray-world white balance (`opencv_contrib/.../xphoto/white_balance.hpp`) is contrib-only (unavailable in our plain `opencv-python 4.13`) and a V-channel normalization would have to be added to the runtime `arc_detector` too or calibration/runtime would desync. Re-running calibration is the lighting-robustness mechanism. Both noted as future options, not built.

Environment confirmation: the project pins full `opencv-python>=4.9` (resolved 4.13.0.92; headless dropped via the `[tool.uv] override-dependencies` marker), so `namedWindow`/`imshow`/`setMouseCallback`/`getWindowImageRect` are present (we already rely on these).

## Architecture

### `calibration.py` — pure logic (unit-tested)

New:

```python
@dataclass(frozen=True)
class ArcCase:
    """One labelled deskewed-ROI frame. `expected` applies to BOTH arcs (they are
    physically the same colour); per-arc detector disagreement is a detection error."""
    frame: np.ndarray          # deskewed ROI, ref_w x ref_h, BGR
    expected: str              # 'red' | 'clear'

@dataclass(frozen=True)
class SweepResult:
    red_bands: list[HsvBand]
    coverage_threshold: float
    separable: bool                       # True iff every case classifies correctly
    unsatisfied: list[int]                # indices of cases still misclassified (best-effort)
    case_detections: list[tuple[bool, bool]]  # (det_left, det_right) per case under the chosen config

def describe_case(expected: str, det_left: bool, det_right: bool) -> str:
    """Human sentence comparing ground truth to detector output, per arc, collapsing to
    'both arcs ...' when the two arcs share a phrase. Reproduces the operator's phrasings:
      (red,   red,   red)   -> 'both arcs correctly detected as red'
      (red,   F,     F)     -> 'both arcs incorrectly detected as not red'
      (red,   red,   F)     -> 'left arc correctly detected as red; right arc incorrectly detected as not red'
      (clear, F,     F)     -> 'both arcs correctly detected as not red'
      (clear, red,   red)   -> 'both arcs incorrectly detected as red'
      ... (8 expected x det_left x det_right combinations)
    det_* True == detector said RED."""

def pick_threshold(red_covs: list[float], clear_covs: list[float], *, floor: float = 0.02) -> tuple[float, bool]:
    """Choose a coverage threshold separating red-case coverages (should be >= t) from
    clear-case coverages (should be < t). If max(clear) < min(red): max-margin midpoint,
    separable=True. Else: the threshold (a midpoint between adjacent pooled values) that
    minimises misclassified coverages, separable=False. Floored to `floor`. Handles empty
    inputs (returns floor)."""

def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    s_floors: Sequence[int] = range(20, 130, 10),
    v_floors: Sequence[int] = range(30, 150, 10),
) -> SweepResult:
    """Tune the red HSV band + coverage threshold against all labelled cases.
    1. Hue anchor: sample_red_band() on the arc crops of the first expected=='red' case ->
       fixed hue bounds (+ the 0/180 wrap -> up to two bands).
    2. Grid over (s_floor, v_floor): build candidate red_bands (anchored hue + these floors),
       compute per-arc red_coverage (same fn as runtime, morph_kernel) for every case, pool
       red-case vs clear-case coverages, threshold = pick_threshold(...).
    3. Score each candidate by (#cases with BOTH arcs classified to expected, then margin);
       return the best as SweepResult with red_bands + threshold + unsatisfied indices +
       per-case detections (for the report)."""
```

Deleted (now dead): `fit_camera_circle`, `arc_bands_from_circle`, `suggest_coverage_threshold` (subsumed by `pick_threshold`). Kept: `sample_red_band` (hue anchor), `_RED_PRIOR`, `_prior_mask`, `screen_roi_from_rect`, `deskew_crop`, the ruamel writer, `_Rect`. Module docstring trimmed of circle-fit text.

### `calibrate_view.py` — interactive shell

`main()` resolves CLI args and config, then calls `run_grab_demo(on_hold=_calibrate_hook)`. `_calibrate_hook(ctx)` opens the USB camera (like `usb_camera_arc_debug.py`) and drives the stages, then closes the camera; `run_grab_demo` handles the arm/hand connect + the `Enter`/`h` release. Helpers reused: `_poll_key` (terminal keys, incl. arrows), `_drag_box`, `_deskew_preview`, `_open_window`, `_capture_frame`, `_confirm` (adapted), `imshow_fit`. `_drag_box` gains an optional `keep_box` parameter to draw an already-confirmed box as a static overlay (for the right-arc drag).

## Flow

```
Stage 0  REQUIRED  run_grab_demo(on_hold=_calibrate_hook)
                   - connect arm+hand, run the 4-stage position-polled grab so the arm-mounted USB
                     camera views the Aurora screen; calibrate inside the hold.
                   - exit: Enter = release in place, h = staged reverse home (no surprise motion).
  inside the hook (camera opened here):
  1  Calib 1/3 screen ROI   capture WHITE -> drag box -> <-/-> rotate -> ENTER     [unchanged, full frame]
  2  switch display to the deskewed ROI window (screen_roi crop, resized to ref)
  3  arc boxes (manual)      3a drag LEFT box -> SPACE/ENTER confirm
                             3b LEFT box stays drawn; drag RIGHT box -> SPACE/ENTER confirm
  4  reference panels        capture RED panel (SPACE) then CLEAR/aligned panel (SPACE)
                             -> two ArcCase{frame, expected}
  5  auto-sweep              sweep_red_detection(cases, left_arc, right_arc) -> red_bands + threshold
                             print the report (separable? which cases wrong?)
  6  test loop (arc-debug)   live ROI + arc boxes + RED/CLEAR HUD using the swept config;
                             SPACE freezes -> tag 1=both-red / 2=both-clear + optional note ->
                             append ArcCase; save clean+annotated+sidecar to media_outputs/<dir>/
                             (audit trail; reuses the arc-debug sidecar shape). d = done.
  7  re-sweep                re-run sweep over ALL cases (panels + test cases); print report
                             (resolved vs still-wrong with describe_case sentences); loop 6-7 until satisfied
  8  confirm + write         confirm panel (RED + CLEAR panels labelled by the swept detector);
                             y -> write_calibration_values(screen_roi, left_arc, right_arc, red_bands, threshold)
```

### Data model

Every case — both reference panels and every test-loop case — is one `ArcCase{deskewed_frame, expected ∈ {red, clear}}`. The sweep consumes the whole list; the reference panels are simply the first two cases and seed the hue anchor (first `red` case). The re-sweep is the same call over a longer list.

### Test-loop case capture + comment

On `SPACE` the current ROI frame freezes; the operator presses `1` (both arcs red on screen) or `2` (both arcs clear on screen), then an optional Enter-to-skip note. The tool runs `detect()` on the frozen frame, calls `describe_case(expected, det_left, det_right)` to produce the sentence, appends the `ArcCase`, and saves `clean.png` + `annotated.png` + a JSON sidecar (frame ground truth, detector output, the generated sentence, the note, the arc boxes, the red_bands, the threshold) to a `media_outputs/` subdirectory for the record.

## Error handling

- Grab staging failures (device connect, port busy) surface through `run_grab_demo` exactly as the demos handle them; the calibrator adds nothing new. Camera-open failure inside the hook prints guidance and returns (the grab still releases cleanly via `run_grab_demo`'s exit path).
- A cancelled drag (`c`/`q` in `_drag_box`) at stage 3 re-prompts that arc; `q` at a capture/test prompt aborts the calibration without writing (the grab still releases via Stage 0's exit).
- `sweep_red_detection` never raises on unsatisfiable cases: it returns the best-effort config with `separable=False` and the `unsatisfied` indices; the flow prints which cases (with `describe_case` sentences) are still wrong and lets the operator re-capture/re-tag/continue/discard.
- `sample_red_band` raising (no red pixels in the red panel's arc crops) is caught: the flow tells the operator to re-show clearly-red, misaligned arcs and re-capture the red panel.

## Testing

Host unit tests (no bus, CI-safe — `calibration.py` imports no `msvcrt`):

- `describe_case`: all 8 `(expected, det_left, det_right)` combinations assert the exact sentence (covers the operator's six phrasings + the two correct-both cases).
- `pick_threshold`: separable inputs → midpoint + `separable=True`; overlapping inputs → a threshold minimising misclassification + `separable=False`; empty inputs → `floor`.
- `sweep_red_detection`: synthesize deskewed-ROI frames whose left/right arc regions carry a known red hue at given S/V (red panel) vs a greenish/low-red fill (clear panel); assert the sweep returns a band+threshold that classifies both panels correctly with `separable=True` and `unsatisfied==[]`. A deliberately contradictory case set asserts `separable=False`, non-empty `unsatisfied`, and that a result is still returned (best-effort).
- Delete the tests whose targets are removed: `test_fit_camera_circle_centres_on_disc`, `test_fit_camera_circle_handles_nonuniform_disc`, `test_arc_bands_from_circle_are_symmetric`, `test_arc_bands_mirror_about_circle_centre`, and `test_suggest_coverage_threshold_midpoint_and_floor`. The deskew-sign guard (`test_deskew_crop_with_stored_angle_uprights_a_tilted_rect`), `test_screen_roi_from_rect_*`, `test_sample_red_band_*`, and `test_write_calibration_*` stay.

The cv2/msvcrt interactive shell (grab hook, drags, capture, test loop, confirm) is exercised by the existing bench procedure, not unit tests. Gates: `ruff format`, `ruff check .`, `mypy src`, `pytest -m 'not hardware'`.

### Bench verification (operator, post-implementation)

Run `calibrate_view.py`: confirm the grab stages and the camera views the Aurora screen; complete Calib 1/3; drag both arcs (right drag shows the left box); capture RED + CLEAR panels; confirm the sweep report; run the test loop tagging a few aligned/misaligned frames; confirm re-sweep resolves them; write; then verify in `usb_camera_arc_debug.py` / `grab_auto_trigger_analysis.py` that the new arcs + red band classify correctly live.

## Iron Laws

- **IL-2:** no `references/` edits; OpenCV patterns reimplemented in `src/`, not imported.
- **IL-3 / IL-4:** the grab uses `run_grab_demo`, the single owner of both buses; arm IDs 1–5, hand 1–8; no new bus access.
- **IL-5:** `system_camera_config.yaml` written only by the validated `write_calibration_values` (+ `.bak`); calibration JSON untouched; saved test-case frames/sidecars go under git-ignored `media_outputs/`.
- **"No surprise movements":** Stage 0 reuses `run_grab_demo` (no startup reset; `Enter` releases in place, `h` does the staged reverse home) — unchanged from the demos.
- IL-1 voltage / IL-6 atomic cross-device / IL-7 docs: no rail change; calibrate_view + calibration + tests + doc touch-ups ship together; CLAUDE.md §3/§7 updated.

## Documentation

- `calibrate_view.py` + `calibration.py` module docstrings: the new grab-staged flow + the sweep/test-loop; remove circle-fit text.
- CLAUDE.md §3 (calibrate_view blurb, calibration.py role) + §7 (the calibration paragraph: arcs now manual-drag, detection auto-swept + test-loop-refined, grab-staged) — done via `/doc_update`.
- `docs/conventions/06-documentation-protocol.md` §10.1 CV registry: add this design (and supersede the circle-based arc-derivation entry's arc-geometry claim).
