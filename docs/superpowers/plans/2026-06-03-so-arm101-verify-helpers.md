# SO-ARM101 Verify/Audit Helper Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four standalone verify/audit scripts for the SO-ARM101 follower (scan, sweep, set_pose, show_calib) under `scripts/calibration/so_arm101/`, keeping lerobot's `calibrate()` as the canonical calibration source.

**Architecture:** Pure, unit-tested calibration math lives in `src/arm101_hand/robots/calibration_summary.py`. Script-setup glue (config load, robot construction) lives in `scripts/calibration/so_arm101/_common.py`. The four scripts are thin CLIs that compose those two. Motion scripts use `RANGE_M100_100` (endpoints = ±100); pose/display scripts use `DEGREES`.

**Tech Stack:** Python 3.12, lerobot (`FeetechMotorsBus`, `SO101FollowerNoGripper` subclass), pydantic configs, pytest, ruff, mypy. `uv run` for all execution.

**Spec:** `docs/superpowers/specs/2026-06-03-so-arm101-verify-helpers-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `src/arm101_hand/robots/calibration_summary.py` | **Create.** Pure calibration math: load JSON → `JointCalib`, `degree_span`, `midpoint_steps`, `degree_bounds`, `clamp_degrees`. No serial. |
| `tests/unit/test_arm_calibration_summary.py` | **Create.** Unit tests for the pure math. |
| `scripts/calibration/so_arm101/_common.py` | **Create.** Script glue: load `app_config.yaml`, build follower / raw bus, gentle velocity. |
| `scripts/calibration/so_arm101/show_calib.py` | **Create.** Read-only calibration dump + optional `--live`. |
| `scripts/calibration/so_arm101/scan.py` | **Create.** Read-only bus health check (raw bus, torque off). |
| `scripts/calibration/so_arm101/sweep.py` | **Create.** Per-joint range-verification sweep (`RANGE_M100_100`). |
| `scripts/calibration/so_arm101/set_pose.py` | **Create.** Drive to a named pose and hold (`DEGREES`). |
| `scripts/calibration/so_arm101/README.md` | **Modify.** Add a "Verify/audit helpers" section. |
| `CLAUDE.md`, `README.md` | **Modify** (via `doc_update`). Add the new scripts to the workflows block. |

**Key reused APIs (verified against `references/lerobot`):**
- `arm101_hand.config.load_app_config(path) -> AppConfig` with `cfg.arm.port`, `cfg.safety.safe_park.park_velocity_arm`.
- `arm101_hand.config.load_arm_poses(path) -> ArmPoseConfig` with `.quick_poses: dict[str, ArmPose]`; `ARM_MOTORS` tuple.
- `SO101FollowerNoGripperConfig(port=..., id="so101_follower", use_degrees=...)` (kw-only `id`/`calibration_dir`; subclass defaults `calibration_dir` in-tree, so `id="so101_follower"` auto-loads `so101_follower.json`).
- `follower.connect(calibrate=False)` — opens bus, `configure()`s with torque **off**, skips lerobot's interactive calibrate prompt.
- `follower.bus.write_calibration(follower.calibration)` — pushes the file's offsets/ranges to the motors (writes motors, **not** the JSON).
- `follower.bus.enable_torque()` / `disable_torque()` / `sync_write("Profile_Velocity", {motor: int})`.
- `follower.send_action({f"{motor}.pos": float})` → `sync_write("Goal_Position", ...)`; `follower.get_observation() -> {f"{motor}.pos": val}`.
- Raw `FeetechMotorsBus(port=..., motors={name: Motor(id, "sts3215", MotorNormMode.DEGREES)})`; `bus.connect(handshake=False)`, `bus.ping(motor) -> int | None`, `bus.read("Present_Position", motor, normalize=False)`, `bus.read("Present_Load"/"Present_Voltage"/"Present_Temperature", motor)`.

---

## Task 1: Pure calibration-summary module (TDD)

**Files:**
- Create: `src/arm101_hand/robots/calibration_summary.py`
- Test: `tests/unit/test_arm_calibration_summary.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_arm_calibration_summary.py`:

```python
"""Unit tests for the pure SO-ARM101 calibration-summary math (no bus)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    JointCalib,
    clamp_degrees,
    degree_bounds,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)

# A complete, well-ordered 5-joint calibration (mirrors so101_follower.json shape).
_GOOD = {
    "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -1918, "range_min": 736, "range_max": 3460},
    "shoulder_lift": {"id": 2, "drive_mode": 0, "homing_offset": -1987, "range_min": 816, "range_max": 3205},
    "elbow_flex": {"id": 3, "drive_mode": 0, "homing_offset": -1993, "range_min": 873, "range_max": 3092},
    "wrist_flex": {"id": 4, "drive_mode": 0, "homing_offset": -2003, "range_min": 950, "range_max": 3202},
    "wrist_roll": {"id": 5, "drive_mode": 0, "homing_offset": 1977, "range_min": 0, "range_max": 4095},
}


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "so101_follower.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_returns_all_five_joints(tmp_path: Path) -> None:
    calib = load_arm_calibration(_write(tmp_path, _GOOD))
    assert set(calib) == set(ARM_JOINTS)
    assert calib["shoulder_pan"] == JointCalib(
        id=1, drive_mode=0, homing_offset=-1918, range_min=736, range_max=3460
    )


def test_load_rejects_missing_joint(tmp_path: Path) -> None:
    bad = {k: v for k, v in _GOOD.items() if k != "wrist_roll"}
    with pytest.raises(ValueError, match="missing joints"):
        load_arm_calibration(_write(tmp_path, bad))


def test_load_rejects_missing_key(tmp_path: Path) -> None:
    bad = {**_GOOD, "elbow_flex": {"id": 3, "drive_mode": 0, "homing_offset": 0, "range_min": 100}}
    with pytest.raises(ValueError, match="missing key"):
        load_arm_calibration(_write(tmp_path, bad))


def test_degree_span_full_turn_is_360() -> None:
    # range 0..4095 over resolution 4096 -> (4095-0)*360/4095 = 360.0
    assert degree_span(0, 4095) == pytest.approx(360.0)


def test_degree_span_partial() -> None:
    # (3460-736)*360/4095
    assert degree_span(736, 3460) == pytest.approx(2724 * 360 / 4095)


def test_midpoint_steps() -> None:
    assert midpoint_steps(736, 3460) == pytest.approx(2098.0)


def test_degree_bounds_symmetric() -> None:
    lo, hi = degree_bounds(736, 3460)
    span = degree_span(736, 3460)
    assert lo == pytest.approx(-span / 2)
    assert hi == pytest.approx(span / 2)


def test_clamp_inside_is_unchanged() -> None:
    _, hi = degree_bounds(736, 3460)
    assert clamp_degrees(hi - 1.0, 736, 3460) == pytest.approx(hi - 1.0)


def test_clamp_above_is_clamped_to_hi() -> None:
    _, hi = degree_bounds(736, 3460)
    assert clamp_degrees(hi + 50.0, 736, 3460) == pytest.approx(hi)


def test_clamp_below_is_clamped_to_lo() -> None:
    lo, _ = degree_bounds(736, 3460)
    assert clamp_degrees(lo - 50.0, 736, 3460) == pytest.approx(lo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_arm_calibration_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'arm101_hand.robots.calibration_summary'`.

- [ ] **Step 3: Write the module**

Create `src/arm101_hand/robots/calibration_summary.py`:

```python
"""Pure (no-bus) helpers for reading and summarizing the SO-ARM101 follower calibration.

Consumed by ``scripts/calibration/so_arm101/show_calib.py`` and ``set_pose.py``. Kept
free of serial I/O so it is unit-testable without hardware (test pyramid, IL §04).

The calibration JSON (``scripts/calibration/so_arm101/so101_follower.json``) stores raw
12-bit encoder steps per joint. lerobot's DEGREES normalization measures degrees relative
to the range MIDPOINT (see ``motors_bus._normalize``), so the usable degree window for a
joint is symmetric: ``[-span/2, +span/2]`` where ``span`` is the degree span.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# STS3215 encoder resolution (counts per full turn). lerobot uses ``resolution - 1`` in
# its degree conversion (``motors_bus._normalize`` / ``_unnormalize``).
STS3215_RESOLUTION = 4096

ARM_JOINTS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
)


@dataclass(frozen=True)
class JointCalib:
    """One joint's calibration in raw encoder steps."""

    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int


def load_arm_calibration(path: Path) -> dict[str, JointCalib]:
    """Parse ``so101_follower.json`` into a ``JointCalib`` per joint.

    Raises ``ValueError`` if any of the five canonical joints is missing, or a joint
    is missing a required key.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [j for j in ARM_JOINTS if j not in raw]
    if missing:
        raise ValueError(f"calibration missing joints: {missing}")
    out: dict[str, JointCalib] = {}
    for joint in ARM_JOINTS:
        entry = raw[joint]
        try:
            out[joint] = JointCalib(
                id=int(entry["id"]),
                drive_mode=int(entry["drive_mode"]),
                homing_offset=int(entry["homing_offset"]),
                range_min=int(entry["range_min"]),
                range_max=int(entry["range_max"]),
            )
        except KeyError as e:
            raise ValueError(f"joint '{joint}' missing key {e}") from e
    return out


def degree_span(range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION) -> float:
    """Full reachable span in degrees: ``(range_max - range_min) * 360 / (resolution - 1)``."""
    return (range_max - range_min) * 360.0 / (resolution - 1)


def midpoint_steps(range_min: int, range_max: int) -> float:
    """Midpoint of the calibrated range, in encoder steps."""
    return (range_min + range_max) / 2.0


def degree_bounds(
    range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION
) -> tuple[float, float]:
    """Usable degree window relative to the range midpoint: ``(-span/2, +span/2)``."""
    half = degree_span(range_min, range_max, resolution) / 2.0
    return (-half, half)


def clamp_degrees(
    value: float, range_min: int, range_max: int, resolution: int = STS3215_RESOLUTION
) -> float:
    """Clamp a degree target to the joint's usable window."""
    lo, hi = degree_bounds(range_min, range_max, resolution)
    return max(lo, min(hi, value))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_arm_calibration_summary.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff format src/arm101_hand/robots/calibration_summary.py tests/unit/test_arm_calibration_summary.py && uv run ruff check src/arm101_hand/robots/calibration_summary.py tests/unit/test_arm_calibration_summary.py && uv run mypy src/arm101_hand/robots/calibration_summary.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/robots/calibration_summary.py tests/unit/test_arm_calibration_summary.py
git commit -m "feat(arm-calib): pure calibration-summary math for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Shared script-setup helper

**Files:**
- Create: `scripts/calibration/so_arm101/_common.py`

- [ ] **Step 1: Write the helper**

Create `scripts/calibration/so_arm101/_common.py`:

```python
"""Shared setup glue for the SO-ARM101 verify/audit helper scripts.

Not pure (loads config + constructs the robot/bus), so the unit-testable calibration
math lives in ``arm101_hand.robots.calibration_summary`` instead. Kept thin.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the duration
of one script run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import load_app_config
from arm101_hand.robots.calibration_summary import ARM_JOINTS

# scripts/calibration/so_arm101/_common.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"
CALIB_PATH = Path(__file__).resolve().parent / "so101_follower.json"

# id="so101_follower" -> SO101FollowerNoGripper loads <calibration_dir>/so101_follower.json,
# and the subclass defaults calibration_dir to this directory.
FOLLOWER_ID = "so101_follower"


def load_arm_app_config():
    """Load + validate data/app_config.yaml (SystemExit with a clear message if absent)."""
    if not APP_CONFIG_PATH.is_file():
        raise SystemExit(f"app_config.yaml not found at {APP_CONFIG_PATH}")
    return load_app_config(APP_CONFIG_PATH)


def build_follower(cfg, *, use_degrees: bool):
    """Construct an (unconnected) SO101FollowerNoGripper from ``cfg``.

    use_degrees=True -> DEGREES norm mode (poses/display);
    use_degrees=False -> RANGE_M100_100 (endpoint sweep, endpoints = +/-100).
    """
    from arm101_hand.robots import SO101FollowerNoGripper, SO101FollowerNoGripperConfig

    robot_cfg = SO101FollowerNoGripperConfig(
        port=cfg.arm.port,
        id=FOLLOWER_ID,
        use_degrees=use_degrees,
    )
    return SO101FollowerNoGripper(robot_cfg)


def build_raw_bus(cfg):
    """Construct an (unconnected) raw 5-motor FeetechMotorsBus for read-only scanning."""
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    motors = {
        name: Motor(motor_id, "sts3215", MotorNormMode.DEGREES)
        for motor_id, name in enumerate(ARM_JOINTS, start=1)
    }
    return FeetechMotorsBus(port=cfg.arm.port, motors=motors)


def gentle_velocity(cfg) -> int:
    """Profile_Velocity (register units) for gentle calibration moves."""
    return cfg.safety.safe_park.park_velocity_arm
```

- [ ] **Step 2: Lint + type-check**

Run: `uv run ruff format scripts/calibration/so_arm101/_common.py && uv run ruff check scripts/calibration/so_arm101/_common.py`
Expected: no errors. (mypy is configured for `src/` only; this file lives under `scripts/`.)

- [ ] **Step 3: Commit**

```bash
git add scripts/calibration/so_arm101/_common.py
git commit -m "feat(arm-calib): shared setup helper for SO-ARM101 verify scripts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `show_calib.py` — calibration dump (+ optional live compare)

This script's **default mode reads only the JSON**, so it is verifiable without hardware.

**Files:**
- Create: `scripts/calibration/so_arm101/show_calib.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/show_calib.py`:

```python
"""Pretty-print the SO-ARM101 follower calibration; optionally compare to live position.

Default mode reads only ``so101_follower.json`` -- no bus, no torque. Prints each joint's
id / homing_offset / range_min / range_max plus the computed degree span and midpoint.

``--live`` additionally opens the bus read-only (``connect(calibrate=False)``, torque stays
off) and prints each joint's present position in degrees vs the calibrated mid. It does NOT
push calibration to the motors, so the live degrees assume the motors still hold the
committed EEPROM calibration from the last ``arm101-calibrate-follower`` run.

Usage:
  uv run python scripts/calibration/so_arm101/show_calib.py
  uv run python scripts/calibration/so_arm101/show_calib.py --live

IL-5: this script never writes so101_follower.json.
"""

from __future__ import annotations

import argparse
import sys

from _common import CALIB_PATH, build_follower, load_arm_app_config

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show SO-ARM101 follower calibration.")
    parser.add_argument(
        "--live", action="store_true", help="also read present positions from the bus (read-only)"
    )
    args = parser.parse_args()

    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)

    print(f"Calibration: {CALIB_PATH}")
    header = f"{'joint':<14}{'id':>3}{'homing':>9}{'min':>7}{'max':>7}{'span_deg':>10}{'mid_steps':>11}"
    print(header)
    print("-" * len(header))
    for joint in ARM_JOINTS:
        c = calib[joint]
        print(
            f"{joint:<14}{c.id:>3}{c.homing_offset:>9}{c.range_min:>7}{c.range_max:>7}"
            f"{degree_span(c.range_min, c.range_max):>10.2f}{midpoint_steps(c.range_min, c.range_max):>11.1f}"
        )

    if not args.live:
        return 0

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)
    print(f"\nConnecting read-only on {cfg.arm.port} (torque stays off) ...")
    follower.connect(calibrate=False)
    try:
        obs = follower.get_observation()  # {f"{joint}.pos": degrees}
        print(f"\n{'joint':<14}{'present_deg':>12}{'mid_deg':>10}")
        print("-" * 36)
        for joint in ARM_JOINTS:
            present = obs[f"{joint}.pos"]
            # In DEGREES mode the calibrated mid is, by definition, 0 deg.
            print(f"{joint:<14}{present:>12.2f}{0.0:>10.2f}")
    finally:
        follower.disconnect()
        print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Note on the `from _common import ...` line: scripts in this directory import the sibling
> `_common` module directly (same pattern the directory already establishes for running
> files standalone via `uv run python scripts/calibration/so_arm101/<name>.py`, where the
> script's own directory is on `sys.path[0]`).

- [ ] **Step 2: Verify default mode runs (no hardware)**

Run: `uv run python scripts/calibration/so_arm101/show_calib.py`
Expected: a table of all 5 joints with `span_deg` (e.g. `wrist_roll` ≈ `360.00`) and `mid_steps`; exit 0.

- [ ] **Step 3: Lint**

Run: `uv run ruff format scripts/calibration/so_arm101/show_calib.py && uv run ruff check scripts/calibration/so_arm101/show_calib.py`
Expected: no errors.

- [ ] **Step 4: Manual hardware check (operator, optional now)**

With the arm powered and on its COM port: `uv run python scripts/calibration/so_arm101/show_calib.py --live`
Expected: prints present degrees per joint; torque stays off (arm stays limp); bus closes cleanly.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/show_calib.py
git commit -m "feat(arm-calib): show_calib.py dump + live compare for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `scan.py` — bus health check

**Files:**
- Create: `scripts/calibration/so_arm101/scan.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/scan.py`:

```python
"""Read-only health check for the SO-ARM101 5-motor bus.

Pings motors 1-5 and, for each responder, reads present position (raw steps),
load, voltage, and temperature. Torque stays OFF the entire time. Uses a raw
FeetechMotorsBus (not the robot wrapper) so a missing/dead motor is reported
rather than crashing the robot's configure() step.

Exit code is non-zero if any of the 5 motors did not respond, so this doubles
as a pre-flight check before sweep.py / set_pose.py.

Usage:
  uv run python scripts/calibration/so_arm101/scan.py

IL-3: motors 1-5 only. IL-4: opens the bus briefly, releases it. Read-only.
"""

from __future__ import annotations

import sys

from _common import build_raw_bus, load_arm_app_config

from arm101_hand.robots.calibration_summary import ARM_JOINTS


def main() -> int:
    cfg = load_arm_app_config()
    bus = build_raw_bus(cfg)

    print(f"Opening arm bus on {cfg.arm.port} (read-only, torque off) ...")
    # handshake=False: do not require every motor to answer at connect time --
    # we want to report which ones are missing.
    bus.connect(handshake=False)

    missing: list[str] = []
    try:
        header = f"{'joint':<14}{'id':>3}{'model':>7}{'pos_raw':>9}{'load':>7}{'volt':>7}{'temp_c':>8}"
        print(header)
        print("-" * len(header))
        for joint in ARM_JOINTS:
            motor_id = bus.motors[joint].id
            model = bus.ping(joint, num_retry=2)
            if model is None:
                missing.append(joint)
                print(f"{joint:<14}{motor_id:>3}{'--':>7}{'(no response)':>34}")
                continue
            pos = bus.read("Present_Position", joint, normalize=False)
            load = bus.read("Present_Load", joint)
            volt = bus.read("Present_Voltage", joint)  # decivolts on Feetech (e.g. 122 = 12.2 V)
            temp = bus.read("Present_Temperature", joint)
            print(
                f"{joint:<14}{motor_id:>3}{model:>7}{pos:>9}{load:>7}{volt / 10:>6.1f}V{temp:>7}"
            )
            if temp >= cfg.safety.temp_warn_c:
                print(f"  WARNING: {joint} temp {temp}C >= warn threshold {cfg.safety.temp_warn_c}C")
    finally:
        # disable_torque=False: we never enabled torque; don't touch it on the way out.
        bus.disconnect(disable_torque=False)
        print(f"Bus closed on {cfg.arm.port}.")

    if missing:
        print(f"\nMISSING motors: {missing}", file=sys.stderr)
        return 1
    print("\nAll 5 motors responded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Lint**

Run: `uv run ruff format scripts/calibration/so_arm101/scan.py && uv run ruff check scripts/calibration/so_arm101/scan.py`
Expected: no errors.

- [ ] **Step 3: Manual hardware check (operator)**

With the arm powered: `uv run python scripts/calibration/so_arm101/scan.py`
Expected: a 5-row table with plausible raw positions, ~12 V, and a low temperature; exit 0. Arm stays limp (no torque). With a motor unplugged, that row shows `(no response)` and exit code is 1.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/so_arm101/scan.py
git commit -m "feat(arm-calib): scan.py read-only bus health check for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `sweep.py` — per-joint range-verification sweep

**Files:**
- Create: `scripts/calibration/so_arm101/sweep.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/sweep.py`:

```python
"""Verify the SO-ARM101 calibration by sweeping joints to their calibrated endpoints.

Uses RANGE_M100_100 normalization, so the calibrated range maps to +/-100 with built-in
clamping -- driving to an endpoint is just commanding +/-margin. For each selected joint
the script: sets a gentle Profile_Velocity, enables torque, moves to mid-range (0) first,
then sweeps 0 -> +margin -> -margin -> 0, watching Present_Load for stalls.

After connect() the script pushes the on-file calibration to the motors
(bus.write_calibration) so the normalized endpoints physically match the recorded range
regardless of the motors' current EEPROM offsets. This writes the MOTORS, never the JSON
(IL-5).

Usage:
  uv run python scripts/calibration/so_arm101/sweep.py shoulder_pan
  uv run python scripts/calibration/so_arm101/sweep.py all --margin 80

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import argparse
import sys
import time

from _common import build_follower, gentle_velocity, load_arm_app_config

from arm101_hand.robots.calibration_summary import ARM_JOINTS

# Present_Load magnitude above which we treat the joint as stalling against a stop.
_LOAD_STALL = 600
_SETTLE_S = 1.2  # dwell after each commanded target so the joint reaches it


def _move(follower, joint: str, target: float) -> None:
    """Command one joint to a normalized target (-100..100) and dwell, watching load."""
    follower.send_action({f"{joint}.pos": target})
    time.sleep(_SETTLE_S)
    load = follower.bus.read("Present_Load", joint)
    flag = "  <-- HIGH LOAD" if abs(load) >= _LOAD_STALL else ""
    print(f"  {joint}: target={target:+6.1f}  load={load:>5}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep SO-ARM101 joints to calibrated endpoints.")
    parser.add_argument("joint", help=f"one of {', '.join(ARM_JOINTS)}, or 'all'")
    parser.add_argument(
        "--margin",
        type=float,
        default=90.0,
        help="how close to the endpoint to sweep, 1..99 (default 90; 100 = the stop itself)",
    )
    args = parser.parse_args()

    margin = max(1.0, min(99.0, args.margin))
    if args.joint == "all":
        joints = list(ARM_JOINTS)
    elif args.joint in ARM_JOINTS:
        joints = [args.joint]
    else:
        print(f"unknown joint {args.joint!r}; pick one of {ARM_JOINTS} or 'all'", file=sys.stderr)
        return 1

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=False)  # RANGE_M100_100
    vel = gentle_velocity(cfg)

    print(f"Connecting on {cfg.arm.port} ...")
    follower.connect(calibrate=False)
    try:
        # Align the motors' onboard frame with the on-file calibration (writes motors, not JSON).
        follower.bus.write_calibration(follower.calibration)
        # Gentle speed BEFORE enabling torque / first move, so nothing snaps.
        follower.bus.sync_write("Profile_Velocity", {j: vel for j in ARM_JOINTS})
        follower.bus.enable_torque()

        print(f"Centering all joints (Profile_Velocity={vel}) ...")
        follower.send_action({f"{j}.pos": 0.0 for j in ARM_JOINTS})
        time.sleep(_SETTLE_S)

        for joint in joints:
            print(f"\nSweeping {joint} (margin +/-{margin:.0f}):")
            _move(follower, joint, +margin)
            _move(follower, joint, -margin)
            _move(follower, joint, 0.0)
        print("\nSweep complete; returning to center.")
        follower.send_action({f"{j}.pos": 0.0 for j in ARM_JOINTS})
        time.sleep(_SETTLE_S)
    except KeyboardInterrupt:
        print("\n^C -- stopping, releasing torque")
    finally:
        follower.bus.disable_torque()
        follower.disconnect()
        print("Torque off, bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Lint**

Run: `uv run ruff format scripts/calibration/so_arm101/sweep.py && uv run ruff check scripts/calibration/so_arm101/sweep.py`
Expected: no errors.

- [ ] **Step 3: Manual hardware check (operator)**

> SAFETY: keep a hand near the PSU switch. Start with a single joint, not `all`.

Run: `uv run python scripts/calibration/so_arm101/sweep.py wrist_flex --margin 70`
Expected: the joint centers, then moves to +70 / -70 / 0 smoothly with no buzz/stall; printed `load` stays well under 600; torque releases at the end. Then try the rest individually, raising `--margin` toward 90 once confident.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/so_arm101/sweep.py
git commit -m "feat(arm-calib): sweep.py per-joint range verification for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `set_pose.py` — drive to a named pose and hold

**Files:**
- Create: `scripts/calibration/so_arm101/set_pose.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/set_pose.py`:

```python
"""Drive the SO-ARM101 to a named pose (degrees) and hold it under torque until Enter.

Poses come from data/arm_config.yaml ``quick_poses`` (zero / home / rest) -- this script
invents none (IL-7). Targets are in DEGREES (relative to each joint's calibrated mid) and
are clamped to the joint's usable window [-span/2, +span/2]; an out-of-range value is
warned and that joint is skipped, matching the behavior arm_config.yaml documents for
the GUI.

After connect() the script pushes the on-file calibration to the motors so degree targets
physically match the recorded range (writes the MOTORS, never the JSON -- IL-5).

Usage:
  uv run python scripts/calibration/so_arm101/set_pose.py home
  uv run python scripts/calibration/so_arm101/set_pose.py rest
  uv run python scripts/calibration/so_arm101/set_pose.py        # prompts

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import sys
import time

from _common import ARM_CONFIG_PATH, CALIB_PATH, build_follower, gentle_velocity, load_arm_app_config

from arm101_hand.config import load_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS, clamp_degrees, load_arm_calibration

_SETTLE_S = 2.0


def _resolve_pose_name(argv: list[str], available: list[str]) -> str:
    if len(argv) > 1 and argv[1] in available:
        return argv[1]
    if len(argv) > 1:
        print(f"  {argv[1]!r} is not a known pose; choose from {available}")
    while True:
        choice = input(f"Pose? ({'/'.join(available)}): ").strip()
        if choice in available:
            return choice
        print(f"  invalid -- pick one of {available}")


def main() -> int:
    poses = load_arm_poses(ARM_CONFIG_PATH).quick_poses
    if not poses:
        print(f"no quick_poses defined in {ARM_CONFIG_PATH}", file=sys.stderr)
        return 1
    available = sorted(poses)
    name = _resolve_pose_name(sys.argv, available)
    pose = poses[name].as_dict()  # {joint: degrees}

    calib = load_arm_calibration(CALIB_PATH)
    # Clamp each joint to its usable degree window; skip out-of-range joints.
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)

    print(f"Connecting on {cfg.arm.port}; driving to '{name}' ...")
    follower.connect(calibrate=False)
    try:
        follower.bus.write_calibration(follower.calibration)
        follower.bus.sync_write("Profile_Velocity", {j: vel for j in ARM_JOINTS})
        follower.bus.enable_torque()
        follower.send_action({f"{j}.pos": v for j, v in targets.items()})
        time.sleep(_SETTLE_S)
        print(f"Holding '{name}' under torque: {targets}")
        input("Press Enter to release torque and exit... ")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- releasing")
    finally:
        follower.bus.disable_torque()
        follower.disconnect()
        print("Torque off, bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Lint**

Run: `uv run ruff format scripts/calibration/so_arm101/set_pose.py && uv run ruff check scripts/calibration/so_arm101/set_pose.py`
Expected: no errors.

- [ ] **Step 3: Manual hardware check (operator)**

> SAFETY: `arm_config.yaml` quick_poses are documented as placeholder values. Verify each
> pose looks sane before relying on it; keep a hand near the PSU.

Run: `uv run python scripts/calibration/so_arm101/set_pose.py rest`
Expected: the arm drives gently to the rest pose and holds; pressing Enter releases torque (arm goes limp). Any joint whose target is outside its calibrated window prints a WARNING and is skipped.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/so_arm101/set_pose.py
git commit -m "feat(arm-calib): set_pose.py named-pose park for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Documentation

**Files:**
- Modify: `scripts/calibration/so_arm101/README.md`
- Modify (via skill): `CLAUDE.md`, `README.md`

- [ ] **Step 1: Append a "Verify/audit helpers" section to the so_arm101 README**

Add the following after the existing troubleshooting section (§5) of
`scripts/calibration/so_arm101/README.md`:

````markdown
## 6. Verify/audit helpers

These standalone scripts sanity-check the 5 servos and the calibration against the
real mechanism. They keep `arm101-calibrate-follower` (lerobot) as the source of
truth and **never write `so101_follower.json`** (IL-5). All read `arm.port` from
`data/app_config.yaml`. Close any other bus owner first (IL-4).

| Script | Motion? | Torque | Norm mode | Purpose |
| --- | --- | --- | --- | --- |
| `show_calib.py` | no | off | DEGREES | Print per-joint calibration (id, homing, range, degree span, midpoint). `--live` also reads present position. |
| `scan.py` | no | off | raw | Ping motors 1–5; report position/load/voltage/temperature. Exit 1 if any motor is missing. |
| `sweep.py` | yes | on→off | RANGE_M100_100 | Drive a joint (or `all`) to its calibrated endpoints (`±margin`, default 90) and back; verify no buzz/stall. |
| `set_pose.py` | yes | on→off | DEGREES | Drive to a `quick_poses` pose from `data/arm_config.yaml` (zero/home/rest) and hold until Enter. |

```powershell
uv run python scripts/calibration/so_arm101/show_calib.py            # offline dump
uv run python scripts/calibration/so_arm101/show_calib.py --live     # + live position
uv run python scripts/calibration/so_arm101/scan.py                  # bus health (pre-flight)
uv run python scripts/calibration/so_arm101/sweep.py wrist_flex --margin 70
uv run python scripts/calibration/so_arm101/set_pose.py rest
```

**Norm-mode split.** `sweep.py` uses `RANGE_M100_100`, where the calibrated range maps
to exactly ±100 with built-in clamping — so driving to an endpoint is just commanding
±margin, inherently bounded. `set_pose.py` / `show_calib.py` use `DEGREES` to match the
`arm_config.yaml` pose convention; in that mode a value is degrees relative to the
range *midpoint*, so each joint's usable window is symmetric `[-span/2, +span/2]`.

**Onboard-frame note.** `sweep.py` and `set_pose.py` push the on-file calibration to the
motors after connecting (`bus.write_calibration`) so normalized targets physically match
the recorded range. This writes the **motors**, not the JSON. `show_calib.py --live` does
not, so its live degrees assume the motors still hold the committed calibration from the
last calibrate run.

**Recommended order:** `scan` (all 5 respond?) → `show_calib` (numbers sane?) →
`sweep` per joint (endpoints clean?) → `set_pose home`/`rest` (parks cleanly?).
````

Also update the one-line summary / overview at the top of the README if present, to note
that verify helpers exist alongside the lerobot calibration runner.

- [ ] **Step 2: Verify the README renders sensibly**

Run: `uv run python -c "print(open('scripts/calibration/so_arm101/README.md', encoding='utf-8').read()[-1200:])"`
Expected: the new section is present and well-formed.

- [ ] **Step 3: Commit the README**

```bash
git add scripts/calibration/so_arm101/README.md
git commit -m "docs(arm-calib): document SO-ARM101 verify/audit helper scripts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Refresh top-level docs**

Invoke the `doc_update` skill to add the four new scripts to the "Common workflows"
block in `CLAUDE.md` and the relevant section of the root `README.md`. (The skill owns
the exact edits; do not hand-edit those files outside it.)

- [ ] **Step 5: Full verification gate**

Run:
```
uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest -m 'not hardware'
```
Expected: all green (the only new tests are `test_arm_calibration_summary.py`; the scripts are hardware-verified manually).

---

## Final verification checklist

- [ ] `test_arm_calibration_summary.py` passes (10 tests).
- [ ] `show_calib.py` (offline) prints all 5 joints with correct degree spans.
- [ ] `scan.py` reports all 5 motors and exits 0 with hardware present (exit 1 with a motor removed).
- [ ] `sweep.py <joint>` centers, reaches ±margin, returns to 0, releases torque.
- [ ] `set_pose.py rest`/`home` parks and holds, releases on Enter, skips out-of-range joints.
- [ ] `ruff`, `mypy src`, and `pytest -m 'not hardware'` are all green.
- [ ] `so101_follower.json` is unchanged (`git diff --stat` shows no edit to it).
- [ ] README + CLAUDE.md/README.md updated.
- [ ] Iron-law audit (IL-3/IL-4/IL-5) clean — consider running the `iron-law-audit` skill before the PR.
