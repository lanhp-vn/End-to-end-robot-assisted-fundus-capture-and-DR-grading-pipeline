# Codebase Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused PySide6 GUI, make `scan`/`show_calib`/`find_port` dual-device under `scripts/diagnostics/`, and rename the AmazingHand calibration assets to short snake_case.

**Architecture:** Three independent, internally-atomic commits (GUI removal → diagnostics → hand rename), each green on lint/type/test, then a `/doc_update` pass. Shared device constructors are promoted to `src/arm101_hand/scripts/device_setup.py`; `so_arm101/_common.py` re-exports them so arm scripts are untouched. No change to existing device-control behavior — only deletion, relocation, renaming, and two new hand branches in the diagnostics.

**Tech Stack:** Python 3.12, uv, lerobot[feetech] (`FeetechMotorsBus`), rustypot (`Scs0009PyController`), pydantic v2, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-06-07-codebase-refactor-design.md`

---

## File Structure

**Workstream 1 (GUI removal) — delete:**
- `src/arm101_hand/gui/` (entire tree)
- `src/arm101_hand/scripts/unified_gui.py`
- `src/arm101_hand/hand/controller.py`
- `tests/unit/test_hand_controller_mock.py`, `tests/unit/test_sequence_parser.py`, `tests/unit/test_safe_park.py`, `tests/unit/test_safety_thresholds.py`

**Workstream 1 — modify:** `pyproject.toml`, `uv.lock` (regen), `tests/hardware/test_hand_real.py` (rewrite off Qt/controller).

**Workstream 2 (diagnostics) — create:** `src/arm101_hand/scripts/device_setup.py`, `scripts/diagnostics/scan.py`, `scripts/diagnostics/show_calib.py`, `scripts/diagnostics/find_port.py`, `tests/unit/test_device_setup.py`.
**Workstream 2 — modify:** `scripts/calibration/so_arm101/_common.py` (re-export). **Delete:** the three old `so_arm101/{scan,show_calib,find_port}.py`.

**Workstream 3 (rename) — `git mv`:** folder + 6 scripts + YAML. **Modify:** each renamed script's `YAML_PATH`/docstring, src docstrings, active tests, folder README.

**Workstream 4:** living docs (CLAUDE.md, README.md, data/README.md, conventions 00–06, `.claude/` agent+skills) via `/doc_update` + manual IL-5 edit.

---

## Pre-flight

- [ ] **Step 0: Confirm branch and clean baseline**

Run:
```powershell
git branch --show-current
uv run pytest -m 'not hardware' -q
```
Expected: branch is `refactor/gui-removal-diagnostics-hand-rename`; tests pass (this is the green baseline every commit must preserve). The working tree may still show pre-existing edits to `data/arm_config.yaml`, `data/hand_config.yaml`, `scripts/calibration/so_arm101/set_pose.py` — leave those unstaged throughout.

---

## Workstream 1 — Remove the GUI

### Task 1: Rewrite the hand hardware smoke test off Qt/controller

Must come **before** deleting `controller.py`, so collection never breaks.

**Files:**
- Rewrite: `tests/hardware/test_hand_real.py`

- [ ] **Step 1: Replace the file contents**

The current test drives `HandController` on a `QThread`. Rewrite it to drive `rustypot.Scs0009PyController` directly (mirroring `scripts/calibration/AmazingHand/jog.py`), keeping the connect → poll-8 → nudge-index → return → torque-off intent. Full new content:

```python
"""Hardware smoke test for the AmazingHand bus (5 V SCS0009 x 8).

Skipped by default. Run with::

    uv run pytest -m hardware --port=COM18

Pre-flight (per IL-1 / IL-4):
- 5 V supply on the hand bus only -- the 12 V arm bus stays cold.
- No other process holds the port (close FD.exe, serial monitors, stale Python).

Drives ``rustypot.Scs0009PyController`` directly (same path as
``scripts/calibration/AmazingHand/jog.py``): connect, poll all 8 servos, nudge
the index finger's base by +5 deg, wait for arrival, return to neutral, then
torque-off. No GUI/Qt dependency.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from arm101_hand.config import load_hand_calibration
from arm101_hand.hand import compose_finger, degrees_to_servo_radians

CALIBRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "amazing_hand"
    / "hand_calib_values.yaml"
)
ARRIVAL_TOLERANCE_DEG = 3.0
ARRIVAL_TIMEOUT_S = 4.0


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


@pytest.mark.hardware
def test_hand_connect_poll_nudge(port: str) -> None:
    from rustypot import Scs0009PyController

    cfg = load_hand_calibration(CALIBRATION_PATH)
    all_ids = [s for b in cfg.fingers.values() for s in (b.servo_1.id, b.servo_2.id)]
    c = Scs0009PyController(serial_port=port, baudrate=cfg.baudrate, timeout=cfg.timeout)
    try:
        for sid in all_ids:
            c.write_torque_enable(sid, 1)
        # Poll all 8 -- every servo must answer.
        for sid in all_ids:
            assert _scalar(c.read_present_position(sid)) is not None

        block = cfg.fingers["index"]
        s1, s2 = block.servo_1, block.servo_2
        lim = block.limits

        def drive(base: float) -> None:
            pos1, pos2 = compose_finger(
                base, 0.0,
                base_min=lim.base_min, base_max=lim.base_max,
                side_min=lim.side_min, side_max=lim.side_max,
            )
            c.write_goal_speed(s1.id, cfg.speed)
            c.write_goal_speed(s2.id, cfg.speed)
            c.write_goal_position(s1.id, degrees_to_servo_radians(s1.id, pos1, s1.middle_pos))
            c.write_goal_position(s2.id, degrees_to_servo_radians(s2.id, pos2, s2.middle_pos))

        target = min(5.0, lim.base_max)
        drive(target)
        deadline = time.monotonic() + ARRIVAL_TIMEOUT_S
        while time.monotonic() < deadline:
            deg = float(np.rad2deg(_scalar(c.read_present_position(s1.id)))) - s1.middle_pos
            if abs(deg - target) <= ARRIVAL_TOLERANCE_DEG:
                break
            time.sleep(0.05)
        drive(0.0)
        time.sleep(0.5)
    finally:
        for sid in all_ids:
            try:
                c.write_torque_enable(sid, 0)
            except Exception:  # noqa: BLE001 -- best-effort torque-off on teardown
                pass
```

Note: this references the **renamed** path (`amazing_hand/hand_calib_values.yaml`). The file doesn't exist until Workstream 3, but this test is hardware-gated (skipped without `--port`) so collection passes now and the path resolves after the rename.

- [ ] **Step 2: Verify collection succeeds (no Qt import)**

Run: `uv run pytest tests/hardware/test_hand_real.py --collect-only -q`
Expected: 1 test collected, no `ModuleNotFoundError: PySide6` and no import error.

### Task 2: Delete the GUI source and tests

**Files:**
- Delete: `src/arm101_hand/gui/`, `src/arm101_hand/scripts/unified_gui.py`, `src/arm101_hand/hand/controller.py`, `tests/unit/test_hand_controller_mock.py`, `tests/unit/test_sequence_parser.py`, `tests/unit/test_safe_park.py`, `tests/unit/test_safety_thresholds.py`

- [ ] **Step 1: Remove the files**

Run:
```powershell
git rm -r src/arm101_hand/gui
git rm src/arm101_hand/scripts/unified_gui.py src/arm101_hand/hand/controller.py
git rm tests/unit/test_hand_controller_mock.py tests/unit/test_sequence_parser.py tests/unit/test_safe_park.py tests/unit/test_safety_thresholds.py
```
Expected: 7 paths removed (the `gui` tree counts as several files).

- [ ] **Step 2: Confirm no surviving import of the removed modules**

Run: `uv run python -c "import arm101_hand.hand; import arm101_hand.config; import arm101_hand.robots"`
Expected: no error.
Then grep for stragglers (should be empty in `src/`+`tests/`, excluding the rewritten hardware test):

Run: `git grep -n "arm101_hand.gui\|hand.controller\|unified_gui\|PySide6" -- src tests`
Expected: no output.

### Task 3: Drop PySide6 dependency and arm101-gui script

**Files:**
- Modify: `pyproject.toml:12` (remove `PySide6` line), `pyproject.toml:20` (remove `arm101-gui` line)
- Modify: `uv.lock` (regenerate)

- [ ] **Step 1: Edit pyproject.toml**

Remove this dependency line:
```toml
    "PySide6>=6.6,<6.9",
```
Remove this console-script line:
```toml
arm101-gui = "arm101_hand.scripts.unified_gui:main"
```

- [ ] **Step 2: Regenerate the lockfile and re-sync**

Run:
```powershell
uv lock
uv sync
```
Expected: `uv.lock` updated; `PySide6` no longer in the resolved set.

- [ ] **Step 3: Full verification gate**

Run:
```powershell
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
uv run pytest --collect-only -q
```
Expected: ruff clean; mypy no new errors (pre-existing `import yaml` `import-untyped` noise is acceptable); unit tests pass; full collection (incl. hardware tests) succeeds with no import errors.

- [ ] **Step 4: Commit**

```powershell
git add -A
git commit -m "refactor: remove unused PySide6 GUI layer"
```

---

## Workstream 2 — Dual-device diagnostics

### Task 4: Promote shared constructors to device_setup.py

**Files:**
- Create: `src/arm101_hand/scripts/device_setup.py`
- Test: `tests/unit/test_device_setup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_device_setup.py
from pathlib import Path

from arm101_hand.scripts import device_setup


def test_calib_path_points_at_so_arm101_json():
    assert device_setup.CALIB_PATH.name == "so101_follower.json"
    assert device_setup.CALIB_PATH.parent.name == "so_arm101"


def test_app_config_path_is_repo_data():
    assert device_setup.APP_CONFIG_PATH.parts[-2:] == ("data", "app_config.yaml")


def test_exports_builders():
    for name in ("build_raw_bus", "build_follower", "load_arm_app_config", "FOLLOWER_ID"):
        assert hasattr(device_setup, name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_device_setup.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'arm101_hand.scripts.device_setup'`.

- [ ] **Step 3: Create device_setup.py**

Move the constructor/config/path glue out of `so_arm101/_common.py` (everything except the motion helpers `_drive_home`/`confirm_and_release`). Paths are recomputed from the repo root (`src/arm101_hand/scripts/device_setup.py` → `parents[3]` = repo root). Full content:

```python
"""Shared application-layer glue for SO-ARM101 device/bus construction.

Promoted out of ``scripts/calibration/so_arm101/_common.py`` so both the arm
calibration scripts AND the device-neutral ``scripts/diagnostics/`` helpers can
import it without a ``sys.path`` hack. Not pure (loads config + constructs the
robot/bus); the unit-testable calibration math lives in
``arm101_hand.robots.calibration_summary``.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the
duration of one run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import load_app_config, load_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS

# src/arm101_hand/scripts/device_setup.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"
CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "so_arm101" / "so101_follower.json"

# id="so101_follower" -> SO101FollowerNoGripper loads <calibration_dir>/so101_follower.json,
# and the subclass defaults calibration_dir to that directory.
FOLLOWER_ID = "so101_follower"


def load_arm_app_config():
    """Load + validate data/app_config.yaml (SystemExit with a clear message if absent)."""
    if not APP_CONFIG_PATH.is_file():
        raise SystemExit(f"app_config.yaml not found at {APP_CONFIG_PATH}")
    return load_app_config(APP_CONFIG_PATH)


def build_follower(cfg, *, use_degrees: bool):
    """Construct an (unconnected) SO101FollowerNoGripper from ``cfg``."""
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
    """Goal_Velocity (STS3215 register 46, raw units) for gentle calibration moves."""
    return cfg.safety.safe_park.park_velocity_arm


def load_home_degrees() -> dict[str, float]:
    """Per-joint default-home target in degrees, from arm_config.yaml ``poses['home']``."""
    if ARM_CONFIG_PATH.is_file():
        home = load_arm_poses(ARM_CONFIG_PATH).poses.get("home")
        if home is not None:
            return home.as_dict()
    return dict.fromkeys(ARM_JOINTS, 0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_device_setup.py -q`
Expected: 3 passed.

### Task 5: Make so_arm101/_common.py re-export from device_setup

**Files:**
- Modify: `scripts/calibration/so_arm101/_common.py`

- [ ] **Step 1: Replace the promoted definitions with re-imports**

Replace lines 10–80 (the imports + path constants + `load_arm_app_config`/`build_follower`/`build_raw_bus`/`gentle_velocity`/`load_home_degrees` definitions) with a re-import block, keeping `_drive_home` and `confirm_and_release` (lines 83–155) unchanged. New top of file (everything above `_drive_home`):

```python
"""Shared setup glue for the SO-ARM101 verify/audit helper scripts.

Device/bus constructors + path constants now live in
``arm101_hand.scripts.device_setup`` (shared with ``scripts/diagnostics/``) and
are re-exported here so the arm scripts' ``from _common import ...`` keeps
working unchanged. The motion helpers below stay local -- only the arm scripts
use them.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the
duration of one script run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

import sys
import time

from arm101_hand.robots.calibration_summary import ARM_JOINTS, STS3215_RESOLUTION
from arm101_hand.scripts.device_setup import (
    APP_CONFIG_PATH,
    ARM_CONFIG_PATH,
    CALIB_PATH,
    FOLLOWER_ID,
    build_follower,
    build_raw_bus,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

__all__ = [
    "APP_CONFIG_PATH",
    "ARM_CONFIG_PATH",
    "CALIB_PATH",
    "FOLLOWER_ID",
    "ARM_JOINTS",
    "STS3215_RESOLUTION",
    "build_follower",
    "build_raw_bus",
    "gentle_velocity",
    "load_arm_app_config",
    "load_home_degrees",
    "confirm_and_release",
]
```

Keep `_drive_home(...)` and `confirm_and_release(...)` exactly as they are (they use `ARM_JOINTS`, `STS3215_RESOLUTION`, `sys`, `time` — all still imported above).

- [ ] **Step 2: Verify the arm scripts still import cleanly**

Run:
```powershell
uv run python -c "import sys; sys.path.insert(0, 'scripts/calibration/so_arm101'); import _common; print(_common.CALIB_PATH.name, _common.FOLLOWER_ID, bool(_common.confirm_and_release))"
```
Expected: `so101_follower.json so101_follower True`.

### Task 6: Move find_port.py to diagnostics (unchanged)

**Files:**
- Move: `scripts/calibration/so_arm101/find_port.py` → `scripts/diagnostics/find_port.py`

- [ ] **Step 1: git mv**

Run:
```powershell
New-Item -ItemType Directory -Force scripts/diagnostics | Out-Null
git mv scripts/calibration/so_arm101/find_port.py scripts/diagnostics/find_port.py
```
Expected: file relocated. No content change (it wraps `lerobot.scripts.lerobot_find_port`, device-agnostic).

### Task 7: Create dual-device scan.py in diagnostics

**Files:**
- Create: `scripts/diagnostics/scan.py`
- Delete: `scripts/calibration/so_arm101/scan.py`

- [ ] **Step 1: Write the new scan.py**

Arm branch is the existing logic (now importing from `device_setup`); hand branch is new (rustypot liveness probe). Full content:

```python
"""Read-only health check for the arm (5x STS3215) or hand (8x SCS0009) bus.

Pings every motor on the selected device and, for each responder, reads present
position, load, voltage, and temperature. Torque stays OFF the entire time.
Exit code is non-zero if any expected motor did not respond.

Usage:
  uv run python scripts/diagnostics/scan.py --device arm
  uv run python scripts/diagnostics/scan.py --device hand

IL-1: 12 V arm rail / 5 V hand rail (separate nominal voltages below).
IL-3: arm motors 1-5 (no gripper ID 6); hand servos 1-8.
IL-4: opens the bus briefly, releases it. Read-only.
"""

from __future__ import annotations

import argparse
import sys

from arm101_hand.robots.calibration_summary import ARM_JOINTS
from arm101_hand.scripts.device_setup import build_raw_bus, load_arm_app_config

_ARM_NOMINAL_V = 12.0  # IL-1: 12 V arm rail.
_HAND_NOMINAL_V = 5.0  # IL-1: 5 V hand rail.
_HAND_SERVO_IDS = (1, 2, 3, 4, 5, 6, 7, 8)
# scripts/calibration/amazing_hand/hand_calib_values.yaml (canonical hand calib, IL-5).


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


def _scan_arm() -> int:
    cfg = load_arm_app_config()
    bus = build_raw_bus(cfg)
    print(f"Opening arm bus on {cfg.arm.port} (read-only, torque off) ...")
    missing: list[str] = []
    try:
        try:
            bus.connect(handshake=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
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
            load = bus.read("Present_Load", joint, normalize=False)
            volt = bus.read("Present_Voltage", joint, normalize=False)
            temp = bus.read("Present_Temperature", joint, normalize=False)
            print(f"{joint:<14}{motor_id:>3}{model:>7}{pos:>9}{load:>7}{volt / 10:>6.1f}V{temp:>7}")
            _warn(cfg, joint, temp, volt / 10, _ARM_NOMINAL_V)
    finally:
        if bus.is_connected:
            bus.disconnect(disable_torque=False)
            print(f"Bus closed on {cfg.arm.port}.")
    return _report(missing, len(ARM_JOINTS))


def _scan_hand() -> int:
    from rustypot import Scs0009PyController

    from arm101_hand.config import load_hand_calibration

    from _hand_paths import HAND_CALIB_PATH  # noqa: PLC0415 -- see Step 2

    cfg = load_arm_app_config()  # safety thresholds (temp/voltage) live here.
    hand = load_hand_calibration(HAND_CALIB_PATH)  # port/baud/timeout from the hand calib YAML.
    print(f"Opening hand bus on {hand.com_port} (read-only) ...")
    try:
        c = Scs0009PyController(serial_port=hand.com_port, baudrate=hand.baudrate, timeout=hand.timeout)
    except (ConnectionError, OSError) as e:
        print(f"ERROR: could not open {hand.com_port}: {e}", file=sys.stderr)
        return 1
    missing: list[str] = []
    header = f"{'servo':>5}{'pos_deg':>9}{'load':>7}{'volt':>7}{'temp_c':>8}"
    print(header)
    print("-" * len(header))
    for sid in _HAND_SERVO_IDS:
        try:
            pos = _scalar(c.read_present_position(sid))
            load = _scalar(c.read_present_load(sid))
            volt = _scalar(c.read_present_voltage(sid)) * 0.1
            temp = _scalar(c.read_present_temperature(sid))
        except Exception:  # noqa: BLE001 -- a silent servo raises; report it missing.
            missing.append(str(sid))
            print(f"{sid:>5}{'(no response)':>31}")
            continue
        import numpy as np

        print(f"{sid:>5}{float(np.rad2deg(pos)):>9.1f}{abs(int(load)):>7}{volt:>6.1f}V{temp:>7.0f}")
        _warn(cfg, f"servo {sid}", temp, volt, _HAND_NOMINAL_V)
    return _report(missing, len(_HAND_SERVO_IDS))


def _warn(cfg, label: str, temp: float, volt_v: float, nominal_v: float) -> None:
    if temp >= cfg.safety.temp_warn_c:
        print(f"  WARNING: {label} temp {temp:.0f}C >= warn threshold {cfg.safety.temp_warn_c}C")
    dev_pct = abs(volt_v - nominal_v) / nominal_v * 100
    if dev_pct >= cfg.safety.voltage_warn_pct:
        print(
            f"  WARNING: {label} voltage {volt_v:.1f}V deviates {dev_pct:.1f}% "
            f"from {nominal_v:.0f}V nominal (warn {cfg.safety.voltage_warn_pct}%)"
        )


def _report(missing: list[str], total: int) -> int:
    if missing:
        print(f"\nMISSING: {missing}", file=sys.stderr)
        return 1
    print(f"\nAll {total} motors responded.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only bus health check for arm or hand.")
    parser.add_argument("--device", required=True, choices=["arm", "hand"])
    args = parser.parse_args()
    return _scan_arm() if args.device == "arm" else _scan_hand()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Create the shared hand-path helper for diagnostics**

The diagnostics scripts need the canonical hand calib path. Create `scripts/diagnostics/_hand_paths.py` (co-located, importable via `sys.path[0]` when run as a script):

```python
"""Canonical hand calibration path for the diagnostics scripts (IL-5)."""

from __future__ import annotations

from pathlib import Path

# scripts/diagnostics/_hand_paths.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
```

- [ ] **Step 3: Delete the old arm-only scan.py**

Run: `git rm scripts/calibration/so_arm101/scan.py`
Expected: removed.

- [ ] **Step 4: Smoke-import the new scan (no bus needed for arg parsing)**

Run: `uv run python scripts/diagnostics/scan.py --help`
Expected: usage text listing `--device {arm,hand}`; exit 0. (A real scan needs hardware; arg parsing must work without it.)

### Task 8: Create dual-device show_calib.py in diagnostics

**Files:**
- Create: `scripts/diagnostics/show_calib.py`
- Delete: `scripts/calibration/so_arm101/show_calib.py`

- [ ] **Step 1: Write the new show_calib.py**

```python
"""Pretty-print the arm or hand calibration; optionally compare to live position.

Arm: reads ``so101_follower.json`` (id/homing/min/max/span/midpoint). Hand:
reads the AmazingHand calib YAML (per-finger servo middle_pos + DOF limits).
``--live`` additionally opens the selected bus read-only (torque stays off).

Usage:
  uv run python scripts/diagnostics/show_calib.py --device arm [--live]
  uv run python scripts/diagnostics/show_calib.py --device hand [--live]

IL-5: never writes any calibration file.
"""

from __future__ import annotations

import argparse
import sys

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)
from arm101_hand.scripts.device_setup import CALIB_PATH, build_follower, load_arm_app_config

from _hand_paths import HAND_CALIB_PATH


def _scalar(value: object) -> float:
    return float(value[0]) if isinstance(value, list | tuple) else float(value)


def _show_arm(live: bool) -> int:
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
    if not live:
        return 0
    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)
    print(f"\nConnecting read-only on {cfg.arm.port} (torque stays off) ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        obs = follower.get_observation()
        print(f"\n{'joint':<14}{'present_deg':>12}{'mid_deg':>10}")
        print("-" * 36)
        for joint in ARM_JOINTS:
            present = obs.get(f"{joint}.pos", float("nan"))
            print(f"{joint:<14}{present:>12.2f}{0.0:>10.2f}")
    finally:
        if follower.is_connected:
            follower.disconnect()
        print("Bus closed.")
    return 0


def _show_hand(live: bool) -> int:
    from arm101_hand.config import load_hand_calibration
    from arm101_hand.hand import servo_radians_to_degrees

    if not HAND_CALIB_PATH.is_file():
        print(f"calibration not found at {HAND_CALIB_PATH}", file=sys.stderr)
        return 1
    cfg = load_hand_calibration(HAND_CALIB_PATH)
    print(f"Calibration: {HAND_CALIB_PATH}")
    header = (
        f"{'finger':<8}{'s1':>3}{'mid1':>8}{'s2':>4}{'mid2':>8}"
        f"{'base_min':>10}{'base_max':>10}{'side_min':>10}{'side_max':>10}"
    )
    print(header)
    print("-" * len(header))
    for name, block in cfg.fingers.items():
        lim = block.limits
        print(
            f"{name:<8}{block.servo_1.id:>3}{block.servo_1.middle_pos:>8.1f}"
            f"{block.servo_2.id:>4}{block.servo_2.middle_pos:>8.1f}"
            f"{lim.base_min:>10.1f}{lim.base_max:>10.1f}{lim.side_min:>10.1f}{lim.side_max:>10.1f}"
        )
    if not live:
        return 0
    from rustypot import Scs0009PyController

    print(f"\nConnecting read-only on {cfg.com_port} (torque stays off) ...")
    try:
        c = Scs0009PyController(serial_port=cfg.com_port, baudrate=cfg.baudrate, timeout=cfg.timeout)
    except (ConnectionError, OSError) as e:
        print(f"ERROR: could not open {cfg.com_port}: {e}", file=sys.stderr)
        return 1
    print(f"\n{'servo':>5}{'present_deg':>12}{'mid_pos':>9}")
    print("-" * 26)
    for name, block in cfg.fingers.items():
        for servo in (block.servo_1, block.servo_2):
            rad = _scalar(c.read_present_position(servo.id))
            deg = servo_radians_to_degrees(servo.id, rad, servo.middle_pos)
            print(f"{servo.id:>5}{deg:>12.2f}{servo.middle_pos:>9.1f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Show arm or hand calibration.")
    parser.add_argument("--device", required=True, choices=["arm", "hand"])
    parser.add_argument("--live", action="store_true", help="also read present positions (read-only)")
    args = parser.parse_args()
    return _show_arm(args.live) if args.device == "arm" else _show_hand(args.live)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Delete the old arm-only show_calib.py**

Run: `git rm scripts/calibration/so_arm101/show_calib.py`
Expected: removed.

- [ ] **Step 3: Smoke-run arm show_calib (no bus; reads the JSON)**

Run: `uv run python scripts/diagnostics/show_calib.py --device arm`
Expected: prints the calibration table from `so101_follower.json` (or a clear "calibration not found" message if the JSON is absent on this machine — either is an acceptable non-crash). `--help` must list `--device {arm,hand}` and `--live`.

### Task 9: Verify and commit Workstream 2

- [ ] **Step 1: Full verification gate**

Run:
```powershell
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
uv run python scripts/diagnostics/scan.py --help
uv run python scripts/diagnostics/show_calib.py --help
```
Expected: ruff clean; mypy no new errors; tests pass (incl. `test_device_setup.py`); both `--help` invocations show `--device`.

- [ ] **Step 2: Confirm arm scripts that import _common still run their --help / dry path**

Run:
```powershell
uv run python scripts/calibration/so_arm101/sweep.py --help
```
Expected: usage text, no `ImportError` from `_common` (proves the re-export works end-to-end).

- [ ] **Step 3: Commit**

```powershell
git add -A
git commit -m "refactor(diagnostics): dual-device scan/show_calib/find_port"
```

---

## Workstream 3 — Rename the hand calibration assets

### Task 10: git mv the folder, scripts, and YAML

**Files:** `scripts/calibration/AmazingHand/` and its contents.

- [ ] **Step 1: Rename the folder**

Run: `git mv scripts/calibration/AmazingHand scripts/calibration/amazing_hand`
Expected: folder relocated.

- [ ] **Step 2: Rename the six scripts and the YAML**

Run:
```powershell
git mv scripts/calibration/amazing_hand/AmazingHand_MotorReset.py scripts/calibration/amazing_hand/motor_reset.py
git mv scripts/calibration/amazing_hand/AmazingHand_MiddlePos_FingerCalib.py scripts/calibration/amazing_hand/middle_calib.py
git mv scripts/calibration/amazing_hand/AmazingHand_RangeCalib.py scripts/calibration/amazing_hand/range_calib.py
git mv scripts/calibration/amazing_hand/AmazingHand_FingerTest.py scripts/calibration/amazing_hand/finger_test.py
git mv scripts/calibration/amazing_hand/AmazingHand_FullHand_Test.py scripts/calibration/amazing_hand/full_hand_test.py
git mv scripts/calibration/amazing_hand/AmazingHand_SetPose.py scripts/calibration/amazing_hand/set_pose.py
git mv scripts/calibration/amazing_hand/AmazingHand_calib_values.yaml scripts/calibration/amazing_hand/hand_calib_values.yaml
```
Expected: 7 renames. `jog.py` and `README.md` keep their names.

### Task 11: Update YAML_PATH constants and intra-script docstrings

**Files (each has `YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"`):**
- `scripts/calibration/amazing_hand/{motor_reset,middle_calib,range_calib,finger_test,full_hand_test,set_pose,jog}.py`

- [ ] **Step 1: Replace the YAML filename in every script's YAML_PATH**

In each of the 7 files, replace:
```python
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"
```
with:
```python
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
```

- [ ] **Step 2: Update docstring/comment references to the old YAML name and sibling script names**

Replace remaining `AmazingHand_calib_values.yaml` → `hand_calib_values.yaml` in docstrings/comments of those files (e.g. `full_hand_test.py:14`, `finger_test.py:13`, `range_calib.py:4`, `middle_calib.py:9`, `set_pose.py:10,17`, `jog.py:8`). In `jog.py:59` update the path comment `scripts/calibration/AmazingHand/jog.py` → `scripts/calibration/amazing_hand/jog.py`.

- [ ] **Step 3: Verify the hand scripts import + resolve the new path**

Run:
```powershell
uv run python -c "import sys; sys.path.insert(0,'scripts/calibration/amazing_hand'); import jog; print(jog.YAML_PATH.name)"
```
Expected: `hand_calib_values.yaml`.

### Task 12: Update src docstrings and active tests

**Files:**
- Modify: `src/arm101_hand/config/calibration.py:1,76,102`, `src/arm101_hand/config/__init__.py:6`, `src/arm101_hand/hand/kinematics.py:18`, `src/arm101_hand/hand/pose_jog.py:1`, `src/arm101_hand/hand/range_calib.py:4`
- Modify: `tests/unit/test_calibration.py:84`, `tests/unit/test_hand_config_save.py:10-16`, `tests/unit/test_hand_kinematics.py:77`

- [ ] **Step 1: Update src docstring paths**

Replace `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` → `scripts/calibration/amazing_hand/hand_calib_values.yaml` and bare `AmazingHand_calib_values.yaml` → `hand_calib_values.yaml` in:
- `config/calibration.py` lines 1, 76, 102
- `config/__init__.py` line 6
- `hand/kinematics.py` line 18
In `hand/pose_jog.py:1` and `hand/range_calib.py:4` replace `scripts/calibration/AmazingHand/jog.py` → `scripts/calibration/amazing_hand/jog.py` and `AmazingHand_RangeCalib.py` → `range_calib.py`.

- [ ] **Step 2: Update test_calibration.py:84**

Replace:
```python
    path = Path("scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml")
```
with:
```python
    path = Path("scripts/calibration/amazing_hand/hand_calib_values.yaml")
```

- [ ] **Step 3: Update test_hand_config_save.py SEED path (lines 10-16)**

Replace:
```python
SEED = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "AmazingHand"
    / "AmazingHand_calib_values.yaml"
)
```
with:
```python
SEED = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "amazing_hand"
    / "hand_calib_values.yaml"
)
```

- [ ] **Step 4: Update test_hand_kinematics.py:77 comment**

Replace the docstring reference `AmazingHand_FullHand_Test convention` → `full_hand_test convention`.

- [ ] **Step 5: Run the affected unit tests**

Run: `uv run pytest tests/unit/test_calibration.py tests/unit/test_hand_config_save.py tests/unit/test_hand_kinematics.py -q`
Expected: all pass (they now resolve the renamed seed YAML).

### Task 13: Update the folder README

**Files:**
- Modify: `scripts/calibration/amazing_hand/README.md`

- [ ] **Step 1: Replace all old names in the README**

Replace throughout: `AmazingHand_calib_values.yaml` → `hand_calib_values.yaml`; `scripts/calibration/AmazingHand/` → `scripts/calibration/amazing_hand/`; and the script names `AmazingHand_MotorReset.py`→`motor_reset.py`, `AmazingHand_MiddlePos_FingerCalib.py`→`middle_calib.py`, `AmazingHand_RangeCalib.py`→`range_calib.py`, `AmazingHand_FingerTest.py`→`finger_test.py`, `AmazingHand_FullHand_Test.py`→`full_hand_test.py`, `AmazingHand_SetPose.py`→`set_pose.py`.

- [ ] **Step 2: Verify no old references remain in the renamed folder**

Run: `git grep -n "AmazingHand_" -- scripts/calibration/amazing_hand`
Expected: no output.

### Task 14: Verify and commit Workstream 3

- [ ] **Step 1: Full verification gate + collection check**

Run:
```powershell
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
uv run pytest --collect-only -q
```
Expected: clean lint/type; unit tests pass; full collection (incl. the rewritten `test_hand_real.py`, which now points at the renamed path) succeeds.

- [ ] **Step 2: Confirm no stray old references outside references/ and historical docs**

Run: `git grep -n "AmazingHand_calib_values\|calibration/AmazingHand" -- ':!references' ':!docs/superpowers' ':!docs/plans/01-unified-gui-spec.md'`
Expected: only living docs flagged for Workstream 4 (CLAUDE.md, README.md, conventions, `.claude/`) — those are updated next.

- [ ] **Step 3: Commit**

```powershell
git add -A
git commit -m "refactor(hand): snake_case calibration scripts + folder/YAML rename"
```

---

## Workstream 4 — Documentation pass

### Task 15: Update IL-5 and other living docs, then run /doc_update

**Files:**
- Modify: `docs/conventions/00-iron-laws.md` (IL-5 path), and any of `docs/conventions/02-06`, `.claude/agents/iron_law_audit.md`, `.claude/skills/iron-law-audit/SKILL.md`, `.claude/skills/ruleset-update/SKILL.md`, `data/README.md` that name the old folder/YAML/GUI.

- [ ] **Step 1: Update IL-5 in docs/conventions/00-iron-laws.md**

Replace the hand calibration path `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` → `scripts/calibration/amazing_hand/hand_calib_values.yaml`.

- [ ] **Step 2: Sweep the remaining living docs**

For each file from the Task 14 Step 2 grep output (excluding CLAUDE.md/README.md, which `/doc_update` owns), replace old folder/YAML/script names with the new ones, and remove GUI mentions (`arm101-gui`, `unified_gui`, PySide6, the GUI tab) where they describe current behavior.

Run to find them: `git grep -n "AmazingHand\|arm101-gui\|unified_gui\|PySide6" -- docs/conventions .claude data/README.md`
Expected after edits: no output for current-behavior references (historical phrasing inside `docs/superpowers` is out of scope and not searched here).

- [ ] **Step 3: Run /doc_update for CLAUDE.md and README.md**

Invoke the `/doc_update` slash command. It refreshes CLAUDE.md (directory tree, Common workflows, IL-5 line, tech-debt) and README.md to reflect: GUI removed, diagnostics at `scripts/diagnostics/` with `--device`, hand folder `amazing_hand/` with renamed scripts.

- [ ] **Step 4: Final full gate**

Run:
```powershell
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware' -q
git grep -n "AmazingHand_calib_values\|calibration/AmazingHand\|arm101-gui\|unified_gui\|PySide6" -- ':!references' ':!docs/superpowers' ':!docs/plans/01-unified-gui-spec.md'
```
Expected: clean lint/type/test; the final grep returns no output (all current-behavior references migrated; only `references/` and historical docs retain old names by design).

- [ ] **Step 5: Commit (created by /doc_update or manually)**

```powershell
git add -A
git commit -m "docs: refresh for GUI removal, diagnostics, hand rename"
```

---

## Self-Review notes

- **Spec coverage:** WS1 §3.1–3.5 → Tasks 1–3 (incl. the `test_hand_real.py` rewrite the spec flagged in §3.5). WS2 §4.1–4.5 → Tasks 4–9 (`device_setup.py` promotion §4.2 → Task 4–5; `find_port` §4.3 → Task 6; `scan --device` §4.4 → Task 7; `show_calib --device` §4.5 → Task 8). WS3 §5 → Tasks 10–14. Docs §6 → Task 15.
- **app_config schema** left intact (spec §3.4) — no task modifies it; `scan` reads `safety.*` thresholds for both devices, `gentle_velocity` reads `safe_park.park_velocity_arm`.
- **Hand port source** (spec §4.4 decision): hand `scan`/`show_calib` read `com_port` from the renamed hand calib YAML; `app_config.safety` supplies thresholds only.
- **references/ untouched** (IL-2): every grep excludes `:!references`.
- **Type/name consistency:** `device_setup` export names match what `_common.py` re-imports (Task 5) and what `scan.py`/`show_calib.py` import (Tasks 7–8); `_scalar` defined locally in each script that uses it; `HAND_CALIB_PATH` defined once in `scripts/diagnostics/_hand_paths.py` and imported by both diagnostics scripts.
```
