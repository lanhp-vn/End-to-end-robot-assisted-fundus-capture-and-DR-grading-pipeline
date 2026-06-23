# System-Camera View Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A calibration tool that re-derives the Aurora screen ROI, the two alignment-arc regions, and the red/green HSV bands from three guided camera captures, with operator confirmation, and writes them into `system_camera_config.yaml`.

**Architecture:** Pure detection math in a new device-layer module (`system_camera/calibration.py`, synthetic-numpy unit-testable, mirroring `arc_detector.py`); a thin interactive cv2 shell in a new script (`scripts/calibration/system_camera/calibrate_view.py`) that captures frames, runs the detectors, shows a confirm screen validated by the **real** `arc_detector.detect()`, and persists via a `ruamel.yaml` round-trip writer. The screen ROI is unified out of the `roi.py` Python constant into a new top-level `screen_roi` config field.

**Tech Stack:** Python 3.12, OpenCV (plain `opencv-python`, main modules only — no contrib), numpy, pydantic v2, ruamel.yaml (writer only), pytest, ruff, mypy.

## Global Constraints

- **Plain `opencv-python` only** — `opencv_contrib` modules (ximgproc, etc.) are NOT importable. Use core/imgproc functions only.
- **References are read-only (IL-2)** — adapt patterns from `references/computer-vision/opencv/samples/python/{squares.py, tutorial_code/imgProc/threshold_inRange/threshold_inRange.py}` into `src/`; never import from `references/`.
- **Reading YAML uses `yaml.safe_load` only** (never `yaml.load`); the new `ruamel.yaml` dependency is used ONLY by the calibration writer.
- **IL-5:** only the calibration tool writes config; demos/controllers never do. The script writes only on explicit operator confirmation, after `SystemCameraConfig.model_validate` passes, and writes a `.bak` first.
- **CI must stay green:** `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest -m 'not hardware'`. (mypy runs on `src` only; scripts are lint-checked, not type-checked.)
- **Code style:** Python 3.12, `from __future__ import annotations`, pydantic `ConfigDict(extra="forbid")`, no `subprocess` without `timeout`.

---

## ⚠️ Execution context — read before starting

The working tree on `main` (HEAD `3566219`) has the operator's **uncommitted WIP** on files this plan also touches:
- `src/arm101_hand/data/system_camera_config.yaml` — WIP removes `still_width`/`still_height` and sets `stable_seconds: 1.0` (committed value is `2.0`).
- `src/arm101_hand/hand/index_toggle.py`, `ONBOARDING.md`, untracked `scripts/diagnostics/fundus_camera/aurora_keepalive_probe.py`.

**Execute this plan in an isolated git worktree off `main` (HEAD `3566219`)** so the WIP stays untouched. This plan targets the **committed** state: `schema_version: 5`, `still_width/still_height` present, `stable_seconds: 2.0`. Against that state the full suite passes (356). When the calibration branch later merges to `main`, `system_camera_config.yaml` will need a manual reconcile with the pending WIP (non-overlapping regions mostly — `screen_roi` is new; the WIP edits `still_*` and `stable_seconds`).

**Do NOT edit the `still_width`/`still_height` or `stable_seconds` lines or their existing test assertions** (`test_data_yaml_loads`). New config assertions go in their own test functions so they pass independently of any WIP.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/arm101_hand/config/system_camera_config.py` | Rename `ArcRegion`→`RoiBox` (+alias); add top-level `screen_roi`; bump `schema_version` | 1 |
| `src/arm101_hand/data/system_camera_config.yaml` | Add `screen_roi` entry; bump `schema_version` | 1 |
| `src/arm101_hand/system_camera/roi.py` | Add `roi_from_region()` (config-free, Protocol-typed) | 2 |
| `src/arm101_hand/system_camera/arc_detector.py` | Delegate `_region_to_roi` to `roi_from_region` | 2 |
| `src/arm101_hand/system_camera/__init__.py` | Export `roi_from_region` | 2 |
| `scripts/demos/grab_trigger_capture.py`, `grab_trigger_capture_analysis.py`, `grab_auto_trigger_analysis.py`; `scripts/diagnostics/system_camera/usb_camera_roi_preview.py`, `usb_camera_focus_probe.py` | Read `cfg.screen_roi` via `roi_from_region` instead of the `AURORA_SCREEN_ROI` constant | 3 |
| `src/arm101_hand/system_camera/calibration.py` | NEW — pure detection: `detect_screen_rect`, `to_roi_candidate`, `detect_arc_regions`, `sample_hsv_band`, `suggest_coverage_threshold`, `write_calibration_values` | 4, 5, 6 |
| `scripts/calibration/system_camera/calibrate_view.py` | NEW — interactive capture/confirm/write shell | 7 |
| `pyproject.toml` | Add `ruamel.yaml` dependency | 6 |
| `tests/unit/test_system_camera_config.py`, `test_roi.py`, `test_system_camera_calibration.py` (NEW) | Unit tests | 1, 2, 4, 5, 6 |
| `CLAUDE.md`, `README.md`, docs registry, memory | Docs refresh | 8 |

---

### Task 1: Config schema — `RoiBox` rename + `screen_roi` field

**Files:**
- Modify: `src/arm101_hand/config/system_camera_config.py`
- Modify: `src/arm101_hand/data/system_camera_config.yaml`
- Test: `tests/unit/test_system_camera_config.py`

**Interfaces:**
- Produces: `RoiBox` (alias of the former `ArcRegion`), `ArcRegion = RoiBox`, `SystemCameraConfig.screen_roi: RoiBox`, `schema_version` default `6`.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_system_camera_config.py`:

```python
def test_arcregion_is_roibox_alias():
    from arm101_hand.config.system_camera_config import ArcRegion, RoiBox

    assert ArcRegion is RoiBox


def test_screen_roi_default_matches_legacy_constant():
    cfg = SystemCameraConfig()
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h) == (60, 75, 196, 147)
    assert (sr.ref_w, sr.ref_h) == (640, 480)


def test_screen_roi_round_trips_at_calibration_resolution():
    cfg = SystemCameraConfig.model_validate(
        {"screen_roi": {"x": 150, "y": 188, "w": 490, "h": 368, "ref_w": 1600, "ref_h": 1200}}
    )
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h, sr.ref_w, sr.ref_h) == (150, 188, 490, 368, 1600, 1200)


def test_schema_default_version_is_6():
    assert SystemCameraConfig().schema_version == 6


def test_data_yaml_has_screen_roi():
    cfg = load_system_camera_config(_DATA)
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h) == (60, 75, 196, 147)
    assert (sr.ref_w, sr.ref_h) == (640, 480)
```

Also in this step, update the ONE existing assertion the schema bump breaks: in `test_resolution_fourcc_defaults`, change `assert cfg.schema_version == 5` to `assert cfg.schema_version == 6`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_config.py::test_arcregion_is_roibox_alias tests/unit/test_system_camera_config.py::test_screen_roi_default_matches_legacy_constant tests/unit/test_system_camera_config.py::test_data_yaml_has_screen_roi -v`
Expected: FAIL (`ImportError: cannot import name 'RoiBox'` / `AttributeError: ... 'screen_roi'`).

- [ ] **Step 3: Rename `ArcRegion`→`RoiBox` + add alias** in `src/arm101_hand/config/system_camera_config.py`. Change the class declaration line `class ArcRegion(BaseModel):` to `class RoiBox(BaseModel):`, update its docstring, and immediately after the class body add the alias:

```python
class RoiBox(BaseModel):
    """A pixel rectangle measured against a reference frame size (mirrors system_camera.Roi).

    Used for the screen ROI (``SystemCameraConfig.screen_roi``) and the auto-trigger arc regions.
    """

    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    ref_w: int = Field(default=640, ge=1)
    ref_h: int = Field(default=480, ge=1)


ArcRegion = RoiBox  # back-compat alias: existing imports/configs keep working
```

- [ ] **Step 4: Update `AutoTriggerConfig` field types** — in the same file, change the `left_arc`/`right_arc` annotations from `ArcRegion` to `RoiBox` (defaults stay; the alias means either name works, but use `RoiBox` for clarity):

```python
    left_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=60, y=110, w=70, h=190))
    right_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=420, y=110, w=70, h=190))
```

- [ ] **Step 5: Add `screen_roi` field + bump `schema_version`** — in `SystemCameraConfig`, change `schema_version: int = 5` to `schema_version: int = 6`, and add the field (place it just before `auto_trigger`):

```python
    screen_roi: RoiBox = Field(
        default_factory=lambda: RoiBox(x=60, y=75, w=196, h=147),
        description="ROI that zooms preview/recording/detection onto just the Aurora screen. "
        "ref_w/ref_h = the resolution it was calibrated at (runtime rescale is then identity). "
        "Re-derived by scripts/calibration/system_camera/calibrate_view.py. Default = legacy "
        "AURORA_SCREEN_ROI",
    )
```

- [ ] **Step 6: Add `screen_roi` to the data YAML + bump version** — in `src/arm101_hand/data/system_camera_config.yaml`, change `schema_version: 5` to `schema_version: 6`, and add a `screen_roi` block immediately above the `# --- Automated trigger ...` separator line (do NOT touch `width`/`height`/`still_*`/`stable_seconds`):

```yaml
# Screen ROI -- the crop that zooms the preview/recording/detection onto just the Aurora screen.
# Re-derived by scripts/calibration/system_camera/calibrate_view.py; ref_w/ref_h = the resolution it
# was calibrated at, so the runtime rescale is identity. Default below = the legacy AURORA_SCREEN_ROI
# (640x480 ref). Run the calibration after any resolution OR lighting change.
screen_roi: {x: 60, y: 75, w: 196, h: 147, ref_w: 640, ref_h: 480}
```

- [ ] **Step 7: Run the full config test file**

Run: `uv run pytest tests/unit/test_system_camera_config.py -v`
Expected: all PASS **except** the pre-existing `test_data_yaml_loads` if the working tree carries the operator's `stable_seconds`/`still_*` WIP. In a clean worktree off HEAD it is all PASS. If only `test_data_yaml_loads` fails on `stable_seconds==2.0` or `still_*`, that is the WIP — leave it; do not "fix" it here.

- [ ] **Step 8: Lint + type-check + commit**

```bash
uv run ruff format src/arm101_hand/config/system_camera_config.py tests/unit/test_system_camera_config.py
uv run ruff check src tests
uv run mypy src
git add src/arm101_hand/config/system_camera_config.py src/arm101_hand/data/system_camera_config.yaml tests/unit/test_system_camera_config.py
git commit -m "feat(config): add screen_roi + rename ArcRegion->RoiBox (schema v6)"
```

---

### Task 2: `roi_from_region` converter

**Files:**
- Modify: `src/arm101_hand/system_camera/roi.py`
- Modify: `src/arm101_hand/system_camera/arc_detector.py:35-36`
- Modify: `src/arm101_hand/system_camera/__init__.py`
- Test: `tests/unit/test_roi.py`

**Interfaces:**
- Consumes: a region object with `x, y, w, h, ref_w, ref_h` int attrs (e.g. `RoiBox` from Task 1).
- Produces: `roi_from_region(region) -> Roi` (exported from `arm101_hand.system_camera`).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_roi.py`:

```python
def test_roi_from_region_builds_roi():
    from arm101_hand.config.system_camera_config import RoiBox
    from arm101_hand.system_camera import roi_from_region

    r = roi_from_region(RoiBox(x=10, y=20, w=30, h=40, ref_w=1600, ref_h=1200))
    assert (r.x, r.y, r.w, r.h, r.ref_w, r.ref_h) == (10, 20, 30, 40, 1600, 1200)
    assert isinstance(r, Roi)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_roi.py::test_roi_from_region_builds_roi -v`
Expected: FAIL (`ImportError: cannot import name 'roi_from_region'`).

- [ ] **Step 3: Add `roi_from_region` to `roi.py`** — keep the module config-free via a `Protocol`. Add near the top imports and after the `Roi` class:

```python
from typing import Protocol


class _RegionLike(Protocol):
    """Structural type for a config RoiBox/ArcRegion (avoids importing the pydantic model here)."""

    x: int
    y: int
    w: int
    h: int
    ref_w: int
    ref_h: int


def roi_from_region(region: _RegionLike) -> Roi:
    """Build a :class:`Roi` from a config ``RoiBox``/``ArcRegion`` (or any x/y/w/h/ref_w/ref_h obj)."""
    return Roi(x=region.x, y=region.y, w=region.w, h=region.h, ref_w=region.ref_w, ref_h=region.ref_h)
```

(Place the `from typing import Protocol` with the existing `from __future__ import annotations` / `from dataclasses import dataclass` import block.)

- [ ] **Step 4: Delegate `arc_detector._region_to_roi`** — in `src/arm101_hand/system_camera/arc_detector.py`, change the import line `from .roi import Roi` to `from .roi import Roi, roi_from_region` and replace the helper body:

```python
def _region_to_roi(region: ArcRegion) -> Roi:
    return roi_from_region(region)
```

- [ ] **Step 5: Export from `__init__.py`** — in `src/arm101_hand/system_camera/__init__.py`, change `from .roi import AURORA_SCREEN_ROI, Roi` to `from .roi import AURORA_SCREEN_ROI, Roi, roi_from_region` and add `"roi_from_region",` to `__all__` (keep it alphabetically near `"resolution_mismatch_warning"`).

- [ ] **Step 6: Run tests + type-check**

Run: `uv run pytest tests/unit/test_roi.py -v && uv run mypy src`
Expected: PASS.

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/roi.py src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/__init__.py tests/unit/test_roi.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/roi.py src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/__init__.py tests/unit/test_roi.py
git commit -m "feat(system_camera): add roi_from_region converter (config-free)"
```

---

### Task 3: Wire consumers to `cfg.screen_roi`

**Files:**
- Modify: `scripts/demos/grab_trigger_capture.py`, `scripts/demos/grab_trigger_capture_analysis.py`, `scripts/demos/grab_auto_trigger_analysis.py`
- Modify: `scripts/diagnostics/system_camera/usb_camera_roi_preview.py`, `scripts/diagnostics/system_camera/usb_camera_focus_probe.py`
- Test: `tests/unit/test_roi.py`

**Interfaces:**
- Consumes: `roi_from_region` (Task 2), `cfg.screen_roi` (Task 1).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_roi.py`:

```python
def test_screen_roi_builds_roi_from_data_config():
    from pathlib import Path

    from arm101_hand.config import load_system_camera_config
    from arm101_hand.system_camera import roi_from_region

    data = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
    r = roi_from_region(load_system_camera_config(data).screen_roi)
    assert isinstance(r, Roi) and r.w >= 1 and r.h >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_roi.py::test_screen_roi_builds_roi_from_data_config -v`
Expected: PASS already (the config + converter exist) — this test guards the wiring contract. If it fails, Task 1/2 are incomplete. (No production code is gated by this test; it documents the seam the scripts use.)

- [ ] **Step 3: Update `grab_auto_trigger_analysis.py`** — it already loads `scfg` and imports `AURORA_SCREEN_ROI`. Change the import line `from arm101_hand.system_camera import AURORA_SCREEN_ROI, WebcamPreview, auto_trigger, detect` to `from arm101_hand.system_camera import WebcamPreview, auto_trigger, detect, roi_from_region`. In `_start_preview(scfg)` (around line 174) build the ROI and use it:

```python
    screen_roi = roi_from_region(scfg.screen_roi)
    preview = WebcamPreview(
        index=scfg.camera_index,
        window_title=scfg.window_title,
        record_dir=_REPO_ROOT / scfg.record_dir,
        fps=scfg.fps,
        backend=scfg.backend,
        roi=screen_roi,
        fourcc=scfg.fourcc,
        width=scfg.width,
        height=scfg.height,
        autofocus=scfg.autofocus,
        focus=scfg.focus,
    )
```

and replace the two `AURORA_SCREEN_ROI.w`/`.h` references in the print (around line 196) with `screen_roi.w`/`screen_roi.h`.

- [ ] **Step 4: Update the other four scripts the same way.** In each, replace the `AURORA_SCREEN_ROI` import with `roi_from_region`, load the system-camera config (each already does, or add `from arm101_hand.config import load_system_camera_config` + load), build `screen_roi = roi_from_region(cfg.screen_roi)`, and pass `roi=screen_roi` / use `screen_roi.w`/`.h` wherever `AURORA_SCREEN_ROI` was used:
  - `scripts/demos/grab_trigger_capture.py`
  - `scripts/demos/grab_trigger_capture_analysis.py`
  - `scripts/diagnostics/system_camera/usb_camera_roi_preview.py` — this defaults its editable `_ROI` to `AURORA_SCREEN_ROI`; default it to `roi_from_region(cfg.screen_roi)` instead (so the diagnostic validates the calibrated value).
  - `scripts/diagnostics/system_camera/usb_camera_focus_probe.py`

  Grep to confirm none remain: `git grep -n AURORA_SCREEN_ROI scripts/`. Expect zero hits in `scripts/` after this step (the constant survives only in `roi.py`, `__init__.py`, and tests).

- [ ] **Step 5: Smoke-compile all five scripts + lint + run roi tests**

```bash
uv run python -m py_compile scripts/demos/grab_trigger_capture.py scripts/demos/grab_trigger_capture_analysis.py scripts/demos/grab_auto_trigger_analysis.py scripts/diagnostics/system_camera/usb_camera_roi_preview.py scripts/diagnostics/system_camera/usb_camera_focus_probe.py
uv run ruff check scripts
uv run pytest tests/unit/test_roi.py -v
```
Expected: compile clean, ruff clean, tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/demos/grab_trigger_capture.py scripts/demos/grab_trigger_capture_analysis.py scripts/demos/grab_auto_trigger_analysis.py scripts/diagnostics/system_camera/usb_camera_roi_preview.py scripts/diagnostics/system_camera/usb_camera_focus_probe.py tests/unit/test_roi.py
git commit -m "refactor(system_camera): read screen_roi from config, not the roi.py constant"
```

---

### Task 4: `calibration.py` — screen detection

**Files:**
- Create: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py` (create)

**Interfaces:**
- Consumes: `RoiBox`, `HsvBand` from `config.system_camera_config`.
- Produces: `detect_screen_rect(white_bgr, *, top_n=3) -> list[tuple[int,int,int,int]]`; `to_roi_candidate(bbox, frame_w, frame_h, *, target_aspect=4/3) -> tuple[int,int,int,int]`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_system_camera_calibration.py`:

```python
import cv2
import numpy as np
import pytest

from arm101_hand.system_camera.calibration import detect_screen_rect, to_roi_candidate


def test_detect_screen_rect_ranks_screen_above_distractor():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (100, 80), (340, 260), (255, 255, 255), -1)  # the screen (240x180)
    cv2.rectangle(img, (500, 400), (560, 440), (255, 255, 255), -1)  # small bright distractor
    rects = detect_screen_rect(img)
    assert len(rects) >= 1
    x, y, w, h = rects[0]
    assert 90 <= x <= 110 and 70 <= y <= 90  # the screen, not the distractor
    assert w > h  # landscape


def test_detect_screen_rect_returns_at_most_top_n():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(5):
        cv2.rectangle(img, (10 + i * 110, 10), (90 + i * 110, 120), (255, 255, 255), -1)
    assert len(detect_screen_rect(img, top_n=3)) <= 3


def test_to_roi_candidate_normalizes_to_4_3_within_bounds():
    x, y, w, h = to_roi_candidate((100, 100, 200, 100), 640, 480)  # 2:1 -> expand height
    assert abs((w / h) - 4 / 3) < 0.05
    assert x >= 0 and y >= 0 and x + w <= 640 and y + h <= 480
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -v`
Expected: FAIL (`ModuleNotFoundError: ...calibration`).

- [ ] **Step 3: Create `calibration.py` with the screen-detection functions:**

```python
"""Pure detection helpers for the system-camera view calibration (device layer).

Re-derives the Aurora screen ROI, the two alignment-arc regions, and the red/green HSV bands
from three sample frames (white startup screen, red arcs, green arcs). Pure numpy + cv2 imgproc
-- no HighGUI window; the one I/O function is ``write_calibration_values`` (ruamel round-trip).
Synthetic-numpy unit-testable, mirroring ``arc_detector.py``. The interactive capture/confirm
shell lives in ``scripts/calibration/system_camera/calibrate_view.py``.

Rectangle detection adapts references/computer-vision/opencv/samples/python/squares.py
(threshold -> findContours -> approxPolyDP -> area/convexity filter). Plain opencv-python only.
"""

from __future__ import annotations

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import HsvBand

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _screen_likeness(contour: np.ndarray, frame_area: int) -> float:
    """Score a bright blob on how screen-like it is: solid, rectangular, sizeable, landscape."""
    area = cv2.contourArea(contour)
    if area <= 0:
        return 0.0
    x, y, w, h = cv2.boundingRect(contour)
    rect_area = w * h
    fill = area / rect_area if rect_area else 0.0
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    corner = 1.0 if len(approx) == 4 else 0.6 if len(approx) <= 6 else 0.25
    size = min(area / frame_area / 0.10, 1.0)  # rewards >= ~10% of the frame
    aspect = w / h if h else 0.0
    aspect_score = 1.0 if 1.0 <= aspect <= 2.2 else 0.3  # the Aurora screen is landscape-ish
    return fill * corner * size * aspect_score


def detect_screen_rect(white_bgr: np.ndarray, *, top_n: int = 3) -> list[tuple[int, int, int, int]]:
    """Ranked candidate ``(x, y, w, h)`` bounding boxes of the bright screen, best first.

    Otsu-thresholds the frame, closes gaps (logo/knob/text), finds external contours, and ranks
    them by :func:`_screen_likeness`. Returns up to ``top_n`` boxes with a positive score.
    """
    gray = cv2.cvtColor(white_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fh, fw = white_bgr.shape[:2]
    frame_area = fw * fh
    scored = sorted(
        ((_screen_likeness(c, frame_area), cv2.boundingRect(c)) for c in contours),
        key=lambda t: t[0],
        reverse=True,
    )
    return [bbox for score, bbox in scored if score > 0.0][:top_n]


def to_roi_candidate(
    bbox: tuple[int, int, int, int], frame_w: int, frame_h: int, *, target_aspect: float = 4 / 3
) -> tuple[int, int, int, int]:
    """Expand ``bbox`` about its centre to ``target_aspect`` (never shrinks -> no content lost),
    then clamp inside the frame. Distortion-free because the crop is later resized to a same-aspect
    reference."""
    x, y, w, h = bbox
    cx, cy = x + w / 2.0, y + h / 2.0
    fw, fh = float(w), float(h)
    if fw / fh < target_aspect:
        fw = target_aspect * fh
    else:
        fh = fw / target_aspect
    nx = int(round(cx - fw / 2.0))
    ny = int(round(cy - fh / 2.0))
    nw = int(round(fw))
    nh = int(round(fh))
    nx = max(0, min(nx, frame_w - 1))
    ny = max(0, min(ny, frame_h - 1))
    nw = max(1, min(nw, frame_w - nx))
    nh = max(1, min(nh, frame_h - ny))
    return nx, ny, nw, nh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src tests
uv run mypy src
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(calibration): screen-rectangle detection + 4:3 ROI candidate"
```

---

### Task 5: `calibration.py` — arc regions, colour bands, threshold

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: `HsvBand`, `RoiBox`; the module-private `_RED_PRIOR`/`_GREEN_PRIOR`.
- Produces: `detect_arc_regions(roi_ref_bgr, *, red_prior=_RED_PRIOR, pad=4) -> tuple[RoiBox, RoiBox]`; `sample_hsv_band(region_bgr, color, *, lo_pct=5, hi_pct=95) -> list[HsvBand]`; `suggest_coverage_threshold(green_cov_on_green, green_cov_on_red, *, floor=0.02) -> float`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_system_camera_calibration.py`:

```python
from arm101_hand.system_camera.calibration import (
    detect_arc_regions,
    sample_hsv_band,
    suggest_coverage_threshold,
)


def _red() -> tuple[int, int, int]:
    return (0, 0, 255)  # BGR red -> HSV hue 0


def test_detect_arc_regions_finds_left_and_right():
    roi = np.zeros((480, 640, 3), dtype=np.uint8)
    roi[200:280, 40:100] = _red()  # left arc
    roi[200:280, 540:600] = _red()  # right arc
    left, right = detect_arc_regions(roi)
    assert left.x < 320 <= right.x
    assert left.w >= 1 and right.h >= 1


def test_detect_arc_regions_raises_when_a_half_is_empty():
    roi = np.zeros((480, 640, 3), dtype=np.uint8)
    roi[200:280, 40:100] = _red()  # only the left half has red
    with pytest.raises(ValueError):
        detect_arc_regions(roi)


def test_sample_hsv_band_green_brackets_the_hue():
    region = np.zeros((40, 40, 3), dtype=np.uint8)
    region[:] = (0, 255, 0)  # BGR green -> HSV hue 60
    bands = sample_hsv_band(region, "green")
    assert len(bands) == 1
    assert bands[0].h_lo <= 60 <= bands[0].h_hi


def test_sample_hsv_band_red_splits_on_hue_wrap():
    hsv = np.zeros((20, 20, 3), dtype=np.uint8)
    hsv[:, :10] = (2, 200, 200)  # near hue 0
    hsv[:, 10:] = (178, 200, 200)  # near hue 180
    region = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    bands = sample_hsv_band(region, "red")
    assert len(bands) == 2


def test_suggest_coverage_threshold_midpoint_and_floor():
    assert suggest_coverage_threshold(0.20, 0.0) == 0.10
    assert suggest_coverage_threshold(0.02, 0.0) == 0.02  # midpoint 0.01 floored to 0.02
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "arc_regions or hsv_band or coverage_threshold" -v`
Expected: FAIL (`ImportError`/`cannot import name`).

- [ ] **Step 3: Add the priors, helpers, and functions** to `src/arm101_hand/system_camera/calibration.py` (append after `to_roi_candidate`):

```python
# Broad colour priors (the schema defaults) used to FIND candidate pixels before percentile
# sampling tightens the band. Red wraps hue 0/180 -> two prior bands.
_RED_PRIOR: list[HsvBand] = [
    HsvBand(h_lo=0, s_lo=80, v_lo=80, h_hi=10, s_hi=255, v_hi=255),
    HsvBand(h_lo=170, s_lo=80, v_lo=80, h_hi=180, s_hi=255, v_hi=255),
]
_GREEN_PRIOR: list[HsvBand] = [HsvBand(h_lo=35, s_lo=40, v_lo=50, h_hi=95, s_hi=255, v_hi=255)]


def _prior_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR the inRange masks for every prior band, then MORPH_OPEN(3) to despeckle."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)


def _bbox_of_nonzero(half: np.ndarray, pad: int, full_w: int, full_h: int, x_off: int) -> "RoiBox":
    ys, xs = np.nonzero(half)
    if xs.size == 0:
        raise ValueError("no matching pixels in this half of the ROI")
    x0, x1 = int(xs.min()) + x_off, int(xs.max()) + x_off
    y0, y1 = int(ys.min()), int(ys.max())
    x = max(0, x0 - pad)
    y = max(0, y0 - pad)
    w = min(full_w - x, (x1 - x0) + 1 + 2 * pad)
    h = min(full_h - y, (y1 - y0) + 1 + 2 * pad)
    return RoiBox(x=x, y=y, w=w, h=h, ref_w=full_w, ref_h=full_h)


def detect_arc_regions(
    roi_ref_bgr: np.ndarray, *, red_prior: list[HsvBand] | None = None, pad: int = 4
) -> tuple["RoiBox", "RoiBox"]:
    """``(left_arc, right_arc)`` regions found from red pixels in the left/right halves of the ROI.

    ``roi_ref_bgr`` is the chosen ROI crop resized to the detection reference. Raises ``ValueError``
    if either half has no red pixels (caller offers redo / manual selectROI)."""
    prior = red_prior if red_prior is not None else _RED_PRIOR
    hsv = cv2.cvtColor(roi_ref_bgr, cv2.COLOR_BGR2HSV)
    mask = _prior_mask(hsv, prior)
    h, w = mask.shape
    mid = w // 2
    left = _bbox_of_nonzero(mask[:, :mid], pad, w, h, x_off=0)
    right = _bbox_of_nonzero(mask[:, mid:], pad, w, h, x_off=mid)
    return left, right


def sample_hsv_band(
    region_bgr: np.ndarray, color: str, *, lo_pct: float = 5, hi_pct: float = 95
) -> list[HsvBand]:
    """Bracket the matched pixels' HSV percentiles into 1 band (green) or up to 2 (red hue-wrap).

    ``color`` is ``"red"`` or ``"green"``. Raises ``ValueError`` if no pixels match the prior."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    prior = _RED_PRIOR if color == "red" else _GREEN_PRIOR
    pts = hsv[_prior_mask(hsv, prior) > 0]
    if pts.size == 0:
        raise ValueError(f"no {color} pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    if color == "green":
        h_lo = int(np.percentile(hue, lo_pct))
        h_hi = int(np.percentile(hue, hi_pct))
        return [HsvBand(h_lo=h_lo, s_lo=s_lo, v_lo=v_lo, h_hi=h_hi, s_hi=255, v_hi=255)]
    bands: list[HsvBand] = []
    near_zero = hue[hue <= 90]
    near_180 = hue[hue > 90]
    if near_zero.size:
        bands.append(
            HsvBand(h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255)
        )
    if near_180.size:
        bands.append(
            HsvBand(h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255)
        )
    return bands


def suggest_coverage_threshold(
    green_cov_on_green: float, green_cov_on_red: float, *, floor: float = 0.02
) -> float:
    """Midpoint between the green-frame (high) and red-frame (low) green coverage, floored."""
    return round(max((green_cov_on_green + green_cov_on_red) / 2.0, floor), 4)
```

Then add `RoiBox` to the config import at the top: change `from arm101_hand.config.system_camera_config import HsvBand` to `from arm101_hand.config.system_camera_config import HsvBand, RoiBox` and drop the string quotes on the `RoiBox` annotations (they were quoted only to read top-to-bottom; with the import present use bare `RoiBox`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint + type-check + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src tests
uv run mypy src
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(calibration): arc-region + HSV-band sampling + coverage-threshold suggestion"
```

---

### Task 6: ruamel.yaml writer + dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: `RoiBox`, `HsvBand`, `SystemCameraConfig`, `load_system_camera_config`.
- Produces: `write_calibration_values(config_path, *, screen_roi, left_arc, right_arc, red_bands, green_bands, coverage_threshold) -> None`.

- [ ] **Step 1: Add the dependency**

```bash
uv add "ruamel.yaml>=0.18"
```
(Updates `pyproject.toml` + `uv.lock`. Confirm `ruamel.yaml` is listed under `[project] dependencies`.)

- [ ] **Step 2: Write the failing test** — append to `tests/unit/test_system_camera_calibration.py`:

```python
from pathlib import Path

from arm101_hand.config import load_system_camera_config
from arm101_hand.config.system_camera_config import HsvBand, RoiBox
from arm101_hand.system_camera.calibration import write_calibration_values

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


def test_write_calibration_preserves_comments_and_updates(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")

    write_calibration_values(
        dst,
        screen_roi=RoiBox(x=150, y=188, w=490, h=368, ref_w=1600, ref_h=1200),
        left_arc=RoiBox(x=50, y=100, w=80, h=200),
        right_arc=RoiBox(x=410, y=100, w=80, h=200),
        red_bands=[HsvBand(h_lo=0, s_lo=90, v_lo=90, h_hi=8, s_hi=255, v_hi=255)],
        green_bands=[HsvBand(h_lo=45, s_lo=60, v_lo=70, h_hi=85, s_hi=255, v_hi=255)],
        coverage_threshold=0.09,
    )

    text = dst.read_text(encoding="utf-8")
    assert "Automated trigger" in text  # a block comment survived the round-trip
    assert dst.with_suffix(".yaml.bak").exists()  # backup written

    cfg = load_system_camera_config(dst)
    assert (cfg.screen_roi.x, cfg.screen_roi.ref_w) == (150, 1600)
    assert (cfg.auto_trigger.left_arc.x, cfg.auto_trigger.right_arc.x) == (50, 410)
    assert cfg.auto_trigger.coverage_threshold == 0.09
    assert len(cfg.auto_trigger.red_bands) == 1 and cfg.auto_trigger.green_bands[0].h_lo == 45


def test_write_calibration_rejects_invalid_without_writing(tmp_path):
    import pytest

    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    before = dst.read_text(encoding="utf-8")
    with pytest.raises(Exception):
        write_calibration_values(
            dst,
            screen_roi=RoiBox(x=0, y=0, w=1, h=1),
            left_arc=RoiBox(x=0, y=0, w=1, h=1),
            right_arc=RoiBox(x=0, y=0, w=1, h=1),
            red_bands=[],  # empty -> invalid (auto-trigger needs >=1 red band for classification)
            green_bands=[],  # empty -> invalid
            coverage_threshold=5.0,  # out of [0,1] -> pydantic ValidationError
        )
    assert dst.read_text(encoding="utf-8") == before  # original untouched on failure
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k write_calibration -v`
Expected: FAIL (`cannot import name 'write_calibration_values'`).

- [ ] **Step 4: Implement the writer** — append to `src/arm101_hand/system_camera/calibration.py`. Add imports at the top: `import io`, `from pathlib import Path`, `import yaml`, `from ruamel.yaml import YAML`, `from ruamel.yaml.comments import CommentedMap`, and extend the config import to `from arm101_hand.config.system_camera_config import HsvBand, RoiBox, SystemCameraConfig`.

```python
def _flow_map(d: dict[str, int]) -> CommentedMap:
    """A ruamel mapping rendered inline ``{a: 1, b: 2}`` to match the file's arc/band style."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map({"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h})


def _hsv_map(b: HsvBand) -> CommentedMap:
    return _flow_map(
        {"h_lo": b.h_lo, "s_lo": b.s_lo, "v_lo": b.v_lo, "h_hi": b.h_hi, "s_hi": b.s_hi, "v_hi": b.v_hi}
    )


def write_calibration_values(
    config_path: Path,
    *,
    screen_roi: RoiBox,
    left_arc: RoiBox,
    right_arc: RoiBox,
    red_bands: list[HsvBand],
    green_bands: list[HsvBand],
    coverage_threshold: float,
) -> None:
    """Round-trip ``system_camera_config.yaml`` (preserving comments), updating screen_roi + the
    auto_trigger regions/bands/threshold. Validates the result via pydantic and writes a ``.bak``
    copy BEFORE touching the original; raises (without writing) if validation fails."""
    yaml_rt = YAML()  # round-trip mode preserves comments + key order
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))

    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["green_bands"] = [_hsv_map(b) for b in green_bands]
    at["coverage_threshold"] = float(coverage_threshold)

    # Validate the would-be file via pydantic BEFORE writing (plain safe_load of the dumped text).
    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))

    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
```

Note: the `red_bands=[]`/`green_bands=[]` invalid-case test relies on `AutoTriggerConfig` having no validator that *rejects* empty lists today — but `coverage_threshold=5.0` is out of the `le=1.0` bound, so `model_validate` raises a `ValidationError` regardless. That guarantees the "rejects invalid" test passes via the threshold bound.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -v`
Expected: PASS (all, including both writer tests).

- [ ] **Step 6: Lint + type-check + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src tests
uv run mypy src
git add pyproject.toml uv.lock src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(calibration): ruamel round-trip writer (comment-preserving) + ruamel dep"
```

---

### Task 7: `calibrate_view.py` interactive shell

**Files:**
- Create: `scripts/calibration/system_camera/calibrate_view.py`
- Create: `scripts/calibration/system_camera/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–6 (`load_system_camera_config`, `open_capture`, `imshow_fit`, `roi_from_region`, `Roi`, `detect`, `AutoTriggerConfig`, and all of `calibration.py`).
- Produces: a runnable console script (no unit tests — UI; verified by lint/compile/`--help` + a bench checklist).

- [ ] **Step 1: Create the script.** Write `scripts/calibration/system_camera/calibrate_view.py`:

```python
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
from arm101_hand.system_camera.calibration import (  # noqa: E402
    detect_arc_regions,
    detect_screen_rect,
    sample_hsv_band,
    suggest_coverage_threshold,
    to_roi_candidate,
    write_calibration_values,
)
from arm101_hand.system_camera.arc_detector import _band_mask, classify_band  # noqa: E402

_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_REF_W, _REF_H = 640, 480


def _crop_to_ref(frame: np.ndarray, roi: Roi) -> np.ndarray:
    """Crop ``frame`` to ``roi`` and resize to the 640x480 detection reference."""
    return cv2.resize(roi.crop(frame), (_REF_W, _REF_H), interpolation=cv2.INTER_AREA)


def _capture_frame(cap, title: str, overlay: Roi | None) -> np.ndarray | None:
    """Stream until SPACE (return the frame) or q/ESC (return None). Draws ``overlay`` if given."""
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                return None
            continue
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
            cv2.putText(panel, f"{label}: L={state.left} R={state.right} ready={state.ready}",
                        (8, 24), _FONT, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
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
        upd.left_arc = RoiBox(x=int(lr[0]), y=int(lr[1]), w=int(lr[2]), h=int(lr[3]), ref_w=_REF_W, ref_h=_REF_H)
    if rr[2] > 0 and rr[3] > 0:
        upd.right_arc = RoiBox(x=int(rr[0]), y=int(rr[1]), w=int(rr[2]), h=int(rr[3]), ref_w=_REF_W, ref_h=_REF_H)
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
    ap.add_argument("--backend", choices=("auto", "dshow"), default=None, help="cv2 backend (default: config)")
    ap.add_argument("--from-files", nargs=3, metavar=("WHITE", "RED", "GREEN"), default=None,
                    help="skip live capture; run detection on three saved frames")
    args = ap.parse_args()

    cfg = load_system_camera_config(_CONFIG_PATH)
    cap = None
    try:
        if args.from_files:
            white, red, green = _load_frames_from_files(args.from_files)
            if (white.shape[1], white.shape[0]) != (cfg.width or white.shape[1], cfg.height or white.shape[0]):
                print(f"WARNING: image size {white.shape[1]}x{white.shape[0]} != config "
                      f"{cfg.width}x{cfg.height}; calibrating for a different resolution.", file=sys.stderr)
            roi = None
            while roi is None:
                roi = _pick_roi(white)
                if roi is None:
                    print("Recapture not available with --from-files; pick a candidate or 'm'.")
        else:
            index = args.camera if args.camera is not None else cfg.camera_index
            backend = args.backend if args.backend is not None else cfg.backend
            print(f"Opening USB camera {index} ({backend}) ...")
            cap = open_capture(index, backend, fourcc=cfg.fourcc, width=cfg.width, height=cfg.height,
                               autofocus=cfg.autofocus, focus=cfg.focus)
            if not cap.isOpened():
                print(f"ERROR: could not open camera {index}. Try --camera N / --backend dshow.", file=sys.stderr)
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
                _region_crop(red_ref, right_arc), "red")
            green_bands = sample_hsv_band(_region_crop(green_ref, left_arc), "green") or sample_hsv_band(
                _region_crop(green_ref, right_arc), "green")
        except ValueError as e:
            print(f"ERROR: auto-detection failed ({e}). Re-run and frame the arcs inside the ROI.",
                  file=sys.stderr)
            return 1

        trial = AutoTriggerConfig(left_arc=left_arc, right_arc=right_arc, red_bands=red_bands,
                                  green_bands=green_bands)
        gcov_g = classify_band(_to_hsv_region(green_ref, left_arc), trial)[1]
        gcov_r = classify_band(_to_hsv_region(red_ref, left_arc), trial)[1]
        threshold = suggest_coverage_threshold(gcov_g, gcov_r)
        trial = trial.model_copy(update={"coverage_threshold": threshold})

        while True:
            action = _confirm(red_ref, green_ref, trial)
            if action == "y":
                screen_roi = RoiBox(x=roi.x, y=roi.y, w=roi.w, h=roi.h, ref_w=roi.ref_w, ref_h=roi.ref_h)
                write_calibration_values(
                    _CONFIG_PATH, screen_roi=screen_roi, left_arc=trial.left_arc, right_arc=trial.right_arc,
                    red_bands=trial.red_bands, green_bands=trial.green_bands,
                    coverage_threshold=trial.coverage_threshold)
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
```

- [ ] **Step 2: Create the README** — `scripts/calibration/system_camera/README.md`:

```markdown
# System-camera view calibration

Re-derive the Aurora screen ROI + alignment-arc regions + red/green HSV bands when the camera
**resolution** or **lighting** changes. Writes `src/arm101_hand/data/system_camera_config.yaml`
(`screen_roi` + the `auto_trigger` block); keeps a `.bak`.

```powershell
# Live guided capture (white startup screen -> red arcs -> green arcs):
uv run python scripts/calibration/system_camera/calibrate_view.py

# Or re-run detection on three already-saved frames:
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files white.jpg red.jpg green.jpg
```

Keys: `1`/`2`/`3` pick an ROI candidate · `m` drag manually · `r` recapture · SPACE capture ·
at confirm: `y` write · `e` re-tune arc boxes · `r` redo · `q` quit.
```

- [ ] **Step 3: Lint, compile, and check `--help`**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python -m py_compile scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
```
Expected: ruff clean, compiles, `--help` prints the usage and exits 0.

- [ ] **Step 4: Verify the `--from-files` path on the existing captures** (host-only, no camera; opens cv2 windows — operator runs at the bench):

```bash
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files \
  media_outputs/camera_captures/usb_cam_20260622_160602_364_1600x1200_startupscreen.jpg \
  media_outputs/camera_captures/usb_cam_20260622_162153_991_1600x1200_red.jpg \
  media_outputs/camera_captures/usb_cam_20260622_162210_025_1600x1200_green.jpg
```
Expected: ROI-candidate window shows boxes over the white screen; after a pick + confirm, the
RED panel reads `ready=False` and the GREEN panel reads `ready=True`; `y` writes (then `git checkout`
the config to undo this dry-run write, or keep it if it looks right).

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/system_camera/calibrate_view.py scripts/calibration/system_camera/README.md
git commit -m "feat(calibration): interactive calibrate_view shell (capture/confirm/write)"
```

---

### Task 8: Docs + memory + final CI

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/conventions/06-documentation-protocol.md` (DRY registry)
- Modify: memory files under the agent memory dir

- [ ] **Step 1: Refresh `CLAUDE.md`** — in §4 (Common workflows) add, under a new "System-camera view calibration" heading near the diagnostics block:

```
# System-camera view calibration (writes system_camera_config.yaml; run after a resolution/lighting change)
uv run python scripts/calibration/system_camera/calibrate_view.py            # live: white -> red -> green -> confirm -> write
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files WHITE RED GREEN  # detect on saved frames
```

In §3 (Directory tree), add `calibration.py` to the `system_camera/` description and note the new `scripts/calibration/system_camera/` dir. In §7 (Tech-debt), update the system-camera note: the screen ROI now lives in `system_camera_config.yaml` (`screen_roi`), re-derived by `calibrate_view.py`; arc regions/bands likewise calibratable.

- [ ] **Step 2: Refresh `README.md`** — add the calibration command alongside the other system-camera commands (mirror the CLAUDE.md lines, human-facing phrasing).

- [ ] **Step 3: Register in the DRY registry** — in `docs/conventions/06-documentation-protocol.md`, add a row pointing to `docs/superpowers/specs/2026-06-22-system-camera-view-calibration-design.md` + this plan (follow the existing arc-auto-trigger registry entry format).

- [ ] **Step 4: Update memory** — edit the agent memory:
  - Update `project_system_camera_2k_stream.md`: note the screen ROI + arc/colour calibration now lives in `system_camera_config.yaml`, re-derived by `scripts/calibration/system_camera/calibrate_view.py` after resolution/lighting changes; `AURORA_SCREEN_ROI` is now the fallback default.
  - Add a one-line pointer in `MEMORY.md`.

- [ ] **Step 5: Full CI gate**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```
Expected: all green. (If the worktree carries the operator's `stable_seconds`/`still_*` WIP, `test_data_yaml_loads` may fail on those pre-existing assertions only — run a clean worktree off HEAD to confirm a fully-green baseline, or `--deselect tests/unit/test_system_camera_config.py::test_data_yaml_loads` and note it.)

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md docs/conventions/06-documentation-protocol.md
git commit -m "docs: register system-camera view calibration + workflow"
```

---

## Bench verification (operator-run, after merge)

Not automatable (needs the camera + Aurora). After merging:
1. Run `calibrate_view.py` live; confirm the white-screen ROI candidates land on the Aurora screen, a pick locks, red/green captures auto-detect arcs, and the confirm screen shows RED=not-ready / GREEN=ready.
2. Accept the write; diff `system_camera_config.yaml` (screen_roi at `ref_w/ref_h = 1600/1200`, tightened bands, suggested threshold; comments intact; `.bak` present).
3. Run `grab_auto_trigger_analysis.py`; confirm the live arc detection + auto-fire now track correctly at the calibrated values.

---

## Self-Review

**Spec coverage:** §3 architecture → Tasks 4–7; §4 flow → Task 7; §5.1 screen detection → Task 4; §5.2–5.3 arcs/colour/threshold → Task 5; §5.4 interfaces → Tasks 4–6 (note: the spec's `build_roi_candidates(bbox)->3` is implemented as `to_roi_candidate(bbox)->1` mapped over the top-3 detected rects + padded to 3 in `_pick_roi`, which realises the "3 options" intent more robustly); §6 confirm/override → Task 7 (`_pick_roi`, `_confirm`, `_retune`); §7 config/schema/consumers → Tasks 1–3; §7.4 ruamel writer → Task 6; §8 error handling → Tasks 5 (ValueError) + 7 (guards); §9 testing → Tasks 1,2,4,5,6; §10 docs → Task 8; §11 future-port → noted in spec.

**Placeholder scan:** none — every code step has complete code; the one "minimal `_retune`" is a deliberate, documented YAGNI (selectROI geometry override; full trackbar tuning flagged as a bench enhancement, not a gap).

**Type consistency:** `RoiBox`/`ArcRegion` alias consistent (Task 1); `roi_from_region` signature consistent (Tasks 2,3,7); `detect_arc_regions(...) -> (RoiBox, RoiBox)`, `sample_hsv_band(...) -> list[HsvBand]`, `suggest_coverage_threshold(float,float)->float`, `write_calibration_values(...)` keyword args all match between Tasks 5/6 and the Task 7 call sites.
