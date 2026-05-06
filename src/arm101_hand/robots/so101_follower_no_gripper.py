"""SO-101 follower with the gripper (motor ID 6) physically removed.

The AmazingHand replaces the gripper, so the upstream SOFollower's hard-coded
6-motor dict would fail when reading motor 6 over the bus. This subclass keeps
the arm motors (IDs 1-5) and registers a new robot type usable from the
`arm101-calibrate-follower` console script (which delegates to lerobot's
calibrate after registering this subclass).
"""

from dataclasses import dataclass
from pathlib import Path

from lerobot.cameras import make_cameras_from_configs
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot.robots.config import RobotConfig
from lerobot.robots.robot import Robot
from lerobot.robots.so_follower.config_so_follower import SOFollowerRobotConfig
from lerobot.robots.so_follower.so_follower import SOFollower

# IL-5: arm calibration must live in-tree. parents[3] = src/arm101_hand/robots/<file> → repo root.
_DEFAULT_CALIBRATION_DIR = Path(__file__).resolve().parents[3] / "scripts" / "calibration" / "so_arm101"


@RobotConfig.register_subclass("so101_follower_no_gripper")
@dataclass
class SO101FollowerNoGripperConfig(SOFollowerRobotConfig):
    """Config for an SO-101 follower without its gripper motor."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if getattr(self, "calibration_dir", None) is None:
            self.calibration_dir = _DEFAULT_CALIBRATION_DIR


class SO101FollowerNoGripper(SOFollower):
    """SO-101 follower with motors 1-5 only; ignores the missing ID 6 (gripper)."""

    config_class = SO101FollowerNoGripperConfig
    name = "so101_follower_no_gripper"

    def __init__(self, config: SO101FollowerNoGripperConfig) -> None:
        # Bypass SOFollower.__init__'s 6-motor dict; build our own from Robot's base.
        Robot.__init__(self, config)
        self.config = config
        nm = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100
        self.bus = FeetechMotorsBus(
            port=config.port,
            motors={
                "shoulder_pan": Motor(1, "sts3215", nm),
                "shoulder_lift": Motor(2, "sts3215", nm),
                "elbow_flex": Motor(3, "sts3215", nm),
                "wrist_flex": Motor(4, "sts3215", nm),
                "wrist_roll": Motor(5, "sts3215", nm),
            },
            calibration=self.calibration,
        )
        self.cameras = make_cameras_from_configs(config.cameras)
