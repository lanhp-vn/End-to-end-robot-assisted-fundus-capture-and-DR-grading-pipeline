"""Pydantic schemas for the runtime config + calibration YAML files (primitive layer).

- ``arm_config``  → ``src/arm101_hand/data/arm_config.yaml``
- ``hand_config`` → ``src/arm101_hand/data/hand_config.yaml``
- ``calibration`` → ``scripts/calibration/amazing_hand/hand_calib_values.yaml``
"""

from .arm_config import (
    ArmConfig,
    ArmConnection,
    ArmPose,
    ArmSafety,
    ArmTuning,
    load_arm_config,
    save_arm_config,
)
from .calibration import (
    FINGER_NAMES,
    DofLimits,
    HandCalibration,
    load_hand_calibration,
    save_hand_calibration,
)
from .camera_config import (
    CameraCapture,
    CameraConfig,
    CameraConnection,
    load_camera_config,
)
from .hand_config import (
    HandConfig,
    HandConnection,
    HandPose,
    HandSafety,
    HandSpeeds,
    HandTuning,
    load_hand_config,
    save_hand_config,
)
from .motor_ids import FINGER_SERVO_IDS

__all__ = [
    "FINGER_NAMES",
    "FINGER_SERVO_IDS",
    "CameraCapture",
    "CameraConfig",
    "CameraConnection",
    "ArmConfig",
    "ArmConnection",
    "ArmPose",
    "ArmSafety",
    "ArmTuning",
    "DofLimits",
    "HandCalibration",
    "HandConfig",
    "HandConnection",
    "HandPose",
    "HandSafety",
    "HandSpeeds",
    "HandTuning",
    "load_arm_config",
    "save_arm_config",
    "load_camera_config",
    "load_hand_calibration",
    "load_hand_config",
    "save_hand_calibration",
    "save_hand_config",
]
