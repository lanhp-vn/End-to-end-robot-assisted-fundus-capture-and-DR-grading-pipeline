# Grab-staged manual-arc + red-sweep calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `calibrate_view.py` into a grab-staged flow that places the two alignment-arc boxes by manual drag and tunes the red/clear detection by an auto-sweep verified + refined against operator-labelled frames; move the sweep/comment logic into pure, unit-tested functions in `calibration.py` and delete the dead circle-fit derivation.

**Architecture:** Pure tuning logic (`describe_case`, `pick_threshold`, `sweep_red_detection`) lives in `src/arm101_hand/system_camera/calibration.py` and is unit-tested. The interactive flow lives in `scripts/calibration/system_camera/calibrate_view.py`, runs inside `run_grab_demo(on_hold=...)` (the demos' staged grab), and reuses the existing drag/capture/confirm helpers plus the arc-debug-style live HUD. The sweep reuses the runtime `arc_detector.detect` for coverage so calibration and runtime stay in lockstep.

**Tech Stack:** Python 3.12, OpenCV (plain `opencv-python` 4.13, no contrib), numpy, pydantic, ruamel.yaml, `msvcrt`, pytest, ruff, mypy.

## Global Constraints

- **Base = current working tree on branch `feat/calibrate-view-manual-roi`.** Builds on the already-committed Calib 1/3 drag+rotate work. Do NOT discard working-tree changes.
- **Touch ONLY:** `src/arm101_hand/system_camera/calibration.py`, `tests/unit/test_system_camera_calibration.py`, `scripts/calibration/system_camera/calibrate_view.py`, `CLAUDE.md`, `docs/conventions/06-documentation-protocol.md`. Leave the unrelated working-tree files (`ONBOARDING.md`, `hand_config.yaml`, `system_camera_config.yaml`, `usb_camera_capture.py`, `usb_camera_roi_preview.py`, `test_roi.py`) untouched.
- **Red-only classification retained.** No positive green band. The calibrator tunes the SAME `arc_detector` pipeline used at runtime; do not modify `arc_detector.py`, `auto_trigger.py`, or the demos.
- **IL-2:** no edits under `references/`; OpenCV patterns reimplemented, never imported.
- **IL-5:** `system_camera_config.yaml` written only by `write_calibration_values` (+ `.bak`); saved test-case frames/sidecars go under git-ignored `media_outputs/`.
- **No surprise movements:** Stage 0 reuses `run_grab_demo` unchanged (no startup reset; `Enter` releases in place, `h` staged reverse home).
- **No `--from-files`.** Drop the offline path.
- **Keep `_drag_box`** (mouse-drag + terminal-confirm); do NOT switch to `cv2.selectROI`/`selectROIs` (console-focus hang — documented).
- **Gates:** `uv run ruff format <files>`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest -m 'not hardware'`.
- **Commit format:** Conventional Commits, scope `(system_camera)`.

---

### Task 1: `describe_case` — the per-arc comment generator (pure)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Produces: `describe_case(expected: str, det_left: bool, det_right: bool) -> str` (`expected` is `'red'`/`'clear'`; `det_*` True = detector said RED).

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_system_camera_calibration.py`; also add `describe_case` to the existing `from arm101_hand.system_camera.calibration import (...)` block)

```python
@pytest.mark.parametrize(
    "expected, det_left, det_right, want",
    [
        ("red", True, True, "both arcs correctly detected as red"),
        ("red", False, False, "both arcs incorrectly detected as not red"),
        ("red", True, False, "left arc correctly detected as red; right arc incorrectly detected as not red"),
        ("red", False, True, "left arc incorrectly detected as not red; right arc correctly detected as red"),
        ("clear", False, False, "both arcs correctly detected as not red"),
        ("clear", True, True, "both arcs incorrectly detected as red"),
        ("clear", True, False, "left arc incorrectly detected as red; right arc correctly detected as not red"),
        ("clear", False, True, "left arc correctly detected as not red; right arc incorrectly detected as red"),
    ],
)
def test_describe_case(expected, det_left, det_right, want):
    assert describe_case(expected, det_left, det_right) == want
```

- [ ] **Step 2: Run it, verify failure**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py::test_describe_case -q`
Expected: FAIL — `ImportError: cannot import name 'describe_case'`.

- [ ] **Step 3: Implement** (add to `calibration.py`, after the imports / near the other pure helpers)

```python
def describe_case(expected: str, det_left: bool, det_right: bool) -> str:
    """Human sentence comparing the on-screen ground truth (`expected` is 'red' or 'clear' and
    applies to BOTH arcs, which are physically the same colour) against the detector's per-arc
    output (`det_left`/`det_right` True == detector said RED). Collapses to 'both arcs ...' when the
    two arcs share a phrase, else 'left arc ...; right arc ...'."""
    exp_red = expected == "red"

    def phrase(detected_red: bool) -> str:
        if exp_red and detected_red:
            return "correctly detected as red"
        if exp_red and not detected_red:
            return "incorrectly detected as not red"
        if not exp_red and not detected_red:
            return "correctly detected as not red"
        return "incorrectly detected as red"

    lp, rp = phrase(det_left), phrase(det_right)
    return f"both arcs {lp}" if lp == rp else f"left arc {lp}; right arc {rp}"
```

- [ ] **Step 4: Run it, verify pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py::test_describe_case -q`
Expected: PASS (8 parametrized cases).

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): describe_case arc-detection comment generator"
```

---

### Task 2: `pick_threshold` — 1-D two-class coverage separator (pure)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Produces: `pick_threshold(red_covs: list[float], clear_covs: list[float], *, floor: float = 0.02) -> tuple[float, bool]` — returns `(threshold, separable)`. `red_covs` should classify `>= threshold`; `clear_covs` `< threshold`.

- [ ] **Step 1: Write the failing test** (append; add `pick_threshold` to the import block)

```python
def test_pick_threshold_separable_midpoint():
    t, sep = pick_threshold(red_covs=[0.20, 0.30], clear_covs=[0.00, 0.01])
    assert sep is True
    assert abs(t - 0.105) < 1e-9  # midpoint of max(clear)=0.01 and min(red)=0.20


def test_pick_threshold_overlap_minimises_misclassification():
    t, sep = pick_threshold(red_covs=[0.22, 0.30, 0.40], clear_covs=[0.00, 0.05, 0.25])
    assert sep is False
    assert abs(t - 0.135) < 1e-9  # min-error cut with the larger margin (between 0.05 and 0.22)


def test_pick_threshold_empty_returns_floor():
    assert pick_threshold([], [0.1]) == (0.02, True)
    assert pick_threshold([0.1], []) == (0.02, True)
```

- [ ] **Step 2: Run it, verify failure**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k pick_threshold -q`
Expected: FAIL — `cannot import name 'pick_threshold'`.

- [ ] **Step 3: Implement** (add to `calibration.py`)

```python
def pick_threshold(
    red_covs: list[float], clear_covs: list[float], *, floor: float = 0.02
) -> tuple[float, bool]:
    """Pick a coverage threshold separating red-case coverages (should be >= t) from clear-case
    coverages (should be < t).

    Separable (max(clear) < min(red)): max-margin midpoint, separable=True. Overlapping: scan
    candidate cuts (midpoints between adjacent pooled coverages, plus the two outer edges) and return
    the one minimising misclassified coverages, tie-broken by the larger distance to the nearest
    coverage (a 1-D two-class separator -- the Otsu/Triangle idea reimplemented for scalar coverages),
    separable=False. Floored to `floor` so a degenerate all-low set never yields t<=0 (which would
    call everything RED). Empty inputs -> (floor, True)."""
    if not red_covs or not clear_covs:
        return floor, True
    lo_red, hi_clear = min(red_covs), max(clear_covs)
    if hi_clear < lo_red:  # cleanly separable -> midpoint, max margin
        return max((lo_red + hi_clear) / 2.0, floor), True
    pooled = sorted(set(red_covs + clear_covs))
    candidates = [(pooled[i] + pooled[i + 1]) / 2.0 for i in range(len(pooled) - 1)]
    candidates += [max(floor, pooled[0] - 1e-3), pooled[-1] + 1e-3]

    def score(t: float) -> tuple[int, float]:
        mis = sum(c >= t for c in clear_covs) + sum(c < t for c in red_covs)
        margin = min(abs(c - t) for c in pooled)
        return (-mis, margin)

    best_t = max(candidates, key=score)
    return max(best_t, floor), False
```

- [ ] **Step 4: Run it, verify pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k pick_threshold -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): pick_threshold 1-D coverage separator"
```

---

### Task 3: `ArcCase` + `SweepResult` + `sweep_red_detection` (pure)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: `pick_threshold` (Task 2), existing `sample_red_band`, `roi_from_region`, `arc_detector.detect`, config `AutoTriggerConfig`/`HsvBand`/`RoiBox`.
- Produces: `ArcCase(frame: np.ndarray, expected: str)`; `SweepResult(red_bands, coverage_threshold, separable, unsatisfied, case_detections)`; `sweep_red_detection(cases, left_arc, right_arc, *, morph_kernel=3, s_floors, v_floors) -> SweepResult`.

- [ ] **Step 1: Add imports to `calibration.py`**

Add at the top (with the existing imports):

```python
from collections.abc import Sequence
from dataclasses import dataclass
```

Add to the config import line (it currently imports `HsvBand, RoiBox, SystemCameraConfig`):

```python
from arm101_hand.config.system_camera_config import AutoTriggerConfig, HsvBand, RoiBox, SystemCameraConfig
```

Add (below `from .roi import roi_from_region`):

```python
from .arc_detector import detect  # reuse the EXACT runtime coverage path so calibration matches runtime
```

- [ ] **Step 2: Write the failing test** (append; add `ArcCase`, `sweep_red_detection` to the import block)

```python
def _roi_with_arcs(left, right, color_bgr):
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    for arc in (left, right):
        img[arc.y : arc.y + arc.h, arc.x : arc.x + arc.w] = color_bgr
    return img


def test_sweep_red_detection_separates_red_from_clear():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))      # pure red arcs (HSV hue 0)
    clear_frame = _roi_with_arcs(left, right, (80, 160, 80))  # greenish, no red
    res = sweep_red_detection([ArcCase(red_frame, "red"), ArcCase(clear_frame, "clear")], left, right)
    assert res.separable is True
    assert res.unsatisfied == []
    assert 0.0 < res.coverage_threshold < 1.0
    from arm101_hand.config.system_camera_config import AutoTriggerConfig
    from arm101_hand.system_camera.arc_detector import detect
    cfg = AutoTriggerConfig(
        left_arc=left, right_arc=right, red_bands=res.red_bands, coverage_threshold=res.coverage_threshold
    )
    assert detect(red_frame, cfg).both_red
    assert detect(clear_frame, cfg).both_clear


def test_sweep_red_detection_reports_unsatisfiable_cases():
    left = RoiBox(x=120, y=120, w=80, h=240, ref_w=800, ref_h=480)
    right = RoiBox(x=600, y=120, w=80, h=240, ref_w=800, ref_h=480)
    red_frame = _roi_with_arcs(left, right, (0, 0, 200))
    contradictory_clear = _roi_with_arcs(left, right, (0, 0, 200))  # tagged 'clear' but actually red
    res = sweep_red_detection(
        [ArcCase(red_frame, "red"), ArcCase(contradictory_clear, "clear")], left, right
    )
    assert res.separable is False
    assert res.unsatisfied  # non-empty -> best-effort, reported not silently dropped
    assert isinstance(res.coverage_threshold, float)
```

- [ ] **Step 3: Run it, verify failure**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k sweep_red_detection -q`
Expected: FAIL — `cannot import name 'ArcCase'` / `sweep_red_detection`.

- [ ] **Step 4: Implement** (add to `calibration.py`)

```python
@dataclass(frozen=True)
class ArcCase:
    """One labelled deskewed-ROI frame. `expected` ('red'|'clear') applies to BOTH arcs (they are
    physically the same colour); per-arc detector disagreement is a detection error."""

    frame: np.ndarray  # deskewed ROI, ref_w x ref_h, BGR
    expected: str


@dataclass(frozen=True)
class SweepResult:
    """Best-effort tuned detection: the red bands + threshold, whether every case is satisfied, the
    indices still misclassified, and the per-case (det_left, det_right) under the chosen config."""

    red_bands: list[HsvBand]
    coverage_threshold: float
    separable: bool
    unsatisfied: list[int]
    case_detections: list[tuple[bool, bool]]


def sweep_red_detection(
    cases: list[ArcCase],
    left_arc: RoiBox,
    right_arc: RoiBox,
    *,
    morph_kernel: int = 3,
    s_floors: Sequence[int] = tuple(range(20, 130, 10)),
    v_floors: Sequence[int] = tuple(range(30, 150, 10)),
) -> SweepResult:
    """Tune the red HSV band + coverage threshold against all labelled cases.

    1. Hue anchor: ``sample_red_band`` on the arc crops of the first 'red' case -> fixed hue bounds
       (+ the 0/180 wrap -> up to two bands).
    2. Grid over (s_floor, v_floor): build candidate red_bands (anchored hue/s_hi/v_hi, swept floors),
       compute per-arc coverage for every case via the runtime ``detect`` (same masking + morph),
       pool red-case vs clear-case coverages, threshold = ``pick_threshold``.
    3. Score by (#cases with BOTH arcs classified to expected, then separation margin); return the
       best as a SweepResult with red_bands + threshold + the unsatisfied indices + per-case detections.

    Raises ValueError if there is no 'red' case to anchor the hue."""
    red_cases = [c for c in cases if c.expected == "red"]
    if not red_cases:
        raise ValueError("sweep needs at least one 'red' case to anchor the hue")
    anchor_frame = red_cases[0].frame
    try:
        anchor = sample_red_band(roi_from_region(left_arc).crop(anchor_frame))
    except ValueError:
        anchor = sample_red_band(roi_from_region(right_arc).crop(anchor_frame))

    best_key: tuple[int, float] = (-1, -1.0)
    best: tuple[list[HsvBand], float, list[tuple[bool, bool]]] | None = None
    for s_floor in s_floors:
        for v_floor in v_floors:
            bands = [
                HsvBand(h_lo=b.h_lo, s_lo=s_floor, v_lo=v_floor, h_hi=b.h_hi, s_hi=b.s_hi, v_hi=b.v_hi)
                for b in anchor
            ]
            trial = AutoTriggerConfig(
                left_arc=left_arc, right_arc=right_arc, red_bands=bands, morph_kernel=morph_kernel
            )
            covs = []
            for c in cases:
                st = detect(c.frame, trial)
                covs.append((st.left_cov, st.right_cov))
            red_covs = [v for c, lr in zip(cases, covs) if c.expected == "red" for v in lr]
            clear_covs = [v for c, lr in zip(cases, covs) if c.expected != "red" for v in lr]
            t, _sep = pick_threshold(red_covs, clear_covs)
            dets = [(lc >= t, rc >= t) for (lc, rc) in covs]
            correct = sum(
                dl == (c.expected == "red") and dr == (c.expected == "red")
                for c, (dl, dr) in zip(cases, dets)
            )
            allv = red_covs + clear_covs
            margin = min((abs(v - t) for v in allv), default=0.0)
            key = (correct, margin)
            if key > best_key:
                best_key = key
                best = (bands, t, dets)

    assert best is not None  # the grid is non-empty, so a best is always chosen
    bands, t, dets = best
    unsatisfied = [
        i
        for i, (c, (dl, dr)) in enumerate(zip(cases, dets))
        if not (dl == (c.expected == "red") and dr == (c.expected == "red"))
    ]
    return SweepResult(
        red_bands=bands,
        coverage_threshold=round(float(t), 4),
        separable=not unsatisfied,
        unsatisfied=unsatisfied,
        case_detections=dets,
    )
```

- [ ] **Step 5: Run it, verify pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k sweep_red_detection -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Full suite + type check + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(system_camera): sweep_red_detection red-band/threshold auto-tuner"
```

Expected: ruff clean; `mypy` Success; pytest green (Task 1-3 tests added; nothing removed yet).

---

### Task 4: `calibrate_view` ROI-capture + arc-drag helpers (GUI)

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`

**Interfaces:**
- Consumes: existing `_drag_box`, `_open_window`, `_poll_key`, `imshow_fit`, `deskew_crop`, `_DETECT`, `_REF_W`, `_REF_H`, `_FONT`; config `RoiBox`.
- Produces: extended `_drag_box(base, prompt, *, keep_box=None)`; `_pick_arcs(roi_frame) -> tuple[RoiBox, RoiBox] | None`; `_capture_roi(cap, screen_roi, title) -> np.ndarray | None`.

GUI code is not unit-tested (cv2 + msvcrt); verification is ruff + an import/`--help` smoke. These helpers are added now and wired by Task 6.

- [ ] **Step 1: Extend `_drag_box` to draw a confirmed box**

Change the signature `def _drag_box(base: np.ndarray, prompt: str) -> tuple[int, int, int, int] | None:` to:

```python
def _drag_box(
    base: np.ndarray, prompt: str, *, keep_box: tuple[int, int, int, int] | None = None
) -> tuple[int, int, int, int] | None:
```

Inside the render loop, immediately after `disp = shown.copy()` and before the `if st["box"]:` active-rect draw, insert:

```python
        if keep_box is not None:  # an already-confirmed box, drawn thin so the new drag references it
            kx, ky, kw, kh = (round(v * scale) for v in keep_box)
            cv2.rectangle(disp, (kx, ky), (kx + kw, ky + kh), (0, 200, 0), 1)
```

- [ ] **Step 2: Add `_pick_arcs` and `_capture_roi`** (new module-level functions)

```python
def _pick_arcs(roi_frame: np.ndarray) -> tuple[RoiBox, RoiBox] | None:
    """Drag the LEFT arc box, then the RIGHT one with the confirmed left box shown, on the deskewed
    ROI frame (already at the 800x480 ref, so drag pixels are ref pixels). Returns (left, right)
    RoiBoxes, or None if either drag is cancelled."""
    left = _drag_box(roi_frame, "Drag the LEFT arc box, then SPACE/ENTER.")
    if left is None:
        return None
    right = _drag_box(roi_frame, "Drag the RIGHT arc box (left box shown), then SPACE/ENTER.", keep_box=left)
    if right is None:
        return None
    return (
        RoiBox(x=left[0], y=left[1], w=left[2], h=left[3], ref_w=_REF_W, ref_h=_REF_H),
        RoiBox(x=right[0], y=right[1], w=right[2], h=right[3], ref_w=_REF_W, ref_h=_REF_H),
    )


def _capture_roi(cap, screen_roi: RoiBox, title: str) -> np.ndarray | None:
    """Stream the deskewed ROI (screen_roi crop -> 800x480) until SPACE (return the frozen ROI) or
    q/ESC (None). Action keys come from the TERMINAL (see _poll_key)."""
    print(f"\n{title}\n  Focus THIS terminal: SPACE = capture, q = quit.")
    opened = False
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            cv2.waitKey(30)
            if _poll_key() in ("q", "Q", "\x1b"):
                return None
            continue
        roi_img = deskew_crop(frame, screen_roi, out=_DETECT)
        if not opened:
            _open_window(title, _REF_W, _REF_H)
            opened = True
        disp = roi_img.copy()
        cv2.putText(disp, title, (10, 24), _FONT, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
        imshow_fit(title, disp)
        cv2.waitKey(1)
        key = _poll_key()
        if key == " ":
            cv2.destroyWindow(title)
            return roi_img
        if key in ("q", "Q", "\x1b"):
            cv2.destroyWindow(title)
            return None
```

- [ ] **Step 3: Format, lint, smoke**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
```

Expected: ruff clean; `--help` exits 0 (module imports cleanly; `RoiBox`/`deskew_crop`/`_DETECT`/`_REF_W`/`_REF_H` are already imported in this file from the prior work).

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/system_camera/calibrate_view.py
git commit -m "feat(system_camera): ROI-capture + two-arc drag helpers in calibrate_view"
```

---

### Task 5: `calibrate_view` sweep/test-loop helpers (GUI)

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`

**Interfaces:**
- Consumes: `detect`, `roi_from_region`, `imshow_fit`, `_open_window`, `_poll_key`, `deskew_crop`, `_DETECT`, `_REF_W`, `_REF_H`, `_FONT`, `_tint_mask`; from `calibration`: `ArcCase`, `SweepResult`, `sweep_red_detection`, `describe_case`; config `AutoTriggerConfig`.
- Produces: `_annotate_live`, `_prompt_expected`, `_prompt_note`, `_save_calib_case`, `_report_sweep`, and a reworked `_confirm`.

- [ ] **Step 1: Add imports** to `calibrate_view.py`

Add to the stdlib imports (top of file):

```python
import datetime as _dt
import json
```

Update the `arc_detector` import to ALSO bring `AlignmentState` (used for type hints) while keeping the existing `_band_mask, detect, red_coverage` (the old `_derive_bands_and_threshold` still uses `red_coverage` until Task 6 deletes it — dropping it now would trip ruff F821):

```python
from arm101_hand.system_camera.arc_detector import AlignmentState, _band_mask, detect, red_coverage  # noqa: E402
```

ADD the new names to the `calibration` import block while KEEPING the existing ones (`arc_bands_from_circle`, `fit_camera_circle`, `suggest_coverage_threshold` are still referenced by `_derive_bands_and_threshold`, which Task 6 deletes — removing them now would trip ruff F821). The block becomes:

```python
from arm101_hand.system_camera.calibration import (  # noqa: E402
    ArcCase,
    SweepResult,
    arc_bands_from_circle,
    describe_case,
    deskew_crop,
    fit_camera_circle,
    sample_red_band,
    screen_roi_from_rect,
    suggest_coverage_threshold,
    sweep_red_detection,
    write_calibration_values,
)
```

Add the config import for `AutoTriggerConfig` (the file already imports `HsvBand, RoiBox` from there in the prior work — extend it):

```python
from arm101_hand.config.system_camera_config import AutoTriggerConfig, HsvBand, RoiBox  # noqa: E402
```

Add a module constant near the other `media_outputs` usage:

```python
_CALIB_CASE_DIR = _REPO_ROOT / "media_outputs" / "arc_calib"
```

- [ ] **Step 2: Add the live-HUD + prompt + save + report helpers**

```python
def _annotate_live(roi_frame: np.ndarray, cfg: AutoTriggerConfig, state: AlignmentState) -> np.ndarray:
    """ROI copy with both arc boxes (RED verdict -> red, clear -> green) + a coverage HUD."""
    disp = roi_frame.copy()
    h, w = disp.shape[:2]
    for region, is_red in ((cfg.left_arc, state.left_red), (cfg.right_arc, state.right_red)):
        x, y, bw, bh = roi_from_region(region).for_frame(w, h)
        cv2.rectangle(disp, (x, y), (x + bw, y + bh), (0, 0, 255) if is_red else (0, 255, 0), 2)
    t = cfg.coverage_threshold
    hud = (
        f"L:{'RED' if state.left_red else 'clr'} {state.left_cov:.3f}   "
        f"R:{'RED' if state.right_red else 'clr'} {state.right_cov:.3f}   thr={t:.3f}"
    )
    cv2.putText(disp, hud, (10, h - 12), _FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return disp


def _prompt_expected(state: AlignmentState) -> str | None:
    """Read the on-screen ground truth from the TERMINAL: '1' both arcs RED, '2' both arcs CLEAR,
    'c'/q to cancel. Returns 'red'/'clear'/None. Window stays pumped via waitKey."""
    print(
        f"  detector said L:{'RED' if state.left_red else 'clr'} R:{'RED' if state.right_red else 'clr'}. "
        "Ground truth -> 1 = both RED, 2 = both CLEAR, c = cancel"
    )
    while True:
        cv2.waitKey(20)
        key = _poll_key()
        if key == "1":
            return "red"
        if key == "2":
            return "clear"
        if key in ("c", "C", "q", "Q", "\x1b"):
            return None


def _prompt_note() -> str:
    """Optional free-text note from the TERMINAL (Enter to skip)."""
    try:
        return input("  optional note (Enter to skip): ").strip()
    except EOFError:
        return ""


def _save_calib_case(
    out_dir: Path,
    roi_img: np.ndarray,
    cfg: AutoTriggerConfig,
    state: AlignmentState,
    expected: str,
    comment: str,
    note: str,
    camera_index: int,
    backend: str,
) -> None:
    """Save clean + annotated PNGs + a JSON sidecar (ground truth, detector output, generated comment,
    note, geometry, bands, threshold) under media_outputs/arc_calib/ for the audit trail."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now()
    stem = ts.strftime("%Y%m%d_%H%M%S_") + f"{ts.microsecond // 1000:03d}"
    cv2.imwrite(str(out_dir / f"calib_{stem}_clean.png"), roi_img)
    cv2.imwrite(str(out_dir / f"calib_{stem}_annotated.png"), _annotate_live(roi_img, cfg, state))
    sidecar = {
        "tool": "calibrate_view",
        "captured_at": ts.isoformat(),
        "expected": expected,
        "comment": comment,
        "note": note,
        "detection": {
            "left_red": state.left_red,
            "left_cov": state.left_cov,
            "right_red": state.right_red,
            "right_cov": state.right_cov,
            "coverage_threshold": cfg.coverage_threshold,
        },
        "left_arc": cfg.left_arc.model_dump(),
        "right_arc": cfg.right_arc.model_dump(),
        "red_bands": [b.model_dump() for b in cfg.red_bands],
        "morph_kernel": cfg.morph_kernel,
        "camera": {"index": camera_index, "backend": backend},
    }
    (out_dir / f"calib_{stem}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(f"  saved calib_{stem} ({comment})")


def _report_sweep(result: SweepResult, cases: list[ArcCase]) -> None:
    """Print the sweep outcome: threshold, separability, and the describe_case sentence for every
    case the chosen config still gets wrong (best-effort)."""
    ok = len(cases) - len(result.unsatisfied)
    print(
        f"\nSweep -> threshold={result.coverage_threshold:.4f}  separable={result.separable}  "
        f"{ok}/{len(cases)} cases correct."
    )
    for i in result.unsatisfied:
        dl, dr = result.case_detections[i]
        print(f"  case {i} STILL WRONG: {describe_case(cases[i].expected, dl, dr)}")
```

- [ ] **Step 3: Replace `_confirm`** — drop the circle param; show RED + CLEAR panels labelled by the swept detector

Replace the entire existing `_confirm(...)` function with:

```python
def _confirm(red_ref: np.ndarray, clear_ref: np.ndarray, cfg: AutoTriggerConfig) -> str:
    """Two deskewed panels (RED + CLEAR) with arc boxes + tinted red masks + real-detector labels.
    Returns the pressed action: 'y' write, 't' test loop + re-sweep, 'r' redo arcs, 'q' quit."""
    title = "Calib confirm (press keys in the TERMINAL)"
    la, ra = roi_from_region(cfg.left_arc), roi_from_region(cfg.right_arc)
    print(
        "\nConfirm calibration -- focus THIS terminal:\n"
        "  y = write config   t = test loop (label cases + re-sweep)   r = redo arcs   q = quit (no write)"
    )
    _open_window(title, 2 * _REF_W, _REF_H)
    try:
        while True:
            panels = []
            for label, frame in (("RED frame", red_ref), ("CLEAR frame", clear_ref)):
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                panel = _tint_mask(frame, _band_mask(hsv, cfg.red_bands), (0, 0, 255))
                for arc in (la, ra):
                    ax, ay, aw, ah = arc.for_frame(_REF_W, _REF_H)
                    cv2.rectangle(panel, (ax, ay), (ax + aw, ay + ah), (255, 255, 0), 1)
                state = detect(frame, cfg)
                cv2.putText(
                    panel,
                    f"{label}: L={'RED' if state.left_red else 'clear'} R={'RED' if state.right_red else 'clear'}",
                    (8, 24),
                    _FONT,
                    0.55,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
                panels.append(panel)
            gate = detect(red_ref, cfg).both_red and detect(clear_ref, cfg).both_clear
            composite = np.hstack(panels)
            cv2.putText(
                composite,
                f"red->both RED and clear->both clear: {gate}",
                (8, _REF_H - 12),
                _FONT,
                0.6,
                (0, 255, 0) if gate else (0, 0, 255),
                2,
                cv2.LINE_AA,
            )
            imshow_fit(title, composite)
            cv2.waitKey(20)
            key = _poll_key()
            if key in ("y", "Y", "t", "T", "r", "R", "q", "Q", "\x1b"):
                return "q" if key in ("q", "Q", "\x1b") else key.lower()
    finally:
        cv2.destroyWindow(title)
```

- [ ] **Step 4: Add the test loop** (new function)

```python
def _test_loop(
    cap, screen_roi: RoiBox, cfg: AutoTriggerConfig, camera_index: int, backend: str
) -> list[ArcCase] | None:
    """Live ROI + arc HUD using cfg; SPACE freezes a frame -> tag 1/2 + optional note -> ArcCase +
    saved case files. 'd' = done (return collected cases), 'q' = abort (None)."""
    print(
        "\nTest loop -- focus THIS terminal:\n"
        "  SPACE = freeze + label a case   d = done (re-sweep)   q = abort"
    )
    title = "Calib test loop (Optomed ROI)"
    _open_window(title, _REF_W, _REF_H)
    new_cases: list[ArcCase] = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                cv2.waitKey(30)
                key = _poll_key()
                if key in ("q", "Q", "\x1b"):
                    return None
                if key in ("d", "D"):
                    return new_cases
                continue
            roi_img = deskew_crop(frame, screen_roi, out=_DETECT)
            state = detect(roi_img, cfg)
            imshow_fit(title, _annotate_live(roi_img, cfg, state))
            cv2.waitKey(1)
            key = _poll_key()
            if key in ("d", "D"):
                return new_cases
            if key in ("q", "Q", "\x1b"):
                return None
            if key == " ":
                expected = _prompt_expected(state)
                if expected is None:
                    continue
                comment = describe_case(expected, state.left_red, state.right_red)
                print(f"  -> {comment}")
                _save_calib_case(
                    _CALIB_CASE_DIR, roi_img, cfg, state, expected, comment, _prompt_note(),
                    camera_index, backend,
                )
                new_cases.append(ArcCase(frame=roi_img, expected=expected))
    finally:
        cv2.destroyWindow(title)
```

- [ ] **Step 5: Format, lint, smoke, commit**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
git add scripts/calibration/system_camera/calibrate_view.py
git commit -m "feat(system_camera): sweep report + test-loop helpers in calibrate_view"
```

Expected: ruff clean (the new imports `ArcCase`/`SweepResult`/`sweep_red_detection`/`describe_case`/`AutoTriggerConfig`/`AlignmentState`/`_band_mask`/`json`/`_dt` are all referenced by the helpers above, so no unused-import errors); `--help` exits 0.

Note: at this point `calibrate_view.py` still has the OLD `main()` calling `_pick_roi` + `_derive_bands_and_threshold` (which import `fit_camera_circle`, `arc_bands_from_circle`, `suggest_coverage_threshold`). Those imports are still present and valid — Task 6 removes them. The new helpers are defined but not yet wired.

---

### Task 6: Rewire `main()` / `_calibrate_hook` — Stage 0 grab + the full flow (GUI)

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`

**Interfaces:**
- Consumes: `run_grab_demo`, `GrabHoldContext` (from `arm101_hand.scripts.grab_common`); `open_capture`, `resolution_mismatch_warning` (from `arm101_hand.system_camera`); all Task 4-5 helpers; `_capture_frame`, `_pick_roi` (existing).
- Produces: a reworked `main()` that returns `run_grab_demo(_calibrate_hook)`.

- [ ] **Step 1: Update imports + drop the dead ones**

Add (with the other `arm101_hand` imports):

```python
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo  # noqa: E402
```

Change the `arm101_hand.system_camera` import to include `resolution_mismatch_warning`:

```python
from arm101_hand.system_camera import (  # noqa: E402
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
    roi_from_region,
)
```

Now that the old callers are being deleted (Step 2 below), drop the dead imports. Do Step 2's function deletions together with these import edits so the end-of-task gate sees a consistent file. Final `arc_detector` import (drop `red_coverage` — only the deleted `_derive_bands_and_threshold` used it):

```python
from arm101_hand.system_camera.arc_detector import AlignmentState, _band_mask, detect  # noqa: E402
```

Final `calibration` import block (drop `arc_bands_from_circle`, `fit_camera_circle`, `suggest_coverage_threshold` — only the deleted functions used them; keep `deskew_crop`):

```python
from arm101_hand.system_camera.calibration import (  # noqa: E402
    ArcCase,
    SweepResult,
    describe_case,
    deskew_crop,
    sample_red_band,
    screen_roi_from_rect,
    sweep_red_detection,
    write_calibration_values,
)
```

- [ ] **Step 2: Delete the now-obsolete helpers and `--from-files` path**

Delete these functions entirely: `_derive_bands_and_threshold`, `_retune`, `_load_frames_from_files`. (They reference circle-fit / single-frame derivation and the offline path, all dropped.) Delete the `--from-files` argparse argument and its branch in `main()`.

- [ ] **Step 3: Replace `main()` with the grab-staged hook**

```python
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="USB camera index (default: config)")
    ap.add_argument(
        "--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: config)"
    )
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    camera_index = args.camera if args.camera is not None else cfg.camera_index
    backend = args.backend if args.backend is not None else cfg.backend

    def _calibrate_hook(_ctx: GrabHoldContext) -> None:
        # _ctx unused: arm+hand hold grab under torque (the only pose where the Aurora screen is in
        # view); this hook opens the USB camera and runs the calibration. Mirrors usb_camera_arc_debug.
        print(f"Opening USB camera {camera_index} ({backend}) ...")
        cap = open_capture(
            camera_index, backend, fourcc=cfg.fourcc, width=cfg.width, height=cfg.height,
            autofocus=cfg.autofocus, focus=cfg.focus,
        )
        if not cap.isOpened():
            cap.release()
            print(
                f"ERROR: could not open camera {camera_index}. Try --camera N / --backend dshow. "
                "(Arm/hand still release at the exit prompt.)",
                file=sys.stderr,
            )
            return
        warn = resolution_mismatch_warning(
            cfg.width, cfg.height,
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        if warn:
            print(warn, file=sys.stderr)
        try:
            # Stage 1: WHITE startup screen -> drag + rotate -> screen_roi (full frame)
            screen_roi = None
            while screen_roi is None:
                white = _capture_frame(cap, "Calib 1/3: frame the WHITE startup screen, SPACE", None)
                if white is None:
                    return
                screen_roi = _pick_roi(white)

            # Stage 2-3: arcs -- freeze a RED-arc ROI, drag LEFT then RIGHT
            arcs = None
            while arcs is None:
                roi_red = _capture_roi(cap, screen_roi, "Show RED arcs; SPACE to freeze for arc-box drag")
                if roi_red is None:
                    return
                arcs = _pick_arcs(roi_red)
            left_arc, right_arc = arcs

            # Stage 4: reference panels
            red_panel = _capture_roi(cap, screen_roi, "Capture RED panel (both arcs RED), SPACE")
            if red_panel is None:
                return
            clear_panel = _capture_roi(cap, screen_roi, "Capture CLEAR/aligned panel, SPACE")
            if clear_panel is None:
                return
            cases = [ArcCase(red_panel, "red"), ArcCase(clear_panel, "clear")]

            # Stage 5: sweep
            def _sweep(case_list: list[ArcCase]) -> AutoTriggerConfig:
                result = sweep_red_detection(case_list, left_arc, right_arc)
                _report_sweep(result, case_list)
                return AutoTriggerConfig(
                    left_arc=left_arc, right_arc=right_arc,
                    red_bands=result.red_bands, coverage_threshold=result.coverage_threshold,
                )

            try:
                trial = _sweep(cases)
            except ValueError as e:
                print(f"ERROR: {e}. Re-run and show clearly RED, misaligned arcs.", file=sys.stderr)
                return

            # Stage 6-8: confirm / test loop / re-sweep / write
            while True:
                action = _confirm(red_panel, clear_panel, trial)
                if action == "y":
                    write_calibration_values(
                        _CONFIG_PATH, screen_roi=screen_roi, left_arc=left_arc, right_arc=right_arc,
                        red_bands=trial.red_bands, coverage_threshold=trial.coverage_threshold,
                    )
                    print(f"Wrote calibration to {_CONFIG_PATH} (backup: {_CONFIG_PATH.name}.bak).")
                    return
                if action == "q":
                    print("Quit without writing.")
                    return
                if action == "r":
                    arcs = None
                    while arcs is None:
                        roi_red = _capture_roi(cap, screen_roi, "Show RED arcs; SPACE to re-drag arc boxes")
                        if roi_red is None:
                            return
                        arcs = _pick_arcs(roi_red)
                    left_arc, right_arc = arcs
                    trial = _sweep(cases)
                if action == "t":
                    new = _test_loop(cap, screen_roi, trial, camera_index, backend)
                    if new is None:
                        print("Test loop aborted; nothing written.")
                        return
                    cases += new
                    trial = _sweep(cases)
        finally:
            cap.release()
            cv2.destroyAllWindows()

    return run_grab_demo(_calibrate_hook)
```

- [ ] **Step 4: Update the module docstring** — replace the old "Flow" section to describe Stage 0 grab + manual arcs + sweep + test loop, and drop the `--from-files` usage line. Keep the terminal-keys note. The new Flow text:

```
Flow (inside run_grab_demo -- the arm+hand stage the grab so the camera views the Aurora screen):
  1. WHITE startup screen -> drag a box + <-/-> rotate to deskew -> screen_roi (full frame).
  2-3. Freeze a RED-arc ROI -> drag the LEFT arc box, then the RIGHT (left box shown).
  4. Capture a RED panel (both arcs red) and a CLEAR/aligned panel.
  5. Auto-sweep the red HSV band + coverage threshold against both panels (report + best-effort).
  6-7. Confirm: 'y' write, 't' test loop (label frames 1=both-red/2=both-clear -> re-sweep), 'r'
       redo arcs, 'q' quit. Loop until satisfied.
  8. Write screen_roi + arc boxes + red_bands + threshold to system_camera_config.yaml.
Action keys are read from the TERMINAL (the cv2 window often has no keyboard focus from a console).
```

Also update the `Usage:` block to drop the `--from-files` line.

- [ ] **Step 5: Format, lint, smoke, regression, commit**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check .
uv run python scripts/calibration/system_camera/calibrate_view.py --help
uv run pytest -m 'not hardware' -q
git add scripts/calibration/system_camera/calibrate_view.py
git commit -m "feat(system_camera): grab-staged manual-arc + sweep + test-loop calibrate_view flow"
```

Expected: ruff `All checks passed!` (no unused imports — the circle-fit / suggest_coverage_threshold imports are gone, `_derive_bands_and_threshold`/`_retune`/`_load_frames_from_files` deleted, `--from-files` gone); `--help` exits 0 and prints the new Flow; pytest green (calibrate_view is not imported by tests).

---

### Task 7: Delete dead circle-fit + threshold-midpoint code from `calibration.py`

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Modify: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Removes: `fit_camera_circle`, `arc_bands_from_circle`, `suggest_coverage_threshold` (now unused — Task 6 removed the last callers).
- Keeps: `sample_red_band`, `_RED_PRIOR`, `_prior_mask`, `screen_roi_from_rect`, `deskew_crop`, `_Rect`, the ruamel writer, and the Task 1-3 additions.

- [ ] **Step 1: Remove the obsolete tests + imports**

In `tests/unit/test_system_camera_calibration.py`, delete `test_fit_camera_circle_centres_on_disc`, `test_fit_camera_circle_handles_nonuniform_disc`, `test_arc_bands_from_circle_are_symmetric`, `test_arc_bands_mirror_about_circle_centre`, `test_suggest_coverage_threshold_midpoint_and_floor`. Remove `arc_bands_from_circle`, `fit_camera_circle`, `suggest_coverage_threshold` from the `from arm101_hand.system_camera.calibration import (...)` block (keep `ArcCase`, `deskew_crop`, `describe_case`, `pick_threshold`, `sample_red_band`, `screen_roi_from_rect`, `sweep_red_detection`, `write_calibration_values`).

- [ ] **Step 2: Run the trimmed suite — confirm green (functions still defined, just untested)**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -q`
Expected: PASS (the remaining tests import only kept symbols; the to-be-deleted functions still exist in `calibration.py`).

- [ ] **Step 3: Delete the functions + trim the docstring**

In `calibration.py`, delete `fit_camera_circle`, `arc_bands_from_circle`, and `suggest_coverage_threshold` in full. In the module docstring, remove any sentence describing the circle fit / symmetric arc-band derivation; the module now does manual-arc support: ROI normalisation + red-band sampling + the sweep/comment helpers + the writer. Keep `_Rect`, `screen_roi_from_rect`, `deskew_crop`, `sample_red_band`, `_RED_PRIOR`, `_prior_mask`.

- [ ] **Step 4: Format, lint, type-check, full suite**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
```

Expected: ruff `All checks passed!` (no unused imports/names from the deletion); `mypy` Success; pytest green — the kept tests (`test_screen_roi_from_rect_*`, `test_deskew_crop_with_stored_angle_uprights_a_tilted_rect`, `test_sample_red_band_*`, `test_write_calibration_*`) plus the Task 1-3 additions, and none of the deleted `test_fit_camera_circle*`/`test_arc_bands*`/`test_suggest_coverage_threshold*`.

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "refactor(system_camera): drop circle-fit arc derivation + threshold midpoint"
```

---

### Task 8: Docs — CLAUDE.md + DRY registry

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/conventions/06-documentation-protocol.md`

- [ ] **Step 1: CLAUDE.md §3 trees** — Read `CLAUDE.md`, then:

In the `src/.../system_camera/` line, change `view-calibration math (calibration.py = 5:3 deskew crop + camera-circle fit + symmetric arc bands + red-band sampling + ruamel round-trip writer)` to `view-calibration math (calibration.py = 5:3 deskew crop + red-band sampling + describe_case/pick_threshold/sweep_red_detection red-detection auto-sweep + ruamel round-trip writer)`.

In the `scripts/.../system_camera/` calibrate_view line, change `interactive re-derive of deskewed 5:3 screen_roi (manual drag + arrow-key rotate) + circle-based arc bands + red HSV band` to `grab-staged interactive re-derive of deskewed 5:3 screen_roi (manual drag + arrow-key rotate) + manually-dragged arc boxes + auto-swept red HSV band/threshold + test-loop refinement`.

- [ ] **Step 2: CLAUDE.md §7** — update the calibration paragraph: arcs are now placed by manual drag (not circle-fit), and the red band + coverage threshold are auto-swept against a RED + CLEAR reference panel and refined by an arc-debug-style test loop, all inside the staged robot grab. Replace the sentence `the white frame is the drag-and-rotate backdrop for the deskewed 5:3 screen_roi (manual box + arrow-key rotation), red samples the arc red band, and the bright frame fits the camera circle (the arc bands are mirror-symmetric on the circle's left/right edges, battery corner excluded) and sets the coverage threshold` with: `the white frame is the drag-and-rotate backdrop for the deskewed 5:3 screen_roi (manual box + arrow-key rotation), the two arc boxes are dragged by hand on the deskewed ROI, and the red HSV band + coverage threshold are auto-swept against a RED + CLEAR reference panel (pick_threshold = max-margin when separable, min-misclassification when not) then refined by an arc-debug-style test loop that re-sweeps over operator-labelled frames -- all inside the staged robot grab (run_grab_demo) that brings the camera to the Aurora screen`.

- [ ] **Step 3: DRY registry** — In `docs/conventions/06-documentation-protocol.md` §10.1, under "Computer vision (system camera)", add two rows:

```
| System-camera arc-sweep calibration design (grab-staged; manual arc drags; red-detection auto-sweep + test-loop refinement) | `docs/superpowers/specs/2026-06-23-system-camera-arc-sweep-calibration-design.md` |
| System-camera arc-sweep calibration implementation plan | `docs/superpowers/plans/2026-06-23-system-camera-arc-sweep-calibration.md` |
```

- [ ] **Step 4: Verify caps + commit**

```bash
test "$(wc -l < CLAUDE.md)" -le 250 && echo "CLAUDE.md within cap"
git add CLAUDE.md docs/conventions/06-documentation-protocol.md
git commit -m "docs(system_camera): document grab-staged arc-sweep calibration"
```

Expected: CLAUDE.md ≤ 250 lines; commit succeeds.

---

## Manual bench verification (operator-run, after all tasks)

Hardware (arm + hand + USB camera + Aurora screen):

1. `uv run python scripts/calibration/system_camera/calibrate_view.py` — confirm the **grab stages** (arm+hand move) and the camera shows the Aurora screen.
2. Calib 1/3: drag + `←/→` rotate the screen box; ENTER.
3. Freeze a RED-arc ROI; drag LEFT then RIGHT arc box (right drag shows the left box).
4. Capture the RED panel, then the CLEAR/aligned panel; read the sweep report.
5. Press `t`; move the device through aligned/misaligned states; `SPACE` + `1`/`2` to label a few cases; `d`; confirm the re-sweep report resolves them.
6. `y` to write; verify the `.bak` and the new `screen_roi`/`left_arc`/`right_arc`/`red_bands`/`coverage_threshold` in `system_camera_config.yaml`.
7. Confirm in `usb_camera_arc_debug.py` / `grab_auto_trigger_analysis.py` that the new arcs + band classify correctly live.
8. On exit, confirm the `Enter`/`h` release behaves as the demos do (no surprise motion).

## Self-review

**Spec coverage:**
- Stage 0 grab (run_grab_demo) → Task 6 Step 3. ✓
- Calib 1/3 unchanged → reused (`_pick_roi`/`_capture_frame`), wired in Task 6. ✓
- Switch to ROI display → `_capture_roi`/`_test_loop` use `deskew_crop` (Task 4/5). ✓
- Manual arc drags, left-confirm-then-right-with-left-shown → `_drag_box` keep_box + `_pick_arcs` (Task 4). ✓
- RED + CLEAR panels → Task 6 Step 3. ✓
- Auto-sweep (hue anchor, S/V grid, pick_threshold, report+best-effort) → Task 3 + `_report_sweep` (Task 5). ✓
- Test loop (1/2 tag + note, describe_case, saved cases) → `_test_loop`/`_prompt_expected`/`_save_calib_case` (Task 5). ✓
- Re-sweep until satisfied → Task 6 `_sweep` re-call on `t`. ✓
- Confirm + write → reworked `_confirm` (Task 5) + `write_calibration_values` (Task 6). ✓
- Red-only retained; `arc_detector`/`auto_trigger`/demos untouched → no task modifies them. ✓
- `--from-files` dropped → Task 6 Step 2. ✓
- Keep `_drag_box`, not `selectROIs` → Task 4 extends `_drag_box`. ✓
- Delete circle-fit + suggest_coverage_threshold + their tests → Task 7. ✓
- Docs (CLAUDE.md §3/§7, DRY registry) → Task 8. ✓

**Placeholder scan:** none — every code step shows complete content.

**Type consistency:** `describe_case(expected: str, det_left: bool, det_right: bool) -> str`; `pick_threshold(list[float], list[float], *, floor) -> tuple[float, bool]`; `sweep_red_detection(list[ArcCase], RoiBox, RoiBox, *, morph_kernel, s_floors, v_floors) -> SweepResult`; `ArcCase(frame, expected)`; `SweepResult(red_bands, coverage_threshold, separable, unsatisfied, case_detections)`. `_confirm(red_ref, clear_ref, cfg) -> str` returns `y`/`t`/`r`/`q`, matched by Task 6's `main()` loop. `_test_loop(...) -> list[ArcCase] | None`. `_pick_arcs(...) -> tuple[RoiBox, RoiBox] | None`. `AutoTriggerConfig` built consistently in `_sweep`. Consistent across tasks.
