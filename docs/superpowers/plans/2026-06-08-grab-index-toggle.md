# grab_toggle Index-Finger Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `grab_toggle.py` demo that runs the staged grab, then lets the operator click the index finger in/out like a button, reusing the grab/reverse/release logic shared with `grab_sequence.py`.

**Architecture:** Extract the staged forward-grab + staged-reverse + connect/release from `grab_sequence.py` into `arm101_hand.scripts.grab_common.run_grab_demo(on_hold=None)` (behavior-preserving). Add a pure, unit-tested toggle core `arm101_hand.hand.index_toggle`. Add the `msvcrt` shell `scripts/demos/grab_toggle.py` whose `on_hold` callback runs the toggle loop. Reduce `grab_sequence.py` to a thin caller.

**Tech Stack:** Python 3.12, rustypot `Scs0009PyController`, lerobot follower subclass, pydantic config, `msvcrt` raw keys (Windows), pytest, ruff, mypy, uv.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/arm101_hand/hand/index_toggle.py` | NEW. Pure two-state toggle core: `ToggleState`, `key_to_action`, `apply_action`, `in_base`, `target_base`, delta bounds. No hardware, no msvcrt. |
| `tests/unit/test_index_toggle.py` | NEW. Host unit test for the toggle core. |
| `src/arm101_hand/scripts/grab_common.py` | NEW. Extracted shared logic: `build_arm_plan`, `GrabHoldContext`, `run_grab_demo(on_hold=None)`. |
| `scripts/demos/grab_sequence.py` | MODIFY. Reduce to a thin `run_grab_demo()` caller + short docstring. |
| `scripts/demos/grab_toggle.py` | NEW. msvcrt shell: `on_hold` toggle loop + `main()`. |

Note: `src/arm101_hand/hand/__init__.py` is intentionally NOT modified. The shell imports `index_toggle` directly (the `pose_jog` precedent), avoiding a name collision with the `apply_action`/`key_to_action` already re-exported from `range_calib`.

---

## Task 1: Pure toggle core (`index_toggle`) — TDD

**Files:**
- Create: `src/arm101_hand/hand/index_toggle.py`
- Test: `tests/unit/test_index_toggle.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_index_toggle.py`:

```python
from arm101_hand.hand.index_toggle import (
    TOGGLE_DELTA_DEFAULT,
    TOGGLE_DELTA_MAX,
    TOGGLE_DELTA_MIN,
    ToggleState,
    apply_action,
    in_base,
    key_to_action,
    target_base,
)

# Index calibrated window (hand_calib_values.yaml): base_min=-20, base_max=70.
BASE_MIN, BASE_MAX = -20, 70
# Where 'grab' settles the index (decoded from pose [72, 6]).
GRAB_OUT_BASE, GRAB_SIDE = 33, -39


def test_key_to_action_map():
    assert key_to_action(" ") == "toggle"
    assert key_to_action("[") == "delta-"
    assert key_to_action("]") == "delta+"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_default_state_is_out_with_default_delta():
    state = ToggleState(out_base=GRAB_OUT_BASE, side=GRAB_SIDE)
    assert state.pressed is False
    assert state.delta == TOGGLE_DELTA_DEFAULT


def test_toggle_flips_pressed():
    state = ToggleState(out_base=GRAB_OUT_BASE, side=GRAB_SIDE)
    state = apply_action(state, "toggle")
    assert state.pressed is True
    state = apply_action(state, "toggle")
    assert state.pressed is False


def test_in_base_is_out_plus_delta():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20)
    assert in_base(state, BASE_MIN, BASE_MAX) == 53


def test_in_base_clamps_to_base_max():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=40)  # 33+40=73 > 70
    assert in_base(state, BASE_MIN, BASE_MAX) == BASE_MAX


def test_target_base_selects_in_when_pressed_out_otherwise():
    out = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=False)
    pressed = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=True)
    assert target_base(out, BASE_MIN, BASE_MAX) == 33
    assert target_base(pressed, BASE_MIN, BASE_MAX) == 53


def test_delta_grows_and_shrinks_within_bounds():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20)
    state = apply_action(state, "delta+")
    assert state.delta == 21
    state = apply_action(state, "delta-")
    assert state.delta == 20


def test_delta_clamps_to_bounds():
    lo = ToggleState(out_base=33, side=GRAB_SIDE, delta=TOGGLE_DELTA_MIN)
    assert apply_action(lo, "delta-").delta == TOGGLE_DELTA_MIN
    hi = ToggleState(out_base=33, side=GRAB_SIDE, delta=TOGGLE_DELTA_MAX)
    assert apply_action(hi, "delta+").delta == TOGGLE_DELTA_MAX


def test_delta_change_does_not_move_pressed_state():
    state = ToggleState(out_base=33, side=GRAB_SIDE, delta=20, pressed=True)
    state = apply_action(state, "delta+")
    assert state.pressed is True  # delta keys never toggle the finger


def test_quit_and_unknown_are_state_noops():
    state = ToggleState(out_base=33, side=GRAB_SIDE)
    assert apply_action(state, "quit") == state
    assert apply_action(state, "nonsense") == state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_index_toggle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.hand.index_toggle'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/arm101_hand/hand/index_toggle.py`:

```python
"""Pure two-state toggle core for ``scripts/demos/grab_toggle.py``.

No hardware, no ``msvcrt`` -- the testable core of the index-finger "button click".
After the grab demo settles, the index finger alternates between an OUT base (where
``grab`` left it) and an IN base (OUT + ``delta``, clamped to the finger's calibrated
``base_max``). Mirrors the pure-core / thin-shell split of ``pose_jog`` and
``range_calib``: this module is the state machine; the script owns keys and the bus.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.hand.kinematics import clamp

# Toggle delta bounds (degrees). Default 20 matches the bench example (grab base 33 ->
# pressed 53). The real ceiling on press depth is the index ``base_max`` enforced by
# ``in_base``'s clamp, so these bounds are deliberately independent of the jog STEP_*.
TOGGLE_DELTA_DEFAULT = 20
TOGGLE_DELTA_MIN = 1
TOGGLE_DELTA_MAX = 40

_KEY_ACTIONS: dict[str, str] = {
    " ": "toggle",
    "[": "delta-",
    "]": "delta+",
    "q": "quit",
}


@dataclass(frozen=True)
class ToggleState:
    """Immutable index-toggle cursor.

    ``out_base``/``side`` are the logical ``(base, side)`` the index settled at in
    ``grab``; ``delta`` is the live press depth; ``pressed`` is True when clicked IN.
    """

    out_base: int
    side: int
    delta: int = TOGGLE_DELTA_DEFAULT
    pressed: bool = False


def key_to_action(key: str) -> str | None:
    """Map a raw key token to a toggle action, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def in_base(state: ToggleState, base_min: int, base_max: int) -> int:
    """The clicked-IN base: ``out_base + delta``, clamped to the calibrated window."""
    return int(clamp(state.out_base + state.delta, base_min, base_max))


def target_base(state: ToggleState, base_min: int, base_max: int) -> int:
    """Base the index should hold now: IN base when ``pressed``, OUT base otherwise."""
    if state.pressed:
        return in_base(state, base_min, base_max)
    return int(clamp(state.out_base, base_min, base_max))


def apply_action(state: ToggleState, action: str) -> ToggleState:
    """Apply an action. ``quit`` / unknown are state no-ops (handled by the shell).

    Delta keys adjust the press depth only -- they never flip ``pressed`` -- so the
    finger never moves on a ``[`` / ``]`` keypress (no surprise movements).
    """
    if action == "toggle":
        return replace(state, pressed=not state.pressed)
    if action == "delta+":
        return replace(state, delta=int(clamp(state.delta + 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    if action == "delta-":
        return replace(state, delta=int(clamp(state.delta - 1, TOGGLE_DELTA_MIN, TOGGLE_DELTA_MAX)))
    return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_index_toggle.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff format src/arm101_hand/hand/index_toggle.py tests/unit/test_index_toggle.py`
Run: `uv run ruff check src/arm101_hand/hand/index_toggle.py tests/unit/test_index_toggle.py`
Run: `uv run mypy src/arm101_hand/hand/index_toggle.py`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/hand/index_toggle.py tests/unit/test_index_toggle.py
git commit -m "feat(hand): add pure index-toggle core (two-state press depth)"
```

---

## Task 2: Extract `grab_common` + reduce `grab_sequence` (behavior-preserving refactor)

This task moves code; it does NOT change runtime behavior. There is no new unit test (the path is hardware-bound). Verification = imports cleanly, the existing host suite still passes, ruff/mypy clean. The mandated hardware re-run of `grab_sequence.py` is in Task 4.

**Files:**
- Create: `src/arm101_hand/scripts/grab_common.py`
- Modify: `scripts/demos/grab_sequence.py` (replace entire file)

- [ ] **Step 1: Create `src/arm101_hand/scripts/grab_common.py`**

Create the file with the full content below (this is `grab_sequence.py`'s current logic, relocated; `_build_arm_plan` becomes public `build_arm_plan`, `main` becomes `run_grab_demo(on_hold=None)` with the `on_hold` hook + `GrabHoldContext` inserted at the hold point, and the hand paths use `parents[3]`):

```python
"""Shared staged grab/reverse/release for the grab demos (application layer).

Both ``scripts/demos/grab_sequence.py`` and ``scripts/demos/grab_toggle.py`` drive the
same staged grab; this module owns it so the logic lives once (IL-7).

Forward sequence (each stage position-confirmed -- arm AND hand -- before the next):
  1. shoulder_pan -> base_max (its calibrated upper bound), AND hand -> pre_grab
     (commanded together, then both polled to completion)
  2. shoulder_lift + elbow_flex + wrist_flex + wrist_roll -> grab
  3. shoulder_pan -> grab
  4. hand fingers -> grab (closes onto the object; stalls -> times out -> continues)

Once both devices hold ``grab`` under torque, ``run_grab_demo`` calls the optional
``on_hold(ctx)`` hook (e.g. an interactive index-toggle loop) before the exit prompt.

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
"""

from __future__ import annotations

import contextlib
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import (
    HandCalibration,
    HandConfig,
    load_arm_config,
    load_hand_calibration,
    load_hand_config,
)
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

# src/arm101_hand/scripts/grab_common.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"

ARM_POSE = "grab"
HAND_POSE = "grab"
HAND_PRE_GRAB_POSE = "pre_grab"
HAND_OPEN_POSE = "open"
PAN_JOINT = "shoulder_pan"
# Everything except the base pan -- moved together in forward step 2 / reverse step R2.
POSTURE_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


@dataclass
class GrabHoldContext:
    """What an ``on_hold`` hook needs while both devices hold ``grab`` under torque.

    No arm handle is exposed on purpose -- a hold hook coordinates the hand only; the
    arm stays parked under torque and is released by ``run_grab_demo``'s exit path.
    """

    hand: Scs0009PyController
    hand_calib: HandCalibration
    hand_cfg: HandConfig


def build_arm_plan() -> tuple[dict[str, float], float]:
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


def run_grab_demo(on_hold: Callable[[GrabHoldContext], None] | None = None) -> int:
    """Stage the arm+hand into ``grab``, hold under torque, then reverse on exit.

    When ``on_hold`` is given, it is called once -- after both devices reach ``grab`` and
    before the exit prompt -- with a ``GrabHoldContext``. With ``on_hold=None`` the
    behavior is identical to the original ``grab_sequence`` demo.
    """
    # Resolve everything before touching hardware -- fail fast, leave no bus open.
    arm_targets, base_max_deg = build_arm_plan()

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
    # Hand position-poll params. tolerance reuses pose_margin_deg (no separate poll-tolerance knob
    # yet -- the same pragmatic reuse the arm makes with home_* above); timeout/poll from config.
    hand_wait_kw = {
        "tolerance_rad": math.radians(hand_cfg.tuning.pose_margin_deg),
        "timeout_s": hand_cfg.tuning.pose_timeout_s,
        "poll_s": hand_cfg.tuning.pose_poll_s,
    }
    # Forward pre_grab (step 2) uses the general jog speed; the grab CLOSE (step 4) uses the
    # dedicated flexion speed; opening moves (reverse pre_grab + open) use the extension speed.
    hand_speed = hand_cfg.tuning.speed
    hand_close_speed = hand_cfg.tuning.speeds.close
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
        # Step 1: command shoulder_pan -> base_max and the hand -> pre_grab together, then
        # confirm BOTH (the hand opens to pre_grab while the base rotates).
        print(
            f"  [1] shoulder_pan -> base_max ({base_max_deg:.1f} deg)"
            f" + hand -> '{HAND_PRE_GRAB_POSE}' (together) ..."
        )
        write_hand_servos(hand, hand_pre_grab_targets, hand_speed)
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        wait_hand_reached(hand, hand_pre_grab_targets, **hand_wait_kw)

        # Step 2: arm posture -> grab (the hand is already holding pre_grab).
        print("  [2] posture -> grab ...")
        drive_arm_joints(follower, posture_grab, vel, **wait_kw)

        if not pan_grab:
            print("  WARNING: shoulder_pan out of range -- [3] is a no-op; pan stays at base_max")
        print("  [3] shoulder_pan -> grab ...")
        drive_arm_joints(follower, pan_grab, vel, **wait_kw)
        print(f"Arm holding '{ARM_POSE}': {arm_targets}")

        # ---- Hand leg: close into grab (stalls on the object -> timeout -> continue) ----
        print(f"  [4] hand -> '{HAND_POSE}' on {hand_cfg.connection.port} ...")
        drive_hand_servos(hand, hand_targets, hand_close_speed, **hand_wait_kw)
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")

        # ---- Optional interactive hold (e.g. index-toggle) ----
        if on_hold is not None:
            on_hold(GrabHoldContext(hand=hand, hand_calib=hand_calib, hand_cfg=hand_cfg))
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
```

- [ ] **Step 2: Replace `scripts/demos/grab_sequence.py` with a thin caller**

Overwrite the whole file with:

```python
"""Grab demo: stage the SO-ARM101 into ``grab``, close the AmazingHand, hold both under
torque, and reverse the whole thing on exit.

The staged forward grab, the reverse-on-'h', and the connect/release live in
``arm101_hand.scripts.grab_common`` (shared with ``grab_toggle.py``); this script is the
plain entry point with no interactive hold.

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import sys

from arm101_hand.scripts.grab_common import run_grab_demo


def main() -> int:
    return run_grab_demo()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Verify both modules import (no hardware contact at import time)**

Run: `uv run python -c "import arm101_hand.scripts.grab_common as g; print(g.ARM_POSE, g.HAND_CALIB_PATH.name, g.HAND_CONFIG_PATH.name)"`
Expected: `grab hand_calib_values.yaml hand_config.yaml` (no serial port opened).

Run: `uv run python -c "import importlib.util, pathlib; s=pathlib.Path('scripts/demos/grab_sequence.py'); spec=importlib.util.spec_from_file_location('gs', s); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(callable(m.main))"`
Expected: `True` (module imports; `main` not called, so no bus).

- [ ] **Step 4: Lint, type-check, full host suite**

Run: `uv run ruff format src/arm101_hand/scripts/grab_common.py scripts/demos/grab_sequence.py`
Run: `uv run ruff check src/arm101_hand/scripts/grab_common.py scripts/demos/grab_sequence.py`
Run: `uv run mypy src`
Run: `uv run pytest -m 'not hardware'`
Expected: ruff clean; mypy clean; all host tests pass (the existing suite still green proves the extraction did not break imports/types).

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/scripts/grab_common.py scripts/demos/grab_sequence.py
git commit -m "refactor(demo): extract grab_common.run_grab_demo; grab_sequence delegates"
```

---

## Task 3: `grab_toggle.py` shell (msvcrt loop)

**Files:**
- Create: `scripts/demos/grab_toggle.py`

- [ ] **Step 1: Create `scripts/demos/grab_toggle.py`**

```python
"""Grab, then click the index finger like a button.

Runs the staged arm+hand grab (see ``arm101_hand.scripts.grab_common``); once both
devices hold ``grab`` under torque, enter an interactive loop that toggles the INDEX
finger in/out like pressing a button. Only the index base moves -- its held side, the
other three fingers, and the arm all stay parked under torque.

Controls (torque ON the whole time):
  SPACE       toggle the index finger IN (pressed) / OUT (released)
  [ / ]       shrink / grow the toggle delta (press depth); applies to the NEXT press
  q / Ctrl+C  stop toggling and go to the exit prompt

The status line and the high-load warning are the jog tool's:
  step=<delta> | *ind b=.. s=.. | mid .. | rin .. | thu ..
  WARNING: high load on servo(s) [..] -- back off one step and mark there

IN is clamped to the index's calibrated base_max, so it never presses past the stop.
The displayed index b/s is read back from the PRESENT (settled) position, so when the
button stalls the finger below the commanded depth the line shows where it really got.

On exit the shared exit prompt runs: Enter releases both in place, 'h' reverses the
whole grab (see grab_common). Windows-only: uses ``msvcrt`` for raw keys.

Usage:
  uv run python scripts/demos/grab_toggle.py
"""

from __future__ import annotations

import math
import msvcrt
import sys

from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand import (
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    drive_hand_servos,
    load_warning,
    servo_radians_to_degrees,
)
from arm101_hand.hand.index_toggle import (
    ToggleState,
    apply_action,
    key_to_action,
    target_base,
)
from arm101_hand.hand.pose_jog import HandJogState, format_hand_status
from arm101_hand.scripts.grab_common import GrabHoldContext, run_grab_demo

# Fingers shown on the status line besides the active index (read once; held under torque).
_STATIC_FINGERS = ("middle", "ring", "thumb")


def _read_key() -> str:
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens (ignored by the map)."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _scalar(v: object) -> float:
    """Unwrap rustypot's single-element list reads (``read_<reg>`` returns a list)."""
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def _read_finger(c, name, block) -> tuple[int, int]:
    """Read one finger's present ``(base, side)`` (logical frame), clamped to its limits."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[name]
    rad1 = float(_scalar(c.read_present_position(id1)))
    rad2 = float(_scalar(c.read_present_position(id2)))
    pos1 = servo_radians_to_degrees(id1, rad1, block.servo_1.middle_pos)
    pos2 = servo_radians_to_degrees(id2, rad2, block.servo_2.middle_pos)
    base, side = decompose_finger(
        round(pos1),
        round(pos2),
        side_min=lim.side_min,
        side_max=lim.side_max,
        base_min=lim.base_min,
        base_max=lim.base_max,
    )
    return int(base), int(side)


def _drive_index(c, block, base, side, speed, wait_kw) -> None:
    """Command the index's two servos to logical ``(base, side)`` and position-poll."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS["index"]
    pos1, pos2 = compose_finger(
        base,
        side,
        base_min=lim.base_min,
        base_max=lim.base_max,
        side_min=lim.side_min,
        side_max=lim.side_max,
    )
    targets = {
        id1: degrees_to_servo_radians(id1, pos1, block.servo_1.middle_pos),
        id2: degrees_to_servo_radians(id2, pos2, block.servo_2.middle_pos),
    }
    drive_hand_servos(c, targets, speed, **wait_kw)


def _status_line(state: ToggleState, index_base: int, index_side: int, others: dict) -> str:
    """Render the jog-style status line; ``step=`` shows the live toggle delta."""
    fingers = {"index": (int(index_base), int(index_side)), **others}
    return format_hand_status(HandJogState(active="index", step=state.delta, fingers=fingers))


def _toggle_loop(ctx: GrabHoldContext) -> None:
    """Interactive index-toggle loop; runs while both devices hold ``grab`` under torque."""
    c = ctx.hand
    calib = ctx.hand_calib
    tuning = ctx.hand_cfg.tuning
    index_block = calib.fingers["index"]
    lim = index_block.limits
    id1, id2 = FINGER_SERVO_IDS["index"]
    wait_kw = {
        "tolerance_rad": math.radians(tuning.pose_margin_deg),
        "timeout_s": tuning.pose_timeout_s,
        "poll_s": tuning.pose_poll_s,
    }

    # Seed OUT base + held side + the static fingers from the PRESENT (settled) grab pose.
    out_base, side = _read_finger(c, "index", index_block)
    others = {name: _read_finger(c, name, calib.fingers[name]) for name in _STATIC_FINGERS}
    state = ToggleState(out_base=out_base, side=side)

    print("Index toggle: SPACE = click in/out, [ / ] = delta, q = exit")
    print("  " + _status_line(state, out_base, side, others))

    try:
        while True:
            action = key_to_action(_read_key())
            if action is None:
                continue
            if action == "quit":
                break
            state = apply_action(state, action)
            if action == "toggle":
                tgt = target_base(state, lim.base_min, lim.base_max)
                speed = tuning.speeds.close if state.pressed else tuning.speeds.open
                _drive_index(c, index_block, tgt, side, speed, wait_kw)
                base_now, side_now = _read_finger(c, "index", index_block)
                print("  " + _status_line(state, base_now, side_now, others))
                load1 = int(_scalar(c.read_present_load(id1)))
                load2 = int(_scalar(c.read_present_load(id2)))
                warn = load_warning(load1, load2)
                if warn:
                    print("  " + warn)
            else:  # delta changed -- reprint (no movement)
                base_now, side_now = _read_finger(c, "index", index_block)
                print("  " + _status_line(state, base_now, side_now, others))
    except KeyboardInterrupt:
        print("\n^C -- leaving toggle mode")


def main() -> int:
    return run_grab_demo(_toggle_loop)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the shell imports (no hardware at import time)**

Run: `uv run python -c "import importlib.util, pathlib; s=pathlib.Path('scripts/demos/grab_toggle.py'); spec=importlib.util.spec_from_file_location('gt', s); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(callable(m.main), callable(m._toggle_loop))"`
Expected: `True True` (imports cleanly; `main` not called, so no bus opened).

- [ ] **Step 3: Lint + type-check**

Run: `uv run ruff format scripts/demos/grab_toggle.py`
Run: `uv run ruff check scripts/demos/grab_toggle.py`
Run: `uv run mypy src`  (the shell lives under `scripts/`, which mypy does not scan; this confirms `src` still type-checks after the new imports are exercised)
Expected: ruff clean; mypy clean.

- [ ] **Step 4: Commit**

```bash
git add scripts/demos/grab_toggle.py
git commit -m "feat(demo): grab_toggle -- click the index finger like a button after grab"
```

---

## Task 4: Hardware verification (manual, on the bench)

No code. Because Task 2 refactored the working `grab_sequence.py`, both demos must be confirmed on the powered rig before this branch merges. Requires the AmazingHand (COM18, 5 V) and the SO-ARM101 (12 V) powered, with no other COM-port owner open (IL-4).

- [ ] **Step 1: Regression — `grab_sequence.py` still behaves as before**

Run: `uv run python scripts/demos/grab_sequence.py`
Expected: stages 1-4 print and complete; arm+hand hold `grab`; at the exit prompt, `h` reverses the whole sequence and both buses release; plain Enter releases in place. Identical to pre-refactor behavior.

- [ ] **Step 2: New demo — `grab_toggle.py`**

Run: `uv run python scripts/demos/grab_toggle.py`
Expected: same staged grab, then the toggle prompt. `SPACE` flexes the index in (status line `b` rises toward ~53, side unchanged); `SPACE` again releases it out (`b` back to ~33). `]` / `[` change the `step=` delta; the next `SPACE` presses to the new depth. Pressing into a hard stop shows the `WARNING: high load on servo(s) [..]` line and the displayed `b` reflects where the finger actually stalled. `q` exits to the same prompt as Step 1.

- [ ] **Step 3: Record the result**

Note pass/fail in the PR description. If a regression appears in `grab_sequence.py`, it is a Task 2 defect (the extraction changed behavior) — fix there, not in `grab_toggle.py`.

---

## Self-Review

**Spec coverage:**
- Toggle target = base + delta, clamped to base_max -> Task 1 (`in_base`, `target_base`) + Task 3 (`_drive_index` clamp via `compose_finger`). OK
- Controls SPACE / `[` `]` / q -> Task 1 (`key_to_action`, delta keys never move) + Task 3 loop. OK
- High load = warn only -> Task 3 (`load_warning`, no back-off). OK
- Shared helper extraction, behavior-preserving, atomic -> Task 2; commits 1-3 land on one branch and merge together (IL-6). OK
- Honest present-position display -> Task 3 (`_read_finger` after the move). OK
- Pure testable core -> Task 1 (10 unit tests). OK
- IL-5 (reads, never writes config/calib) -> no write calls in any task. OK
- Hardware re-run of grab_sequence -> Task 4 Step 1. OK
- Spec said "export toggle symbols in `__init__`": intentionally dropped (direct import, `pose_jog` precedent) — no functional gap; documented in File Structure note.

**Placeholder scan:** none — every code step is complete; every command has expected output.

**Type consistency:** `ToggleState(out_base, side, delta, pressed)`, `key_to_action`, `apply_action(state, action)`, `in_base(state, base_min, base_max)`, `target_base(state, base_min, base_max)` are used identically across Task 1 and Task 3. `GrabHoldContext(hand, hand_calib, hand_cfg)` is defined in Task 2 and consumed with the same field names in Task 3. `run_grab_demo(on_hold=None)` signature matches both callers.
