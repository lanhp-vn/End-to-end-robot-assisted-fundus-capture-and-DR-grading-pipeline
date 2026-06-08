# Staged Grab Sequence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `scripts/demos/grab_sequence.py` so the arm reaches its grab pose in four ordered stages (each finishing before the next), and so exiting with `'h'` runs that sequence in exact reverse.

**Architecture:** Add a reusable, position-polling `drive_arm_joints` helper in `device_setup.py` (drives a *subset* of joints and waits until reached); refactor the existing `_drive_home` to delegate to it. Generalize `confirm_and_release` with an optional `home_routine` callable so the demo can inject its staged reverse in place of the built-in "drive all joints home at once". Rewrite the demo to call these for the forward stages and the reverse exit.

**Tech Stack:** Python 3.12, lerobot follower bus (`sync_write`/`sync_read`, raw encoder steps), `rustypot` `Scs0009PyController` for the hand, pytest (host unit tests, no hardware).

---

## Background facts (verified against the codebase)

- `shoulder_pan` calibration in `scripts/calibration/so_arm101/so101_follower.json`: `range_min=763, range_max=3481` → usable window `±119.47°`. So **base_max = `degree_bounds(763, 3481)[1]` ≈ +119.47°** (matches the user's "+119.5°").
- Arm degree→raw conversion (from existing `_drive_home`): `raw = round(mid + deg * (STS3215_RESOLUTION - 1) / 360)`, where `mid = (range_min + range_max) / 2` and `STS3215_RESOLUTION = 4096`.
- `follower.calibration[joint]` exposes `.range_min` / `.range_max`. `follower.bus.sync_write("Goal_Position", raw_dict, normalize=False)` and `follower.bus.sync_read("Present_Position", normalize=False)` are the raw-step I/O used today.
- Wait tuning lives in `arm_config.yaml` `tuning`: `home_tolerance_steps: 25`, `home_timeout_s: 8.0`, `home_poll_s: 0.05` (exposed on `cfg.tuning`).
- `resolve_hand_pose_targets(hand_calib, hand_cfg, "open")` already works without `"open"` being in `hand_config.yaml` (it is limits-derived; the current demo already calls it).
- `confirm_and_release` is called by `jog.py`, `set_pose.py`, `sweep.py`, `capture_pose.py` (via `_common.py`) — all pass only existing args, so **adding a keyword-only `home_routine=None` is backward compatible**. No tests reference `confirm_and_release`, `_drive_home`, or `grab_sequence`.
- No `scripts/demos/README.md`; the grab demo is documented only in its own module docstring (IL-7). **No CLAUDE.md / README.md change is required.**

## File Structure

- **Modify** `src/arm101_hand/scripts/device_setup.py`
  - Add `drive_arm_joints(follower, targets_deg, vel, *, tolerance, timeout_s, poll_s)` — drive a joint subset, poll until reached.
  - Refactor `_drive_home` into a thin wrapper over `drive_arm_joints` (all five joints).
  - Add keyword-only `home_routine: Callable[[], None] | None = None` to `confirm_and_release`; when set, the `'h'` branch calls it instead of `_drive_home` + `on_home`.
- **Modify** `tests/unit/test_device_setup.py`
  - Add a tiny inline fake bus/follower and tests for `drive_arm_joints`, `_drive_home`, and the `confirm_and_release` `home_routine` / default / release-in-place branches.
- **Rewrite** `scripts/demos/grab_sequence.py`
  - Forward staged grab (pan→posture→pan→hand) using `drive_arm_joints`; reverse closure passed as `home_routine`.

---

### Task 1: `drive_arm_joints` helper + `_drive_home` delegation

**Files:**
- Modify: `src/arm101_hand/scripts/device_setup.py` (add `drive_arm_joints`; rewrite `_drive_home` body, lines 78-108)
- Test: `tests/unit/test_device_setup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_device_setup.py` (the file currently has only path/export assertions and imports `from arm101_hand.scripts import device_setup`). Add the imports and helpers at the top after the existing import line, and the test functions at the end:

```python
from dataclasses import dataclass


@dataclass
class _Cal:
    range_min: int
    range_max: int


class _FakeBus:
    """Records sync_write calls; a Goal_Position write instantly 'moves' present pos to goal."""

    def __init__(self, present: dict[str, int]):
        self.present = dict(present)
        self.writes: list[tuple[str, dict, object]] = []

    def sync_write(self, reg, values, normalize=True):
        self.writes.append((reg, dict(values), normalize))
        if reg == "Goal_Position":
            self.present.update(values)

    def sync_read(self, reg, normalize=True):
        assert reg == "Present_Position"
        return dict(self.present)

    def disable_torque(self):
        self.writes.append(("disable_torque", {}, None))


class _FakeFollower:
    def __init__(self, calibration, bus, is_connected=True):
        self.calibration = calibration
        self.bus = bus
        self.is_connected = is_connected


def _full_calib():
    return {j: _Cal(0, 4095) for j in device_setup.ARM_JOINTS}


def test_drive_arm_joints_writes_only_subset_and_converts_degrees():
    bus = _FakeBus({j: 0 for j in device_setup.ARM_JOINTS})
    follower = _FakeFollower(_full_calib(), bus)
    device_setup.drive_arm_joints(
        follower, {"shoulder_pan": 90.0}, vel=600, tolerance=25, timeout_s=1.0, poll_s=0.0
    )
    goal_writes = [w for w in bus.writes if w[0] == "Goal_Position"]
    assert len(goal_writes) == 1
    _reg, values, normalize = goal_writes[0]
    assert normalize is False
    assert set(values) == {"shoulder_pan"}, "only the requested joint is commanded"
    mid = (0 + 4095) / 2
    expected = int(round(mid + 90.0 * (4096 - 1) / 360))
    assert values["shoulder_pan"] == expected
    vel_writes = [w for w in bus.writes if w[0] == "Goal_Velocity"]
    assert vel_writes and set(vel_writes[0][1]) == {"shoulder_pan"}, "velocity scoped to subset"


def test_drive_arm_joints_empty_is_noop():
    bus = _FakeBus({})
    follower = _FakeFollower({}, bus)
    device_setup.drive_arm_joints(follower, {}, vel=600)
    assert bus.writes == []


def test_drive_home_commands_all_five_joints():
    bus = _FakeBus({j: 0 for j in device_setup.ARM_JOINTS})
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    device_setup._drive_home(follower, home, vel=600, timeout_s=1.0, poll_s=0.0)
    goal = [w for w in bus.writes if w[0] == "Goal_Position"][0]
    assert set(goal[1]) == set(device_setup.ARM_JOINTS), "_drive_home still drives all joints"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_device_setup.py -v`
Expected: `test_drive_arm_joints_writes_only_subset_and_converts_degrees` and `test_drive_arm_joints_empty_is_noop` FAIL with `AttributeError: module 'arm101_hand.scripts.device_setup' has no attribute 'drive_arm_joints'`. (`test_drive_home_commands_all_five_joints` may already pass against the current `_drive_home`.)

- [ ] **Step 3: Add `drive_arm_joints` and refactor `_drive_home`**

In `src/arm101_hand/scripts/device_setup.py`, **replace** the entire current `_drive_home` definition (lines 78-108, from `def _drive_home(` through the `print("  (home not fully reached ...")` line) with the new helper plus a thin wrapper:

```python
def drive_arm_joints(
    follower,
    targets_deg: dict[str, float],
    vel: int,
    *,
    tolerance: int = 25,
    timeout_s: float = 8.0,
    poll_s: float = 0.05,
) -> None:
    """Drive a SUBSET of joints to degree targets, then WAIT until they arrive. No torque change.

    ``targets_deg`` maps joint name -> degrees relative to that joint's calibrated mid; only the
    joints present are commanded (the rest hold their current torqued position). Degrees are
    converted to raw encoder steps and written with ``normalize=False`` so it works for ANY
    follower norm mode (DEGREES or RANGE_M100_100). After commanding, polls ``Present_Position``
    until every commanded joint is within ``tolerance`` raw steps of its goal -- so a slow, gentle
    move actually completes before the caller proceeds. Gives up after ``timeout_s`` (prints a
    note) so it never hangs. An empty ``targets_deg`` is a no-op. The caller owns enable/disable.
    """
    if not targets_deg:
        return
    goal_raw: dict[str, int] = {}
    for joint, deg in targets_deg.items():
        cal = follower.calibration[joint]
        mid = (cal.range_min + cal.range_max) / 2
        goal_raw[joint] = int(round(mid + deg * (STS3215_RESOLUTION - 1) / 360))
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(goal_raw, vel))
    follower.bus.sync_write("Goal_Position", goal_raw, normalize=False)

    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        present = follower.bus.sync_read("Present_Position", normalize=False)
        if all(abs(present[j] - goal_raw[j]) <= tolerance for j in goal_raw):
            return
        time.sleep(poll_s)
    print("  (target not fully reached within timeout -- continuing)", file=sys.stderr)


def _drive_home(
    follower,
    home: dict[str, float],
    vel: int,
    tolerance: int = 25,
    timeout_s: float = 8.0,
    poll_s: float = 0.05,
) -> None:
    """Drive ALL joints to the ``home`` pose (degrees) and wait. Thin wrapper over drive_arm_joints."""
    drive_arm_joints(
        follower,
        {j: home[j] for j in ARM_JOINTS},
        vel,
        tolerance=tolerance,
        timeout_s=timeout_s,
        poll_s=poll_s,
    )
```

(`ARM_JOINTS`, `STS3215_RESOLUTION`, `time`, `sys` are already imported at the top of the file.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_device_setup.py -v`
Expected: all tests PASS (the three new ones plus the existing three).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff format src tests; uv run ruff check src tests; uv run mypy src`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/scripts/device_setup.py tests/unit/test_device_setup.py
git commit -m "feat(arm): add drive_arm_joints subset+wait helper; _drive_home delegates"
```

---

### Task 2: `confirm_and_release` accepts a `home_routine`

**Files:**
- Modify: `src/arm101_hand/scripts/device_setup.py` (`confirm_and_release` signature + `'h'` branch)
- Test: `tests/unit/test_device_setup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_device_setup.py` (reuses `_FakeBus`, `_FakeFollower`, `_full_calib` from Task 1):

```python
def test_confirm_and_release_runs_home_routine_on_h(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus({j: 0 for j in device_setup.ARM_JOINTS})
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    called = []
    device_setup.confirm_and_release(
        follower, True, home, 600, offer_home=True, home_routine=lambda: called.append(True)
    )
    assert called == [True], "home_routine ran on 'h'"
    assert not any(w[0] == "Goal_Position" for w in bus.writes), "staged routine replaces default homing"
    assert any(w[0] == "disable_torque" for w in bus.writes), "arm torque always released (IL-4)"


def test_confirm_and_release_default_homes_on_h_without_routine(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "h")
    bus = _FakeBus({j: 0 for j in device_setup.ARM_JOINTS})
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    device_setup.confirm_and_release(follower, True, home, 600, offer_home=True)
    assert any(w[0] == "Goal_Position" for w in bus.writes), "default 'h' path drives home"
    assert any(w[0] == "disable_torque" for w in bus.writes)


def test_confirm_and_release_release_in_place_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    bus = _FakeBus({j: 0 for j in device_setup.ARM_JOINTS})
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    called = []
    device_setup.confirm_and_release(
        follower, True, home, 600, offer_home=True, home_routine=lambda: called.append(True)
    )
    assert called == [], "plain Enter does not run the staged reverse"
    assert not any(w[0] == "Goal_Position" for w in bus.writes), "no movement on release-in-place"
    assert any(w[0] == "disable_torque" for w in bus.writes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_device_setup.py -k confirm_and_release -v`
Expected: `test_confirm_and_release_runs_home_routine_on_h` and `test_confirm_and_release_release_in_place_on_enter` FAIL with `TypeError: confirm_and_release() got an unexpected keyword argument 'home_routine'`.

- [ ] **Step 3: Add the `home_routine` parameter and branch**

In `src/arm101_hand/scripts/device_setup.py`, add the keyword-only parameter to the `confirm_and_release` signature. **Change** this (currently lines 111-119):

```python
def confirm_and_release(
    follower,
    torque_on: bool,
    home: dict[str, float],
    vel: int,
    *,
    offer_home: bool = True,
    on_home: Callable[[], None] | None = None,
    tuning: ArmTuning | None = None,
) -> None:
```

to:

```python
def confirm_and_release(
    follower,
    torque_on: bool,
    home: dict[str, float],
    vel: int,
    *,
    offer_home: bool = True,
    on_home: Callable[[], None] | None = None,
    home_routine: Callable[[], None] | None = None,
    tuning: ArmTuning | None = None,
) -> None:
```

Then in the `'h'` branch, **replace** the current block (currently lines 168-179):

```python
        if choice == "h":
            print("Returning to home ...")
            try:
                _drive_home(follower, home, vel, **drive_kw)  # type: ignore[arg-type]
                print("Home reached.")
            except BaseException as e:
                print(f"  (homing interrupted: {e!r}) -- releasing torque anyway", file=sys.stderr)
            if on_home is not None:
                try:
                    on_home()
                except BaseException as e:
                    print(f"  (on_home hook failed: {e!r})", file=sys.stderr)
```

with:

```python
        if choice == "h":
            if home_routine is not None:
                print("Returning home (staged reverse) ...")
                try:
                    home_routine()
                except BaseException as e:
                    print(
                        f"  (staged reverse interrupted: {e!r}) -- releasing torque anyway",
                        file=sys.stderr,
                    )
            else:
                print("Returning to home ...")
                try:
                    _drive_home(follower, home, vel, **drive_kw)  # type: ignore[arg-type]
                    print("Home reached.")
                except BaseException as e:
                    print(f"  (homing interrupted: {e!r}) -- releasing torque anyway", file=sys.stderr)
                if on_home is not None:
                    try:
                        on_home()
                    except BaseException as e:
                        print(f"  (on_home hook failed: {e!r})", file=sys.stderr)
```

Also update the docstring: after the `on_home` paragraph (currently lines 131-134), add a sentence:

```python
    ``home_routine`` (optional): when supplied, the ``h`` branch calls it INSTEAD of the built-in
    "drive all joints home, then run on_home" -- a coordinated caller (e.g. the grab demo) uses it
    to run a fully custom, staged return (e.g. open the hand, then reverse the arm stage by stage).
    Its failure is reported but never blocks the arm torque-off. ``on_home`` is ignored when
    ``home_routine`` is set.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_device_setup.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Confirm existing callers still type-check (backward compatibility)**

Run: `uv run ruff check src; uv run mypy src`
Expected: no errors (the new param is keyword-only with a default; `jog.py`/`set_pose.py`/`sweep.py`/`capture_pose.py` are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/scripts/device_setup.py tests/unit/test_device_setup.py
git commit -m "feat(arm): confirm_and_release accepts a home_routine for staged exit"
```

---

### Task 3: Rewrite `grab_sequence.py` for the staged forward + reverse flow

**Files:**
- Rewrite: `scripts/demos/grab_sequence.py`

- [ ] **Step 1: Replace the file with the staged implementation**

Overwrite `scripts/demos/grab_sequence.py` with exactly:

```python
"""Grab demo: stage the SO-ARM101 into its ``grab`` pose, close the AmazingHand,
hold both under torque, and reverse the whole thing on exit.

Forward sequence (each stage finishes -- position-confirmed for the arm -- before
the next begins):
  1. shoulder_pan -> base_max (its calibrated upper bound, ~+119.5 deg)
  2. shoulder_lift + elbow_flex + wrist_flex + wrist_roll -> grab (simultaneous)
  3. shoulder_pan -> grab
  4. hand fingers -> grab

The two devices live on separate buses (arm = 12 V STS3215, hand = 5 V SCS0009)
on different COM ports, so both are held open at once -- no shared rail (IL-1) and
one owner per bus (IL-4).

On exit:
  * plain Enter / Ctrl+C / EOF -> release BOTH in place (no movement); the arm will
    sag from a raised pose -- you are warned at the prompt.
  * 'h' -> run the sequence in REVERSE (4 -> 1): open the hand, then shoulder_pan ->
    base_max, then the four posture joints -> home, then shoulder_pan -> home. The
    arm waits for each stage to finish before the next. Torque on BOTH buses is always
    cut at the end, even if a leg fails partway through.

Poses come from version-controlled config (IL-5; never written here):
  * arm  -> src/arm101_hand/data/arm_config.yaml ``poses['grab']`` / ``poses['home']``
  * hand -> src/arm101_hand/data/hand_config.yaml ``poses['grab']`` (``open`` is built-in)

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_arm_config, load_hand_calibration, load_hand_config
from arm101_hand.hand import resolve_hand_pose_targets
from arm101_hand.hand.protocol import SERVO_SYNC_S
from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    clamp_degrees,
    degree_bounds,
    load_arm_calibration,
)
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    drive_arm_joints,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

# scripts/demos/grab_sequence.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"

ARM_POSE = "grab"
HAND_POSE = "grab"
HAND_OPEN_POSE = "open"
PAN_JOINT = "shoulder_pan"
# Everything except the base pan -- moved together in forward step 2 / reverse step R2.
POSTURE_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
_HAND_SETTLE_S = 1.0


def _build_arm_plan() -> tuple[dict[str, float], float]:
    """Return (clamped ``grab`` targets per joint, shoulder_pan base_max in degrees).

    Grab degrees are clamped to each joint's calibrated window (an out-of-range joint is
    warned and skipped). ``base_max`` is shoulder_pan's calibrated upper degree bound.
    """
    poses = load_arm_config(ARM_CONFIG_PATH).poses
    if ARM_POSE not in poses:
        raise SystemExit(f"arm pose {ARM_POSE!r} not in {ARM_CONFIG_PATH} (poses)")
    pose = poses[ARM_POSE].as_dict()
    calib = load_arm_calibration(CALIB_PATH)
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: arm {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped
    if not targets:
        raise SystemExit("ERROR: all arm joints out of range -- nothing to drive")
    pan = calib[PAN_JOINT]
    base_max_deg = degree_bounds(pan.range_min, pan.range_max)[1]
    return targets, base_max_deg


def _drive_hand(c: Scs0009PyController, targets: dict[int, float], speed: int) -> None:
    """Enable torque and command every servo to its wire-radians target at ``speed``."""
    for sid in targets:
        c.write_torque_enable(sid, 1)
    for sid in sorted(targets):
        c.write_goal_speed(sid, speed)
        time.sleep(SERVO_SYNC_S)
    for sid in sorted(targets):
        c.write_goal_position(sid, targets[sid])
        time.sleep(SERVO_SYNC_S)


def main() -> int:
    # Resolve everything before touching hardware -- fail fast, leave no bus open.
    arm_targets, base_max_deg = _build_arm_plan()

    hand_calib = load_hand_calibration(HAND_CALIB_PATH)
    hand_cfg = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else None
    if hand_cfg is None or HAND_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    hand_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_POSE)
    # Open targets are built-in (limits-derived); used only on the 'h' (reverse) exit.
    hand_open_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_OPEN_POSE)

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()
    arm_tuning = cfg.tuning
    # The home_* tuning (tolerance/timeout/poll) governs every staged arm move here, not just
    # homing -- the grab stages use the same position-wait, so we reuse it rather than add new keys.
    wait_kw = {
        "tolerance": arm_tuning.home_tolerance_steps,
        "timeout_s": arm_tuning.home_timeout_s,
        "poll_s": arm_tuning.home_poll_s,
    }

    # Per-stage joint subsets (degrees). Built from arm_targets so a skipped joint drops out.
    posture_grab = {j: arm_targets[j] for j in POSTURE_JOINTS if j in arm_targets}
    posture_home = {j: home[j] for j in POSTURE_JOINTS}
    pan_grab = {PAN_JOINT: arm_targets[PAN_JOINT]} if PAN_JOINT in arm_targets else {}
    pan_base_max = {PAN_JOINT: base_max_deg}
    pan_home = {PAN_JOINT: home[PAN_JOINT]}

    hand = Scs0009PyController(
        serial_port=hand_cfg.connection.port,
        baudrate=hand_cfg.connection.baudrate,
        timeout=hand_cfg.connection.timeout,
    )

    def _staged_reverse() -> None:
        """Mirror of the forward grab (steps 4 -> 1), run when the user types 'h' on exit.

        The hand opens first and then holds 'open' under torque while the arm reverses; both
        torques are cut by the caller (confirm_and_release for the arm, the outer finally for
        the hand) after this returns.
        """
        print(f"  [R4] opening hand on {hand_cfg.connection.port} ...")
        _drive_hand(hand, hand_open_targets, hand_cfg.tuning.speeds.open)
        time.sleep(_HAND_SETTLE_S)
        print(f"  [R3] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [R2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> home ...")
        drive_arm_joints(follower, posture_home, vel, **wait_kw)
        print("  [R1] shoulder_pan -> home ...")
        drive_arm_joints(follower, pan_home, vel, **wait_kw)

    arm_torque_on = False
    try:
        # ---- Arm leg: connect, push on-file calibration, enable torque ----
        print(f"Connecting arm on {cfg.connection.port} ...")
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open arm port {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.enable_torque()
        arm_torque_on = True

        # ---- Forward grab, staged (each stage position-confirmed before the next) ----
        print(f"  [1] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> grab ...")
        drive_arm_joints(follower, posture_grab, vel, **wait_kw)
        print("  [3] shoulder_pan -> grab ...")
        drive_arm_joints(follower, pan_grab, vel, **wait_kw)
        print(f"Arm holding '{ARM_POSE}'.")

        # ---- Hand leg: close into grab ----
        print(f"  [4] driving hand on {hand_cfg.connection.port} to '{HAND_POSE}' ...")
        _drive_hand(hand, hand_targets, hand_cfg.tuning.speed)
        time.sleep(_HAND_SETTLE_S)
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Release the arm first: confirm_and_release prompts 'h'/Enter (both stay gripped
        # during the prompt). 'h' runs _staged_reverse (hand opens first, then the arm
        # reverses stage by stage); plain Enter releases in place. The hand release lives
        # in an inner finally so it ALWAYS runs -- neither bus may be left torqued (IL-4).
        try:
            if follower.is_connected:
                confirm_and_release(
                    follower,
                    arm_torque_on,
                    home,
                    vel,
                    offer_home=True,
                    home_routine=_staged_reverse,
                    tuning=arm_tuning,
                )
                with contextlib.suppress(Exception):
                    follower.disconnect()
                print("Arm bus closed.")
        finally:
            print("Releasing hand torque.")
            for sid in hand_targets:
                with contextlib.suppress(Exception):
                    hand.write_torque_enable(sid, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Syntax + lint + style**

Run: `uv run ruff format scripts/demos/grab_sequence.py; uv run ruff check scripts/demos/grab_sequence.py`
Expected: "1 file reformatted" or "left unchanged", and `ruff check` reports no errors. (`grab_sequence.py` lives under `scripts/`, which is outside `mypy src`, so mypy does not cover it.)

- [ ] **Step 3: Import/compile smoke check (no hardware, no bus opened)**

Run: `uv run python -c "import ast; ast.parse(open(r'scripts/demos/grab_sequence.py', encoding='utf-8').read()); print('parse-ok')"`
Expected: prints `parse-ok` (verifies syntax without executing `main()` / opening serial ports).

- [ ] **Step 4: Full host test suite (regression)**

Run: `uv run pytest -m 'not hardware'`
Expected: all tests PASS (Task 1 + Task 2 additions plus the pre-existing suite).

- [ ] **Step 5: Manual hardware verification (operator, with both buses powered)**

> Requires the arm on its COM port (12 V) and hand on its COM port (5 V), both free (IL-4). Start with the arm near home.

Run: `uv run python scripts/demos/grab_sequence.py`

Confirm, in order:
- [ ] **Forward** prints `[1] -> [2] -> [3] -> [4]` and each arm stage visibly completes before the next starts: pan swings to base_max, then lift/elbow/wrist move together, then pan swings to grab, then the hand closes.
- [ ] **No `  (target not fully reached within timeout -- continuing)` line prints during ANY stage** (forward or reverse). If it does, a stage advanced before finishing — bump `home_timeout_s` in `arm_config.yaml` for the long wrist_flex move (step 2 / R2 is ~129°) and re-run. This is the concrete check that "each step finished before the next" actually held.
- [ ] At the prompt, press **Enter**: both arm and hand release where they are (`Arm bus closed.` then `Releasing hand torque.`), no motion.
- [ ] Re-run; at the prompt type **`h` + Enter**: prints `[R4] -> [R3] -> [R2] -> [R1]` — hand opens first, then pan→base_max, then posture→home, then pan→home; arm ends at home; both torques release.
- [ ] Re-run; press **Ctrl+C during the forward sequence**: the script exits and BOTH torques are released (arm prompt may appear; Ctrl+C/Enter at it still releases).

- [ ] **Step 6: Commit**

```bash
git add scripts/demos/grab_sequence.py
git commit -m "feat(demo): stage grab_sequence (pan->posture->pan->hand) with reverse on 'h'"
```

---

## Self-Review

**Spec coverage:**
- Forward step 1 (pan→base_max) → Task 3 `[1]` + `pan_base_max` + `base_max_deg` from `_build_arm_plan` (Task 1 conversion, Task 1 `drive_arm_joints`). ✓
- Forward step 2 (wrist flex+roll, plus lift+elbow per the "with wrist" answer, simultaneous) → Task 3 `[2]` `posture_grab` over `POSTURE_JOINTS`. ✓
- Forward step 3 (pan→grab) → Task 3 `[3]` `pan_grab`. ✓
- Forward step 4 (hand→grab) → Task 3 `[4]` `_drive_hand`. ✓
- "each step finished before the next" → position-poll wait in `drive_arm_joints` (arm) / `_HAND_SETTLE_S` (hand). ✓
- Reverse 4→1 on `'h'` → `_staged_reverse` (R4 hand open, R3 pan→base_max, R2 posture→home, R1 pan→home) injected via `home_routine` (Task 2). ✓
- base_max derived from calibration → `degree_bounds(pan.range_min, pan.range_max)[1]`. ✓
- plain Enter = release in place → unchanged `confirm_and_release` Enter branch (Task 2 test `..._release_in_place_on_enter`). ✓
- IL-1 / IL-4 both buses end torque-off → outer/inner `finally` + `confirm_and_release` always `disable_torque`. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every run step shows the command and expected output. ✓

**Type/name consistency:** `drive_arm_joints` signature identical in Task 1 (def), Task 3 (calls), and tests. `home_routine` keyword identical in Task 2 (def + tests) and Task 3 (call). `POSTURE_JOINTS`, `PAN_JOINT`, `base_max_deg`, `wait_kw` defined once and used consistently. `_build_arm_plan` returns the 2-tuple consumed in `main`. ✓
