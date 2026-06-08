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

---

# Extension (2026-06-08): pre_grab hand pose + hand position polling

**Goal:** Step 2 also drives the hand to a new `pre_grab` pose (commanded with the arm posture, both confirmed before step 3); step 4 closes to `grab`. The `'h'` reverse becomes: hand→`pre_grab` → pan→base_max → posture→home → pan→home → (arm at home) hand→`open`. The hand now confirms each move by **position polling** (timeout-then-continue, so a grip that stalls on an object doesn't hang), and that polling helper is adopted across the hand scripts.

**Decisions (from interview):** lift+elbow stay in step 2 (4 posture joints); "wait for both" in step 2; hand grab-close uses timeout-then-continue; retrofit `set_pose.py`/`finger_test.py`/`full_hand_test.py` now. `pre_grab` already saved to `hand_config.yaml`. Hand poll tolerance = `radians(pose_margin_deg)`; timeout/poll are NEW config knobs (`save_hand_config` round-trips the full model, so they persist).

**New helper API (`src/arm101_hand/hand/motion.py`):** `write_hand_servos` (write only), `wait_hand_reached` (poll only, returns bool), `drive_hand_servos` (write+wait). All wire-radians; never raise/hang.

---

### Task 4: Hand poll tuning knobs `pose_timeout_s` / `pose_poll_s`

**Files:**
- Modify: `src/arm101_hand/config/hand_config.py` (`HandTuning`)
- Modify: `src/arm101_hand/data/hand_config.yaml` (add 2 knobs; also commits the saved `pre_grab` pose + the jog-save reformat)
- Modify: `src/arm101_hand/data/README.md` (document the 2 knobs)
- Test: `tests/unit/test_hand_config_schema.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_hand_config_schema.py` (add `import pytest` and `from pydantic import ValidationError` and `from arm101_hand.config.hand_config import HandTuning` if not already imported there):

```python
def test_pose_poll_knobs_default():
    t = HandTuning()
    assert t.pose_timeout_s == 2.0
    assert t.pose_poll_s == 0.03


def test_pose_poll_knobs_reject_nonpositive():
    with pytest.raises(ValidationError):
        HandTuning(pose_timeout_s=0)
    with pytest.raises(ValidationError):
        HandTuning(pose_poll_s=0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_hand_config_schema.py -k pose_poll -v`
Expected: FAIL — `HandTuning` has no `pose_timeout_s` (default path) / `extra="forbid"` rejects the kwarg.

- [ ] **Step 3: Add the two fields** to `HandTuning` in `src/arm101_hand/config/hand_config.py`, immediately after the `pose_margin_deg` line:

```python
    pose_timeout_s: float = Field(
        default=2.0, gt=0, description="hand move position-poll timeout (s) before continuing"
    )
    pose_poll_s: float = Field(
        default=0.03, gt=0, description="hand move position-poll interval (s)"
    )
```

- [ ] **Step 4: Add the knobs to the committed YAML.** In `src/arm101_hand/data/hand_config.yaml`, under `tuning:`, immediately after the `pose_margin_deg: 5.0` line, insert:

```yaml
  pose_timeout_s: 2.0
  pose_poll_s: 0.03
```

- [ ] **Step 5: Document the knobs.** In `src/arm101_hand/data/README.md`, find where `pose_margin_deg` is documented and add two sibling entries mirroring its style, e.g.:

```
- `tuning.pose_timeout_s` — seconds to wait for a hand move to position-confirm before continuing (a gripping close that stalls on an object simply times out and continues).
- `tuning.pose_poll_s` — interval between `read_present_position` polls while waiting for a hand move to arrive.
```

- [ ] **Step 6: Verify**

Run: `uv run pytest tests/unit/test_hand_config_schema.py -v; uv run pytest tests/unit/test_hand_config_save.py -v; uv run ruff check . ; uv run mypy src`
Expected: all pass; the round-trip test confirms the new knobs persist through `save_hand_config`.

- [ ] **Step 7: Commit** (stage exactly these four files — the YAML carries `pre_grab` + the knobs + your jog reformat):

```bash
git add src/arm101_hand/config/hand_config.py src/arm101_hand/data/hand_config.yaml src/arm101_hand/data/README.md tests/unit/test_hand_config_schema.py
git commit -m "feat(hand): add pose_timeout_s/pose_poll_s poll knobs; commit pre_grab pose"
```

---

### Task 5: Hand motion helper (`write_hand_servos`, `wait_hand_reached`, `drive_hand_servos`)

**Files:**
- Create: `src/arm101_hand/hand/motion.py`
- Modify: `src/arm101_hand/hand/__init__.py` (export the three)
- Test: `tests/unit/test_hand_motion.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_hand_motion.py`:

```python
"""Unit tests for hand motion helpers (fake controller, no bus)."""

from __future__ import annotations

import math

from arm101_hand.hand import drive_hand_servos, wait_hand_reached, write_hand_servos


class _FakeController:
    def __init__(self, *, stuck: bool = False):
        self.present: dict[int, float] = {}
        self.torque: dict[int, int] = {}
        self.speed: dict[int, int] = {}
        self.calls: list[tuple] = []
        self._stuck = stuck

    def write_torque_enable(self, sid, on):
        self.torque[sid] = on
        self.calls.append(("torque", sid, on))

    def write_goal_speed(self, sid, sp):
        self.speed[sid] = sp
        self.calls.append(("speed", sid, sp))

    def write_goal_position(self, sid, rad):
        self.calls.append(("goal", sid, rad))
        if not self._stuck:
            self.present[sid] = rad  # instant arrival unless stuck (gripping)

    def read_present_position(self, sid):
        return [self.present.get(sid, 0.0)]  # rustypot returns a single-element list


def test_write_hand_servos_writes_torque_speed_goal():
    c = _FakeController()
    write_hand_servos(c, {1: 0.5, 2: -0.5}, speed=3)
    assert c.torque == {1: 1, 2: 1}
    assert c.speed == {1: 3, 2: 3}
    goals = {sid: rad for kind, sid, rad in c.calls if kind == "goal"}
    assert goals == {1: 0.5, 2: -0.5}


def test_write_hand_servos_can_skip_torque():
    c = _FakeController()
    write_hand_servos(c, {1: 0.1}, speed=3, enable_torque=False)
    assert c.torque == {}
    assert any(kind == "goal" for kind, *_ in c.calls)


def test_wait_hand_reached_true_when_within_tolerance():
    c = _FakeController()
    c.present = {1: 0.50, 2: -0.50}
    assert (
        wait_hand_reached(
            c, {1: 0.51, 2: -0.49}, tolerance_rad=math.radians(5), timeout_s=1.0, poll_s=0.0
        )
        is True
    )


def test_wait_hand_reached_empty_is_true_without_reads():
    c = _FakeController()
    assert wait_hand_reached(c, {}, tolerance_rad=0.1, timeout_s=1.0, poll_s=0.0) is True
    assert c.calls == []


def test_wait_hand_reached_times_out_returns_false(capsys):
    c = _FakeController(stuck=True)
    c.present = {1: 0.0}
    ok = wait_hand_reached(c, {1: 1.0}, tolerance_rad=math.radians(5), timeout_s=0.05, poll_s=0.0)
    assert ok is False
    assert "hand not fully reached within timeout" in capsys.readouterr().err


def test_drive_hand_servos_writes_then_confirms():
    c = _FakeController()  # not stuck -> goal write sets present -> reaches immediately
    ok = drive_hand_servos(
        c, {1: 0.2, 2: 0.3}, speed=3, tolerance_rad=math.radians(5), timeout_s=1.0, poll_s=0.0
    )
    assert ok is True
    assert c.torque == {1: 1, 2: 1}


def test_drive_hand_servos_timeout_then_continue_returns_false(capsys):
    c = _FakeController(stuck=True)
    c.present = {1: 0.0}
    ok = drive_hand_servos(
        c, {1: 1.0}, speed=3, tolerance_rad=math.radians(5), timeout_s=0.05, poll_s=0.0
    )
    assert ok is False  # stalled (gripping) -> continues, never hangs
    assert "hand not fully reached within timeout" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_hand_motion.py -v`
Expected: FAIL at import — `cannot import name 'drive_hand_servos' from 'arm101_hand.hand'`.

- [ ] **Step 3: Create `src/arm101_hand/hand/motion.py`** with exactly:

```python
"""Hand motion helpers: command SCS0009 servos and confirm arrival by position-polling.

Mirrors the arm's ``drive_arm_joints`` position-wait so BOTH devices confirm a move
finished (within tolerance) before the caller proceeds, instead of a blind sleep. A
gripping close that stalls on an object simply times out and continues
(timeout-then-continue) -- these helpers never raise and never hang. All positions are
SCS0009 wire-radians (what ``write_goal_position`` accepts and ``read_present_position``
returns); convert a degree tolerance with ``math.radians`` at the call site.
"""

from __future__ import annotations

import sys
import time

from arm101_hand.hand.protocol import SERVO_SYNC_S


def _scalar(v: object) -> float:
    """Unwrap rustypot's single-element list reads (``read_<reg>`` returns a list)."""
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def write_hand_servos(
    controller,
    targets: dict[int, float],
    speed: int,
    *,
    enable_torque: bool = True,
) -> None:
    """Command every servo to its wire-radians goal at ``speed`` (no wait).

    Order mirrors the bench-proven sequence: torque on (optional) -> goal speed -> goal
    position, each goal write spaced by ``SERVO_SYNC_S`` for clean bus arbitration. An
    empty ``targets`` is a no-op. The caller owns torque-off.
    """
    if enable_torque:
        for sid in targets:
            controller.write_torque_enable(sid, 1)
    for sid in sorted(targets):
        controller.write_goal_speed(sid, speed)
        time.sleep(SERVO_SYNC_S)
    for sid in sorted(targets):
        controller.write_goal_position(sid, targets[sid])
        time.sleep(SERVO_SYNC_S)


def wait_hand_reached(
    controller,
    targets: dict[int, float],
    *,
    tolerance_rad: float,
    timeout_s: float,
    poll_s: float,
) -> bool:
    """Poll ``read_present_position`` until every servo in ``targets`` (id -> goal radians) is
    within ``tolerance_rad`` of its goal. Returns True if all reached; on timeout prints a note
    to stderr and returns False (never raises, never hangs). Empty ``targets`` -> True.
    """
    if not targets:
        return True
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if all(
            abs(_scalar(controller.read_present_position(sid)) - goal) <= tolerance_rad
            for sid, goal in targets.items()
        ):
            return True
        time.sleep(poll_s)
    print("  (hand not fully reached within timeout -- continuing)", file=sys.stderr)
    return False


def drive_hand_servos(
    controller,
    targets: dict[int, float],
    speed: int,
    *,
    tolerance_rad: float,
    timeout_s: float,
    poll_s: float,
    enable_torque: bool = True,
) -> bool:
    """``write_hand_servos`` then ``wait_hand_reached``; returns the wait's bool."""
    write_hand_servos(controller, targets, speed, enable_torque=enable_torque)
    return wait_hand_reached(
        controller, targets, tolerance_rad=tolerance_rad, timeout_s=timeout_s, poll_s=poll_s
    )
```

- [ ] **Step 4: Export from `src/arm101_hand/hand/__init__.py`.** Add an import block (after the `.kinematics` import) and three `__all__` entries:

```python
from .motion import (
    drive_hand_servos,
    wait_hand_reached,
    write_hand_servos,
)
```

and add `"drive_hand_servos"`, `"wait_hand_reached"`, `"write_hand_servos"` to `__all__`.

- [ ] **Step 5: Verify**

Run: `uv run pytest tests/unit/test_hand_motion.py -v; uv run ruff check . ; uv run mypy src`
Expected: 7 tests pass; ruff + mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/hand/motion.py src/arm101_hand/hand/__init__.py tests/unit/test_hand_motion.py
git commit -m "feat(hand): add write/wait/drive hand-servo position-poll helpers"
```

---

### Task 6: Rewire `grab_sequence.py` for pre_grab + hand polling + new reverse

**Files:** Rewrite `scripts/demos/grab_sequence.py`.

- [ ] **Step 1: Overwrite the file** with exactly:

```python
"""Grab demo: stage the SO-ARM101 into its ``grab`` pose, close the AmazingHand,
hold both under torque, and reverse the whole thing on exit.

Forward sequence (each stage position-confirmed -- arm AND hand -- before the next):
  1. shoulder_pan -> base_max (its calibrated upper bound)
  2. shoulder_lift + elbow_flex + wrist_flex + wrist_roll -> grab, AND hand -> pre_grab
     (commanded together, then both polled to completion)
  3. shoulder_pan -> grab
  4. hand fingers -> grab (closes onto the object; stalls -> times out -> continues)

The two devices live on separate buses (arm = 12 V STS3215, hand = 5 V SCS0009) on
different COM ports, so both are held open at once -- no shared rail (IL-1) and one
owner per bus (IL-4).

On exit:
  * plain Enter / Ctrl+C / EOF -> release BOTH in place (no movement); the arm will
    sag from a raised pose -- you are warned at the prompt.
  * 'h' -> run the sequence in REVERSE: hand -> pre_grab, then shoulder_pan -> base_max,
    then the four posture joints -> home, then shoulder_pan -> home, then (arm now at
    home) hand -> open. Each move is position-confirmed. Torque on BOTH buses is always
    cut at the end, even if a leg fails partway through.

Poses come from version-controlled config (IL-5; never written here):
  * arm  -> src/arm101_hand/data/arm_config.yaml ``poses['grab']`` / ``poses['home']``
  * hand -> src/arm101_hand/data/hand_config.yaml ``poses['grab']`` / ``poses['pre_grab']``
            (``open`` is built-in / limits-derived)

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import contextlib
import math
import sys
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_arm_config, load_hand_calibration, load_hand_config
from arm101_hand.hand import (
    drive_hand_servos,
    resolve_hand_pose_targets,
    wait_hand_reached,
    write_hand_servos,
)
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
HAND_PRE_GRAB_POSE = "pre_grab"
HAND_OPEN_POSE = "open"
PAN_JOINT = "shoulder_pan"
# Everything except the base pan -- moved together in forward step 2 / reverse step R2.
POSTURE_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


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


def main() -> int:
    # Resolve everything before touching hardware -- fail fast, leave no bus open.
    arm_targets, base_max_deg = _build_arm_plan()

    hand_calib = load_hand_calibration(HAND_CALIB_PATH)
    hand_cfg = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else None
    if hand_cfg is None or HAND_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    if HAND_PRE_GRAB_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_PRE_GRAB_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    hand_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_POSE)
    hand_pre_grab_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_PRE_GRAB_POSE)
    # Open targets are built-in (limits-derived); used as the final reverse step.
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
    # Hand position-poll params: tolerance from pose_margin_deg (radians), timeout/poll from config.
    hand_wait_kw = {
        "tolerance_rad": math.radians(hand_cfg.tuning.pose_margin_deg),
        "timeout_s": hand_cfg.tuning.pose_timeout_s,
        "poll_s": hand_cfg.tuning.pose_poll_s,
    }
    hand_speed = hand_cfg.tuning.speed
    hand_open_speed = hand_cfg.tuning.speeds.open

    # Per-stage arm joint subsets (degrees). Built from arm_targets so a skipped joint drops out.
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
        """Reverse of the forward grab, run when the user types 'h' on exit.

        hand -> pre_grab, then arm pan->base_max / posture->home / pan->home, then (arm at
        home) hand -> open. The hand holds each pose under torque between stages; both torques
        are cut by the caller (confirm_and_release for the arm, the outer finally for the hand).
        """
        print(f"  [Rh] hand -> '{HAND_PRE_GRAB_POSE}' on {hand_cfg.connection.port} ...")
        drive_hand_servos(hand, hand_pre_grab_targets, hand_open_speed, **hand_wait_kw)
        print(f"  [R3] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [R2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> home ...")
        drive_arm_joints(follower, posture_home, vel, **wait_kw)
        print("  [R1] shoulder_pan -> home ...")
        drive_arm_joints(follower, pan_home, vel, **wait_kw)
        print(f"  [Ro] arm home -- hand -> '{HAND_OPEN_POSE}' ...")
        drive_hand_servos(hand, hand_open_targets, hand_open_speed, **hand_wait_kw)

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

        # ---- Forward grab, staged ----
        print(f"  [1] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)

        # Step 2: command the hand (pre_grab) and the arm posture together, then confirm BOTH.
        print(f"  [2] posture -> grab + hand -> '{HAND_PRE_GRAB_POSE}' (together) ...")
        write_hand_servos(hand, hand_pre_grab_targets, hand_speed)
        drive_arm_joints(follower, posture_grab, vel, **wait_kw)
        wait_hand_reached(hand, hand_pre_grab_targets, **hand_wait_kw)

        if not pan_grab:
            print("  WARNING: shoulder_pan out of range -- [3] is a no-op; pan stays at base_max")
        print("  [3] shoulder_pan -> grab ...")
        drive_arm_joints(follower, pan_grab, vel, **wait_kw)
        print(f"Arm holding '{ARM_POSE}': {arm_targets}")

        # ---- Hand leg: close into grab (stalls on the object -> timeout -> continue) ----
        print(f"  [4] hand -> '{HAND_POSE}' on {hand_cfg.connection.port} ...")
        drive_hand_servos(hand, hand_targets, hand_speed, **hand_wait_kw)
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Release the arm first: confirm_and_release prompts 'h'/Enter (both stay gripped
        # during the prompt). 'h' runs _staged_reverse; plain Enter releases in place. The hand
        # release lives in an inner finally so it ALWAYS runs -- neither bus may be left torqued (IL-4).
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

- [ ] **Step 2: Verify (host)**

Run: `uv run ruff format scripts/demos/grab_sequence.py; uv run ruff check . ; uv run python -c "import ast; ast.parse(open(r'scripts/demos/grab_sequence.py', encoding='utf-8').read()); print('parse-ok')"; uv run pytest -m 'not hardware'`
Expected: ruff clean, `parse-ok`, all tests pass.

- [ ] **Step 3: SKIP the hardware run** — operator-only (see the consolidated hardware checklist at the end of this extension). Note it as pending in the report.

- [ ] **Step 4: Commit**

```bash
git add scripts/demos/grab_sequence.py
git commit -m "feat(demo): grab_sequence step-2 pre_grab + hand position polling + reverse-to-open"
```

---

### Task 7: Retrofit `set_pose.py` to position polling

**Files:** Modify `scripts/calibration/amazing_hand/set_pose.py`.

- [ ] **Step 1: Update imports.** Replace:

```python
import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import HandConfig, load_hand_calibration, load_hand_config
from arm101_hand.hand import BUILTIN_POSES, available_pose_names, resolve_hand_pose_targets
from arm101_hand.hand.protocol import SERVO_SYNC_S
```

with:

```python
import contextlib
import math
import sys
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import HandConfig, load_hand_calibration, load_hand_config
from arm101_hand.hand import (
    BUILTIN_POSES,
    available_pose_names,
    drive_hand_servos,
    resolve_hand_pose_targets,
)
```

- [ ] **Step 2: Delete the local `drive_targets` function** (the whole `def drive_targets(c, targets, speed): ...` block).

- [ ] **Step 3: Replace the drive call.** In `main`, change:

```python
    print(f"Setting hand to '{pose}'{detail}...")
    try:
        drive_targets(c, targets, speed)
        print(f"Hand held at '{pose}' under torque.")
        input("Press Enter to release torque and exit... ")
```

to:

```python
    print(f"Setting hand to '{pose}'{detail}...")
    try:
        drive_hand_servos(
            c,
            targets,
            speed,
            tolerance_rad=math.radians(hcfg.tuning.pose_margin_deg),
            timeout_s=hcfg.tuning.pose_timeout_s,
            poll_s=hcfg.tuning.pose_poll_s,
            enable_torque=False,  # set_pose already enabled torque above
        )
        print(f"Hand held at '{pose}' under torque.")
        input("Press Enter to release torque and exit... ")
```

- [ ] **Step 4: Verify (host)**

Run: `uv run ruff format scripts/calibration/amazing_hand/set_pose.py; uv run ruff check . ; uv run python -c "import ast; ast.parse(open(r'scripts/calibration/amazing_hand/set_pose.py', encoding='utf-8').read()); print('parse-ok')"`
Expected: ruff clean (no unused `time`/`SERVO_SYNC_S`), `parse-ok`.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/amazing_hand/set_pose.py
git commit -m "refactor(hand): set_pose.py drives via position-polling drive_hand_servos"
```

---

### Task 8: Retrofit `finger_test.py` to poll-then-dwell

**Files:** Modify `scripts/calibration/amazing_hand/finger_test.py`.

- [ ] **Step 1: Update imports.** Change:

```python
from arm101_hand.hand import compose_finger, degrees_to_servo_radians
```

to:

```python
import math

from arm101_hand.hand import compose_finger, degrees_to_servo_radians, wait_hand_reached
```

(Place `import math` with the other stdlib imports at the top; the `wait_hand_reached` goes on the existing `from arm101_hand.hand import` line.)

- [ ] **Step 2: Make `_send_pose` return its goal dict and drop its trailing sleep.** Replace:

```python
def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.01)
```

with:

```python
def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    """Write the finger's two goals; return {id: goal_radians} for arrival polling."""
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    rad1 = degrees_to_servo_radians(id1, pos1, mp1)
    rad2 = degrees_to_servo_radians(id2, pos2, mp2)
    c.write_goal_position(id1, rad1)
    c.write_goal_position(id2, rad2)
    return {id1: rad1, id2: rad2}
```

- [ ] **Step 3: Poll arrival before each observation dwell.** In `main`, add the poll params after `speed = hcfg.tuning.speed`:

```python
    tol_rad = math.radians(hcfg.tuning.pose_margin_deg)
    timeout_s = hcfg.tuning.pose_timeout_s
    poll_s = hcfg.tuning.pose_poll_s
```

and change the loop body:

```python
            for label, base, side, dwell in sequence:
                print(f"  -> {label}  (base={base}, side={side})")
                _send_pose(c, id1, id2, mp1, mp2, base, side, speed)
                time.sleep(dwell)
```

to:

```python
            for label, base, side, dwell in sequence:
                print(f"  -> {label}  (base={base}, side={side})")
                targets = _send_pose(c, id1, id2, mp1, mp2, base, side, speed)
                wait_hand_reached(c, targets, tolerance_rad=tol_rad, timeout_s=timeout_s, poll_s=poll_s)
                time.sleep(dwell)
```

- [ ] **Step 4: Verify (host)**

Run: `uv run ruff format scripts/calibration/amazing_hand/finger_test.py; uv run ruff check . ; uv run python -c "import ast; ast.parse(open(r'scripts/calibration/amazing_hand/finger_test.py', encoding='utf-8').read()); print('parse-ok')"`
Expected: ruff clean, `parse-ok`.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/amazing_hand/finger_test.py
git commit -m "refactor(hand): finger_test.py confirms arrival before each observation dwell"
```

---

### Task 9: Retrofit `full_hand_test.py` to poll-then-dwell

**Files:** Rewrite `scripts/calibration/amazing_hand/full_hand_test.py` with exactly (keep the module docstring; the changes are: `_move`/`Move_*`/`Open/Close/InitialPose` return their `{id: rad}` dicts, a `_wait` helper, and `wait_hand_reached` before each observation dwell):

```python
"""
AmazingHand Full-Hand Test Script

This script runs a complete sequence of movements for the 4-finger AmazingHand
(right hand configuration). It cycles through:
1. Closing all fingers into a fist
2. Opening all fingers
3. Isolating and moving the Index finger
4. Isolating and moving the Middle finger
5. Isolating and moving the Ring finger
6. Isolating and moving the Thumb
7. Returning to the neutral (initial) pose

It reads calibration values from 'hand_calib_values.yaml' and is intended
to be the final end-to-end sanity check that proves the calibration is correct.
"""

import contextlib
import math
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_hand_calibration, load_hand_config
from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import compose_finger, degrees_to_servo_radians, wait_hand_reached
from arm101_hand.hand.protocol import SERVO_SYNC_S

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "hand_calib_values.yaml"
HAND_CONFIG_PATH = SCRIPT_DIR.parents[2] / "src" / "arm101_hand" / "data" / "hand_config.yaml"

_cfg = load_hand_calibration(YAML_PATH)
_hcfg = load_hand_config(HAND_CONFIG_PATH)
_FINGERS = _cfg.fingers

# Demo-specific speeds from hand_config.yaml tuning.speeds.
MaxSpeed = _hcfg.tuning.speeds.open  # extension (quicker)
CloseSpeed = _hcfg.tuning.speeds.close  # flexion (gentler settle)

# Position-poll params (confirm arrival before each observation dwell).
_TOL_RAD = math.radians(_hcfg.tuning.pose_margin_deg)
_TIMEOUT_S = _hcfg.tuning.pose_timeout_s
_POLL_S = _hcfg.tuning.pose_poll_s

c = Scs0009PyController(
    serial_port=_hcfg.connection.port,
    baudrate=_hcfg.connection.baudrate,
    timeout=_hcfg.connection.timeout,
)


def _wait(targets):
    """Poll until the just-commanded servos arrive (or timeout); caller keeps its dwell."""
    wait_hand_reached(c, targets, tolerance_rad=_TOL_RAD, timeout_s=_TIMEOUT_S, poll_s=_POLL_S)


# ---------- per-finger motion helpers ----------


def _move(name, base, side, speed):
    block = _FINGERS[name]
    id1, id2 = FINGER_SERVO_IDS[name]
    mp1 = block.servo_1.middle_pos
    mp2 = block.servo_2.middle_pos
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(SERVO_SYNC_S)
    c.write_goal_speed(id2, speed)
    time.sleep(SERVO_SYNC_S)
    rad1 = degrees_to_servo_radians(id1, pos1, mp1)
    rad2 = degrees_to_servo_radians(id2, pos2, mp2)
    c.write_goal_position(id1, rad1)
    c.write_goal_position(id2, rad2)
    return {id1: rad1, id2: rad2}


def _limits(name):
    return _FINGERS[name].limits


def Move_Index(base, side, speed):  # noqa: N802
    return _move("index", base, side, speed)


def Move_Middle(base, side, speed):  # noqa: N802
    return _move("middle", base, side, speed)


def Move_Ring(base, side, speed):  # noqa: N802
    return _move("ring", base, side, speed)


def Move_Thumb(base, side, speed):  # noqa: N802
    return _move("thumb", base, side, speed)


_MOVERS = {
    "index": Move_Index,
    "middle": Move_Middle,
    "ring": Move_Ring,
    "thumb": Move_Thumb,
}


# ---------- poses (right hand) ----------


def _close(name):
    return _limits(name).base_max, 0


def _open(name):
    return _limits(name).base_min, 0


def _spread_pose(name, frac):
    # An isolation pose: nearly open, spread by ``frac`` of the side range.
    lim = _limits(name)
    side = int((lim.side_max if frac > 0 else lim.side_min) * abs(frac))
    return lim.base_min, side


def InitialPose():  # noqa: N802
    targets = {}
    for name, mover in _MOVERS.items():
        targets.update(mover(*_open(name), MaxSpeed))
    return targets


def OpenHand():  # noqa: N802
    targets = {}
    for name, mover in _MOVERS.items():
        targets.update(mover(*_open(name), MaxSpeed))
    return targets


def CloseHand():  # noqa: N802
    targets = {}
    for name, mover in _MOVERS.items():
        targets.update(mover(*_close(name), CloseSpeed))
    return targets


def _isolate(active):
    # Close every finger except ``active``, which sweeps its spread range.
    targets = {}
    for name, mover in _MOVERS.items():
        if name != active:
            targets.update(mover(*_close(name), MaxSpeed))
    targets.update(_MOVERS[active](*_open(active), MaxSpeed))
    _wait(targets)
    time.sleep(0.6)
    _wait(_MOVERS[active](*_spread_pose(active, 1.0), MaxSpeed))
    time.sleep(0.4)
    _wait(_MOVERS[active](*_spread_pose(active, -1.0), MaxSpeed))
    time.sleep(0.4)
    _wait(_MOVERS[active](*_open(active), MaxSpeed))


def IndexOnly():  # noqa: N802
    _isolate("index")


def MiddleOnly():  # noqa: N802
    _isolate("middle")


def RingOnly():  # noqa: N802
    _isolate("ring")


def ThumbOnly():  # noqa: N802
    _isolate("thumb")


def main():
    for finger_name in _FINGERS:
        id1, id2 = FINGER_SERVO_IDS[finger_name]
        c.write_torque_enable(id1, 1)
        c.write_torque_enable(id2, 1)

    print("Running AmazingHand full-hand demo (right hand, one cycle)...")
    try:
        _wait(CloseHand())
        time.sleep(2)

        _wait(OpenHand())
        time.sleep(1)

        IndexOnly()
        time.sleep(1.5)

        MiddleOnly()
        time.sleep(1.5)

        RingOnly()
        time.sleep(1.5)

        ThumbOnly()
        time.sleep(1.5)

        _wait(InitialPose())
        time.sleep(1)
        print("Cycle complete -- holding middle (initial) pose under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- aborting")
    finally:
        for finger_name in _FINGERS:
            id1, id2 = FINGER_SERVO_IDS[finger_name]
            for sid in (id1, id2):
                with contextlib.suppress(Exception):
                    c.write_torque_enable(sid, 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 1b: Verify (host)**

Run: `uv run ruff format scripts/calibration/amazing_hand/full_hand_test.py; uv run ruff check . ; uv run python -c "import ast; ast.parse(open(r'scripts/calibration/amazing_hand/full_hand_test.py', encoding='utf-8').read()); print('parse-ok')"; uv run pytest -m 'not hardware'`
Expected: ruff clean, `parse-ok`, all tests pass.

- [ ] **Step 2: Commit**

```bash
git add scripts/calibration/amazing_hand/full_hand_test.py
git commit -m "refactor(hand): full_hand_test.py confirms arrival before each observation dwell"
```

---

## Consolidated hardware verification (operator-only, after Tasks 4-9)

> Both buses powered (arm 12 V, hand 5 V), ports free (IL-4). The hand scripts and grab_sequence cannot be unit-tested for motion ordering — these manual checks are the only guard.

- [ ] **grab_sequence forward:** `[1] -> [2] (posture + hand pre_grab together) -> [3] -> [4]`; in step 2 the arm posture and the hand visibly move together, and step 3 does not start until BOTH have settled.
- [ ] **No `(hand not fully reached within timeout -- continuing)` during pre_grab / open** (free-space moves should reach). It IS expected during `[4]` if a real object is gripped.
- [ ] **No `(target not fully reached within timeout -- continuing)`** (arm) during any stage.
- [ ] **grab_sequence exit 'h':** hand -> pre_grab, then pan -> base_max, posture -> home, pan -> home, then hand -> open; both torques release at the end.
- [ ] **grab_sequence exit Enter / Ctrl+C:** both release in place, no motion.
- [ ] **set_pose.py `<pose>`:** drives to the pose and holds; for a free-space pose it confirms arrival quietly; `open`/`close` reach; a stalled pose times out then holds.
- [ ] **finger_test.py:** each endpoint is reached (no buzz) and then dwelt-on for the full observation pause.
- [ ] **full_hand_test.py:** the cycle runs; each pose confirms arrival before its dwell; releases on Enter.

## Extension self-review

- **Spec coverage:** step-2 hand pre_grab + "wait for both" (Task 6 `write_hand_servos` → `drive_arm_joints` → `wait_hand_reached`); grab-close timeout-then-continue (`drive_hand_servos` returns False on stall, never hangs); reverse hand→pre_grab first + hand→open last (Task 6 `_staged_reverse`); hand polling everywhere (Task 5 helper, retrofits Tasks 7-9); config knobs (Task 4). ✓
- **Placeholder scan:** all code blocks complete; README edit gives the exact text to add. ✓
- **Type/name consistency:** `write_hand_servos`/`wait_hand_reached`/`drive_hand_servos` signatures identical across motion.py (def), grab_sequence/set_pose/finger_test/full_hand_test (calls), and tests. `pose_timeout_s`/`pose_poll_s` identical in `HandTuning`, yaml, README, and every reader. `hand_wait_kw` keys (`tolerance_rad`/`timeout_s`/`poll_s`) match `wait_hand_reached`'s keyword-only params. ✓
