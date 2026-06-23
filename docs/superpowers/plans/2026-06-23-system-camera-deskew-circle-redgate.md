# System-Camera Deskew + Circle-Based Red-Gate Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revise the system-camera arc pipeline to crop the screen as an upright 5:3 region (deskew), locate mirror-symmetric arc bands on the camera circle, and trigger captures via a red→not-red gated state machine.

**Architecture:** `screen_roi` gains a rotation `angle`; `Roi.crop` deskews to an 800×480 (5:3) reference. Arc detection becomes red-only (`red` vs `not-red`). The auto-trigger becomes a gated machine: both-arcs-RED must precede both-arcs-clear before a fire. The calibration tool re-derives all of it from 3 captures (white/red/bright) and writes config via the existing ruamel writer.

**Tech Stack:** Python 3.12, OpenCV (plain `opencv-python`, main modules only), numpy, pydantic v2, ruamel.yaml (writer only), pytest, ruff, mypy.

## Global Constraints

- **Plain `opencv-python` only** — no `opencv_contrib`. Core/imgproc functions only.
- **References read-only (IL-2)**; adapt patterns into `src/`, never import from `references/`.
- **Reading YAML = `yaml.safe_load`**; `ruamel.yaml` only in the calibration writer.
- **IL-5:** only the calibration tool writes config; it validates via pydantic + writes a `.bak` first; writes only on operator confirm.
- **Detection reference = 800×480 (5:3).** `screen_roi` AND `left_arc`/`right_arc` are stored at `ref_w=800, ref_h=480`. `WebcamPreview` resizes the ROI crop to `(ref_w, ref_h)` as it already does — no change needed there.
- **`schema_version` 6 → 7.** Removing `green_bands`/`require_no_red`/`require_clear_between` means old configs fail validation (`extra="forbid"`) — intended; recalibration is required.
- **CI must stay green:** `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest -m 'not hardware'`. (mypy on `src` only; scripts are lint-checked.)
- Python 3.12 style: `from __future__ import annotations`, pydantic `ConfigDict(extra="forbid")`.

---

## Execution context

Work on `main` (the prior calibration feature is merged there; this revises it). The repo is local, ~19 commits ahead of `origin` (unpushed). The working tree has one untracked file (`scripts/diagnostics/fundus_camera/aurora_keepalive_probe.py`) unrelated to this plan — leave it untouched; use path-scoped `git add` only. If executing in an isolated worktree, init the `lerobot` submodule and `uv sync --extra dev` first (the venv needs `ruamel.yaml`, already in `pyproject.toml`).

Bench sample frames for headless verification live in `media_outputs/camera_captures/`: `*_startupscreen.jpg` (white), `*_red.jpg`, `*_green.jpg` (the bright/ready frame).

## File Structure

| File | Change | Task |
|---|---|---|
| `src/arm101_hand/config/system_camera_config.py` | `RoiBox.angle`; `AutoTriggerConfig` red-only (drop green + flags); arc ref 800×480; v7 | 1 |
| `src/arm101_hand/data/system_camera_config.yaml` | angle, arc ref 800×480, drop green/flags, v7 | 1 |
| `src/arm101_hand/system_camera/roi.py` | `Roi.angle` + deskew `crop`; `roi_from_region` carries angle | 2 |
| `src/arm101_hand/system_camera/arc_detector.py` | red-only `AlignmentState` + `detect` | 3 |
| `src/arm101_hand/system_camera/auto_trigger.py` | red-gate state machine | 4 |
| `src/arm101_hand/system_camera/calibration.py` | rotated-rect screen, 5:3 candidate, deskew, circle, symmetric bands, red sampling, writer | 5, 6 |
| `scripts/calibration/system_camera/calibrate_view.py` | 3-capture deskew/circle/red-only confirm | 7 |
| `scripts/demos/grab_auto_trigger_analysis.py` | HUD red/clear + phase | 8 |
| `CLAUDE.md`, `README.md`, `docs/conventions/06-documentation-protocol.md` | docs | 9 |
| `tests/unit/test_system_camera_config.py`, `test_roi.py`, `test_arc_detector.py`, `test_auto_trigger.py`, `test_system_camera_calibration.py` | tests | 1–6 |

`preview.py` needs **no change** (it already resizes the ROI crop to `(ref_w, ref_h)` = 800×480 and feeds that to detection; deskew is inside `Roi.crop`).

---

### Task 1: Schema v7 — `RoiBox.angle` + red-only `AutoTriggerConfig`

**Files:**
- Modify: `src/arm101_hand/config/system_camera_config.py`
- Modify: `src/arm101_hand/data/system_camera_config.yaml`
- Test: `tests/unit/test_system_camera_config.py`

**Interfaces:**
- Produces: `RoiBox` with `angle: float`; `AutoTriggerConfig` with fields `left_arc, right_arc, red_bands, coverage_threshold, morph_kernel, stable_seconds, cooldown_seconds, detect_interval_s` (no green/require_* ); `schema_version` default 7.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_system_camera_config.py`:

```python
def test_roibox_angle_default_and_round_trip():
    from arm101_hand.config.system_camera_config import RoiBox

    assert RoiBox(x=1, y=2, w=3, h=4).angle == 0.0
    assert RoiBox(x=1, y=2, w=3, h=4, angle=-0.9).angle == -0.9


def test_schema_default_version_is_7():
    assert SystemCameraConfig().schema_version == 7


def test_auto_trigger_is_red_only():
    at = SystemCameraConfig().auto_trigger
    assert len(at.red_bands) >= 1
    assert (at.left_arc.ref_w, at.left_arc.ref_h) == (800, 480)
    assert (at.right_arc.ref_w, at.right_arc.ref_h) == (800, 480)
    assert not hasattr(at, "green_bands")
    assert not hasattr(at, "require_no_red")
    assert not hasattr(at, "require_clear_between")


def test_auto_trigger_rejects_removed_keys():
    for bad in ("green_bands", "require_no_red", "require_clear_between"):
        with pytest.raises(ValidationError):
            SystemCameraConfig.model_validate({"auto_trigger": {bad: [] if bad == "green_bands" else True}})
```

Also UPDATE these existing tests for the new shape (they reference removed fields / old version):
- `test_schema_default_version_is_6` → delete (replaced by `test_schema_default_version_is_7`).
- `test_auto_trigger_defaults`: remove the `green_bands` assertion; change `left_arc.ref_w/ref_h` expectation to `(800, 480)`; drop `require_clear_between`/`require_no_red` assertions.
- `test_auto_trigger_rejects_empty_bands`: keep only the `red_bands: []` case (delete the `green_bands` case).
- `test_data_yaml_loads`: remove any `green_bands`/`require_*` references; keep `len(at.red_bands) >= 1`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_config.py::test_roibox_angle_default_and_round_trip tests/unit/test_system_camera_config.py::test_auto_trigger_is_red_only -v`
Expected: FAIL (`angle` missing / `green_bands` still present).

- [ ] **Step 3: Add `angle` to `RoiBox`** — in `system_camera_config.py`, inside `class RoiBox`, after `ref_h`:

```python
    angle: float = Field(default=0.0, description="deskew rotation in degrees; 0 = axis-aligned crop")
```

- [ ] **Step 4: Make `AutoTriggerConfig` red-only** — replace the class body's fields with:

```python
class AutoTriggerConfig(BaseModel):
    """Red-only arc auto-trigger: each arc is RED (>= coverage_threshold) or not. ready/fire is
    gated by a red -> not-red transition (see arm101_hand.system_camera.auto_trigger)."""

    model_config = ConfigDict(extra="forbid")
    left_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=110, y=130, w=70, h=230, ref_w=800, ref_h=480))
    right_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=620, y=130, w=70, h=230, ref_w=800, ref_h=480))
    red_bands: list[HsvBand] = Field(
        min_length=1,  # >=1 band: red is the only colour classified (no degenerate empty config)
        default_factory=lambda: [
            HsvBand(h_lo=0, s_lo=35, v_lo=50, h_hi=12, s_hi=255, v_hi=255),
            HsvBand(h_lo=158, s_lo=35, v_lo=50, h_hi=180, s_hi=255, v_hi=255),
        ],
    )
    coverage_threshold: float = Field(default=0.04, ge=0.0, le=1.0, description="arc is RED at >= this")
    morph_kernel: int = Field(default=3, ge=1)
    stable_seconds: float = Field(default=1.0, gt=0.0)
    cooldown_seconds: float = Field(default=3.0, ge=0.0)
    detect_interval_s: float = Field(default=0.2, gt=0.0)
```

(Delete `green_bands`, `require_no_red`, `require_clear_between`.)

- [ ] **Step 5: Bump `schema_version`** — in `SystemCameraConfig`, change `schema_version: int = 6` to `schema_version: int = 7`. Leave `screen_roi`'s default as-is (legacy fallback, `angle` defaults 0).

- [ ] **Step 6: Update the data YAML** — in `src/arm101_hand/data/system_camera_config.yaml`: set `schema_version: 7`; add `, angle: 0` inside the `screen_roi: {...}` mapping; in `auto_trigger`, set the arc regions to ref 800×480 and **remove** the `green_bands:` block, `require_clear_between`, and `require_no_red` lines:

```yaml
screen_roi: {x: 60, y: 75, w: 196, h: 147, ref_w: 640, ref_h: 480, angle: 0}
# --- Automated trigger (grab_auto_trigger_analysis.py) -------------------------------------------
# Red-only: each arc is RED (>= coverage_threshold) or not; a capture fires after BOTH arcs go RED
# (misaligned) then BOTH go not-red (aligned), held stable_seconds. Regions/bands are STARTING
# ESTIMATES -- re-derive with scripts/calibration/system_camera/calibrate_view.py.
auto_trigger:
  left_arc:  {x: 110, y: 130, w: 70, h: 230, ref_w: 800, ref_h: 480}
  right_arc: {x: 620, y: 130, w: 70, h: 230, ref_w: 800, ref_h: 480}
  red_bands:                       # red wraps hue 0/180 -> two bands OR'd (pale arcs -> low S/V floors)
    - {h_lo: 0,   s_lo: 35, v_lo: 50, h_hi: 12,  s_hi: 255, v_hi: 255}
    - {h_lo: 158, s_lo: 35, v_lo: 50, h_hi: 180, s_hi: 255, v_hi: 255}
  coverage_threshold: 0.04         # an arc counts as RED at >= this fraction
  morph_kernel: 3                  # MORPH_OPEN kernel (px) to despeckle the mask
  stable_seconds: 1.0              # both arcs must hold not-red this long before firing
  cooldown_seconds: 3.0            # min gap after a fire
  detect_interval_s: 0.2           # how often to sample the ROI in auto mode
```

- [ ] **Step 7: Run the config tests**

Run: `uv run pytest tests/unit/test_system_camera_config.py -v`
Expected: all PASS.

- [ ] **Step 8: Lint + type-check + commit**

```bash
uv run ruff format src/arm101_hand/config/system_camera_config.py src/arm101_hand/data/system_camera_config.yaml tests/unit/test_system_camera_config.py
uv run ruff check src tests && uv run mypy src
git add src/arm101_hand/config/system_camera_config.py src/arm101_hand/data/system_camera_config.yaml tests/unit/test_system_camera_config.py
git commit -m "feat(config): RoiBox.angle + red-only AutoTriggerConfig (schema v7)"
```

---

### Task 2: `Roi` deskew crop

**Files:**
- Modify: `src/arm101_hand/system_camera/roi.py`
- Test: `tests/unit/test_roi.py`

**Interfaces:**
- Consumes: `RoiBox.angle` (Task 1).
- Produces: `Roi` with `angle: float = 0.0` and a deskewing `crop`; `roi_from_region` carries `angle`.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_roi.py`:

```python
def test_roi_angle_zero_crop_is_plain_slice():
    roi = Roi(10, 20, 30, 40, ref_w=640, ref_h=480)  # angle defaults 0
    frame = _frame(640, 480)
    assert roi.crop(frame).shape == (40, 30, 3)


def test_roi_deskew_uprights_a_tilted_rect():
    import cv2

    # a white rect tilted +10deg on black; cropping with angle=+10 should deskew it upright so the
    # crop is (almost) all white (the rect fills the crop after rotation).
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    box = cv2.boxPoints(((320, 240), (200, 120), 10.0)).astype(np.int32)
    cv2.fillPoly(frame, [box], (255, 255, 255))
    roi = Roi(x=220, y=180, w=200, h=120, ref_w=640, ref_h=480, angle=10.0)
    crop = roi.crop(frame)
    white_frac = (crop.reshape(-1, 3).min(axis=1) > 200).mean()
    assert white_frac > 0.9  # deskewed crop is essentially the upright white rect


def test_roi_from_region_carries_angle():
    from arm101_hand.config.system_camera_config import RoiBox
    from arm101_hand.system_camera import roi_from_region

    r = roi_from_region(RoiBox(x=1, y=2, w=3, h=4, ref_w=800, ref_h=480, angle=-0.9))
    assert r.angle == -0.9 and (r.ref_w, r.ref_h) == (800, 480)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_roi.py::test_roi_deskew_uprights_a_tilted_rect tests/unit/test_roi.py::test_roi_from_region_carries_angle -v`
Expected: FAIL (`angle` not a field).

- [ ] **Step 3: Add `angle` + deskew to `Roi`** — in `roi.py`, add the field (after `ref_h`) and replace `crop`:

```python
    ref_h: int = 480
    angle: float = 0.0  # deskew rotation in degrees; 0 = plain axis-aligned slice (fast path)

    def for_frame(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        # ... unchanged ...

    def crop(self, frame: np.ndarray) -> np.ndarray:
        """ROI sub-image of ``frame`` (rescaled + clamped). If ``angle`` != 0, the region is rotated
        by ``angle`` about the box centre first (deskew), so a slightly-tilted screen comes out
        upright. ``angle`` == 0 takes a plain-slice fast path (unchanged behaviour/perf)."""
        import cv2  # local import: roi.py stays cv2-free on the angle==0 path

        fh, fw = frame.shape[:2]
        x, y, w, h = self.for_frame(fw, fh)
        if self.angle == 0.0:
            return frame[y : y + h, x : x + w]
        cx, cy = x + w / 2.0, y + h / 2.0
        pad = int(round(0.2 * max(w, h))) + 2  # cover the rotated corners
        px0, py0 = max(0, x - pad), max(0, y - pad)
        px1, py1 = min(fw, x + w + pad), min(fh, y + h + pad)
        region = frame[py0:py1, px0:px1]
        m = cv2.getRotationMatrix2D((cx - px0, cy - py0), self.angle, 1.0)
        rot = cv2.warpAffine(region, m, (region.shape[1], region.shape[0]))
        rx, ry = int(round(cx - px0 - w / 2.0)), int(round(cy - py0 - h / 2.0))
        return rot[max(0, ry) : ry + h, max(0, rx) : rx + w]
```

(Note: the `import numpy as np` already at the top stays. The local `import cv2` keeps the cheap angle==0 path dependency-free; ruff allows the local import.)

- [ ] **Step 4: Carry `angle` through `roi_from_region` + the Protocol** — update `_RegionLike` (add `angle: float`) and `roi_from_region`:

```python
class _RegionLike(Protocol):
    x: int
    y: int
    w: int
    h: int
    ref_w: int
    ref_h: int
    angle: float


def roi_from_region(region: _RegionLike) -> Roi:
    """Build a :class:`Roi` from a config ``RoiBox`` (carries the deskew ``angle``)."""
    return Roi(
        x=region.x, y=region.y, w=region.w, h=region.h, ref_w=region.ref_w, ref_h=region.ref_h,
        angle=region.angle,
    )
```

- [ ] **Step 5: Run tests + type-check**

Run: `uv run pytest tests/unit/test_roi.py -v && uv run mypy src`
Expected: PASS. (If `test_roi_deskew_uprights_a_tilted_rect` is below 0.9, the sign/geometry is off — the stored `angle` must be the rotation that *uprights* the box; adjust the sign in `getRotationMatrix2D` and re-run. The calibration Task 5 stores the angle with the matching sign.)

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/roi.py tests/unit/test_roi.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/roi.py tests/unit/test_roi.py
git commit -m "feat(system_camera): Roi deskew crop (angle) + roi_from_region carries it"
```

---

### Task 3: `arc_detector` — red-only classification

**Files:**
- Modify: `src/arm101_hand/system_camera/arc_detector.py`
- Modify: `src/arm101_hand/system_camera/__init__.py` (export name unchanged: `detect`, `AlignmentState`; drop `classify_band` if exported)
- Test: `tests/unit/test_arc_detector.py` (create)

**Interfaces:**
- Consumes: `AutoTriggerConfig` (red-only, Task 1); `roi_from_region` (Task 2).
- Produces: `AlignmentState(left_red, right_red, left_cov, right_cov)` with `.both_red` / `.both_clear` properties; `detect(roi_bgr, cfg) -> AlignmentState`.

- [ ] **Step 1: Write failing tests** — create `tests/unit/test_arc_detector.py`:

```python
import cv2
import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox
from arm101_hand.system_camera.arc_detector import AlignmentState, detect

_RED = (0, 0, 255)


def _cfg() -> AutoTriggerConfig:
    # arcs at the left/right thirds of an 800x480 reference frame
    return AutoTriggerConfig(
        left_arc=RoiBox(x=40, y=160, w=120, h=160, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=640, y=160, w=120, h=160, ref_w=800, ref_h=480),
        coverage_threshold=0.2,
    )


def test_alignmentstate_properties():
    assert AlignmentState(True, True, 0.5, 0.5).both_red is True
    assert AlignmentState(False, False, 0.0, 0.0).both_clear is True
    assert AlignmentState(True, False, 0.5, 0.0).both_red is False
    assert AlignmentState(True, False, 0.5, 0.0).both_clear is False


def test_detect_both_red():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)
    frame[160:320, 40:160] = _RED
    frame[160:320, 640:760] = _RED
    s = detect(frame, _cfg())
    assert s.both_red is True


def test_detect_both_clear_on_blank():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)  # nothing red
    s = detect(frame, _cfg())
    assert s.both_clear is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_arc_detector.py -v`
Expected: FAIL (`AlignmentState` signature mismatch / no `both_red`).

- [ ] **Step 3: Rewrite `arc_detector.py`:**

```python
"""Red-arc detection for the arm-mounted USB observation camera (device layer).

The Optomed Aurora shows two symmetric alignment arcs (left + right of its circular live view) that
are RED when misaligned. We classify each arc region by HSV RED coverage (cheap, robust per-region
masking; plain opencv-python). "Aligned / ready" is simply NOT red -- no other colour is thresholded
(the bright/aligned screen reads greenish overall, so green coverage is unreliable). The auto-trigger
gates each fire on a both-red -> both-not-red transition. Pure functions (no cv2 window).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig, HsvBand

from .roi import roi_from_region


@dataclass(frozen=True)
class AlignmentState:
    """Per-arc red classification + coverages (coverages are for the HUD/debug)."""

    left_red: bool
    right_red: bool
    left_cov: float
    right_cov: float

    @property
    def both_red(self) -> bool:
        return self.left_red and self.right_red

    @property
    def both_clear(self) -> bool:
        return not self.left_red and not self.right_red


def _band_mask(hsv: np.ndarray, bands: list[HsvBand]) -> np.ndarray:
    """OR together the inRange masks for every HSV band (red needs two for the 0/180 wrap)."""
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for b in bands:
        lo = np.array([b.h_lo, b.s_lo, b.v_lo], dtype=np.uint8)
        hi = np.array([b.h_hi, b.s_hi, b.v_hi], dtype=np.uint8)
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    return mask


def red_coverage(hsv_band: np.ndarray, cfg: AutoTriggerConfig) -> float:
    """Fraction of an arc band matching the red bands after a MORPH_OPEN despeckle."""
    mask = _band_mask(hsv_band, cfg.red_bands)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.morph_kernel, cfg.morph_kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    area = mask.shape[0] * mask.shape[1]
    return cv2.countNonZero(mask) / area if area else 0.0


def detect(roi_bgr: np.ndarray, cfg: AutoTriggerConfig) -> AlignmentState:
    """Classify both arcs as RED / not-red by red coverage vs ``cfg.coverage_threshold``."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    lc = red_coverage(roi_from_region(cfg.left_arc).crop(hsv), cfg)
    rc = red_coverage(roi_from_region(cfg.right_arc).crop(hsv), cfg)
    t = cfg.coverage_threshold
    return AlignmentState(left_red=lc >= t, right_red=rc >= t, left_cov=lc, right_cov=rc)
```

- [ ] **Step 4: Fix `__init__.py` exports** — in `src/arm101_hand/system_camera/__init__.py`, change `from .arc_detector import AlignmentState, classify_band, detect` to `from .arc_detector import AlignmentState, detect` and remove `"classify_band"` from `__all__`. (If `calibration.py` or anything imported `classify_band`/`_band_mask`, Task 5/7 update those; `_band_mask` is still importable as a module member.)

- [ ] **Step 5: Run tests + type-check**

Run: `uv run pytest tests/unit/test_arc_detector.py -v && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/__init__.py tests/unit/test_arc_detector.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/arc_detector.py src/arm101_hand/system_camera/__init__.py tests/unit/test_arc_detector.py
git commit -m "feat(arc_detector): red-only AlignmentState + detect"
```

---

### Task 4: `auto_trigger` — red-gate state machine

**Files:**
- Modify: `src/arm101_hand/system_camera/auto_trigger.py`
- Modify: `src/arm101_hand/system_camera/__init__.py` (re-exports `arm`, `update`, `AutoTriggerState` — unchanged names)
- Test: `tests/unit/test_auto_trigger.py` (create)

**Interfaces:**
- Consumes: `AlignmentState` (`.both_red`, `.both_clear`); `AutoTriggerConfig` (`stable_seconds`, `cooldown_seconds`).
- Produces: `AutoTriggerState(phase, clear_since, fired_at)`; `arm() -> AutoTriggerState`; `update(state, alignment, now, cfg) -> tuple[AutoTriggerState, bool]`; constants `WAIT_RED, WAIT_CLEAR, STABILIZING, COOLDOWN`.

- [ ] **Step 1: Write failing tests** — create `tests/unit/test_auto_trigger.py`:

```python
from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import AlignmentState
from arm101_hand.system_camera.auto_trigger import WAIT_CLEAR, WAIT_RED, arm, update

_CFG = AutoTriggerConfig(stable_seconds=1.0, cooldown_seconds=3.0)
_RED = AlignmentState(True, True, 0.5, 0.5)
_CLEAR = AlignmentState(False, False, 0.0, 0.0)


def _run(states, t0=0.0, dt=0.5):
    st, fired, t = arm(), [], t0
    for a in states:
        st, f = update(st, a, t, _CFG)
        fired.append(f)
        t += dt
    return st, fired


def test_no_fire_without_red_gate():
    # clear from the start (blank/aligned screen) must never fire
    st, fired = _run([_CLEAR] * 10)
    assert not any(fired)
    assert st.phase == WAIT_RED


def test_fires_on_red_then_clear_held_stable():
    # both_red (gate) -> both_clear held >= stable_seconds (1.0s, dt 0.5 -> 3 clears) -> one fire
    _, fired = _run([_RED, _CLEAR, _CLEAR, _CLEAR])
    assert fired == [False, False, False, True]


def test_requires_regate_after_cooldown():
    # after a fire, staying clear must NOT fire again until red is seen again
    seq = [_RED, _CLEAR, _CLEAR, _CLEAR] + [_CLEAR] * 10
    _, fired = _run(seq)
    assert sum(fired) == 1


def test_red_flicker_during_stabilizing_resets():
    # clear, clear, RED (flicker), clear... the stable window restarts; first window broken
    _, fired = _run([_RED, _CLEAR, _RED, _CLEAR, _CLEAR, _CLEAR])
    assert sum(fired) == 1  # only fires after an uninterrupted stable window post-flicker
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_auto_trigger.py -v`
Expected: FAIL (`WAIT_CLEAR` not defined / wrong behaviour).

- [ ] **Step 3: Rewrite `auto_trigger.py`:**

```python
"""Auto-trigger lifecycle for the red-gated Aurora capture (device layer, pure + clock-injected).

Each capture requires a fresh transition: BOTH arcs RED (misaligned -> the gate) -> BOTH arcs
not-red (aligned) held ``stable_seconds`` -> fire ONE capture -> cooldown -> re-require red. A blank
/ menu / already-aligned screen has no red, so it never passes the gate (no false fire). ``update``
is pure: (state, latest AlignmentState, monotonic ``now``, config) -> (next state, should_fire).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .arc_detector import AlignmentState

WAIT_RED = "WAIT_RED"  # armed; waiting to see both arcs RED (the per-capture gate)
WAIT_CLEAR = "WAIT_CLEAR"  # gate passed; waiting for both arcs not-red
STABILIZING = "STABILIZING"  # both not-red; holding for stable_seconds
COOLDOWN = "COOLDOWN"  # fired; waiting out cooldown_seconds, then re-gate


@dataclass(frozen=True)
class AutoTriggerState:
    phase: str = WAIT_RED
    clear_since: float | None = None
    fired_at: float | None = None


def arm() -> AutoTriggerState:
    """Fresh state, ready to watch for the red gate (called when the operator enters AUTO mode)."""
    return AutoTriggerState()


def update(
    state: AutoTriggerState, alignment: AlignmentState, now: float, cfg: AutoTriggerConfig
) -> tuple[AutoTriggerState, bool]:
    """Advance the lifecycle; return (next_state, should_fire). ``should_fire`` is True one tick."""
    if state.phase == WAIT_RED:
        if alignment.both_red:
            return replace(state, phase=WAIT_CLEAR), False
        return state, False

    if state.phase == WAIT_CLEAR:
        if alignment.both_clear:
            return replace(state, phase=STABILIZING, clear_since=now), False
        return state, False

    if state.phase == STABILIZING:
        if not alignment.both_clear:
            return replace(state, phase=WAIT_CLEAR, clear_since=None), False
        assert state.clear_since is not None
        if now - state.clear_since >= cfg.stable_seconds:
            return replace(state, phase=COOLDOWN, fired_at=now), True
        return state, False

    # COOLDOWN
    assert state.fired_at is not None
    if now - state.fired_at >= cfg.cooldown_seconds:
        return replace(state, phase=WAIT_RED, clear_since=None, fired_at=None), False
    return state, False
```

- [ ] **Step 4: Run tests + type-check**

Run: `uv run pytest tests/unit/test_auto_trigger.py -v && uv run mypy src`
Expected: PASS. (If `test_red_flicker...` fires twice or zero times, check the STABILIZING→WAIT_CLEAR reset.)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/auto_trigger.py tests/unit/test_auto_trigger.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/auto_trigger.py tests/unit/test_auto_trigger.py
git commit -m "feat(auto_trigger): red-gate state machine (both-red -> both-clear -> fire)"
```

---

### Task 5: `calibration.py` — rotated-rect screen, 5:3 candidate, deskew, circle, symmetric bands

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Consumes: `RoiBox` (with `angle`), `Roi`, `roi_from_region`.
- Produces:
  - `detect_screen_rects(white_bgr, *, top_n=3) -> list[tuple[tuple[float,float], tuple[float,float], float]]` — top-3 interior rotated rects `((cx,cy),(w,h),angle)`, best first.
  - `screen_roi_from_rect(rect, frame_w, frame_h, *, ref_w=800, ref_h=480) -> RoiBox` — normalise to 5:3 + store at 800×480 ref with the deskew angle.
  - `deskew_crop(frame, region, *, out=(800,480)) -> np.ndarray` — deskewed 5:3 crop (uses `roi_from_region(region).crop` + resize).
  - `fit_camera_circle(bright_ref_bgr) -> tuple[float,float,float]` — `(cx,cy,r)`.
  - `arc_bands_from_circle(cx, cy, r, *, ref_w=800, ref_h=480) -> tuple[RoiBox, RoiBox]` — symmetric left/right bands.

- [ ] **Step 1: Write failing tests** — replace the screen-detection tests and add the new geometry tests in `tests/unit/test_system_camera_calibration.py` (delete `test_detect_screen_rect_*`, `test_to_roi_candidate_*`, `test_detect_arc_regions_*`, and the `detect_arc_regions`/`to_roi_candidate`/`detect_screen_rect` imports; add):

```python
from arm101_hand.config.system_camera_config import RoiBox
from arm101_hand.system_camera.calibration import (
    arc_bands_from_circle,
    detect_screen_rects,
    fit_camera_circle,
    screen_roi_from_rect,
)


def test_detect_screen_rects_finds_tilted_interior_rect():
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    box = cv2.boxPoints(((400, 240), (360, 220), 6.0)).astype(np.int32)  # 5:3-ish, tilted 6deg, interior
    cv2.fillPoly(img, [box], (255, 255, 255))
    rects = detect_screen_rects(img, top_n=3)
    assert len(rects) >= 1
    (cx, cy), (w, h), angle = rects[0]
    assert 360 <= cx <= 440 and 200 <= cy <= 280
    assert abs(abs(angle) - 6.0) < 2.0  # recovered the tilt


def test_screen_roi_from_rect_is_5_3_at_800x480_ref_with_angle():
    rect = ((800.0, 600.0), (400.0, 240.0), -1.0)  # in a 1600x1200 frame
    sr = screen_roi_from_rect(rect, 1600, 1200)
    assert (sr.ref_w, sr.ref_h) == (800, 480)
    assert sr.angle == -1.0
    # the stored box, scaled back to the frame, keeps ~5:3
    from arm101_hand.system_camera import roi_from_region

    x, y, w, h = roi_from_region(sr).for_frame(1600, 1200)
    assert abs((w / h) - 5 / 3) < 0.1


def test_fit_camera_circle_centres_on_disc():
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    cv2.circle(img, (400, 240), 180, (255, 255, 255), -1)
    cx, cy, r = fit_camera_circle(img)
    assert abs(cx - 400) <= 15 and abs(cy - 240) <= 15 and abs(r - 180) <= 25


def test_arc_bands_from_circle_are_symmetric():
    left, right = arc_bands_from_circle(400, 240, 200, ref_w=800, ref_h=480)
    assert left.x < 400 <= right.x
    assert (left.w, left.h) == (right.w, right.h)
    assert abs(right.x - (800 - (left.x + left.w))) <= 1  # mirror across centre
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "screen_rects or screen_roi_from_rect or camera_circle or arc_bands" -v`
Expected: FAIL (import errors).

- [ ] **Step 3: Implement the geometry functions** — in `calibration.py`, replace `detect_screen_rect`/`to_roi_candidate`/`detect_arc_regions` (and `_screen_likeness` stays, adapted) with:

```python
def _norm_rect(rect):
    (cx, cy), (w, h), angle = rect
    if w < h:
        w, h = h, w
        angle += 90.0
    if angle > 45.0:
        angle -= 90.0
    elif angle < -45.0:
        angle += 90.0
    return (cx, cy), (w, h), angle


def detect_screen_rects(white_bgr: np.ndarray, *, top_n: int = 3):
    """Top-``top_n`` interior bright rotated rects ``((cx,cy),(w,h),angle)``, best first.

    Same high-threshold + interior + screen-likeness ranking as before, but returns the rotated
    ``minAreaRect`` (so the slight screen tilt is recovered) instead of an axis-aligned bbox."""
    gray = cv2.cvtColor(white_bgr, cv2.COLOR_BGR2GRAY)
    fh, fw = gray.shape
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    min_area = _SCREEN_MIN_AREA_FRAC * fw * fh
    scored: list[tuple[float, tuple]] = []
    for frac in _SCREEN_THRESH_FRACS:
        thr = int(otsu + (255 - otsu) * frac)
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            bbox = cv2.boundingRect(c)
            s = _screen_likeness(bbox, area, fw, fh)
            if s > 0.0:
                scored.append((s, _norm_rect(cv2.minAreaRect(c))))
    scored.sort(key=lambda t: t[0], reverse=True)
    kept: list[tuple] = []
    for _, rect in scored:
        bbox = (int(rect[0][0] - rect[1][0] / 2), int(rect[0][1] - rect[1][1] / 2), int(rect[1][0]), int(rect[1][1]))
        if all(_bbox_iou(bbox, kb) < 0.5 for kb in kept):
            kept.append(bbox)
            scored_rects.append(rect) if False else None  # see below
        if len([k for k in kept]) >= top_n:
            break
    # (de-dupe by bbox IoU but return the rects)
    out, seen = [], []
    for _, rect in scored:
        bbox = (int(rect[0][0] - rect[1][0] / 2), int(rect[0][1] - rect[1][1] / 2), int(rect[1][0]), int(rect[1][1]))
        if all(_bbox_iou(bbox, b) < 0.5 for b in seen):
            seen.append(bbox)
            out.append(rect)
        if len(out) >= top_n:
            break
    return out


def screen_roi_from_rect(rect, frame_w: int, frame_h: int, *, ref_w: int = 800, ref_h: int = 480) -> RoiBox:
    """Normalise a rotated screen rect to 5:3 and store it at the ``ref_w x ref_h`` detection ref
    (so ``Roi.crop`` -> resize lands a 5:3 deskewed image). Carries the deskew ``angle``."""
    (cx, cy), (w, h), angle = rect
    target = 5 / 3
    if w / h < target:  # widen the short side to 5:3 (never shrink -> no content lost)
        w = target * h
    else:
        h = w / target
    sx, sy = ref_w / frame_w, ref_h / frame_h
    bx = int(round((cx - w / 2) * sx))
    by = int(round((cy - h / 2) * sy))
    return RoiBox(
        x=max(0, bx), y=max(0, by), w=max(1, int(round(w * sx))), h=max(1, int(round(h * sy))),
        ref_w=ref_w, ref_h=ref_h, angle=float(angle),
    )


def deskew_crop(frame: np.ndarray, region: RoiBox, *, out: tuple[int, int] = (800, 480)) -> np.ndarray:
    """Deskewed 5:3 crop of ``frame`` for ``region`` (screen_roi), resized to ``out``."""
    return cv2.resize(roi_from_region(region).crop(frame), out, interpolation=cv2.INTER_AREA)


def fit_camera_circle(bright_ref_bgr: np.ndarray) -> tuple[float, float, float]:
    """Centre + radius of the bright central disc in a deskewed ``out``-sized aligned frame.

    Thresholds the bright disc, takes the largest blob, and fits a circle via the contour's area
    (radius = sqrt(area/pi)) about its centroid -- tighter than minEnclosingCircle, which the glare
    streak inflates."""
    g = cv2.cvtColor(bright_ref_bgr, cv2.COLOR_BGR2GRAY)
    _, m = cv2.threshold(g, int(np.percentile(g, 80)), 255, cv2.THRESH_BINARY)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("no bright disc found for the camera circle")
    disc = max(contours, key=cv2.contourArea)
    mo = cv2.moments(disc)
    area = cv2.contourArea(disc)
    cx = mo["m10"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[1] / 2
    cy = mo["m01"] / mo["m00"] if mo["m00"] else bright_ref_bgr.shape[0] / 2
    r = float(np.sqrt(area / np.pi))
    return float(cx), float(cy), r


def arc_bands_from_circle(
    cx: float, cy: float, r: float, *, ref_w: int = 800, ref_h: int = 480, pad: int = 4
) -> tuple[RoiBox, RoiBox]:
    """Two mirror-symmetric bands on the circle's left/right edges (outer ~35% of the radius,
    middle 60% vertically) -- excludes the top corners (battery) and the top/bottom of the disc."""
    bw = max(1, int(round(0.35 * r)))
    y = max(0, int(round(cy - 0.6 * r)) - pad)
    h = min(ref_h - y, int(round(1.2 * r)) + 2 * pad)
    lx = max(0, int(round(cx - r)) - pad)
    rx = min(ref_w - bw, int(round(cx + r - 0.35 * r)))
    # enforce exact mirror symmetry about the frame centre using the left band
    rx = ref_w - (lx + bw)
    left = RoiBox(x=lx, y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    right = RoiBox(x=max(0, rx), y=y, w=bw, h=h, ref_w=ref_w, ref_h=ref_h)
    return left, right
```

(Clean up the stray first loop in `detect_screen_rects` — keep only the second de-dupe loop that builds `out`; the snippet shows the intent, implement just the `out`/`seen` loop. `_SCREEN_*` constants, `_screen_likeness`, `_bbox_iou` stay from the prior implementation.)

- [ ] **Step 4: Run the geometry tests + type-check**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "screen_rects or screen_roi_from_rect or camera_circle or arc_bands" -v && uv run mypy src`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(calibration): rotated-rect screen + 5:3 deskew ref + circle + symmetric bands"
```

---

### Task 6: `calibration.py` — red sampling, threshold, writer (drop green)

**Files:**
- Modify: `src/arm101_hand/system_camera/calibration.py`
- Test: `tests/unit/test_system_camera_calibration.py`

**Interfaces:**
- Produces:
  - `sample_red_band(region_bgr, *, lo_pct=5, hi_pct=95) -> list[HsvBand]` — 1–2 red bands (0/180 wrap).
  - `suggest_coverage_threshold(red_cov_on_red, red_cov_on_bright, *, floor=0.02) -> float`.
  - `write_calibration_values(config_path, *, screen_roi, left_arc, right_arc, red_bands, coverage_threshold) -> None`.
- Removed: `sample_hsv_band` (green path), `_GREEN_PRIOR`, the `green_bands` arg of the writer.

- [ ] **Step 1: Write failing tests** — update `tests/unit/test_system_camera_calibration.py`: rename/retarget the sampling + writer tests:

```python
from arm101_hand.system_camera.calibration import (
    sample_red_band,
    suggest_coverage_threshold,
    write_calibration_values,
)


def test_sample_red_band_brackets_and_wraps():
    hsv = np.zeros((20, 20, 3), dtype=np.uint8)
    hsv[:, :10] = (2, 200, 200)  # near hue 0
    hsv[:, 10:] = (178, 200, 200)  # near hue 180
    bands = sample_red_band(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
    assert len(bands) == 2
    assert any(b.h_lo == 0 for b in bands) and any(b.h_hi == 180 for b in bands)


def test_suggest_coverage_threshold_midpoint_and_floor():
    assert suggest_coverage_threshold(0.20, 0.0) == 0.10
    assert suggest_coverage_threshold(0.20, 0.10) == 0.15
    assert suggest_coverage_threshold(0.02, 0.0) == 0.02


def test_write_calibration_preserves_comments_and_updates(tmp_path):
    dst = tmp_path / "system_camera_config.yaml"
    dst.write_text(_DATA.read_text(encoding="utf-8"), encoding="utf-8")
    write_calibration_values(
        dst,
        screen_roi=RoiBox(x=10, y=8, w=400, h=240, ref_w=800, ref_h=480, angle=-0.9),
        left_arc=RoiBox(x=90, y=130, w=70, h=230, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=640, y=130, w=70, h=230, ref_w=800, ref_h=480),
        red_bands=[HsvBand(h_lo=0, s_lo=40, v_lo=50, h_hi=10, s_hi=255, v_hi=255)],
        coverage_threshold=0.08,
    )
    text = dst.read_text(encoding="utf-8")
    assert "Automated trigger" in text and dst.with_suffix(".yaml.bak").exists()
    cfg = load_system_camera_config(dst)
    assert cfg.screen_roi.angle == -0.9 and (cfg.screen_roi.ref_w, cfg.screen_roi.ref_h) == (800, 480)
    assert cfg.auto_trigger.coverage_threshold == 0.08
    assert (cfg.auto_trigger.left_arc.x, cfg.auto_trigger.right_arc.x) == (90, 640)
```

(Delete the old `test_sample_hsv_band_*`, `test_write_calibration_rejects_invalid_without_writing` keeps working only if it doesn't pass `green_bands` — update it to the new writer signature with `red_bands=[]` to trigger the `min_length` rejection.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -k "red_band or coverage_threshold or write_calibration" -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `calibration.py`: rename `sample_hsv_band` to `sample_red_band` dropping the `color`/green branch (keep the red hue-wrap split logic + `_RED_PRIOR`); delete `_GREEN_PRIOR`; keep `suggest_coverage_threshold`; update `write_calibration_values` to drop `green_bands` and write `screen_roi` (incl. `angle` via `_roibox_map`) — add `"angle": r.angle` to `_roibox_map`:

```python
def _roibox_map(r: RoiBox) -> CommentedMap:
    return _flow_map(
        {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "ref_w": r.ref_w, "ref_h": r.ref_h, "angle": r.angle}
    )


def sample_red_band(region_bgr: np.ndarray, *, lo_pct: float = 5, hi_pct: float = 95) -> list[HsvBand]:
    """Percentile-bracket the red arc pixels into 1-2 bands (0/180 hue wrap). Raises if none match."""
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    pts = hsv[_prior_mask(hsv, _RED_PRIOR) > 0]
    if pts.size == 0:
        raise ValueError("no red pixels to sample in this region")
    s_lo = int(np.percentile(pts[:, 1], lo_pct))
    v_lo = int(np.percentile(pts[:, 2], lo_pct))
    hue = pts[:, 0]
    bands: list[HsvBand] = []
    near_zero, near_180 = hue[hue <= 90], hue[hue > 90]
    if near_zero.size:
        bands.append(HsvBand(h_lo=0, s_lo=s_lo, v_lo=v_lo, h_hi=int(np.percentile(near_zero, hi_pct)), s_hi=255, v_hi=255))
    if near_180.size:
        bands.append(HsvBand(h_lo=int(np.percentile(near_180, lo_pct)), s_lo=s_lo, v_lo=v_lo, h_hi=180, s_hi=255, v_hi=255))
    return bands


def write_calibration_values(
    config_path: Path, *, screen_roi: RoiBox, left_arc: RoiBox, right_arc: RoiBox,
    red_bands: list[HsvBand], coverage_threshold: float,
) -> None:
    """Round-trip the YAML (comments preserved), updating screen_roi + the auto_trigger arc regions /
    red bands / threshold. Validates via pydantic + writes a .bak BEFORE touching the original."""
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    data = yaml_rt.load(config_path.read_text(encoding="utf-8"))
    data["screen_roi"] = _roibox_map(screen_roi)
    at = data["auto_trigger"]
    at["left_arc"] = _roibox_map(left_arc)
    at["right_arc"] = _roibox_map(right_arc)
    at["red_bands"] = [_hsv_map(b) for b in red_bands]
    at["coverage_threshold"] = float(coverage_threshold)
    buf = io.StringIO()
    yaml_rt.dump(data, buf)
    SystemCameraConfig.model_validate(yaml.safe_load(buf.getvalue()))
    config_path.with_suffix(config_path.suffix + ".bak").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with config_path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)
```

(Delete `detect_arc_regions`, `_half_bbox`, `_mirror_bbox` if now unused — the circle-based bands replace them. Keep `_prior_mask`, `_RED_PRIOR`.)

- [ ] **Step 4: Run all calibration tests + type-check**

Run: `uv run pytest tests/unit/test_system_camera_calibration.py -v && uv run mypy src`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff format src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
uv run ruff check src tests
git add src/arm101_hand/system_camera/calibration.py tests/unit/test_system_camera_calibration.py
git commit -m "feat(calibration): red-only band sampling + writer (angle, no green)"
```

---

### Task 7: `calibrate_view.py` — 3-capture deskew/circle/red-only confirm

**Files:**
- Modify: `scripts/calibration/system_camera/calibrate_view.py`
- Modify: `scripts/calibration/system_camera/README.md`

**Interfaces:** Consumes everything from Tasks 1–6.

- [ ] **Step 1: Rewrite the pipeline body of `calibrate_view.py`.** Keep the existing terminal-key infra (`_poll_key`, `_open_window`, `imshow_fit`, the `_pick_*` window pattern). Change:
  - imports: `from arm101_hand.system_camera.calibration import (arc_bands_from_circle, deskew_crop, detect_screen_rects, fit_camera_circle, sample_red_band, screen_roi_from_rect, suggest_coverage_threshold, write_calibration_values)`; `from arm101_hand.system_camera.arc_detector import _band_mask, detect, red_coverage`; `from arm101_hand.system_camera import Roi, imshow_fit, open_capture, roi_from_region`.
  - `_DETECT = (800, 480)`.
  - `_pick_roi(white)`: build candidates from `detect_screen_rects` → `screen_roi_from_rect` (each a `RoiBox` at 800×480 ref with angle); draw each candidate by deskewing the white frame (`deskew_crop`) as a thumbnail OR draw the rotated box on the full frame via `cv2.boxPoints`. Return the chosen `RoiBox` (not a `Roi`). Keep `m` = manual `selectROI` (angle 0).
  - Flow: capture/`--from-files` WHITE, RED, BRIGHT. `screen_roi = _pick_roi(white)`. `red_ref = deskew_crop(red, screen_roi)`, `bright_ref = deskew_crop(bright, screen_roi)`.
  - `cx, cy, r = fit_camera_circle(bright_ref)`; `left_arc, right_arc = arc_bands_from_circle(cx, cy, r)`.
  - `red_bands = sample_red_band(roi_from_region(left_arc).crop(red_ref))` (fall back to the right arc on `ValueError`).
  - threshold: `red_cov_red = red_coverage(cv2.cvtColor(roi_from_region(left_arc).crop(red_ref), cv2.COLOR_BGR2HSV), trial)`; `red_cov_bright = ...(bright_ref...)`; `coverage_threshold = suggest_coverage_threshold(red_cov_red, red_cov_bright)`. Build a `trial` `AutoTriggerConfig(left_arc, right_arc, red_bands, coverage_threshold)`; recompute coverages with `trial` then re-derive the threshold (two-pass) so the sampled bands drive it.
  - Confirm screen (two panels via `imshow_fit(np.hstack)`): RED panel = `red_ref` + both arc boxes + red mask tint + `detect(red_ref, trial)` → caption `L=RED R=RED` (expect both red); BRIGHT panel = `bright_ref` + the fitted circle (`cv2.circle`) + both arc boxes + red mask tint + `detect(bright_ref, trial)` → caption `L=clear R=clear`. Verdict line: `gate both RED: {both_red on red} | release both clear: {both_clear on bright}`.
  - Keys: `y` write (`write_calibration_values(_CONFIG_PATH, screen_roi=screen_roi, left_arc=left_arc, right_arc=right_arc, red_bands=red_bands, coverage_threshold=coverage_threshold)`), `e` nudge (selectROI the two arc bands on `red_ref`, rebuild `trial`), `r` redo, `q` quit.
  - `--from-files WHITE RED BRIGHT` (rename the 3rd metavar to `BRIGHT`).
  - On `fit_camera_circle` `ValueError`: print a warning and fall back to fixed symmetric bands `arc_bands_from_circle(_DETECT[0] * 0.5, _DETECT[1] * 0.5, _DETECT[1] * 0.42)`.

Provide the full rewritten file (the existing file is ~340 lines; keep all helper infra, replace the green/arc-region logic with the above). Update the module docstring Flow to: white → pick deskewed 5:3 ROI; red → red band; bright → circle + symmetric bands + validate not-red; confirm red-only.

- [ ] **Step 2: Update the README** — `scripts/calibration/system_camera/README.md`: 3 captures (white / red / **bright/aligned**), `--from-files WHITE RED BRIGHT`, red-only confirm (RED panel both-red, BRIGHT panel both-clear), keys.

- [ ] **Step 3: Lint, compile, `--help`**

```bash
uv run ruff format scripts/calibration/system_camera/calibrate_view.py
uv run ruff check scripts/calibration/system_camera/calibrate_view.py
uv run python -m py_compile scripts/calibration/system_camera/calibrate_view.py
uv run python scripts/calibration/system_camera/calibrate_view.py --help
```
Expected: clean, exits 0. (Do NOT run the GUI `--from-files` flow here — it opens windows. The operator validates at the bench.)

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/system_camera/calibrate_view.py scripts/calibration/system_camera/README.md
git commit -m "feat(calibration): calibrate_view 3-capture deskew/circle/red-only confirm"
```

---

### Task 8: Demo HUD + consumer compile

**Files:**
- Modify: `scripts/demos/grab_auto_trigger_analysis.py`

- [ ] **Step 1: Update the HUD** — in `grab_auto_trigger_analysis.py`, `_overlay` (line ~138) uses `alignment.left`/`alignment.right` (gone). Replace with the red-only fields + the phase:

```python
def _overlay(mode: str, alignment, armed_phase: str) -> tuple[str, tuple[int, int, int]]:
    """Build the on-screen status line + colour from the current mode and alignment."""
    if mode != "AUTO":
        return "MANUAL  (m=auto, SPACE=capture)", (200, 200, 200)
    lr = "RED" if alignment.left_red else "clear"
    rr = "RED" if alignment.right_red else "clear"
    label = f"AUTO [{armed_phase}]  L:{lr} R:{rr}"
    color = (0, 0, 255) if (alignment.left_red or alignment.right_red) else (0, 255, 0)
    return label, color
```

Also: this demo references `auto_trigger.WAIT_GREEN` or `state_auto.phase` for the HUD `armed_phase`? Check `git grep -n "WAIT_GREEN\|\.phase\|green_since" scripts/demos/grab_auto_trigger_analysis.py` and replace any `WAIT_GREEN` import/use with the new phases (`WAIT_RED` etc.); `armed_phase` just displays `state_auto.phase` (a string) — no change needed beyond imports.

- [ ] **Step 2: Compile + lint all touched consumers**

```bash
uv run python -m py_compile scripts/demos/grab_auto_trigger_analysis.py scripts/demos/grab_trigger_capture.py scripts/demos/grab_trigger_capture_analysis.py scripts/diagnostics/system_camera/usb_camera_roi_preview.py scripts/diagnostics/system_camera/usb_camera_focus_probe.py
uv run ruff check scripts
git grep -n "green_bands\|require_no_red\|require_clear_between\|\.left\b\|classify_band\|AlignmentState(.*ready" scripts/ src/ | grep -v test || echo "no stale refs"
```
Expected: compile clean, ruff clean, no stale references (the grep should return only legitimate matches or nothing).

- [ ] **Step 3: Commit**

```bash
git add scripts/demos/grab_auto_trigger_analysis.py
git commit -m "feat(demo): grab_auto_trigger_analysis HUD shows red/clear + phase"
```

---

### Task 9: Docs + memory + full CI

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/conventions/06-documentation-protocol.md`

- [ ] **Step 1: `CLAUDE.md`** — update the `system_camera` §3 description + §7 tech-debt note: ROI is now a deskewed **5:3 (800×480)** crop (`screen_roi` carries a rotation `angle`); arc detection is **red-only** with a **red→not-red gated** auto-trigger; arc regions are circle-based symmetric bands; calibration takes **3 captures (white/red/bright)**. Update the `calibrate_view` workflow line's `--from-files` to `WHITE RED BRIGHT`.

- [ ] **Step 2: `README.md`** — mirror the calibrate_view command + the red-only/deskew description.

- [ ] **Step 3: DRY registry** — in `docs/conventions/06-documentation-protocol.md`, add a row for this spec (`2026-06-23-system-camera-deskew-circle-redgate-design.md`) + this plan, noting it supersedes the arc geometry/logic of the 2026-06-22 view-calibration entry.

- [ ] **Step 4: Full CI gate**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md docs/conventions/06-documentation-protocol.md
git commit -m "docs: 5:3 deskew + red-gate arc detection + 3-capture calibration"
```

---

## Bench verification (operator-run, after merge)
Not automatable. Run `calibrate_view.py --from-files <white> <red> <bright>` on the existing captures (the `*_green.jpg` is the bright frame): confirm candidate #1 lands on the screen, the deskewed crop is upright 5:3 with a round circle, the arc bands sit on the circle's edges (battery excluded), and the confirm reads RED panel `both RED` / BRIGHT panel `both clear`. Then a live run + a real `grab_auto_trigger_analysis` AUTO pass (misalign → align fires once; blank screen does not fire).

## Self-Review

**Spec coverage:** §3 logic → Tasks 3 (detector) + 4 (gate machine) + 8 (HUD); §4 deskew/5:3/angle → Tasks 1 (schema) + 2 (Roi) + 5 (screen_roi_from_rect); §5 circle + symmetric bands → Task 5; §6 captures/calibration funcs → Tasks 5 + 6 + 7; §7 schema v7 → Task 1; §8 device layer/consumers → Tasks 2,3,4,8 (preview unchanged — noted); §9 confirm UX → Task 7; §10 error handling → Task 5 (`fit_camera_circle` raises → Task 7 fallback) + Task 6 (`sample_red_band` raises) + writer validation; §11 testing → Tasks 1–6; §12 docs → Task 9.

**Placeholder scan:** the `detect_screen_rects` snippet in Task 5 Step 3 shows a stray first loop — the step text explicitly says implement only the `out`/`seen` de-dupe loop; everything else is concrete code.

**Type consistency:** `AlignmentState(left_red, right_red, left_cov, right_cov)` consistent across Tasks 3, 4, 8; `screen_roi_from_rect(...) -> RoiBox(ref 800×480, angle)` consistent with `Roi`/`roi_from_region` (Task 2) and the writer `_roibox_map` (Task 6, adds `angle`); `detect_screen_rects -> rotated rect` consumed by `screen_roi_from_rect` (Task 5) and `calibrate_view` (Task 7); `arc_bands_from_circle -> (RoiBox, RoiBox)` consumed by Task 7; `write_calibration_values(... no green_bands ...)` matches Task 7's call. `coverage_threshold` single knob throughout.
