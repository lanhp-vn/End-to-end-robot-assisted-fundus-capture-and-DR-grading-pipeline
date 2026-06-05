"""Pure multi-finger jog state machine for ``scripts/calibration/AmazingHand/jog.py``.

No hardware, no ``msvcrt`` -- the testable core of the hand jog tool. The script reads
raw keys, maps them via ``key_to_action``, advances a ``HandJogState`` via
``apply_action`` (clamping the active finger to its calibrated ``DofLimits``), then
composes each finger's ``(base, side)`` cursor into servo commands.

Frame: ``base``/``side`` are the logical DOF (see ``hand.kinematics``). Unlike
``range_calib`` (single finger, generous discovery envelope, mark actions), this jogs
*all four* fingers within their already-measured limits and saves a whole-hand pose.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from arm101_hand.config import DofLimits
from arm101_hand.hand.kinematics import clamp
from arm101_hand.hand.range_calib import STEP_DEFAULT, STEP_MAX, STEP_MIN

FINGERS: tuple[str, ...] = ("index", "middle", "ring", "thumb")

_KEY_ACTIONS: dict[str, str] = {
    "1": "select_index",
    "2": "select_middle",
    "3": "select_ring",
    "4": "select_thumb",
    "UP": "base+",
    "DOWN": "base-",
    "RIGHT": "side+",
    "LEFT": "side-",
    "[": "step-",
    "]": "step+",
    "h": "home",
    "H": "home_all",
    "s": "save",
    "q": "quit",
}

_SELECT: dict[str, str] = {
    "select_index": "index",
    "select_middle": "middle",
    "select_ring": "ring",
    "select_thumb": "thumb",
}


def _neutral_fingers() -> dict[str, tuple[int, int]]:
    return dict.fromkeys(FINGERS, (0, 0))


@dataclass(frozen=True)
class HandJogState:
    """Immutable cursor: active finger, shared step, and each finger's (base, side)."""

    active: str = "index"
    step: int = STEP_DEFAULT
    fingers: dict[str, tuple[int, int]] = field(default_factory=_neutral_fingers)


def key_to_action(key: str) -> str | None:
    """Map a normalized key token to an action name, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def apply_action(
    state: HandJogState,
    action: str,
    limits_by_finger: dict[str, DofLimits],
) -> HandJogState:
    """Apply an action, clamping the active finger to its calibrated ``DofLimits``.

    ``save`` / ``quit`` / unknown actions are no-ops on state (handled by the script).
    """
    if action in _SELECT:
        return replace(state, active=_SELECT[action])
    # kinematics.clamp returns float; int() keeps the cursor integer-valued.
    if action == "step+":
        return replace(state, step=int(clamp(state.step + 1, STEP_MIN, STEP_MAX)))
    if action == "step-":
        return replace(state, step=int(clamp(state.step - 1, STEP_MIN, STEP_MAX)))
    if action == "home":
        fingers = dict(state.fingers)
        fingers[state.active] = (0, 0)
        return replace(state, fingers=fingers)
    if action == "home_all":
        return replace(state, fingers=_neutral_fingers())
    if action in ("base+", "base-", "side+", "side-"):
        base, side = state.fingers[state.active]
        lim = limits_by_finger[state.active]
        if action == "base+":
            base = clamp(base + state.step, lim.base_min, lim.base_max)
        elif action == "base-":
            base = clamp(base - state.step, lim.base_min, lim.base_max)
        elif action == "side+":
            side = clamp(side + state.step, lim.side_min, lim.side_max)
        else:  # side-
            side = clamp(side - state.step, lim.side_min, lim.side_max)
        fingers = dict(state.fingers)
        fingers[state.active] = (int(base), int(side))
        return replace(state, fingers=fingers)
    return state  # save / quit / unmapped


def format_hand_status(state: HandJogState) -> str:
    """One-line multi-finger status; the active finger is marked with ``*``."""
    parts = []
    for name in FINGERS:
        base, side = state.fingers[name]
        mark = "*" if name == state.active else " "
        parts.append(f"{mark}{name[:3]} b={base:>4} s={side:>4}")
    return f"step={state.step:>2} | " + " | ".join(parts)
