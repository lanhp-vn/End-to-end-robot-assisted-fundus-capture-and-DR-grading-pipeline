# Arc Auto-Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-fire the robot Aurora capture when the on-screen alignment arcs turn green, via a new demo cloned from the DR-grading analysis demo.

**Architecture:** Two pure, host-tested modules in `system_camera` — `arc_detector` (BGR ROI frame → `AlignmentState` via HSV per-arc-band masking) and `auto_trigger` (a clock-injected lifecycle state machine). `WebcamPreview` gains a thread-safe frame snapshot + status overlay so the new demo's main loop can detect on the live ROI and drive the existing capture cycle.

**Tech Stack:** Python 3.12, OpenCV (`cv2`, full wheel), numpy, pydantic v2, pytest. Windows host (`msvcrt` keyboard).

**Spec:** `docs/superpowers/specs/2026-06-19-arc-auto-trigger-design.md`

## Global Constraints

- Config lives in `src/arm101_hand/data/system_camera_config.yaml`, hand-editable, **never written at runtime** (IL-5).
- `references/` is read-only (IL-2). No `pyproject.toml` dependency changes (cv2 + numpy already present).
- `yaml.safe_load` only; no `yaml.load`.
- The two existing demos (`grab_trigger_capture.py`, `grab_trigger_capture_analysis.py`) stay **untouched** — this work is additive.
- No surprise movements: the new demo starts in MANUAL; AUTO is an explicit toggle; quit releases torque (inherited from `run_grab_demo`).
- Verification gate (must pass before each commit): `uv run ruff format --check .` ; `uv run ruff check src tests scripts` ; `uv run mypy src` ; `uv run pytest -m 'not hardware' -q`.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass.
- HSV is OpenCV 8-bit: H 0–180, S/V 0–255. Red wraps hue 0/180 (two bands OR'd). Green hue band kept ≤ ~90 to avoid the screen's blue UI icons.

---

### Task 1: `auto_trigger` config block (schema v5)

**Files:**
- Modify: `src/arm101_hand/config/system_camera_config.py`
- Modify: `src/arm101_hand/data/system_camera_config.yaml`
- Test: `tests/unit/test_system_camera_config.py`

**Interfaces:**
- Produces: `HsvBand(h_lo,s_lo,v_lo,h_hi,s_hi,v_hi: int)`; `ArcRegion(x,y,w,h: int, ref_w=640, ref_h=480)`; `AutoTriggerConfig` (fields below); `SystemCameraConfig.auto_trigger: AutoTriggerConfig`, `SystemCameraConfig.schema_version == 5`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_system_camera_config.py`:

```python
def test_auto_trigger_defaults():
    at = SystemCameraConfig().auto_trigger
    assert at.stable_seconds == 1.0
    assert at.cooldown_seconds == 3.0
    assert at.detect_interval_s == 0.2
    assert at.require_red_between is True
    assert at.require_no_red is False
    assert at.coverage_threshold == 0.04
    assert len(at.red_bands) == 2  # red wraps hue 0/180
    assert len(at.green_bands) == 1
    assert (at.left_arc.ref_w, at.left_arc.ref_h) == (640, 480)


def test_auto_trigger_coverage_threshold_bounds():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"coverage_threshold": 1.5}})


def test_auto_trigger_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"bogus": 1}})
```

Also update the existing schema assertion in `test_resolution_fourcc_defaults` from `assert cfg.schema_version == 4` to:

```python
    assert cfg.schema_version == 5
```

And extend `test_data_yaml_loads` with:

```python
    at = cfg.auto_trigger
    assert at.left_arc.w == 70 and at.right_arc.w == 70
    assert len(at.red_bands) == 2 and len(at.green_bands) == 1
    assert at.stable_seconds == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_config.py -q`
Expected: FAIL — `AttributeError: 'SystemCameraConfig' object has no attribute 'auto_trigger'` and the schema assertion fails (4 ≠ 5).

- [ ] **Step 3: Add the models + field**

In `src/arm101_hand/config/system_camera_config.py`, add these models above `SystemCameraConfig`:

```python
class HsvBand(BaseModel):
    """An inclusive OpenCV HSV threshold band (8-bit: H 0-180, S/V 0-255)."""

    model_config = ConfigDict(extra="forbid")
    h_lo: int = Field(ge=0, le=180)
    s_lo: int = Field(ge=0, le=255)
    v_lo: int = Field(ge=0, le=255)
    h_hi: int = Field(ge=0, le=180)
    s_hi: int = Field(ge=0, le=255)
    v_hi: int = Field(ge=0, le=255)


class ArcRegion(BaseModel):
    """A pixel rectangle measured against a reference frame size (mirrors system_camera.Roi)."""

    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    ref_w: int = Field(default=640, ge=1)
    ref_h: int = Field(default=480, ge=1)


class AutoTriggerConfig(BaseModel):
    """Arc-based auto-trigger detection + lifecycle tuning (grab_auto_trigger_analysis demo)."""

    model_config = ConfigDict(extra="forbid")
    left_arc: ArcRegion = Field(default_factory=lambda: ArcRegion(x=60, y=110, w=70, h=190))
    right_arc: ArcRegion = Field(default_factory=lambda: ArcRegion(x=420, y=110, w=70, h=190))
    red_bands: list[HsvBand] = Field(
        default_factory=lambda: [
            HsvBand(h_lo=0, s_lo=80, v_lo=80, h_hi=10, s_hi=255, v_hi=255),
            HsvBand(h_lo=170, s_lo=80, v_lo=80, h_hi=180, s_hi=255, v_hi=255),
        ]
    )
    green_bands: list[HsvBand] = Field(
        default_factory=lambda: [HsvBand(h_lo=40, s_lo=40, v_lo=60, h_hi=90, s_hi=255, v_hi=255)]
    )
    coverage_threshold: float = Field(default=0.04, ge=0.0, le=1.0)
    morph_kernel: int = Field(default=3, ge=1)
    stable_seconds: float = Field(default=1.0, gt=0.0)
    cooldown_seconds: float = Field(default=3.0, ge=0.0)
    detect_interval_s: float = Field(default=0.2, gt=0.0)
    require_red_between: bool = True
    require_no_red: bool = False
```

Then bump the version and add the field on `SystemCameraConfig`:

```python
    schema_version: int = 5
```

and, after the `focus` field:

```python
    auto_trigger: AutoTriggerConfig = Field(default_factory=AutoTriggerConfig)
```

- [ ] **Step 4: Add the YAML block + bump the data schema version**

In `src/arm101_hand/data/system_camera_config.yaml`, change `schema_version: 4` to `schema_version: 5`, then append:

```yaml
# --- Automated trigger (grab_auto_trigger_analysis.py) -------------------------------------------
# Detect the Aurora's two on-screen alignment ARCS in the ROI; when they turn GREEN (aligned),
# auto-fire the capture cycle. HSV masking per arc-band (OpenCV H 0-180, S/V 0-255). The regions +
# HSV bands below are STARTING ESTIMATES -- tune them against the saved red/green ROI frames.
auto_trigger:
  left_arc:  {x: 60,  y: 110, w: 70, h: 190, ref_w: 640, ref_h: 480}   # left arc band in the ROI
  right_arc: {x: 420, y: 110, w: 70, h: 190, ref_w: 640, ref_h: 480}   # right arc band in the ROI
  red_bands:                       # red wraps hue 0/180 -> two bands OR'd
    - {h_lo: 0,   s_lo: 80, v_lo: 80, h_hi: 10,  s_hi: 255, v_hi: 255}
    - {h_lo: 170, s_lo: 80, v_lo: 80, h_hi: 180, s_hi: 255, v_hi: 255}
  green_bands:                     # single green band; keep h_hi <= ~90 so blue UI icons don't leak
    - {h_lo: 40, s_lo: 40, v_lo: 60, h_hi: 90, s_hi: 255, v_hi: 255}
  coverage_threshold: 0.04         # fraction of an arc band that must match a color to count
  morph_kernel: 3                  # MORPH_OPEN kernel (px) to despeckle the mask
  stable_seconds: 1.0              # green must hold this long before firing
  cooldown_seconds: 3.0            # min gap after a fire
  detect_interval_s: 0.2           # how often to sample the ROI in auto mode
  require_red_between: true        # after a fire, wait for arcs to go red again before re-arming
  require_no_red: false            # if true, "ready" also requires NO band is red (tighter)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_config.py -q`
Expected: PASS (all).

- [ ] **Step 6: Run the full gate + commit**

```bash
uv run ruff format --check . && uv run ruff check src tests scripts && uv run mypy src && uv run pytest -m 'not hardware' -q
git add src/arm101_hand/config/system_camera_config.py src/arm101_hand/data/system_camera_config.yaml tests/unit/test_system_camera_config.py
git commit -m "feat(config): auto_trigger config block (schema v5)"
```

---

### Task 2: `arc_detector` — HSV red/green alignment-arc classification

**Files:**
- Create: `src/arm101_hand/system_camera/arc_detector.py`
- Modify: `src/arm101_hand/system_camera/__init__.py`
- Test: `tests/unit/test_arc_detector.py`

**Interfaces:**
- Consumes: `AutoTriggerConfig`, `ArcRegion`, `HsvBand` (Task 1); `Roi` (`system_camera.roi`).
- Produces: `AlignmentState(left: str, right: str, ready: bool, left_green: float, left_red: float, right_green: float, right_red: float)`; `detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState`; `classify_band(hsv_band: np.ndarray, cfg: AutoTriggerConfig) -> tuple[str, float, float]`. Band labels are `"RED"|"GREEN"|"NONE"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_arc_detector.py`:

```python
import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import detect

_GREEN = (0, 255, 0)  # BGR
_RED = (0, 0, 255)
_WHITE = (255, 255, 255)


def _frame(left_bgr=None, right_bgr=None, *, fill=1.0):
    """640x480 black frame; fill the configured arc bands with the given BGR colour.
    ``fill`` is the fraction of each band's height to paint (from the top)."""
    cfg = AutoTriggerConfig()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for region, color in ((cfg.left_arc, left_bgr), (cfg.right_arc, right_bgr)):
        if color is None:
            continue
        rows = max(1, int(region.h * fill))
        frame[region.y : region.y + rows, region.x : region.x + region.w] = color
    return cfg, frame


def test_both_green_ready():
    cfg, frame = _frame(_GREEN, _GREEN)
    st = detect(frame, cfg)
    assert st.left == "GREEN" and st.right == "GREEN"
    assert st.ready is True


def test_both_red_not_ready():
    cfg, frame = _frame(_RED, _RED)
    st = detect(frame, cfg)
    assert st.left == "RED" and st.right == "RED"
    assert st.ready is False


def test_either_green_is_ready():
    cfg, frame = _frame(_RED, _GREEN)
    st = detect(frame, cfg)
    assert st.left == "RED" and st.right == "GREEN"
    assert st.ready is True  # either-arc-green rule


def test_require_no_red_blocks_mixed():
    cfg, frame = _frame(_RED, _GREEN)
    cfg = cfg.model_copy(update={"require_no_red": True})
    st = detect(frame, cfg)
    assert st.ready is False


def test_white_glare_is_none():
    cfg, frame = _frame(_WHITE, _WHITE)
    st = detect(frame, cfg)
    assert st.left == "NONE" and st.right == "NONE"
    assert st.ready is False


def test_below_coverage_threshold_is_none():
    cfg, frame = _frame(_GREEN, None, fill=0.02)  # ~2% < 4% threshold
    st = detect(frame, cfg)
    assert st.left == "NONE"


def test_above_coverage_threshold_is_green():
    cfg, frame = _frame(_GREEN, None, fill=0.07)  # ~7% > 4% threshold
    st = detect(frame, cfg)
    assert st.left == "GREEN"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_arc_detector.py -q`
Expected: FAIL — `ModuleNotFoundError: arm101_hand.system_camera.arc_detector`.

- [ ] **Step 3: Implement the detector**

Create `src/arm101_hand/system_camera/arc_detector.py`:

```python
"""Red/green alignment-arc detection for the arm-mounted USB observation camera (device layer).

The Optomed Aurora shows two arcs (left + right of its circular live view): RED when misaligned,
GREEN when at the correct working distance. We classify each arc by HSV colour coverage inside a
fixed sub-region of the ROI -- cheap, robust per-region masking (a deep-dive into the vendored
OpenCV refs confirmed shape detection like HoughCircles/fitEllipse is overkill for fixed-geometry
partial arcs). Pure functions (no cv2 window); the auto-trigger demo feeds frames from the preview.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import ArcRegion, AutoTriggerConfig, HsvBand

from .roi import Roi


@dataclass(frozen=True)
class AlignmentState:
    """Per-arc classification + the overall ready flag. Coverage fractions are for the HUD/debug."""

    left: str  # "RED" | "GREEN" | "NONE"
    right: str
    ready: bool
    left_green: float
    left_red: float
    right_green: float
    right_red: float


def _region_to_roi(region: ArcRegion) -> Roi:
    return Roi(x=region.x, y=region.y, w=region.w, h=region.h, ref_w=region.ref_w, ref_h=region.ref_h)


def _band_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR together the inRange masks for every HSV band (red needs two for the 0/180 wrap)."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    return mask


def _coverage(hsv_band: np.ndarray, bands: list[HsvBand], kernel: int) -> float:
    """Fraction of the band's pixels matching any of ``bands`` after a MORPH_OPEN despeckle."""
    mask = _band_mask(hsv_band, bands)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def classify_band(hsv_band: np.ndarray, cfg: AutoTriggerConfig) -> tuple[str, float, float]:
    """Classify one arc band as RED/GREEN/NONE; returns (label, green_fraction, red_fraction)."""
    green = _coverage(hsv_band, cfg.green_bands, cfg.morph_kernel)
    red = _coverage(hsv_band, cfg.red_bands, cfg.morph_kernel)
    if green >= cfg.coverage_threshold and green >= red:
        return "GREEN", green, red
    if red >= cfg.coverage_threshold:
        return "RED", green, red
    return "NONE", green, red


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs in an ROI frame; ready when EITHER arc is green (optionally + no red)."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    left, lg, lr = classify_band(_region_to_roi(cfg.left_arc).crop(hsv), cfg)
    right, rg, rr = classify_band(_region_to_roi(cfg.right_arc).crop(hsv), cfg)
    green_present = left == "GREEN" or right == "GREEN"
    red_present = left == "RED" or right == "RED"
    ready = green_present and (not cfg.require_no_red or not red_present)
    return AlignmentState(left, right, ready, lg, lr, rg, rr)
```

- [ ] **Step 4: Export from the package**

In `src/arm101_hand/system_camera/__init__.py`, add after the `.focus` import line:

```python
from .arc_detector import AlignmentState, classify_band, detect
```

and add `"AlignmentState"`, `"classify_band"`, `"detect"` to `__all__` (keep it sorted).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_arc_detector.py -q`
Expected: PASS (all 7).

- [ ] **Step 6: Run the full gate + commit**

```bash
uv run ruff format --check . && uv run ruff check src tests scripts && uv run mypy src && uv run pytest -m 'not hardware' -q
git add src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/__init__.py tests/unit/test_arc_detector.py
git commit -m "feat(system_camera): arc_detector -- HSV red/green alignment-arc classification"
```

---

### Task 3: `auto_trigger` — lifecycle state machine

**Files:**
- Create: `src/arm101_hand/system_camera/auto_trigger.py`
- Modify: `src/arm101_hand/system_camera/__init__.py`
- Test: `tests/unit/test_auto_trigger.py`

**Interfaces:**
- Consumes: `AutoTriggerConfig` (Task 1), `AlignmentState` (Task 2).
- Produces: phase constants `WAIT_GREEN, STABILIZING, COOLDOWN, WAIT_RED`; `AutoTriggerState(phase: str, green_since: float | None, fired_at: float | None)`; `arm() -> AutoTriggerState`; `update(state: AutoTriggerState, alignment: AlignmentState, now: float, cfg: AutoTriggerConfig) -> tuple[AutoTriggerState, bool]` (second element is a one-shot `should_fire`).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_auto_trigger.py`:

```python
from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import AlignmentState
from arm101_hand.system_camera.auto_trigger import (
    COOLDOWN,
    STABILIZING,
    WAIT_GREEN,
    WAIT_RED,
    arm,
    update,
)

_CFG = AutoTriggerConfig()  # stable 1.0s, cooldown 3.0s, require_red_between True


def _al(ready, left="NONE", right="NONE"):
    return AlignmentState(left, right, ready, 0.0, 0.0, 0.0, 0.0)


def test_green_then_stable_fires_once():
    s = arm()
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 100.0, _CFG)
    assert s.phase == STABILIZING and fire is False
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 100.5, _CFG)
    assert fire is False  # 0.5s < stable_seconds
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, _CFG)
    assert fire is True and s.phase == COOLDOWN


def test_green_drops_before_stable_resets():
    s = arm()
    s, _ = update(s, _al(True, "GREEN"), 100.0, _CFG)
    s, fire = update(s, _al(False), 100.5, _CFG)
    assert s.phase == WAIT_GREEN and fire is False


def test_cooldown_then_wait_red_then_rearm():
    s = arm()
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 100.0, _CFG)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, _CFG)
    assert fire is True and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 102.0, _CFG)  # within cooldown
    assert fire is False and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 104.0, _CFG)  # cooldown elapsed
    assert s.phase == WAIT_RED and fire is False
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 105.0, _CFG)  # still green, can't fire
    assert s.phase == WAIT_RED and fire is False
    s, _ = update(s, _al(False, "RED", "RED"), 106.0, _CFG)  # red seen -> re-arm
    assert s.phase == WAIT_GREEN
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 106.0, _CFG)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 107.0, _CFG)
    assert fire is True  # fires again on the next patient


def test_require_red_between_false_returns_to_wait_green():
    cfg = _CFG.model_copy(update={"require_red_between": False})
    s = arm()
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 100.0, cfg)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, cfg)
    assert fire is True and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 104.0, cfg)
    assert s.phase == WAIT_GREEN and fire is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_auto_trigger.py -q`
Expected: FAIL — `ModuleNotFoundError: arm101_hand.system_camera.auto_trigger`.

- [ ] **Step 3: Implement the state machine**

Create `src/arm101_hand/system_camera/auto_trigger.py`:

```python
"""Auto-trigger lifecycle for the arc-driven Aurora capture (device layer, pure + clock-injected).

Drives the transition: wait for the alignment arcs to go GREEN -> require it stable for
``stable_seconds`` -> fire ONE capture -> cooldown -> (optionally) wait for the arcs to go RED again
before re-arming. ``update`` is pure: it takes the current state, the latest AlignmentState, a
monotonic ``now``, and the config, and returns the next state plus a one-shot ``should_fire``.
No cv2, no time calls -- the caller injects ``now`` (so it is fully unit-testable).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .arc_detector import AlignmentState

WAIT_GREEN = "WAIT_GREEN"
STABILIZING = "STABILIZING"
COOLDOWN = "COOLDOWN"
WAIT_RED = "WAIT_RED"


@dataclass(frozen=True)
class AutoTriggerState:
    phase: str = WAIT_GREEN
    green_since: float | None = None
    fired_at: float | None = None


def arm() -> AutoTriggerState:
    """Fresh state, ready to watch for green (called when the operator enters AUTO mode)."""
    return AutoTriggerState()


def update(
    state: AutoTriggerState,
    alignment: AlignmentState,
    now: float,
    cfg: AutoTriggerConfig,
) -> tuple[AutoTriggerState, bool]:
    """Advance the lifecycle; return (next_state, should_fire). ``should_fire`` is True one tick."""
    if state.phase == WAIT_GREEN:
        if alignment.ready:
            return replace(state, phase=STABILIZING, green_since=now), False
        return state, False

    if state.phase == STABILIZING:
        if not alignment.ready:
            return replace(state, phase=WAIT_GREEN, green_since=None), False
        assert state.green_since is not None
        if now - state.green_since >= cfg.stable_seconds:
            return replace(state, phase=COOLDOWN, fired_at=now), True
        return state, False

    if state.phase == COOLDOWN:
        assert state.fired_at is not None
        if now - state.fired_at >= cfg.cooldown_seconds:
            nxt = WAIT_RED if cfg.require_red_between else WAIT_GREEN
            return replace(state, phase=nxt, green_since=None, fired_at=None), False
        return state, False

    # WAIT_RED: re-arm once the arcs go red again (operator repositioned for the next patient).
    if alignment.left == "RED" or alignment.right == "RED":
        return replace(state, phase=WAIT_GREEN), False
    return state, False
```

- [ ] **Step 4: Export from the package**

In `src/arm101_hand/system_camera/__init__.py`, add:

```python
from .auto_trigger import AutoTriggerState, arm, update
```

and add `"AutoTriggerState"`, `"arm"`, `"update"` to `__all__` (keep sorted). Note: `arm`/`update` are intentionally generic names — import the module (`from arm101_hand.system_camera import auto_trigger`) in the demo to call `auto_trigger.arm()` / `auto_trigger.update()` and avoid shadowing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_auto_trigger.py -q`
Expected: PASS (all 4).

- [ ] **Step 6: Run the full gate + commit**

```bash
uv run ruff format --check . && uv run ruff check src tests scripts && uv run mypy src && uv run pytest -m 'not hardware' -q
git add src/arm101_hand/system_camera/auto_trigger.py src/arm101_hand/system_camera/__init__.py tests/unit/test_auto_trigger.py
git commit -m "feat(system_camera): auto_trigger lifecycle state machine"
```

---

### Task 4: `WebcamPreview` frame snapshot + status overlay

**Files:**
- Modify: `src/arm101_hand/system_camera/preview.py`
- Test: `tests/unit/test_system_camera_open_capture.py`

**Interfaces:**
- Produces: `WebcamPreview.latest_frame() -> np.ndarray | None` (thread-safe copy of the most recent ROI-cropped frame; `None` before the first frame); `WebcamPreview.set_status_text(text: str, color: tuple[int, int, int] = (0, 255, 0)) -> None` (thread-safe; capture thread overlays it bottom-left, display-only).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_system_camera_open_capture.py`:

```python
def test_latest_frame_none_before_start(tmp_path):
    p = WebcamPreview(index=0, window_title="t", record_dir=tmp_path)
    assert p.latest_frame() is None


def test_set_status_text_does_not_raise(tmp_path):
    p = WebcamPreview(index=0, window_title="t", record_dir=tmp_path)
    p.set_status_text("AUTO armed", (0, 255, 0))  # safe before the thread starts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_open_capture.py -q`
Expected: FAIL — `AttributeError: 'WebcamPreview' object has no attribute 'latest_frame'`.

- [ ] **Step 3: Add the fields, methods, and capture-thread wiring**

In `WebcamPreview.__init__` (after the existing `self._still_pending: bytes | None = None` block), add:

```python
        # Latest ROI-cropped frame for callers that detect on it (e.g. the auto-trigger demo),
        # plus an optional status string the capture thread overlays. Both thread-safe.
        self._latest_lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._status_lock = threading.Lock()
        self._status_text = ""
        self._status_color: tuple[int, int, int] = (0, 255, 0)
```

Add these methods in the caller-API section (next to `show_still`):

```python
    def latest_frame(self) -> np.ndarray | None:
        """Thread-safe copy of the most recent ROI-cropped frame, or None before the first frame."""
        with self._latest_lock:
            return None if self._latest is None else self._latest.copy()

    def set_status_text(self, text: str, color: tuple[int, int, int] = (0, 255, 0)) -> None:
        """Thread-safe: a status line the capture thread overlays bottom-left (display only)."""
        with self._status_lock:
            self._status_text = text
            self._status_color = color
```

In `_run`, immediately AFTER the `if self._roi is not None:` crop block and BEFORE `want = self._record.is_set()`, store the clean frame:

```python
                with self._latest_lock:
                    self._latest = frame.copy()  # clean ROI frame for latest_frame() consumers
```

Then, immediately BEFORE `imshow_fit(self._title, frame)` (after the `REC` putText block), draw the status overlay on the display copy:

```python
                with self._status_lock:
                    status, status_color = self._status_text, self._status_color
                if status:
                    cv2.putText(
                        frame, status, (12, frame.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2, cv2.LINE_AA,
                    )
```

(The snapshot is stored from the CLEAN frame before the `REC`/status overlays, so detection never sees the overlay text.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_system_camera_open_capture.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full gate + commit**

```bash
uv run ruff format --check . && uv run ruff check src tests scripts && uv run mypy src && uv run pytest -m 'not hardware' -q
git add src/arm101_hand/system_camera/preview.py tests/unit/test_system_camera_open_capture.py
git commit -m "feat(system_camera): WebcamPreview latest_frame + status-text overlay"
```

---

### Task 5: `grab_auto_trigger_analysis.py` demo (manual ⇄ auto)

**Files:**
- Create: `scripts/demos/grab_auto_trigger_analysis.py` (cloned from `scripts/demos/grab_trigger_capture_analysis.py`)

**Interfaces:**
- Consumes: `detect`, `AlignmentState` (Task 2); `auto_trigger.arm`, `auto_trigger.update`, `AutoTriggerState` (Task 3); `WebcamPreview.latest_frame`, `WebcamPreview.set_status_text` (Task 4); `scfg.auto_trigger` (Task 1); the existing capture/grading helpers in the source demo.

This task has no host unit test (cv2 + msvcrt + bus glue, like the other demo scripts); it is verified by import/`--help` smoke + ruff/mypy-not-applicable-to-scripts + the hardware checklist below.

- [ ] **Step 1: Clone the source demo**

```bash
cp scripts/demos/grab_trigger_capture_analysis.py scripts/demos/grab_auto_trigger_analysis.py
```

- [ ] **Step 2: Update the module docstring**

Replace the title line and the first paragraph of the docstring with text describing the auto variant. Keep the PREREQUISITES and research/educational disclaimer. Replace the Controls block with:

```text
Controls (torque ON the whole time; focus the TERMINAL, not the preview window):
  m           toggle MANUAL <-> AUTO trigger mode (AUTO is the explicit arm; starts MANUAL)
  SPACE       (manual override, any mode) fire one capture cycle
  g           analyze the current patient turn (grade every shot, show the combined panel)
  r           start / stop recording the USB preview
  [ / ]       shrink / grow the press depth (applies to the NEXT press)
  q / Ctrl+C  stop and go to the exit prompt
```

Update the `Usage:` line to `uv run python scripts/demos/grab_auto_trigger_analysis.py [--grade | --no-grade]`.

- [ ] **Step 3: Add the imports**

Add to the `from arm101_hand.system_camera import ...` line so it reads:

```python
from arm101_hand.system_camera import AURORA_SCREEN_ROI, WebcamPreview, auto_trigger, detect
```

(If `auto_trigger`/`detect` are not re-exported as a submodule attribute, instead add: `from arm101_hand.system_camera import arc_detector, auto_trigger` and call `arc_detector.detect(...)`. Prefer importing the modules to keep the generic `arm`/`update` names namespaced.)

- [ ] **Step 4: Add a non-blocking key poll helper**

Add next to the existing `_read_key`:

```python
def _poll_key() -> str:
    """Non-blocking single keypress from the TERMINAL via msvcrt ('' if none waiting)."""
    if not msvcrt.kbhit():
        return ""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix -> consume the 2nd byte, ignore
        msvcrt.getwch()
        return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch
```

- [ ] **Step 5: Add a status-overlay formatter**

Add at module scope:

```python
def _overlay(mode: str, alignment, armed_phase: str) -> tuple[str, tuple[int, int, int]]:
    """Build the on-screen status line + colour from the current mode and alignment."""
    if mode != "AUTO":
        return "MANUAL  (m=auto, SPACE=capture)", (200, 200, 200)
    label = f"AUTO [{armed_phase}]  L:{alignment.left} R:{alignment.right}"
    color = (0, 255, 0) if alignment.ready else (0, 0, 255)
    return label, color
```

- [ ] **Step 6: Extract the capture cycle into a nested helper**

Inside `_trigger_loop`, BEFORE the `while True:` loop, define a nested function that contains the EXISTING "fire one capture cycle" body (the block currently from `# ---- fire one capture cycle ----` through the per-shot save/append/`show_still`/target-print). Convert each loop `continue` in that body to `return`, and rename the inner capture timestamp to `captured_at` to avoid clashing with the loop's monotonic `now`:

```python
        def _fire_capture_cycle() -> None:
            # (1) Baseline filelist BEFORE pressing (retried past intermittent GET_FILELIST CODE_FAIL).
            try:
                camera.ensure_connected()
                before = snapshot_filenames(camera, cap.dcim_root)
            except (CameraError, OSError) as e:
                print(f"  camera not ready: {e}\n  -> retry.")
                return
            # (2) Trigger: press -> hold -> release.  [copy the existing press/hold/release block]
            # (3) Pull wait + per-shot validate/save/append/show_still.  [copy the existing block]
            #     Use `captured_at = _dt.datetime.now(_dt.UTC)` instead of `now` for save_capture.
```

(Copy the press/hold/release and pull/save blocks verbatim from `grab_trigger_capture_analysis.py`, changing only `now` -> `captured_at` for the datetime and `continue` -> `return`. `turn_shots.append(...)` and `trigger_no["n"] += 1` keep working via closure.)

- [ ] **Step 7: Replace the blocking key loop with the non-blocking mode loop**

Replace the `while True:` body (the part that did `key = _read_key()` and dispatched `r`/`g`/action) with:

```python
        mode = "MANUAL"
        state_auto = auto_trigger.arm()
        last_detect = 0.0
        print("Trigger: m=auto/manual, SPACE=capture, g=analyze, r=record, [ / ]=depth, q=exit")
        _print_prereqs()
        print("  " + _status_line(state, out_base, side, others))

        try:
            while True:
                now = time.monotonic()
                key = _poll_key()
                if key in ("m", "M"):
                    mode = "AUTO" if mode == "MANUAL" else "MANUAL"
                    if mode == "AUTO":
                        state_auto = auto_trigger.arm()
                    print(f"  mode -> {mode}")
                elif key in ("r", "R"):
                    _toggle_recording(preview)
                elif key in ("g", "G"):
                    # (copy the existing 'g' analyze-turn block here, verbatim)
                    ...
                else:
                    action = key_to_action(key) if key else None
                    if action == "quit":
                        break
                    if action == "fire":
                        _fire_capture_cycle()
                    elif action is not None:
                        state = apply_action(state, action)
                        base_now, side_now = read_finger(c, "index", index_block)
                        print("  " + _status_line(state, base_now, side_now, others))

                if mode == "AUTO" and preview is not None and now - last_detect >= scfg.auto_trigger.detect_interval_s:
                    last_detect = now
                    frame = preview.latest_frame()
                    if frame is not None:
                        alignment = detect(frame, scfg.auto_trigger)
                        state_auto, fire = auto_trigger.update(state_auto, alignment, now, scfg.auto_trigger)
                        text, color = _overlay(mode, alignment, state_auto.phase)
                        preview.set_status_text(text, color)
                        if fire:
                            print("  AUTO: arcs green + stable -> firing capture")
                            _fire_capture_cycle()
                elif mode == "AUTO" and preview is None:
                    print("  AUTO unavailable: no USB preview window -- staying MANUAL.")
                    mode = "MANUAL"

                time.sleep(0.01)  # ~100 Hz poll; keeps the CPU sane (loop no longer blocks on a key)
        except KeyboardInterrupt:
            print("\n^C -- leaving trigger mode")
            if turn_shots:
                print(f"  ({len(turn_shots)} shot(s) captured but not analyzed)")
```

`scfg` is the loaded `SystemCameraConfig`; it is currently consumed only inside `_start_preview`. Capture it for the loop: in `main`, keep a reference — `scfg = load_system_camera_config(_SYSTEM_CAMERA_CONFIG_PATH)` then `preview = _start_preview(scfg)` — and pass `scfg` into `_trigger_loop` (e.g. via closure by defining `_trigger_loop` after `scfg` is loaded, as the demo already defines it inside `main`).

- [ ] **Step 8: Smoke-test imports + argparse (no camera)**

Run: `uv run python scripts/demos/grab_auto_trigger_analysis.py --help`
Expected: prints usage including `--grade`/`--no-grade`; exits 0 (this exercises every import — `detect`, `auto_trigger`, `latest_frame` references resolve).

- [ ] **Step 9: Lint + format + commit**

```bash
uv run ruff format scripts/demos/grab_auto_trigger_analysis.py
uv run ruff format --check . && uv run ruff check src tests scripts && uv run mypy src && uv run pytest -m 'not hardware' -q
git add scripts/demos/grab_auto_trigger_analysis.py
git commit -m "feat(demos): grab_auto_trigger_analysis -- auto/manual arc-triggered capture"
```

- [ ] **Step 10: Hardware validation (operator, bench)**

1. `uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py` — capture fresh red AND green ROI frames (SPACE); confirm the arc bands in `auto_trigger.left_arc/right_arc` cover the arcs. Tune the YAML regions/HSV if needed.
2. Run the demo; in MANUAL, SPACE still captures (regression check).
3. Press `m` -> AUTO; align the Aurora to green and confirm: on-screen status shows `L:GREEN R:GREEN`, it fires after ~1 s stable, pulls the image, then will not re-fire until the arcs go red again.
4. Confirm `q` exit releases torque with no surprise motion; `g` still grades a turn.

---

### Task 6: Docs refresh

**Files:**
- Modify: `CLAUDE.md`, `README.md` (via the doc_update skill)

- [ ] **Step 1: Run the doc_update skill**

Invoke `/doc_update`. Ensure these points are covered (one canonical home each, IL-7):
- CLAUDE.md demos line (§3 tree) + workflows list: add `grab_auto_trigger_analysis.py` (auto/manual arc-triggered capture; `m` toggles auto).
- CLAUDE.md tech-debt (system-camera bullet): note the arc auto-trigger (`arc_detector` + `auto_trigger`, HSV per-arc-band masking, `auto_trigger` config block).
- README §4: mention the auto-trigger demo + that it watches the Aurora arcs (red→green) to fire.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: refresh CLAUDE.md/README for the arc auto-trigger demo"
```

---

## Self-review notes

- **Spec coverage:** §2 CV approach → Task 2; §4 modules → Tasks 2–4; §5 demo flow → Task 5; §6 state machine → Task 3; §7 config → Task 1; §8 testing → Tasks 1–4 tests; §9 iron laws → Global Constraints + Task 5 hardware step; §10 commits → Tasks 1–6 (reordered config-first, noted up top); §11 out-of-scope → not built.
- **Sequencing:** config (1) precedes the modules (2,3) that import it; preview (4) and demo (5) follow; docs (6) last.
- **Type consistency:** `AlignmentState` fields, `detect`/`classify_band`/`arm`/`update` signatures, and `latest_frame`/`set_status_text` match across tasks and the demo's consumption.
- **Known soft spot:** the arc-region + HSV defaults are estimates; Task 5 step 10.1 is the calibration-against-real-frames step (values land in the Task 1 YAML).
