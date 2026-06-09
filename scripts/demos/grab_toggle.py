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
