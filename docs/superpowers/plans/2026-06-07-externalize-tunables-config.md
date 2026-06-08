# Externalize Tunables + Restructure Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `src/arm101_hand/data/{arm,hand}_config.yaml` the single operator tuning surface per device (connection · safety · tuning · poses), keep calibration files as measurement-only artifacts, and consolidate motor-ID identity into one code location each.

**Architecture:** Additive-then-switch refactor. First introduce the new primitive schemas + the IL-3 finger→servo-ID table alongside the old ones (repo stays green). Then switch the hand consumers, then the arm consumers, then docs. Each commit independently leaves host tests + `mypy src` + `ruff` green (IL-6's sanctioned split for a >8-file cross-device refactor).

**Tech Stack:** Python 3.12, pydantic v2, PyYAML (`safe_load`/`safe_dump` only), pytest, ruff, mypy, uv.

**Design spec:** `docs/superpowers/specs/2026-06-07-externalize-tunables-config-design.md`

**Branch:** `refactor/externalize-config-tunables` (already created).

---

## Conventions for every commit

- Commit message: Conventional Commits, scope per `05-git-workflow.md`. Reference Iron Laws in the body.
- Green gate before each commit (run all three):
  ```bash
  uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest -m "not hardware" -q
  ```
- `yaml.safe_load`/`yaml.safe_dump` only. No `subprocess` without `timeout=`.
- pydantic models: `model_config = ConfigDict(extra="forbid")`; every knob carries `Field(description=...)` (the single doc home, IL-7).

---

## File structure (what each touched file is responsible for, after the refactor)

| File | Responsibility |
|---|---|
| `src/arm101_hand/config/motor_ids.py` *(new)* | IL-3 canon: `FINGER_NAMES` + `FINGER_SERVO_IDS` (finger→(id1,id2)) lookup tables (primitive). |
| `src/arm101_hand/config/arm_config.py` *(new, replaces `arm_poses.py`)* | `ArmConfig` schema: `connection`/`safety`/`tuning`/`poses` + `load_arm_config`/`save_arm_config`. |
| `src/arm101_hand/config/hand_config.py` *(new, replaces `hand_poses.py`)* | `HandConfig` schema: `connection`/`safety`/`tuning`/`poses` (per-finger) + `load_hand_config`/`save_hand_config`. |
| `src/arm101_hand/config/calibration.py` | Trimmed: measured `middle_pos` + `limits` only; `id` dropped; `middle_pos_by_id()` uses `FINGER_SERVO_IDS`; require `schema_version >= 3`. |
| `src/arm101_hand/config/app_config.py` | **Deleted.** |
| `src/arm101_hand/config/__init__.py` | Export new names; drop deleted ones. |
| `src/arm101_hand/data/arm_config.yaml` *(new home)* | Arm connection/safety/tuning/poses. |
| `src/arm101_hand/data/hand_config.yaml` *(new home)* | Hand connection/safety/tuning/poses (per-finger). |
| `src/arm101_hand/data/README.md` | Doc home for what each knob means. |
| `scripts/calibration/amazing_hand/hand_calib_values.yaml` | Trimmed to `fingers: {middle_pos, limits}`, `schema_version: 3`. |
| `src/arm101_hand/hand/pose_resolver.py` | New `HandPose` shape + `FINGER_SERVO_IDS`; `margin_deg` default from `HandConfig.tuning`. |
| `src/arm101_hand/scripts/device_setup.py` | Loads `arm_config`; path constants under `src/.../data`; tuning knobs from `ArmConfig.tuning`. |
| `src/arm101_hand/scripts/arm_torque.py` | Reuses device-layer bus; `_ARM_MOTOR_IDS` deleted. |
| `scripts/calibration/so_arm101/{sweep,set_pose,jog,capture_pose}.py` | Knobs from `ArmConfig.tuning`. |
| `scripts/calibration/amazing_hand/{motor_reset,middle_calib,range_calib,finger_test,full_hand_test,set_pose,jog}.py` | Connection from `HandConfig.connection`; knobs/speeds from `HandConfig.tuning`. |
| `scripts/diagnostics/{scan,show_calib}.py` | Per-device thresholds; hand connection from `HandConfig`. |
| `scripts/demos/grab_sequence.py` | New config paths + loaders. |
| `pyproject.toml` | Ship `src/arm101_hand/data/*.yaml` as package data. |
| `tests/unit/*` | New schema shapes/paths; `test_app_config.py` deleted. |

---

## TASK 1 — Introduce primitive schemas + ID canon (additive; old code untouched)

**Commit boundary:** `feat(config): add ArmConfig/HandConfig schemas + finger→servo-id canon`

### Files
- Create: `src/arm101_hand/config/motor_ids.py`
- Create: `src/arm101_hand/config/arm_config.py`
- Create: `src/arm101_hand/config/hand_config.py`
- Modify: `src/arm101_hand/config/__init__.py` (add exports; keep old ones for now)
- Create: `tests/unit/test_motor_ids.py`, `tests/unit/test_arm_config_schema.py`, `tests/unit/test_hand_config_schema.py`

- [ ] **Step 1.1 — Write `motor_ids.py`**

```python
# src/arm101_hand/config/motor_ids.py
"""IL-3 motor identity canon for the hand (primitive lookup tables).

Single source for the finger→servo-ID mapping. Lives in the primitive layer so
both ``config.calibration.HandCalibration.middle_pos_by_id`` and the device-layer
``hand.pose_resolver`` can use it without an upward import (01-module-layering §2).
Odd ID = servo_1, even ID = servo_2; even IDs are wire-inverted in kinematics.
"""

from __future__ import annotations

# Canonical finger order (IL-3): pointer → pinky-side → thumb.
FINGER_NAMES: tuple[str, ...] = ("index", "middle", "ring", "thumb")

# Each finger's two SCS0009 servo IDs, (servo_1, servo_2). IDs 1-8, odd/even per finger.
FINGER_SERVO_IDS: dict[str, tuple[int, int]] = {
    "index": (1, 2),
    "middle": (3, 4),
    "ring": (5, 6),
    "thumb": (7, 8),
}
```

- [ ] **Step 1.2 — Write `tests/unit/test_motor_ids.py` (failing import first)**

```python
from arm101_hand.config.motor_ids import FINGER_NAMES, FINGER_SERVO_IDS


def test_finger_names_canonical_order():
    assert FINGER_NAMES == ("index", "middle", "ring", "thumb")


def test_servo_ids_cover_1_to_8_uniquely():
    flat = [i for pair in FINGER_SERVO_IDS.values() for i in pair]
    assert sorted(flat) == list(range(1, 9))


def test_each_finger_has_odd_then_even():
    for s1, s2 in FINGER_SERVO_IDS.values():
        assert s1 % 2 == 1 and s2 % 2 == 0 and s2 == s1 + 1


def test_keys_match_finger_names():
    assert tuple(FINGER_SERVO_IDS.keys()) == FINGER_NAMES
```

- [ ] **Step 1.3 — Run:** `uv run pytest tests/unit/test_motor_ids.py -q` → PASS.

- [ ] **Step 1.4 — Write `arm_config.py`**

```python
# src/arm101_hand/config/arm_config.py
"""Pydantic schema for ``src/arm101_hand/data/arm_config.yaml``.

The single operator tuning surface for the SO-ARM101: connection, diagnostics
safety thresholds, motion tuning knobs, and named poses (degrees per joint,
lerobot ``use_degrees`` frame). Measured calibration stays in so101_follower.json.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ArmConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str = Field(default="COM20", description="SO-ARM101 12V bus COM port")


class ArmSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0, description="servo coil temp warn (°C)")
    voltage_warn_pct: float = Field(default=5.0, ge=0.0, le=100.0, description="warn when bus V deviates this % from 12V nominal")


class ArmTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    park_velocity: int = Field(default=600, ge=0, le=4095, description="gentle home-move Goal_Velocity (raw units)")
    jog_step_min: float = Field(default=1.0, gt=0, description="min jog step (deg)")
    jog_step_max: float = Field(default=15.0, gt=0, description="max jog step (deg)")
    jog_step_default: float = Field(default=5.0, gt=0, description="initial jog step (deg)")
    jog_step_increment: float = Field(default=1.0, gt=0, description="+/- jog step adjust (deg)")
    load_warn: int = Field(default=600, ge=0, description="jog high-load warning (raw)")
    load_stall: int = Field(default=600, ge=0, description="sweep stall threshold (raw)")
    sweep_margin_default: float = Field(default=90.0, gt=0, lt=100, description="sweep stop margin from endpoint (deg)")
    settle_sweep_s: float = Field(default=1.2, ge=0, description="sweep dwell after target (s)")
    settle_set_pose_s: float = Field(default=2.0, ge=0, description="set_pose settle after reach (s)")
    settle_capture_s: float = Field(default=0.5, ge=0, description="capture_pose settle (s)")
    home_tolerance_steps: int = Field(default=25, ge=0, description="home-reach tolerance (raw steps)")
    home_timeout_s: float = Field(default=8.0, ge=0, description="home-move timeout (s)")
    home_poll_s: float = Field(default=0.05, gt=0, description="home-reach poll interval (s)")


class ArmPose(BaseModel):
    """One arm pose: degrees per motor, all five required."""
    model_config = ConfigDict(extra="forbid")
    shoulder_pan: float
    shoulder_lift: float
    elbow_flex: float
    wrist_flex: float
    wrist_roll: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class ArmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: ArmConnection = Field(default_factory=ArmConnection)
    safety: ArmSafety = Field(default_factory=ArmSafety)
    tuning: ArmTuning = Field(default_factory=ArmTuning)
    poses: dict[str, ArmPose] = Field(default_factory=dict)


def load_arm_config(path: Path) -> ArmConfig:
    """Parse and validate ``arm_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ArmConfig.model_validate(raw)


_ARM_SAVE_HEADER = (
    "# arm_config.yaml -- SO-ARM101 operator config (single tuning surface).\n"
    "# connection/safety/tuning are hand-editable; 'poses' is written by jog.py /\n"
    "# capture_pose.py. Comments are regenerated on save -- see data/README.md for\n"
    "# what each tuning knob means. 'home' is the default parking / safe-park pose.\n"
)


def save_arm_config(path: Path, config: ArmConfig) -> None:
    """Write an ``ArmConfig`` to YAML atomically (tmp + ``os.replace``).

    Dumps the whole model so a load-modify-save (e.g. saving one pose) preserves
    connection/safety/tuning. ``sort_keys=False`` keeps field order.
    """
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_ARM_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 1.5 — Write `tests/unit/test_arm_config_schema.py`**

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import ArmConfig, load_arm_config, save_arm_config


def test_defaults_match_seed():
    cfg = ArmConfig()
    assert cfg.connection.port == "COM20"
    assert cfg.safety.temp_warn_c == 50.0
    assert cfg.tuning.park_velocity == 600
    assert cfg.tuning.jog_step_default == 5.0


def test_round_trip_preserves_all_sections(tmp_path: Path):
    cfg = ArmConfig(poses={"home": {"shoulder_pan": 1, "shoulder_lift": 2,
        "elbow_flex": 3, "wrist_flex": 4, "wrist_roll": 5}})  # type: ignore[arg-type]
    cfg.tuning.load_warn = 555
    out = tmp_path / "arm_config.yaml"
    save_arm_config(out, cfg)
    reloaded = load_arm_config(out)
    assert reloaded.tuning.load_warn == 555
    assert reloaded.poses["home"].as_dict()["wrist_roll"] == 5
    assert reloaded.connection.port == "COM20"


def test_extra_key_rejected(tmp_path: Path):
    out = tmp_path / "bad.yaml"
    out.write_text("schema_version: 1\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_arm_config(out)
```

- [ ] **Step 1.6 — Write `hand_config.py`**

```python
# src/arm101_hand/config/hand_config.py
"""Pydantic schema for ``src/arm101_hand/data/hand_config.yaml``.

Single operator tuning surface for the AmazingHand: connection, diagnostics
safety thresholds, motion tuning knobs, and named poses grouped per finger
(``[servo_1, servo_2]`` servo-frame degrees rel. to each servo's calibrated
middle, even-ID pre-inverted). Measured calibration stays in hand_calib_values.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from arm101_hand.config.motor_ids import FINGER_NAMES

_PAIR = Field(min_length=2, max_length=2)


class HandConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str = Field(default="COM18", description="AmazingHand 5V bus COM port")
    baudrate: int = Field(default=1_000_000, ge=9600, description="SCS0009 bus baudrate")
    timeout: float = Field(default=0.5, ge=0.0, le=5.0, description="serial read timeout (s)")


class HandSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0, description="servo coil temp warn (°C)")
    voltage_warn_pct: float = Field(default=5.0, ge=0.0, le=100.0, description="warn when bus V deviates this % from 5V nominal")


class HandSpeeds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: int = Field(default=5, ge=1, le=7, description="extension speed")
    close: int = Field(default=3, ge=1, le=7, description="flexion speed")


class HandTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: int = Field(default=4, ge=1, le=7, description="jog speed (1-7)")
    speeds: HandSpeeds = Field(default_factory=HandSpeeds)
    step_min: int = Field(default=1, ge=1, description="min jog step (deg)")
    step_max: int = Field(default=15, ge=1, description="max jog step (deg)")
    step_default: int = Field(default=5, ge=1, description="initial jog step (deg)")
    pose_margin_deg: float = Field(default=5.0, ge=0, description="open/close back-off from limits (deg)")
    load_warn_threshold: int = Field(default=80, ge=0, le=100, description="SCS0009 load %% duty warn")
    jog_base_min: float = Field(default=-60, description="range_calib base discovery floor (logical deg)")
    jog_base_max: float = Field(default=130, description="range_calib base discovery ceil (logical deg)")
    jog_side_min: float = Field(default=-60, description="range_calib side discovery floor (logical deg)")
    jog_side_max: float = Field(default=60, description="range_calib side discovery ceil (logical deg)")


class HandPose(BaseModel):
    """One pose: each finger's [servo_1, servo_2] servo-frame degrees."""
    model_config = ConfigDict(extra="forbid")
    index: list[int] = _PAIR
    middle: list[int] = _PAIR
    ring: list[int] = _PAIR
    thumb: list[int] = _PAIR

    def by_finger(self) -> dict[str, list[int]]:
        return {"index": self.index, "middle": self.middle, "ring": self.ring, "thumb": self.thumb}


class HandConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: HandConnection = Field(default_factory=HandConnection)
    safety: HandSafety = Field(default_factory=HandSafety)
    tuning: HandTuning = Field(default_factory=HandTuning)
    poses: dict[str, HandPose] = Field(default_factory=dict)


def load_hand_config(path: Path) -> HandConfig:
    """Parse and validate ``hand_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandConfig.model_validate(raw)


_HAND_SAVE_HEADER = (
    "# hand_config.yaml -- AmazingHand operator config (single tuning surface).\n"
    "# connection/safety/tuning are hand-editable; 'poses' is written by jog.py.\n"
    "# Comments are regenerated on save -- see data/README.md for knob meanings.\n"
)


def save_hand_config(path: Path, config: HandConfig) -> None:
    """Write a ``HandConfig`` to YAML atomically (tmp + ``os.replace``)."""
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_HAND_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)


# Re-exported so callers don't reach into motor_ids for the canonical order.
__all__ = ["HandConfig", "HandConnection", "HandSafety", "HandSpeeds", "HandTuning",
           "HandPose", "load_hand_config", "save_hand_config", "FINGER_NAMES"]
```

- [ ] **Step 1.7 — Write `tests/unit/test_hand_config_schema.py`**

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import HandConfig, load_hand_config, save_hand_config


def test_defaults():
    cfg = HandConfig()
    assert cfg.connection.port == "COM18"
    assert cfg.connection.baudrate == 1_000_000
    assert cfg.tuning.speeds.open == 5


def test_pose_per_finger_round_trip(tmp_path: Path):
    cfg = HandConfig(poses={"grab": {"index": [70, 2], "middle": [59, -19],
        "ring": [64, -14], "thumb": [59, -59]}})  # type: ignore[arg-type]
    out = tmp_path / "hand_config.yaml"
    save_hand_config(out, cfg)
    reloaded = load_hand_config(out)
    assert reloaded.poses["grab"].by_finger()["index"] == [70, 2]
    assert reloaded.poses["grab"].thumb == [59, -59]


def test_pose_pair_must_be_length_2(tmp_path: Path):
    out = tmp_path / "bad.yaml"
    out.write_text(
        "poses:\n  x:\n    index: [1]\n    middle: [1,2]\n    ring: [1,2]\n    thumb: [1,2]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_hand_config(out)
```

- [ ] **Step 1.8 — Update `config/__init__.py`** — add the new names, keep old ones (still in use):

Add imports:
```python
from .arm_config import ArmConfig, ArmConnection, ArmPose, ArmSafety, ArmTuning, load_arm_config, save_arm_config
from .hand_config import (
    HandConfig, HandConnection, HandPose, HandSafety, HandSpeeds, HandTuning,
    load_hand_config, save_hand_config,
)
from .motor_ids import FINGER_NAMES as FINGER_NAMES  # noqa: PLC0414  (re-export)
from .motor_ids import FINGER_SERVO_IDS
```
Add each new public name to `__all__`. (Do **not** remove `AppConfig`, `ArmPoseConfig`, `HandPoseConfig`, etc. yet — Tasks 2–3 retire them.) Note: `FINGER_NAMES` is now also exported from `motor_ids`; keep the existing `calibration.FINGER_NAMES` re-export line until Task 2 makes `calibration` import it from `motor_ids`.

- [ ] **Step 1.9 — Green gate** (full command block above). Expected: all pass; new tests included.

- [ ] **Step 1.10 — Commit**
```bash
git add src/arm101_hand/config/motor_ids.py src/arm101_hand/config/arm_config.py \
  src/arm101_hand/config/hand_config.py src/arm101_hand/config/__init__.py \
  tests/unit/test_motor_ids.py tests/unit/test_arm_config_schema.py tests/unit/test_hand_config_schema.py
git commit -m "feat(config): add ArmConfig/HandConfig schemas + finger->servo-id canon

Additive: introduces the new single-tuning-surface schemas and the IL-3
finger->servo-id lookup table (primitive layer, so config + device can share
it without an upward import). Old app_config/arm_poses/hand_poses remain until
the hand and arm consumers switch in the next commits. Refs IL-3, IL-7."
```

---

## TASK 2 — Switch the hand (calibration trim + data move + consumers)

**Commit boundary:** `refactor(hand): move connection/speeds/poses to hand_config; calib YAML is measurement-only`

This commit changes the hand calib schema (drop `id`, bump to v3), moves hand config data into the package with the per-finger pose shape, rewires `pose_resolver`, and switches every hand script + hand diagnostics path. Arm is untouched → repo stays green.

### Files
- Modify: `src/arm101_hand/config/calibration.py`
- Modify: `src/arm101_hand/hand/pose_resolver.py`
- Modify: `scripts/calibration/amazing_hand/hand_calib_values.yaml`
- Create: `src/arm101_hand/data/hand_config.yaml`
- Delete: `src/arm101_hand/config/hand_poses.py`; Modify `config/__init__.py` (drop hand_poses exports)
- Modify: `scripts/calibration/amazing_hand/{motor_reset,middle_calib,range_calib,finger_test,full_hand_test,set_pose,jog}.py`
- Modify: `scripts/diagnostics/{scan,show_calib}.py` (hand half)
- Modify/replace tests: `test_calibration.py`, `test_hand_pose_resolver.py`, `test_hand_poses_schema.py`→`test_hand_config_data.py`, `test_hand_config_save.py`

- [ ] **Step 2.1 — Trim `calibration.py`.** Edits:
  1. Replace the `FINGER_NAMES = (...)` definition with an import from the canon:
     ```python
     from arm101_hand.config.motor_ids import FINGER_NAMES, FINGER_SERVO_IDS
     ```
  2. Delete the `id: int = Field(ge=1, le=8)` field from `ServoCalibration` (leaving only `middle_pos: float`).
  3. Change `HandCalibration.schema_version` to require v3: `schema_version: int = Field(ge=3)`.
  4. Delete the fields `com_port`, `baudrate`, `timeout`, `speed`, `speeds` and the `PoseSpeeds` class (moved to `hand_config.HandSpeeds`/`HandTuning`).
  5. Rewrite `middle_pos_by_id` to use the canon table:
     ```python
     def middle_pos_by_id(self) -> dict[int, float]:
         """Flat ``{servo_id: middle_pos}`` lookup, IDs from FINGER_SERVO_IDS (IL-3)."""
         out: dict[int, float] = {}
         for name, finger in self.fingers.items():
             id1, id2 = FINGER_SERVO_IDS[name]
             out[id1] = finger.servo_1.middle_pos
             out[id2] = finger.servo_2.middle_pos
         return out
     ```
  Update the module docstring to say the file holds measured values only.

- [ ] **Step 2.2 — Update `config/__init__.py`:** remove `PoseSpeeds` from the `calibration` import line and from `__all__`. Remove `from .hand_poses import (...)` and its `__all__` entries (`POSITIONS_LEN`, `HandPose` *(old)*, `HandPoseConfig`, `load_hand_poses`, `save_hand_poses`). The new `HandPose` (per-finger) comes from `hand_config`.

- [ ] **Step 2.3 — Delete old module:** `git rm src/arm101_hand/config/hand_poses.py`.

- [ ] **Step 2.4 — Rewrite `pose_resolver.py`** to the new shape. Replace the import block and the two resolver helpers:

```python
from arm101_hand.config import HandCalibration, HandConfig
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.kinematics import compose_finger, degrees_to_servo_radians, even_id_inversion
```
`available_pose_names` and `resolve_hand_pose_targets` keep their signatures but take a `HandConfig` (was `HandPoseConfig`) — rename the param type and read `cfg.poses`. The `margin_deg` default stays `DEFAULT_POSE_MARGIN_DEG` (the CLI passes `cfg.tuning.pose_margin_deg`). New helpers:

```python
def _resolve_builtin(calib: HandCalibration, pose_name: str, margin_deg: float) -> dict[int, float]:
    targets: dict[int, float] = {}
    for finger_name, finger in calib.fingers.items():
        lim = finger.limits
        base = lim.base_max - margin_deg if pose_name == "close" else lim.base_min + margin_deg
        pos1, pos2 = compose_finger(int(round(base)), 0)  # logical; side = 0 (neutral)
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        targets[id1] = degrees_to_servo_radians(id1, pos1, finger.servo_1.middle_pos)
        targets[id2] = degrees_to_servo_radians(id2, pos2, finger.servo_2.middle_pos)
    return targets


def _resolve_stored(calib: HandCalibration, pose) -> dict[int, float]:
    """Decode a per-finger servo-frame pose to wire radians per servo."""
    targets: dict[int, float] = {}
    for finger_name, (s1_stored, s2_stored) in pose.by_finger().items():
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        finger = calib.fingers[finger_name]
        for servo_id, stored, mid in (
            (id1, s1_stored, finger.servo_1.middle_pos),
            (id2, s2_stored, finger.servo_2.middle_pos),
        ):
            deg_logical = even_id_inversion(servo_id, stored)
            targets[servo_id] = degrees_to_servo_radians(servo_id, deg_logical, mid)
    return targets
```
And in `resolve_hand_pose_targets`, change `poses_cfg.poses[pose_name].positions` → `poses_cfg.poses[pose_name]` (pass the `HandPose`). Update the module docstring's "stored 8-int array" wording to "per-finger [s1,s2] pairs".

- [ ] **Step 2.5 — Trim `hand_calib_values.yaml`** to measurement-only (bump to v3), preserving the current measured values:
```yaml
schema_version: 3
fingers:
  index:
    servo_1: {middle_pos: 30}
    servo_2: {middle_pos: -2}
    limits: {base_min: -20, base_max: 70, side_min: -40, side_max: 35}
  middle:
    servo_1: {middle_pos: -32}
    servo_2: {middle_pos: 20}
    limits: {base_min: -35, base_max: 65, side_min: -20, side_max: 15}
  ring:
    servo_1: {middle_pos: 22}
    servo_2: {middle_pos: 0}
    limits: {base_min: -35, base_max: 65, side_min: -25, side_max: 20}
  thumb:
    servo_1: {middle_pos: 0}
    servo_2: {middle_pos: -12}
    limits: {base_min: -40, base_max: 100, side_min: -55, side_max: 50}
```

- [ ] **Step 2.6 — Create `src/arm101_hand/data/hand_config.yaml`** (migrate connection/speeds from old calib YAML; convert the old flat `grab`/`middle` arrays to per-finger pairs — index=[p0,p1], middle=[p2,p3], ring=[p4,p5], thumb=[p6,p7]):
```yaml
schema_version: 1
connection:
  port: COM18
  baudrate: 1000000
  timeout: 0.5
safety:
  temp_warn_c: 50
  voltage_warn_pct: 5.0
tuning:
  speed: 4
  speeds: {open: 5, close: 3}
  step_min: 1
  step_max: 15
  step_default: 5
  pose_margin_deg: 5.0
  load_warn_threshold: 80
  jog_base_min: -60
  jog_base_max: 130
  jog_side_min: -60
  jog_side_max: 60
poses:
  middle:
    index: [0, 0]
    middle: [0, 0]
    ring: [0, 0]
    thumb: [0, 0]
  grab:
    index: [70, 2]
    middle: [59, -19]
    ring: [64, -14]
    thumb: [59, -59]
```

- [ ] **Step 2.7 — Switch hand scripts to `HandConfig` connection + tuning.** For each of `motor_reset.py`, `middle_calib.py`, `range_calib.py`, `finger_test.py`, `full_hand_test.py`, `set_pose.py`, `jog.py`:
  - Add a `HAND_CONFIG_PATH` constant pointing at the new package data dir. From a script at `scripts/calibration/amazing_hand/<x>.py`, repo root is `SCRIPT_DIR.parents[2]`, so:
    ```python
    HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"
    ```
  - Load it once at startup: `hcfg = load_hand_config(HAND_CONFIG_PATH)`.
  - Replace every `cfg.com_port` → `hcfg.connection.port`, `cfg.baudrate` → `hcfg.connection.baudrate`, `cfg.timeout` → `hcfg.connection.timeout` (grep showed `serial_port=cfg.com_port,` at: `full_hand_test.py:38`, `set_pose.py:87`, `range_calib.py:99`, `motor_reset.py:76`, `middle_calib.py:77`, `jog.py:174`, `finger_test.py:81`). Here `cfg` is the loaded calibration; calibration no longer carries the port.
  - Replace the in-script step/speed/dwell constants the spec lists for the hand with reads from `hcfg.tuning` (e.g. `full_hand_test.py` `MaxSpeed`/`CloseSpeed` → `hcfg.tuning.speeds.open`/`.close`; `range_calib.py` `STEP_*`, `JOG_*`, `LOAD_WARN_THRESHOLD` → `hcfg.tuning.*`).
  - `set_pose.py` / `jog.py` already had `HAND_CONFIG_PATH` pointing at `data/hand_config.yaml` (grep `set_pose.py:40`, `jog.py:60`) — repoint to the new package path and switch `load_hand_poses`→`load_hand_config`, `HandPoseConfig`→`HandConfig`. `set_pose.py` should pass `margin_deg=hcfg.tuning.pose_margin_deg` and `speeds` from `hcfg.tuning.speeds`.
  - **Servo-protocol micro-delays:** define `_SERVO_SYNC_S = 0.0002` (and `_SETTLE_S` where used) as a single named constant in `src/arm101_hand/hand/` (e.g. a new `hand/protocol.py` exposing `SERVO_SYNC_S`) and import it; do not scatter literals. (Operator-meaningful dwell times in demos/tests stay where they are or move to tuning per the spec — not protocol delays.)

- [ ] **Step 2.8 — Switch the hand half of diagnostics.** In `scan.py` (lines ~80-84) and `show_calib.py` (lines ~99-103), the hand port currently comes from the calibration object (`hand.com_port` / `cfg.com_port`). Load `HandConfig` and use `hcfg.connection.{port,baudrate,timeout}`. Use `hcfg.safety.{temp_warn_c,voltage_warn_pct}` for the hand thresholds (was the arm `app_config` safety). Keep `_HAND_NOMINAL_V = 5.0` in code (IL-1).

- [ ] **Step 2.9 — Update hand tests.**
  - `git rm tests/unit/test_hand_poses_schema.py`; create `tests/unit/test_hand_config_data.py` that loads the seeded `src/arm101_hand/data/hand_config.yaml` and asserts `poses["middle"].by_finger()` is all `[0,0]` and `connection.port == "COM18"`.
  - `test_hand_config_save.py`: update to build a `HandCalibration` v3 **without** `id`/`com_port`/`speed(s)` (use the §2.5 shape), and exercise `save_hand_config`/`load_hand_config` per-finger round trip.
  - `test_calibration.py`: drop `com_port`/`speed`/`id` from fixtures; set `schema_version: 3`; assert `middle_pos_by_id()` returns `{1:..,2:..,..,8:..}` via the canon table.
  - `test_hand_pose_resolver.py`: build `HandConfig` poses + v3 `HandCalibration`; assert `_resolve_stored` and `_resolve_builtin` produce the same `{servo_id: radians}` keys (1..8).

- [ ] **Step 2.10 — Green gate.** Expected: all pass.

- [ ] **Step 2.11 — Commit** (`refactor(hand): ...`, body references IL-3/IL-5/IL-7 and notes connection/speeds moved out of the calib YAML; calib bumped to schema v3).

---

## TASK 3 — Switch the arm (data move + ArmConfig consumers + SSOT) and delete `app_config`

**Commit boundary:** `refactor(arm): move arm config to package data; ArmConfig is the tuning surface; drop _ARM_MOTOR_IDS`

### Files
- Create: `src/arm101_hand/data/arm_config.yaml`
- Delete: `src/arm101_hand/config/app_config.py`, `src/arm101_hand/config/arm_poses.py`; Modify `config/__init__.py`
- Modify: `src/arm101_hand/scripts/device_setup.py`, `src/arm101_hand/scripts/arm_torque.py`
- Modify: `scripts/calibration/so_arm101/{sweep,set_pose,jog,capture_pose}.py`
- Modify: `scripts/diagnostics/{scan,show_calib}.py` (arm half)
- Modify: `scripts/demos/grab_sequence.py`
- Modify: `pyproject.toml`
- Delete `data/` (repo-root) once empty; Modify tests

- [ ] **Step 3.1 — Create `src/arm101_hand/data/arm_config.yaml`** (migrate `data/arm_config.yaml` poses + `data/app_config.yaml` arm/safety + arm tuning defaults):
```yaml
schema_version: 1
connection:
  port: COM20
safety:
  temp_warn_c: 50
  voltage_warn_pct: 5.0
tuning:
  park_velocity: 600
  jog_step_min: 1.0
  jog_step_max: 15.0
  jog_step_default: 5.0
  jog_step_increment: 1.0
  load_warn: 600
  load_stall: 600
  sweep_margin_default: 90.0
  settle_sweep_s: 1.2
  settle_set_pose_s: 2.0
  settle_capture_s: 0.5
  home_tolerance_steps: 25
  home_timeout_s: 8.0
  home_poll_s: 0.05
poses:
  home:
    shoulder_pan: 80.79120879120879
    shoulder_lift: -104.52747252747253
    elbow_flex: 90.76923076923077
    wrist_flex: -97.31868131868131
    wrist_roll: -0.3076923076923077
  grab:
    shoulder_pan: 84.21978021978022
    shoulder_lift: -102.68131868131869
    elbow_flex: 90.85714285714286
    wrist_flex: 31.64835164835165
    wrist_roll: -84.96703296703296
```

- [ ] **Step 3.2 — Rewrite `device_setup.py` config wiring.**
  - Path constants: `_REPO_ROOT = Path(__file__).resolve().parents[3]` stays; replace `APP_CONFIG_PATH`/`ARM_CONFIG_PATH` with a single `ARM_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "arm_config.yaml"`. (`CALIB_PATH` unchanged.)
  - Replace `load_app_config`/`load_arm_poses` imports with `load_arm_config`.
  - `load_arm_app_config()` → returns `ArmConfig` from `ARM_CONFIG_PATH` (keep the function name to limit churn, or rename to `load_arm_config_file()` and update the 5 callers — see Step 3.4; prefer keeping the name).
  - `build_follower`/`build_raw_bus`: `cfg.arm.port` → `cfg.connection.port`.
  - `gentle_velocity(cfg)` → `cfg.tuning.park_velocity`.
  - `load_home_degrees()` → read `load_arm_config(ARM_CONFIG_PATH).poses.get("home")`.
  - `_drive_home` default args `tolerance/timeout_s/poll_s` → make the caller pass `cfg.tuning.home_tolerance_steps/home_timeout_s/home_poll_s`; keep literal fallbacks only if a non-cfg caller exists (none do — thread cfg through).

- [ ] **Step 3.3 — Consolidate arm motor SSOT in `arm_torque.py`.** Delete `_ARM_MOTOR_IDS`, `_APP_CONFIG_PATH`, and the `load_app_config` import. Build the bus from the device subclass instead:
```python
from arm101_hand.scripts.device_setup import ARM_CONFIG_PATH, build_follower
from arm101_hand.config import load_arm_config
...
cfg = load_arm_config(ARM_CONFIG_PATH)
follower = build_follower(cfg, use_degrees=True)
follower.bus.connect()
# enable_torque / disable_torque on follower.bus; report follower.bus.motors keys/ids.
```
Adjust the file/validation error messages to `arm_config.yaml`. The motor-id mapping now has exactly one source (the subclass).

- [ ] **Step 3.4 — Switch arm motion scripts** `sweep.py`, `set_pose.py`, `jog.py`, `capture_pose.py`:
  - They import `load_arm_app_config`, `ARM_CONFIG_PATH` from `device_setup` (via `_common`) and `load_arm_poses` from config. Replace `load_arm_poses(ARM_CONFIG_PATH).poses` → `load_arm_config(ARM_CONFIG_PATH).poses`; for save paths (`jog.py`, `capture_pose.py`) replace `ArmPoseConfig`/`save_arm_poses` → `ArmConfig`/`save_arm_config` with **load-modify-save** (load existing `ArmConfig`, mutate `.poses`, save whole model — preserves tuning/connection).
  - Replace each module-level knob with a `cfg.tuning` read: `sweep.py` `_LOAD_STALL`→`cfg.tuning.load_stall`, `_SETTLE_S`→`cfg.tuning.settle_sweep_s`, `--margin` default→`cfg.tuning.sweep_margin_default`; `jog.py` `_LOAD_WARN`→`cfg.tuning.load_warn` and the `JOG_STEP_*`/`_STEP_INCREMENT` (in `robots/arm_jog.py`) sourced from `cfg.tuning.jog_step_*` (pass them into the jog state machine constructor rather than importing module constants); `set_pose.py` `_SETTLE_S`→`cfg.tuning.settle_set_pose_s`; `capture_pose.py` `_SETTLE_S`→`cfg.tuning.settle_capture_s`.
  - `robots/arm_jog.py`: keep `ARM_JOINTS`? No — `ARM_JOINTS` is defined in `calibration_summary.py` (device, single source); `arm_jog.py` should import it, not redefine. Convert `JOG_STEP_MIN/MAX/DEFAULT/_STEP_INCREMENT` from module constants to constructor parameters fed from `cfg.tuning`.

- [ ] **Step 3.5 — Switch arm half of diagnostics.** `scan.py`/`show_calib.py`: `load_arm_app_config()` now returns `ArmConfig`; `cfg.arm.port`→`cfg.connection.port`, `cfg.safety.*` unchanged shape (still `cfg.safety.temp_warn_c`). Keep `_ARM_NOMINAL_V = 12.0` in code (IL-1).

- [ ] **Step 3.6 — Switch `grab_sequence.py`.** `HAND_CONFIG_PATH`/`ARM_CONFIG_PATH` → new package data paths; `load_arm_poses`→`load_arm_config`, `load_hand_poses`→`load_hand_config`; hand connection from `HandConfig.connection` (was `hand_calib.com_port`).

- [ ] **Step 3.7 — Delete dead modules + exports.** `git rm src/arm101_hand/config/app_config.py src/arm101_hand/config/arm_poses.py`. In `config/__init__.py` remove `AppConfig`/`load_app_config` and `ARM_MOTORS`/`ArmPoseConfig`/`load_arm_poses`/`save_arm_poses` imports + `__all__` entries. Remove the now-empty repo-root `data/` dir (`git rm data/app_config.yaml data/arm_config.yaml data/hand_config.yaml`; move `data/README.md` content into Task 4's new `src/arm101_hand/data/README.md`).

- [ ] **Step 3.8 — `pyproject.toml` package data.** Under `[tool.setuptools.packages.find]` add:
```toml
[tool.setuptools.package-data]
arm101_hand = ["data/*.yaml", "data/*.md"]
```
(Confirm `uv run python -c "import arm101_hand"` still imports; the YAMLs are read by path, not import, so this is for sdist/wheel completeness.)

- [ ] **Step 3.9 — Update arm tests.** `git rm tests/unit/test_app_config.py`. Update `test_device_setup.py` (`test_app_config_path_is_repo_data` → assert `ARM_CONFIG_PATH.parts[-4:] == ("arm101_hand","data","arm_config.yaml")`-style; adjust to actual). Update `test_arm_poses_schema.py`→`test_arm_config_data.py` (load seeded `src/.../arm_config.yaml`, assert `poses["home"]` keys + `connection.port`). Update `test_arm_poses_save.py` to `ArmConfig`/`save_arm_config` load-modify-save.

- [ ] **Step 3.10 — Green gate.** Expected: all pass; `mypy src` clean (no references to deleted modules).

- [ ] **Step 3.11 — Manual import smoke** (no hardware): `uv run python -c "from arm101_hand.scripts.device_setup import load_arm_app_config, ARM_CONFIG_PATH; print(load_arm_app_config().connection.port)"` → prints `COM20`.

- [ ] **Step 3.12 — Commit** (`refactor(arm): ...`, body references IL-3/IL-5/IL-6/IL-7: app_config deleted, arm/hand config now under `src/arm101_hand/data/`, `_ARM_MOTOR_IDS` removed in favor of the device subclass SSOT).

---

## TASK 4 — Docs + Iron-Law audit

**Commit boundary:** `docs: point config docs at src/arm101_hand/data; refresh CLAUDE.md/README`

- [ ] **Step 4.1 — Write `src/arm101_hand/data/README.md`** documenting the two config files and, crucially, **what each tuning knob means** (the single doc home referenced by the schema `Field` descriptions). Carry over the editing-rules/atomic-writes/`safe_load` notes from the old `data/README.md`, and state the calibration-vs-config split (calibration files = measurement outputs; config = tuning surface).

- [ ] **Step 4.2 — Update `docs/conventions/00-iron-laws.md` IL-5** wording: calibration paths unchanged; add that runtime **config** now lives at `src/arm101_hand/data/{arm,hand}_config.yaml` (in-tree, version-controlled). Update any `data/`/`app_config.yaml` mentions in `docs/conventions/` (grep).

- [ ] **Step 4.3 — Invoke the `doc_update` skill** to refresh `CLAUDE.md` (§3 tree, §4 workflows, §5 IL-5 line, §1/§6 mentions of `data/app_config.yaml`) and the repo-root `README.md`. Verify the workflow command examples reference the new paths.

- [ ] **Step 4.4 — Invoke the `iron-law-audit` skill** on the branch diff. Resolve any findings. Confirm the proposed "Iron Laws touched" checklist for the PR body (expect IL-2/IL-3/IL-5/IL-6/IL-7).

- [ ] **Step 4.5 — Final green gate** + commit.

---

## TASK 5 — Optional: `/code-review` + finish branch

- [ ] **Step 5.1 — Run `/code-review`** on the full branch diff (now that a diff exists) for SSOT/DRY/KISS/correctness. Apply fixes.
- [ ] **Step 5.2 — Invoke `superpowers:finishing-a-development-branch`** to choose merge/PR. PR body: summarize the tuning-surface change, the calib-vs-config split rationale, and the IL checklist from Task 4.4.

---

## Self-review (plan vs spec)

- **Spec §1 principle (knobs/measured/canon split):** Tasks 1 (canon table), 2 (calib=measured), 3 (knobs→tuning). ✓
- **Spec §2 file layout + per-finger poses:** Steps 2.6, 3.1, schema 1.6. ✓
- **Spec §2.3 calib trim (drop id/connection/speeds, v3):** Steps 2.1, 2.5. ✓
- **Spec §3 identity→code (arm subclass + hand table):** Steps 1.1, 3.3, 3.4 (`ARM_JOINTS` single source). ✓
- **Spec §4 schema changes + 4(a)/4(b)/4(c):** Tasks 1–3; 4(a) save headers (1.4/1.6); 4(b) `hand/protocol.py` (2.7); 4(c) (3.3). ✓
- **Spec §5 blast radius (~20 files):** every listed file has a step. ✓
- **Spec §6 Iron Laws:** Task 4.4 audit. ✓
- **Placeholder scan:** new schemas/tests have complete code; consumer edits give exact old→new + grep file:line. No TBDs.
- **Type consistency:** `load_arm_config`/`save_arm_config`, `load_hand_config`/`save_hand_config`, `HandConfig`/`ArmConfig`, `FINGER_SERVO_IDS`, `HandPose.by_finger()` used consistently across tasks.
