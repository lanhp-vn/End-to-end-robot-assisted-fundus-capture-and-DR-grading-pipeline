# src/arm101_hand/hand/finger_io.py
"""Read/drive one AmazingHand finger in the logical (base, side) frame (device layer).

Factored out of ``scripts/demos/grab_toggle.py`` so the trigger demo shares the exact
same finger read/drive (IL-7). Needs a live controller and the finger's calibration block.
"""

from __future__ import annotations

from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.kinematics import (
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    servo_radians_to_degrees,
)
from arm101_hand.hand.motion import drive_hand_servos


def _scalar(v: object) -> float:
    if isinstance(v, (list, tuple)):
        return float(v[0])
    return float(v)  # type: ignore[arg-type]


def read_finger(controller, name, block) -> tuple[int, int]:
    """Present ``(base, side)`` (logical, clamped to limits) for finger ``name``."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[name]
    pos1 = servo_radians_to_degrees(id1, _scalar(controller.read_present_position(id1)), block.servo_1.middle_pos)
    pos2 = servo_radians_to_degrees(id2, _scalar(controller.read_present_position(id2)), block.servo_2.middle_pos)
    base, side = decompose_finger(
        round(pos1), round(pos2),
        side_min=lim.side_min, side_max=lim.side_max,
        base_min=lim.base_min, base_max=lim.base_max,
    )
    return int(base), int(side)


def drive_finger(controller, name, block, base, side, speed, *, tolerance_rad, timeout_s, poll_s) -> None:
    """Command finger ``name`` to logical ``(base, side)`` and position-poll to completion."""
    lim = block.limits
    id1, id2 = FINGER_SERVO_IDS[name]
    pos1, pos2 = compose_finger(
        base, side,
        base_min=lim.base_min, base_max=lim.base_max,
        side_min=lim.side_min, side_max=lim.side_max,
    )
    targets = {
        id1: degrees_to_servo_radians(id1, pos1, block.servo_1.middle_pos),
        id2: degrees_to_servo_radians(id2, pos2, block.servo_2.middle_pos),
    }
    drive_hand_servos(controller, targets, speed, tolerance_rad=tolerance_rad, timeout_s=timeout_s, poll_s=poll_s)
