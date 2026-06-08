"""Resolve a named hand pose to per-servo wire targets (pure; no hardware).

Two pose sources are unified here so every caller (the ``set_pose`` CLI, demos)
shares one decode path:

- Built-in ``open`` / ``close`` -- computed from each finger's calibrated
  ``base_min`` / ``base_max`` (logical frame, neutral spread), backed off the
  endpoint by ``margin_deg`` so the fingers never slam the measured limits.
- Any named pose stored in ``data/hand_config.yaml`` (e.g. ``grab``, ``middle``)
  -- the stored 8-int array is already in **servo frame** (even-ID pre-inverted,
  degrees relative to each servo's calibrated ``middle_pos``), so the wire angle
  is simply ``deg2rad(stored + middle_pos)``.

Output is ``{servo_id: wire_radians}`` ready for
``Scs0009PyController.write_goal_position``. Driving the bus is the caller's job
-- this module never touches hardware.
"""

from __future__ import annotations

from arm101_hand.config import HandCalibration, HandPoseConfig
from arm101_hand.hand.kinematics import (
    compose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
)

#: Poses computed from the calibrated limits rather than stored in hand_config.yaml.
BUILTIN_POSES: tuple[str, ...] = ("open", "close")

#: Degrees to back the open/close poses off the calibrated endpoints (safety margin).
DEFAULT_POSE_MARGIN_DEG = 5.0


def available_pose_names(poses_cfg: HandPoseConfig) -> list[str]:
    """Built-in poses plus every pose stored in ``hand_config.yaml`` (deduped, ordered)."""
    names = list(BUILTIN_POSES)
    for name in poses_cfg.poses:
        if name not in names:
            names.append(name)
    return names


def resolve_hand_pose_targets(
    calib: HandCalibration,
    poses_cfg: HandPoseConfig,
    pose_name: str,
    *,
    margin_deg: float = DEFAULT_POSE_MARGIN_DEG,
) -> dict[int, float]:
    """``pose_name`` -> ``{servo_id: wire_radians}`` for all 8 servos.

    ``open``/``close`` take precedence over a same-named stored pose. Raises
    ``KeyError(pose_name)`` if the name is neither built-in nor present in
    ``poses_cfg``.
    """
    if pose_name in BUILTIN_POSES:
        return _resolve_builtin(calib, pose_name, margin_deg)
    if pose_name in poses_cfg.poses:
        return _resolve_stored(calib, poses_cfg.poses[pose_name].positions)
    raise KeyError(pose_name)


def _resolve_builtin(calib: HandCalibration, pose_name: str, margin_deg: float) -> dict[int, float]:
    """``open``/``close``: every finger at ``base_min + m`` / ``base_max - m``, neutral spread."""
    targets: dict[int, float] = {}
    for finger in calib.fingers.values():
        lim = finger.limits
        base = lim.base_max - margin_deg if pose_name == "close" else lim.base_min + margin_deg
        pos1, pos2 = compose_finger(int(round(base)), 0)  # logical frame; side = 0 (neutral spread)
        s1, s2 = finger.servo_1, finger.servo_2
        targets[s1.id] = degrees_to_servo_radians(s1.id, pos1, s1.middle_pos)
        targets[s2.id] = degrees_to_servo_radians(s2.id, pos2, s2.middle_pos)
    return targets


def _resolve_stored(calib: HandCalibration, positions: list[int]) -> dict[int, float]:
    """Decode a stored servo-frame positions array to wire radians per servo."""
    targets: dict[int, float] = {}
    for servo_id, middle_pos in calib.middle_pos_by_id().items():
        stored = positions[servo_id - 1]  # servo-frame degrees rel. to middle (even-ID pre-inverted)
        # Undo the pre-inversion so degrees_to_servo_radians re-applies the same
        # even-ID flip; the round trip nets out to deg2rad(stored + middle_pos).
        deg_logical = even_id_inversion(servo_id, stored)
        targets[servo_id] = degrees_to_servo_radians(servo_id, deg_logical, middle_pos)
    return targets
