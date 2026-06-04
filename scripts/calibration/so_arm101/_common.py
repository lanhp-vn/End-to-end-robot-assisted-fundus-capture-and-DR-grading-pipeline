"""Shared setup glue for the SO-ARM101 verify/audit helper scripts.

Not pure (loads config + constructs the robot/bus), so the unit-testable calibration
math lives in ``arm101_hand.robots.calibration_summary`` instead. Kept thin.

IL-3: motors 1-5 only (no gripper ID 6). IL-4: callers own the bus for the duration
of one script run. IL-5: reads ``so101_follower.json``; never writes it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from arm101_hand.config import load_app_config
from arm101_hand.robots.calibration_summary import ARM_JOINTS

# scripts/calibration/so_arm101/_common.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"
CALIB_PATH = Path(__file__).resolve().parent / "so101_follower.json"

# id="so101_follower" -> SO101FollowerNoGripper loads <calibration_dir>/so101_follower.json,
# and the subclass defaults calibration_dir to this directory.
FOLLOWER_ID = "so101_follower"


def load_arm_app_config():
    """Load + validate data/app_config.yaml (SystemExit with a clear message if absent)."""
    if not APP_CONFIG_PATH.is_file():
        raise SystemExit(f"app_config.yaml not found at {APP_CONFIG_PATH}")
    return load_app_config(APP_CONFIG_PATH)


def build_follower(cfg, *, use_degrees: bool):
    """Construct an (unconnected) SO101FollowerNoGripper from ``cfg``.

    use_degrees=True -> DEGREES norm mode (poses/display);
    use_degrees=False -> RANGE_M100_100 (endpoint sweep, endpoints = +/-100).
    """
    from arm101_hand.robots import SO101FollowerNoGripper, SO101FollowerNoGripperConfig

    robot_cfg = SO101FollowerNoGripperConfig(
        port=cfg.arm.port,
        id=FOLLOWER_ID,
        use_degrees=use_degrees,
    )
    return SO101FollowerNoGripper(robot_cfg)


def build_raw_bus(cfg):
    """Construct an (unconnected) raw 5-motor FeetechMotorsBus for read-only scanning."""
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    motors = {
        name: Motor(motor_id, "sts3215", MotorNormMode.DEGREES)
        for motor_id, name in enumerate(ARM_JOINTS, start=1)
    }
    return FeetechMotorsBus(port=cfg.arm.port, motors=motors)


def gentle_velocity(cfg) -> int:
    """Goal_Velocity (STS3215 register 46, raw units) for gentle calibration moves."""
    return cfg.safety.safe_park.park_velocity_arm


def park_home_and_release(follower, vel: int, settle_s: float = 2.0) -> None:
    """Drive all joints to the calibrated home (0) at gentle ``vel``, then disable torque.

    Convention for every *motion* helper script (sweep, set_pose): return to the centered
    home before cutting torque so the arm doesn't drop under gravity from an extended pose
    (mirrors the ``safe_park`` intent in ``data/app_config.yaml``). Home is ``0`` in both
    norm modes — DEGREES and RANGE_M100_100 measure from the calibrated midpoint.

    Call only when torque is currently enabled and the bus is connected. Best-effort: any
    error during the homing move (including a second Ctrl+C) still falls through to
    torque-off, so the caller can always disconnect cleanly afterward.
    """
    try:
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
        follower.send_action({f"{j}.pos": 0.0 for j in ARM_JOINTS})
        time.sleep(settle_s)
    except BaseException as e:
        print(f"  (homing interrupted: {e!r}) -- releasing torque anyway", file=sys.stderr)
    finally:
        follower.bus.disable_torque()
