"""AmazingHand device layer (rustypot-based) and pure-math kinematics."""

from .kinematics import (
    MAX_NAME_LEN,
    clamp,
    compose_finger,
    decompose_finger,
    degrees_to_servo_radians,
    even_id_inversion,
    finger_positions_to_servo_frame,
    servo_radians_to_degrees,
    validate_pose_name,
)
from .motion import (
    drive_hand_servos,
    wait_hand_reached,
    write_hand_servos,
)
from .pose_resolver import (
    BUILTIN_POSES,
    DEFAULT_POSE_MARGIN_DEG,
    available_pose_names,
    resolve_hand_pose_targets,
)
from .range_calib import (
    JogState,
    apply_action,
    format_status,
    key_to_action,
    load_warning,
)

__all__ = [
    "MAX_NAME_LEN",
    "clamp",
    "compose_finger",
    "decompose_finger",
    "degrees_to_servo_radians",
    "even_id_inversion",
    "finger_positions_to_servo_frame",
    "servo_radians_to_degrees",
    "validate_pose_name",
    "drive_hand_servos",
    "wait_hand_reached",
    "write_hand_servos",
    "BUILTIN_POSES",
    "DEFAULT_POSE_MARGIN_DEG",
    "available_pose_names",
    "resolve_hand_pose_targets",
    "JogState",
    "apply_action",
    "format_status",
    "key_to_action",
    "load_warning",
]
