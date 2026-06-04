# SO-ARM101 Keyboard Jog Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive `msvcrt` keyboard-jog script for the SO-ARM101 follower that drives each motor in degrees (clamped to its calibrated range), supports hand-posing via a torque toggle, per-joint home, and saving the current pose to a separate file that `set_pose.py` can drive back to.

**Architecture:** A pure, unit-tested jog state machine in `src/arm101_hand/robots/arm_jog.py`; a thin `msvcrt` I/O shell `scripts/calibration/so_arm101/jog.py`; a `save_arm_poses` helper in `config/arm_poses.py` writing `data/arm_jog_poses.yaml`; and a small `set_pose.py` change to resolve poses from both `arm_config.yaml` and the jog-poses file. Mirrors the AmazingHand `RangeCalib` pure-logic/IO split.

**Tech Stack:** Python 3.12, lerobot (`SO101FollowerNoGripper`, DEGREES norm mode), pydantic, `msvcrt`, pytest, ruff, mypy. `uv run` for everything.

**Spec:** `docs/superpowers/specs/2026-06-03-so-arm101-keyboard-jog-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `src/arm101_hand/robots/arm_jog.py` | **Create.** Pure jog state machine: `ArmJogState`, `initial_state`, `key_to_action`, `apply_action`, `format_status`. No bus. |
| `tests/unit/test_arm_jog.py` | **Create.** State-machine unit tests. |
| `src/arm101_hand/config/arm_poses.py` | **Modify.** Add `save_arm_poses(path, config)`. |
| `src/arm101_hand/config/__init__.py` | **Modify.** Export `save_arm_poses`. |
| `tests/unit/test_arm_poses_save.py` | **Create.** `save_arm_poses` round-trip tests. |
| `scripts/calibration/so_arm101/_common.py` | **Modify.** Add `ARM_JOG_POSES_PATH` constant. |
| `scripts/calibration/so_arm101/jog.py` | **Create.** `msvcrt` I/O loop + bus. |
| `scripts/calibration/so_arm101/set_pose.py` | **Modify.** Resolve poses from `quick_poses` + jog-poses file. |
| `scripts/calibration/so_arm101/README.md` | **Modify.** §6 jog row + control table. |
| `CLAUDE.md`, `README.md` | **Modify** (via `doc_update`). |
| `data/arm_jog_poses.yaml` | Created at runtime by the save feature — not committed by the plan. |

**Verified APIs (from `references/lerobot` + this repo):**
- `ArmPose(shoulder_pan=…, shoulder_lift=…, elbow_flex=…, wrist_flex=…, wrist_roll=…)` (all float, `extra="forbid"`), `.as_dict()`; `ArmPoseConfig(schema_version:int=1, quick_poses:dict[str,ArmPose], poses:dict[str,ArmPose])`; `load_arm_poses(path) -> ArmPoseConfig`.
- `follower.get_observation() -> {f"{joint}.pos": degrees}` (DEGREES mode → relative to calibrated mid, so 0.0 = mid).
- `follower.send_action({f"{joint}.pos": float})`; `follower.bus.sync_read("Present_Load") -> {joint: int}`; `follower.bus.sync_write("Goal_Velocity", {joint:int})`; `enable_torque()`/`disable_torque()`; `follower.is_connected`; `follower.connect(calibrate=False)`/`disconnect()`.
- `calibration_summary.degree_bounds(range_min, range_max) -> (lo, hi)`; `load_arm_calibration(path)`.
- `_common`: `build_follower(cfg, *, use_degrees)`, `gentle_velocity(cfg)`, `load_arm_app_config()`, `CALIB_PATH`, `park_home_and_release(follower, vel)`.
- `msvcrt.getwch` arrow handling: prefix `"\x00"`/`"\xe0"` then `H/P/K/M` → UP/DOWN/LEFT/RIGHT (see `AmazingHand_RangeCalib.read_key`).

---

## Task 1: Pure jog state machine (TDD)

**Files:**
- Create: `src/arm101_hand/robots/arm_jog.py`
- Test: `tests/unit/test_arm_jog.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_arm_jog.py`:

```python
"""Unit tests for the pure SO-ARM101 jog state machine (no bus)."""

from __future__ import annotations

import pytest

from arm101_hand.robots.arm_jog import (
    ARM_JOINTS,
    JOG_STEP_MAX,
    JOG_STEP_MIN,
    apply_action,
    format_status,
    initial_state,
    key_to_action,
)

_BOUNDS = {j: (-100.0, 100.0) for j in ARM_JOINTS}


def _state(**kw):
    base = initial_state({j: 0.0 for j in ARM_JOINTS})
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def test_initial_state_active_is_first_joint():
    s = initial_state({j: 0.0 for j in ARM_JOINTS})
    assert s.active == ARM_JOINTS[0]
    assert s.torque_on is True
    assert s.step == pytest.approx(5.0)


def test_key_to_action_digits_select_joints():
    assert key_to_action("1") == f"select:{ARM_JOINTS[0]}"
    assert key_to_action("5") == f"select:{ARM_JOINTS[4]}"


def test_key_to_action_arrows_and_letters():
    assert key_to_action("UP") == "jog_up"
    assert key_to_action("DOWN") == "jog_down"
    assert key_to_action("[") == "step_down"
    assert key_to_action("]") == "step_up"
    assert key_to_action("h") == "home_active"
    assert key_to_action("t") == "toggle_torque"
    assert key_to_action("s") == "save"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_select_changes_active():
    s2, eff = apply_action(_state(), f"select:{ARM_JOINTS[2]}", _BOUNDS)
    assert s2.active == ARM_JOINTS[2]
    assert eff is None


def test_jog_up_moves_active_by_step():
    s = _state()
    s2, eff = apply_action(s, "jog_up", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(5.0)
    assert eff == "move"


def test_jog_down_moves_active_negative():
    s = _state()
    s2, _ = apply_action(s, "jog_down", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(-5.0)


def test_jog_clamps_to_bounds():
    s = _state(step=15.0)
    bounds = {j: (-10.0, 10.0) for j in ARM_JOINTS}
    s2, _ = apply_action(s, "jog_up", bounds)
    assert s2.cursors[s.active] == pytest.approx(10.0)  # clamped, not 15


def test_step_up_and_down_clamp():
    s2, eff = apply_action(_state(step=JOG_STEP_MAX), "step_up", _BOUNDS)
    assert s2.step == pytest.approx(JOG_STEP_MAX)
    assert eff is None
    s3, _ = apply_action(_state(step=JOG_STEP_MIN), "step_down", _BOUNDS)
    assert s3.step == pytest.approx(JOG_STEP_MIN)


def test_home_active_sets_cursor_zero():
    s = _state()
    s.cursors[s.active] = 42.0
    s2, eff = apply_action(s, "home_active", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(0.0)
    assert eff == "move"


def test_toggle_torque_flips_and_signals():
    s2, eff = apply_action(_state(), "toggle_torque", _BOUNDS)
    assert s2.torque_on is False
    assert eff == "toggle_torque"


def test_jog_blocked_when_torque_off():
    s = _state(torque_on=False)
    s.cursors[s.active] = 3.0
    s2, eff = apply_action(s, "jog_up", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(3.0)  # unchanged
    assert eff is None


def test_save_and_quit_effects():
    _, eff_save = apply_action(_state(), "save", _BOUNDS)
    assert eff_save == "save"
    _, eff_quit = apply_action(_state(), "quit", _BOUNDS)
    assert eff_quit == "quit"


def test_format_status_marks_active_and_shows_torque():
    s = _state()
    line = format_status(s, {j: 0 for j in ARM_JOINTS})
    assert "torque ON" in line
    assert f"*{s.active}" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_arm_jog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'arm101_hand.robots.arm_jog'`.

- [ ] **Step 3: Write the module**

Create `src/arm101_hand/robots/arm_jog.py`:

```python
"""Pure (no-bus) state machine for the SO-ARM101 keyboard jog tool (scripts/.../jog.py).

All jog decision logic lives here so it is unit-testable without hardware. The I/O shell
(jog.py) only does msvcrt key reads, bus calls, and printing.

Cursors are per-joint targets in DEGREES relative to each joint's calibrated midpoint
(0 = mid), matching DEGREES norm mode. Jogging is clamped to per-joint bounds the caller
supplies (from calibration_summary.degree_bounds).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.robots.calibration_summary import ARM_JOINTS

JOG_STEP_MIN = 1.0
JOG_STEP_MAX = 15.0
JOG_STEP_DEFAULT = 5.0
_STEP_INCREMENT = 1.0

_DIGIT_TO_JOINT = {str(i + 1): name for i, name in enumerate(ARM_JOINTS)}


@dataclass
class ArmJogState:
    """Mutable jog cursor state. cursors: per-joint target in degrees from calibrated mid."""

    cursors: dict[str, float]
    active: str
    step: float = JOG_STEP_DEFAULT
    torque_on: bool = True


def initial_state(cursors: dict[str, float]) -> ArmJogState:
    """Starting state from initial per-joint degree positions; active = first joint."""
    return ArmJogState(
        cursors=dict(cursors), active=ARM_JOINTS[0], step=JOG_STEP_DEFAULT, torque_on=True
    )


def key_to_action(key: str) -> str | None:
    """Map a raw key token (arrows already normalized to UP/DOWN/LEFT/RIGHT) to an action."""
    if key in _DIGIT_TO_JOINT:
        return f"select:{_DIGIT_TO_JOINT[key]}"
    return {
        "UP": "jog_up",
        "DOWN": "jog_down",
        "[": "step_down",
        "]": "step_up",
        "h": "home_active",
        "H": "home_active",
        "t": "toggle_torque",
        "T": "toggle_torque",
        "s": "save",
        "S": "save",
        "q": "quit",
        "Q": "quit",
    }.get(key)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def apply_action(
    state: ArmJogState, action: str | None, bounds: dict[str, tuple[float, float]]
) -> tuple[ArmJogState, str | None]:
    """Apply an action, returning (new_state, effect).

    effect: "move" | "toggle_torque" | "save" | "quit" | None.
    jog_up/jog_down/home_active are no-ops (effect None) while torque is off.
    """
    if action is None:
        return state, None

    if action.startswith("select:"):
        joint = action.split(":", 1)[1]
        return (replace(state, active=joint) if joint in state.cursors else state), None

    if action == "step_down":
        return replace(state, step=_clamp(state.step - _STEP_INCREMENT, JOG_STEP_MIN, JOG_STEP_MAX)), None
    if action == "step_up":
        return replace(state, step=_clamp(state.step + _STEP_INCREMENT, JOG_STEP_MIN, JOG_STEP_MAX)), None

    if action == "toggle_torque":
        return replace(state, torque_on=not state.torque_on), "toggle_torque"
    if action == "save":
        return state, "save"
    if action == "quit":
        return state, "quit"

    if action in ("jog_up", "jog_down", "home_active"):
        if not state.torque_on:
            return state, None  # I/O layer prints a hint
        lo, hi = bounds[state.active]
        if action == "home_active":
            target = 0.0
        else:
            target = state.cursors[state.active] + (state.step if action == "jog_up" else -state.step)
        new_cursors = dict(state.cursors)
        new_cursors[state.active] = _clamp(target, lo, hi)
        return replace(state, cursors=new_cursors), "move"

    return state, None


def format_status(state: ArmJogState, loads: dict[str, int]) -> str:
    """One-line status: torque/step header + per-joint cursor and load; active marked '*'."""
    tq = "ON" if state.torque_on else "OFF"
    parts = [
        f"{'*' if j == state.active else ' '}{j}={state.cursors[j]:+6.1f}deg(L{loads.get(j, 0)})"
        for j in ARM_JOINTS
    ]
    return f"[torque {tq} step {state.step:.0f}] " + " ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_arm_jog.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Lint + type-check**

Run each (PowerShell — separately):
`uv run ruff format src/arm101_hand/robots/arm_jog.py tests/unit/test_arm_jog.py`
`uv run ruff check src/arm101_hand/robots/arm_jog.py tests/unit/test_arm_jog.py`
`uv run mypy src/arm101_hand/robots/arm_jog.py`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/robots/arm_jog.py tests/unit/test_arm_jog.py
git commit -m "feat(arm-jog): pure jog state machine for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `save_arm_poses` helper (TDD)

**Files:**
- Modify: `src/arm101_hand/config/arm_poses.py`
- Modify: `src/arm101_hand/config/__init__.py`
- Test: `tests/unit/test_arm_poses_save.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_arm_poses_save.py`:

```python
"""Round-trip tests for save_arm_poses (no bus)."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses

_POSE = {
    "shoulder_pan": 1.0,
    "shoulder_lift": -2.0,
    "elbow_flex": 3.0,
    "wrist_flex": 4.0,
    "wrist_roll": 5.0,
}


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "arm_jog_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    loaded = load_arm_poses(path)
    assert loaded.poses["a"].as_dict() == _POSE


def test_save_upserts_into_existing(tmp_path: Path) -> None:
    path = tmp_path / "arm_jog_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    cfg = load_arm_poses(path)
    cfg.poses["b"] = ArmPose(**{**_POSE, "wrist_roll": 9.0})
    save_arm_poses(path, cfg)
    loaded = load_arm_poses(path)
    assert set(loaded.poses) == {"a", "b"}
    assert loaded.poses["b"].as_dict()["wrist_roll"] == 9.0


def test_save_leaves_no_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "arm_jog_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    assert path.is_file()
    assert not (tmp_path / "arm_jog_poses.yaml.tmp").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_arm_poses_save.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_arm_poses'`.

- [ ] **Step 3: Add `save_arm_poses` to `arm_poses.py`**

In `src/arm101_hand/config/arm_poses.py`, add `import os` to the imports (top, after `from pathlib import Path`) and append this function at the end of the file:

```python
def save_arm_poses(path: Path, config: ArmPoseConfig) -> None:
    """Write an ``ArmPoseConfig`` to YAML atomically (tmp file + ``os.replace``).

    Used for the jog-pose file (``data/arm_jog_poses.yaml``) -- a fresh, code-owned file,
    so no comment preservation is needed. ``sort_keys=False`` keeps a stable field order.
    """
    payload = config.model_dump(mode="python")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Export it from the config package**

In `src/arm101_hand/config/__init__.py`, update the `arm_poses` import line and the `__all__` list to include `save_arm_poses`:

Change:
```python
from .arm_poses import ARM_MOTORS, ArmPose, ArmPoseConfig, load_arm_poses
```
to:
```python
from .arm_poses import ARM_MOTORS, ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
```
And add `"save_arm_poses",` to the `__all__` list (anywhere in the list).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_arm_poses_save.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + type-check**

`uv run ruff format src/arm101_hand/config/arm_poses.py src/arm101_hand/config/__init__.py tests/unit/test_arm_poses_save.py`
`uv run ruff check src/arm101_hand/config/arm_poses.py src/arm101_hand/config/__init__.py tests/unit/test_arm_poses_save.py`
`uv run mypy src/arm101_hand/config/arm_poses.py`
Expected: ruff clean; mypy reports only the pre-existing `yaml` `import-untyped` note (baseline, CLAUDE.md §7) — no new errors.

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/arm_poses.py src/arm101_hand/config/__init__.py tests/unit/test_arm_poses_save.py
git commit -m "feat(config): add save_arm_poses (atomic YAML write) for jog poses

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `ARM_JOG_POSES_PATH` constant

**Files:**
- Modify: `scripts/calibration/so_arm101/_common.py`

- [ ] **Step 1: Add the constant**

In `scripts/calibration/so_arm101/_common.py`, directly after the `ARM_CONFIG_PATH = …` line, add:

```python
ARM_JOG_POSES_PATH = _REPO_ROOT / "data" / "arm_jog_poses.yaml"
```

- [ ] **Step 2: Verify lint + import**

`uv run ruff check scripts/calibration/so_arm101/_common.py`
`uv run python -c "import sys; sys.path.insert(0,'scripts/calibration/so_arm101'); import _common; print(_common.ARM_JOG_POSES_PATH.name)"`
Expected: ruff clean; prints `arm_jog_poses.yaml`.

- [ ] **Step 3: Commit**

```bash
git add scripts/calibration/so_arm101/_common.py
git commit -m "feat(arm-jog): add ARM_JOG_POSES_PATH to _common

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `jog.py` I/O script

**Files:**
- Create: `scripts/calibration/so_arm101/jog.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/jog.py`:

```python
"""Interactive keyboard jog for the SO-ARM101 follower (manual per-motor positioning).

Select a joint (1-5) and nudge it in degrees (clamped to its calibrated range). Toggle
torque to hand-pose the arm, home a single joint, or save the current pose to
data/arm_jog_poses.yaml (drivable later by set_pose.py).

Controls (torque ON to move):
  1..5          select joint (shoulder_pan shoulder_lift elbow_flex wrist_flex wrist_roll)
  Up / Down     jog active joint + / - step (deg), clamped to calibrated range
  [ / ]         shrink / grow jog step (1..15 deg)
  h             home active joint to 0 (calibrated mid)
  t             toggle torque (off = hand-pose by hand; on = resync + hold)
  s             save current pose to data/arm_jog_poses.yaml (prompts for a name)
  q / Ctrl+C    return all joints home, release torque, exit
                (if torque is OFF, just disconnects in place)

Windows-only: uses msvcrt.getwch for raw key reads. Pure jog logic is in
arm101_hand.robots.arm_jog (unit-tested); this file only does I/O.

IL-3: motors 1-5. IL-4: single bus owner; torque released on exit. IL-5: never writes
so101_follower.json (reads it for the clamp range).
"""

from __future__ import annotations

import msvcrt
import sys

from _common import (
    ARM_JOG_POSES_PATH,
    CALIB_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    park_home_and_release,
)

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from arm101_hand.robots.arm_jog import (
    ARM_JOINTS,
    apply_action,
    format_status,
    initial_state,
    key_to_action,
)
from arm101_hand.robots.calibration_summary import degree_bounds, load_arm_calibration

_LOAD_WARN = 600


def read_key() -> str:
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _hold_at(follower, cursors: dict[str, float], vel: int) -> None:
    """Set goal=cursors at gentle velocity, then enable torque (no-jump hold)."""
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
    follower.send_action({f"{j}.pos": cursors[j] for j in ARM_JOINTS})
    follower.bus.enable_torque()


def _save_pose(follower) -> None:
    """Prompt for a name and save current LIVE present pose to arm_jog_poses.yaml."""
    try:
        name = input("\n  save pose as (name, blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        print("  save cancelled")
        return
    config = load_arm_poses(ARM_JOG_POSES_PATH) if ARM_JOG_POSES_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**_present_degrees(follower))
    save_arm_poses(ARM_JOG_POSES_PATH, config)
    print(f"  saved '{name}' -> {ARM_JOG_POSES_PATH}")


def main() -> int:
    cfg = load_arm_app_config()
    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)
    bounds = {j: degree_bounds(calib[j].range_min, calib[j].range_max) for j in ARM_JOINTS}

    follower = build_follower(cfg, use_degrees=True)
    vel = gentle_velocity(cfg)

    print(__doc__)
    print(f"Connecting on {cfg.arm.port} ...")
    state = None
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        cursors = _present_degrees(follower)
        _hold_at(follower, cursors, vel)
        state = initial_state(cursors)
        print(format_status(state, follower.bus.sync_read("Present_Load")))

        while True:
            action = key_to_action(read_key())
            state, effect = apply_action(state, action, bounds)
            if effect == "quit":
                break
            if effect == "toggle_torque":
                if state.torque_on:
                    state.cursors = _present_degrees(follower)
                    _hold_at(follower, state.cursors, vel)
                    print("  torque ON (holding current pose)")
                else:
                    follower.bus.disable_torque()
                    print("  torque OFF -- hand-pose the arm; press t to re-hold")
            elif effect == "save":
                _save_pose(follower)
            elif effect == "move":
                follower.send_action({f"{state.active}.pos": state.cursors[state.active]})
            elif action in ("jog_up", "jog_down", "home_active") and not state.torque_on:
                print("  (torque OFF -- press t to enable before jogging)")

            loads = follower.bus.sync_read("Present_Load") if state.torque_on else {}
            print(format_status(state, loads))
            high = [j for j, v in loads.items() if abs(v) >= _LOAD_WARN]
            if high:
                print(f"  WARNING: high load on {high}")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        # Return home then release torque, unless torque was toggled off (hand-pose mode),
        # in which case leave the arm where it is and just disconnect.
        if follower.is_connected:
            if state is not None and state.torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, vel)
                print("Home reached; torque off.")
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Lint + compile**

`uv run ruff format scripts/calibration/so_arm101/jog.py`
`uv run ruff check scripts/calibration/so_arm101/jog.py` (clean; preserve the `_common` group / blank line / `arm101_hand` group if it reorders)
`uv run python -m py_compile scripts/calibration/so_arm101/jog.py` (no output)
Expected: all clean.

- [ ] **Step 3: Static checks only — do NOT run `jog.py` in automated verification**

> The dev arm is live on COM20. Running `jog.py` will connect and enter the interactive
> `msvcrt` key loop (which blocks and needs a real console) — it will NOT cleanly exit. So
> verify statically only:

`uv run python -m py_compile scripts/calibration/so_arm101/jog.py` (no output)
And confirm the sibling import resolves without opening the bus:
`uv run python -c "import sys; sys.path.insert(0,'scripts/calibration/so_arm101'); import jog; print(hasattr(jog,'main'), hasattr(jog,'read_key'))"`
Expected: `py_compile` clean; prints `True True` (importing the module does not call `main()`, so no bus access).

- [ ] **Step 4: Manual hardware check (operator, at the bench)**

With the arm powered: jog each joint with `1`–`5` + `↑`/`↓`; confirm it clamps at the calibrated ends; `t` releases torque (limp) then re-holds; `h` homes the active joint; `s` saves a pose (creates `data/arm_jog_poses.yaml`); `q` returns home and releases.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/jog.py
git commit -m "feat(arm-jog): jog.py keyboard per-motor positioning for SO-ARM101

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `set_pose.py` reads jog poses too

**Files:**
- Modify: `scripts/calibration/so_arm101/set_pose.py`

- [ ] **Step 1: Update the import line**

In `scripts/calibration/so_arm101/set_pose.py`, the `_common` import currently is a multi-line block ending with `load_arm_app_config,` and `park_home_and_release,`. Add `ARM_JOG_POSES_PATH,` to that import block (order doesn't matter — `ruff format` will sort it). The block becomes:

```python
from _common import (
    ARM_CONFIG_PATH,
    ARM_JOG_POSES_PATH,
    CALIB_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    park_home_and_release,
)
```

- [ ] **Step 2: Merge quick_poses + jog poses in `main`**

In `set_pose.py`, find the start of `main()`:

```python
    poses = load_arm_poses(ARM_CONFIG_PATH).quick_poses
    if not poses:
        print(f"no quick_poses defined in {ARM_CONFIG_PATH}", file=sys.stderr)
        return 1
```

Replace it with (merge the canned quick_poses with any jog-saved poses; jog poses are
added on top so a saved name is drivable):

```python
    quick = load_arm_poses(ARM_CONFIG_PATH).quick_poses
    jog = load_arm_poses(ARM_JOG_POSES_PATH).poses if ARM_JOG_POSES_PATH.is_file() else {}
    poses = {**quick, **jog}
    if not poses:
        print(
            f"no poses defined in {ARM_CONFIG_PATH} (quick_poses) or {ARM_JOG_POSES_PATH} (poses)",
            file=sys.stderr,
        )
        return 1
```

(`pose = poses[name].as_dict()` and everything after is unchanged — `poses` is still a
`dict[str, ArmPose]`.)

- [ ] **Step 3: Static checks only — do NOT run `set_pose.py` in automated verification**

> The dev arm is live on COM20: running `set_pose.py rest` would connect, enable torque,
> and physically drive the arm, then block on the "Press Enter" hold. Verify statically:

`uv run ruff format scripts/calibration/so_arm101/set_pose.py`
`uv run ruff check scripts/calibration/so_arm101/set_pose.py` (clean)
`uv run python -m py_compile scripts/calibration/so_arm101/set_pose.py` (no output)
(The merge is a plain `{**quick, **jog}` dict; its inputs are covered by the `save_arm_poses`
round-trip test and the `load_arm_poses` schema. Live drive is the operator's bench step.)

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/so_arm101/set_pose.py
git commit -m "feat(arm-jog): set_pose resolves poses from arm_config + arm_jog_poses

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Documentation

**Files:**
- Modify: `scripts/calibration/so_arm101/README.md`
- Modify (via skill): `CLAUDE.md`, `README.md`

- [ ] **Step 1: Add `jog.py` to README §6**

In `scripts/calibration/so_arm101/README.md` §6, add a row to the script table (after the
`set_pose.py` row):

```markdown
| `jog.py` | yes | on→off | DEGREES | Keyboard-jog each motor in degrees (clamped to range); `t` hand-pose toggle; `h` home a joint; `s` save current pose to `data/arm_jog_poses.yaml`. Returns home before releasing torque. |
```

Add a run example to the code block:

```powershell
uv run python scripts/calibration/so_arm101/jog.py                  # interactive keyboard jog
```

And append this subsection after the "Safe release" note:

````markdown
**Jog controls (`jog.py`).** Torque ON to move; `msvcrt` raw keys:

| Key | Action |
| --- | --- |
| `1`–`5` | select joint (shoulder_pan … wrist_roll) |
| `↑` / `↓` | jog active joint ± step (deg), clamped to calibrated range |
| `[` / `]` | shrink / grow step (1–15°) |
| `h` | home active joint to 0 |
| `t` | toggle torque (off = hand-pose by hand; on = resync + hold) |
| `s` | save current pose to `data/arm_jog_poses.yaml` (prompts for a name) |
| `q` / `Ctrl+C` | return home, release torque, exit (if torque off: disconnect in place) |

Saved poses land in `data/arm_jog_poses.yaml` (a separate file from `arm_config.yaml`) and
are drivable by name with `set_pose.py`, which resolves from both files.
````

- [ ] **Step 2: Verify the README contains the new content**

Run: `uv run python -c "t=open('scripts/calibration/so_arm101/README.md', encoding='utf-8').read(); print('jog.py' in t, 'arm_jog_poses.yaml' in t)"`
Expected: `True True`.

- [ ] **Step 3: Commit the README**

```bash
git add scripts/calibration/so_arm101/README.md
git commit -m "docs(arm-jog): document jog.py controls + arm_jog_poses.yaml

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Refresh top-level docs**

Invoke the `doc_update` skill to add `jog.py` to the "Common workflows" block in `CLAUDE.md`
and the SO-ARM101 section of the root `README.md` (point to `so_arm101/README.md` §6 for
detail; do not duplicate the control table). The skill owns those edits.

- [ ] **Step 5: Full verification gate**

Run (separately):
`uv run ruff check src/arm101_hand/robots/arm_jog.py src/arm101_hand/config/arm_poses.py src/arm101_hand/config/__init__.py scripts/calibration/so_arm101/jog.py scripts/calibration/so_arm101/set_pose.py scripts/calibration/so_arm101/_common.py tests/unit/test_arm_jog.py tests/unit/test_arm_poses_save.py`
`uv run mypy src`
`uv run pytest -m 'not hardware' -q`
Expected: ruff clean; mypy only the pre-existing `yaml` baseline notes; pytest all pass (the new 13 + 3 tests added).

---

## Final verification checklist

- [ ] `test_arm_jog.py` (13) and `test_arm_poses_save.py` (3) pass.
- [ ] `jog.py`, `set_pose.py` lint + compile clean; no-hardware runs fail cleanly (exit 1, no traceback).
- [ ] `save_arm_poses` round-trips through `arm_jog_poses.yaml`; `arm_config.yaml` is never written by these scripts.
- [ ] `so101_follower.json` unchanged (`git diff main..HEAD -- scripts/calibration/so_arm101/so101_follower.json` empty).
- [ ] `ruff`, `mypy src`, `pytest -m 'not hardware'` green.
- [ ] README §6 + CLAUDE.md/README updated.
- [ ] Iron-law audit (IL-3/4/5) clean — consider the `iron-law-audit` skill before the PR.
```
