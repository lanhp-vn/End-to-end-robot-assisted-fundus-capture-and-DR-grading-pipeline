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
    return ArmJogState(cursors=dict(cursors), active=ARM_JOINTS[0], step=JOG_STEP_DEFAULT, torque_on=True)


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
