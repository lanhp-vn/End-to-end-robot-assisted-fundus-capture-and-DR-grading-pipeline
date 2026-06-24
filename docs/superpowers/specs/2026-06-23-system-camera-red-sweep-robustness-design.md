# System-camera red-detection sweep robustness — pooled hue + low floor + transitional-frame exclusion

**Date:** 2026-06-23
**Status:** Design approved; pending implementation plan.
**Builds on:** `docs/superpowers/specs/2026-06-23-system-camera-arc-sweep-calibration-design.md` (the grab-staged manual-arc + red-sweep + test-loop flow, implemented on `feat/calibrate-view-manual-roi`).
**Touches:** `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py`, `scripts/calibration/system_camera/calibrate_view.py` (+ doc touch-ups). **Runtime detection is OUT of scope** — `arc_detector.py`, `auto_trigger.py`, and the demos are not modified.

## Problem

The first bench run of the arc-sweep calibration produced a non-separable sweep: `threshold=0.0200 separable=False`, only 10/22 cases correct, with a dozen cases reported "STILL WRONG." Analysis of the 20 saved test cases (`media_outputs/arc_calib/*.json` + `*_clean.png`) pinned three faults:

1. **The coverage floor is too high.** Aligned/clear arcs read **exactly 0.0000** red coverage (no false positives). Genuinely-red arcs span 0.000–0.11 with **median 0.5%**. `pick_threshold`'s 0.02 floor forced the threshold up to 0.02, suppressing the majority of real red signal. Dropping the floor to 0.005 alone raised correctness 9→13/20.
2. **The red band is sampled too narrowly.** `sweep_red_detection` anchors the hue from `sample_red_band` on the **first** red case only, yielding a razor-thin hue band (observed: hue 0–2 ∪ 158–180). Pale / hue-drifted arcs on other frames are missed — e.g. a frame where both arcs are visibly red scored the left arc at 0.4% because its hue fell outside the anchor; a wider hue band (0–10 ∪ 150–180) caught it fully (~10× the red pixels).
3. **Several "both-red" labels are transitional.** 8 of 14 frames labeled "both red" actually have **one arc essentially blank** (<1% coverage), switching sides frame to frame. The operator confirmed the two Aurora arcs are meant to be red *together* when misaligned, so these are transitional moments captured mid-alignment. Because some "red" arcs carry no red, red and clear coverages overlap **at zero**, which no single threshold can separate — this is what pins the sweep to the floor and reports it non-separable.

Box geometry is **not** a primary fault: auto-fitting tight boxes to where the red pixels concentrate produced boxes nearly identical to the operator's drags (L 157,90,76,294 vs 152,87,93,305) and the same correctness. The operator's placement was fine; dilution is secondary.

## Goal

Make `sweep_red_detection` robust to the real data so it produces a separable, sensible red band + coverage threshold from the existing labelled cases, while keeping the runtime detection pipeline (`arc_detector.detect` / `red_coverage` / both-red→both-clear gate) **byte-for-byte unchanged** (calibration tunes the same pipeline used at runtime). Three changes:

1. **Lower the sweep's coverage floor** (the threshold floor used while fitting), since aligned arcs read ~0 so a low threshold is both safe and necessary.
2. **Pool the hue anchor across all red cases** instead of anchoring on the first, so the band spans the full observed hue range and catches pale / hue-drifted arcs.
3. **Exclude internally-contradictory "red" frames** (one arc reads clear) from threshold fitting, and **report** them — never silently drop — so the over-labeled transitional frames stop dragging the threshold to the floor, and the operator learns which frames to relabel/skip.

### Validation (offline, on the existing 20 cases)

The combined change yields: band `hue[0–10] ∪ hue[158–180], S≥15, V≥30`, **threshold 0.0055, separable over the fitted set (10/10: 4 genuine both-red + 6 clear)**, with the 10 transitional frames flagged and excluded. The threshold sits safely between clear (0) and the clean both-red coverages (0.03–0.14).

### Non-goals

- **No runtime change.** `arc_detector.py`, `auto_trigger.py`, and `scripts/demos/*` are untouched. The both-red→both-clear gate and red-only classification are retained (the operator confirmed both arcs are red together when misaligned).
- **No arc-box geometry rework** (placement was fine; the boxes stay operator-dragged in `calibrate_view.py`).
- **No per-arc thresholds** (would require changing `arc_detector`, which is out of scope; the asymmetry is transitional, not systematic).
- **No new color space, illumination normalization, live HSV trackbar, or shape/template matching.** Considered and deferred — re-running calibration is the lighting-robustness mechanism, consistent with the prior arc-sweep spec's deferrals.
- **No data re-collection requirement.** The operator chose to tune on the existing 20 cases; the transitional filter makes that viable. (Cleaner labeling next time is operator guidance, documented, not code.)

## Approach (chosen: tune the sweep in `calibration.py`, keep the runtime pipeline)

All algorithm changes are pure functions in `calibration.py`, unit-tested with synthetic numpy frames. `calibrate_view.py` only changes its sweep *report* to surface the excluded frames. Runtime is untouched.

Rejected: (a) lowering `pick_threshold`'s general default floor (it is a general-purpose helper; thread a per-call floor from the sweep instead — no test churn); (b) reworking the gate to not require both arcs (rejected by the operator — arcs are red together); (c) a new color space / template matcher (premature given a low floor + wider hue + label filtering already separate the data).

## Architecture

### `calibration.py` — pure logic (unit-tested)

**New shared helper (DRY refactor):**

```python
def _bands_from_points(pts: np.ndarray, lo_pct: float, hi_pct: float) -> list[HsvBand]:
    """Percentile-bracket pre-masked HSV points into 1-2 red bands (0/180 hue wrap)."""
```

`sample_red_band` is refactored to call `_bands_from_points` (behavior unchanged; `test_sample_red_band_brackets_and_wraps` still passes).

**New pooled anchor:**

```python
def _pooled_red_anchor(
    red_frames: list[np.ndarray], left_arc: RoiBox, right_arc: RoiBox, *, lo_pct=5, hi_pct=95
) -> list[HsvBand]:
    """Pool _RED_PRIOR-masked pixels across the LEFT+RIGHT arc crops of ALL red cases, then
    percentile-bracket the pooled hue into 1-2 bands. Wider/robust vs anchoring on one frame
    (catches arcs whose hue/saturation drifts frame to frame). Raises ValueError if no red pixels
    in any red case."""
```

**`SweepResult` gains one field:**

```python
@dataclass(frozen=True)
class SweepResult:
    red_bands: list[HsvBand]
    coverage_threshold: float
    separable: bool                  # all FITTED (non-excluded) cases classify correctly
    unsatisfied: list[int]           # fitted cases the chosen config still gets wrong
    case_detections: list[tuple[bool, bool]]
    excluded_transitional: list[int] # 'red' cases excluded from fitting (one arc read clear)
```

**`sweep_red_detection` reworked** (signature gains `floor` + `blank_eps`; hue pooled; transitional frames excluded from the fit pool):

```python
def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    s_floors: Sequence[int] = tuple(range(15, 130, 5)),
    v_floors: Sequence[int] = tuple(range(30, 150, 10)),
    floor: float = 0.005,
    blank_eps: float = 0.008,
) -> SweepResult:
```

Algorithm:
1. **Hue anchor:** `_pooled_red_anchor` over all red cases (raises ValueError if none).
2. **Grid over (s_floor, v_floor):** build candidate `red_bands` (pooled hue/s_hi/v_hi, swept floors), compute per-arc coverage for every case via the runtime `detect` (same masking + morph). A `'red'` case whose weaker arc is `< blank_eps` is **transitional** (it can't be a true both-red frame — the arcs are physically the same colour) and is **excluded** from the red-coverage fit pool. Pool the *clean* red-case coverages vs clear-case coverages → `threshold = pick_threshold(..., floor=floor)`.
3. **Score** each candidate by `(# clean+clear cases classified to expected, then −#transitional, then separation margin)` — naturally prefers the band that recovers the most genuine both-red arcs while keeping clear at ~0. Return the best as a `SweepResult` with the chosen band/threshold, `excluded_transitional`, `unsatisfied` (clean cases still wrong), `separable = not unsatisfied`, and per-case detections.
4. Raises ValueError if no candidate recovers a single clean both-red case (operator must show clearer RED arcs).

`pick_threshold` is **unchanged** (its `floor` default stays 0.02; the sweep passes `floor=0.005`).

### `calibrate_view.py` — interactive shell (report only)

`_report_sweep` prints the threshold, separability over the fitted set, the `ok/n_fit` tally with the excluded count, the `describe_case` sentence for each clean case still wrong, **and** a distinct line per excluded transitional case (e.g. `case N EXCLUDED (transitional): left arc correctly detected as red; right arc incorrectly detected as not red -- one arc read clear; relabel as clear or recapture as a clean both-red frame`). No other `calibrate_view.py` change — `_sweep`, `_confirm`, `_test_loop`, and `main()` call the new sweep with its defaults.

## Data flow

Unchanged from the arc-sweep flow. The test loop and reference panels still produce `ArcCase{frame, expected}`; the only difference is the sweep now (a) pools the hue across all red cases, (b) fits the threshold with a 0.005 floor on the clean red subset, and (c) returns `excluded_transitional` for the report. Runtime reads the written `red_bands`/`coverage_threshold` exactly as before.

## Error handling

- `_pooled_red_anchor` raises ValueError if **no** red pixels exist across any red case; the existing `_sweep` try/except in `calibrate_view.main()` catches it and tells the operator to re-show clearly-red arcs.
- A sweep where no candidate band recovers a clean both-red case raises ValueError (same handling).
- Transitional `'red'` frames are reported (`excluded_transitional` + `_report_sweep`), never silently dropped.
- Clear cases are never excluded (only `'red'` cases are checked for the one-arc-blank condition); a contradictory `'clear'` frame (actually red) still surfaces as `unsatisfied` / non-separable.

## Testing

Host unit tests (no bus, CI-safe — `calibration.py` imports no `msvcrt`):

- **Pooled hue widening:** a sweep over two red cases with *different* arc hues (e.g. one near hue 2, one near hue 178) plus a clear case → assert the resulting band spans both hue clusters and both red panels classify `both_red` while the clear classifies `both_clear`. Guards the single-frame-anchor regression.
- **Transitional exclusion:** red case (both arcs red) + clear case (both clear) + a case labeled `'red'` with ONE arc red and the other greenish/clear → assert `result.separable is True`, the contradictory case index is in `result.excluded_transitional`, it is NOT in `unsatisfied`, and the chosen config classifies the clean red as `both_red` and the clear as `both_clear`.
- **Existing sweep tests keep passing:** `test_sweep_red_detection_separates_red_from_clear` (pure both-red → clean, separable), `test_sweep_red_detection_reports_unsatisfiable_cases` (a contradictory `'clear'` case → non-separable, `unsatisfied` non-empty, result still returned).
- **Unchanged:** `test_sample_red_band_brackets_and_wraps` (refactor preserves behavior), `pick_threshold` tests (default floor 0.02 unchanged), `screen_roi_from_rect`/`deskew_crop`/`describe_case`/`write_calibration_*`.

The cv2/msvcrt interactive shell is exercised by the existing bench procedure, not unit tests. Gates: `ruff format`, `ruff check .`, `mypy src`, `pytest -m 'not hardware'`.

### Bench verification (operator, post-implementation)

Re-run `calibrate_view.py` on the device (or re-use the existing flow): drag the screen ROI + arc boxes, capture RED + CLEAR panels, run the test loop, and confirm the sweep now reports `separable=True` over the fitted cases with the transitional frames listed as excluded; write; verify in `usb_camera_arc_debug.py` / `grab_auto_trigger_analysis.py` that the new band + threshold classify aligned/misaligned live.

## Data caveat (operator guidance, documented — not code)

Only 4 of the 14 labelled "red" frames in the captured set were genuinely both-arcs-red. The threshold derived from them is sound for this data, but for a stronger fit the operator should label only **unambiguous** both-red / both-clear frames in the test loop and skip transitional ones (the new excluded-case report makes the transitional frames obvious).

## OpenCV techniques leveraged (read-only; reimplemented in `src/`, never imported — IL-2)

- **Percentile HSV `inRange` bracketing pooled over samples** — the existing `sample_red_band` percentile bracket, generalized to pool pixels across many frames (more robust class statistics).
- **1-D two-class separation** — `pick_threshold` (Otsu/Triangle idea for scalar coverages) is unchanged; the fix is *what it is fed* (a low floor + a clean red pool), not its algorithm.
- **Label-noise robustness** — excluding internally-contradictory positives from a fit is standard; here it is principled by the physical fact that the two arcs share a colour, so a one-arc-blank "both-red" frame is self-contradictory.

## Iron Laws

- **IL-2:** no `references/` edits; OpenCV patterns reimplemented, not imported.
- **IL-5:** `system_camera_config.yaml` is still written only by `write_calibration_values`; saved cases stay under git-ignored `media_outputs/`.
- **IL-6 / IL-7:** calibration + tests + report + doc touch-ups ship together; CLAUDE.md §7 + the DRY registry are updated. No arm/hand bus access changes (IL-1/IL-3/IL-4 untouched — this is host-only CV logic).

## Documentation

- `calibration.py` module + `sweep_red_detection` docstrings: pooled hue anchor across red cases, the `floor`/`blank_eps` params, and the transitional-frame exclusion.
- CLAUDE.md §7: note the sweep now pools the red band across all labelled red frames, fits a low coverage floor, and excludes/report transitional one-arc-blank frames (runtime detection unchanged).
- `docs/conventions/06-documentation-protocol.md` §10.1 CV registry: add this design + its implementation plan.
