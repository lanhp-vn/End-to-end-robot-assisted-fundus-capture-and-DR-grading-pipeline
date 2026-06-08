"""Pydantic schemas for the runtime config + calibration YAML files (primitive layer).

- ``app_config`` → ``data/app_config.yaml``
- ``hand_poses`` → ``data/hand_config.yaml``
- ``arm_poses`` → ``data/arm_config.yaml``
- ``calibration`` → ``scripts/calibration/amazing_hand/hand_calib_values.yaml``
"""

from .app_config import AppConfig, load_app_config

# NOTE (transition): arm_config.ArmPose and hand_config.HandPose are intentionally
# NOT imported here, so the bare names ArmPose / HandPose keep resolving to the
# legacy arm_poses / hand_poses classes that existing scripts still use. Once
# arm_poses.py / hand_poses.py are removed in the hand/arm switch tasks, promote
# the new classes to the bare names and delete this note.
from .arm_config import ArmConfig, ArmConnection, ArmSafety, ArmTuning, load_arm_config, save_arm_config
from .arm_poses import ARM_MOTORS, ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from .calibration import (
    FINGER_NAMES,
    DofLimits,
    HandCalibration,
    PoseSpeeds,
    load_hand_calibration,
    save_hand_calibration,
)
from .hand_config import (
    HandConfig,
    HandConnection,
    HandSafety,
    HandSpeeds,
    HandTuning,
    load_hand_config,
    save_hand_config,
)
from .hand_poses import (
    POSITIONS_LEN,
    HandPose,
    HandPoseConfig,
    load_hand_poses,
    save_hand_poses,
)
from .motor_ids import FINGER_SERVO_IDS

__all__ = [
    "ARM_MOTORS",
    "FINGER_NAMES",
    "FINGER_SERVO_IDS",
    "POSITIONS_LEN",
    "AppConfig",
    "ArmConfig",
    "ArmConnection",
    "ArmSafety",
    "ArmTuning",
    "DofLimits",
    "ArmPose",
    "ArmPoseConfig",
    "HandCalibration",
    "HandConfig",
    "HandConnection",
    "HandSafety",
    "HandSpeeds",
    "HandTuning",
    "PoseSpeeds",
    "HandPose",
    "HandPoseConfig",
    "load_app_config",
    "load_arm_config",
    "load_arm_poses",
    "save_arm_config",
    "save_arm_poses",
    "load_hand_calibration",
    "load_hand_config",
    "load_hand_poses",
    "save_hand_calibration",
    "save_hand_config",
    "save_hand_poses",
]
