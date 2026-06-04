"""Drive the SO-ARM101 to a named pose (degrees) and hold it under torque until Enter.

Poses come from data/arm_config.yaml ``quick_poses`` (zero / home / rest) merged with
data/arm_jog_poses.yaml ``poses`` (saved via jog.py; a jog pose wins on a name collision)
-- this script invents none (IL-7). Targets are in DEGREES (relative to each joint's
calibrated mid) and
are clamped to the joint's usable window [-span/2, +span/2]; an out-of-range value is
warned and that joint is skipped, matching the behavior arm_config.yaml documents for
the GUI.

After connect() the script pushes the on-file calibration to the motors so degree targets
physically match the recorded range (writes the MOTORS, never the JSON -- IL-5).

On exit (Enter, Ctrl+C, or EOF) the arm is first driven back to the centered home (0) and
only then is torque released, so it never drops under gravity from the held pose (see
_common.park_home_and_release).

Usage:
  uv run python scripts/calibration/so_arm101/set_pose.py home
  uv run python scripts/calibration/so_arm101/set_pose.py rest
  uv run python scripts/calibration/so_arm101/set_pose.py        # prompts

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import sys
import time

from _common import (
    ARM_CONFIG_PATH,
    ARM_JOG_POSES_PATH,
    CALIB_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    park_home_and_release,
)

from arm101_hand.config import load_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS, clamp_degrees, load_arm_calibration

_SETTLE_S = 2.0


def _resolve_pose_name(argv: list[str], available: list[str]) -> str:
    if len(argv) > 1 and argv[1] in available:
        return argv[1]
    if len(argv) > 1:
        print(f"  {argv[1]!r} is not a known pose; choose from {available}")
    while True:
        try:
            choice = input(f"Pose? ({'/'.join(available)}): ").strip()
        except EOFError:
            print("\nNo input -- exiting.", file=sys.stderr)
            raise SystemExit(1) from None
        if choice in available:
            return choice
        print(f"  invalid -- pick one of {available}")


def main() -> int:
    quick = load_arm_poses(ARM_CONFIG_PATH).quick_poses
    jog = load_arm_poses(ARM_JOG_POSES_PATH).poses if ARM_JOG_POSES_PATH.is_file() else {}
    poses = {**quick, **jog}
    if not poses:
        print(
            f"no poses defined in {ARM_CONFIG_PATH} (quick_poses) or {ARM_JOG_POSES_PATH} (poses)",
            file=sys.stderr,
        )
        return 1
    available = sorted(poses)
    name = _resolve_pose_name(sys.argv, available)
    pose = poses[name].as_dict()  # {joint: degrees}

    calib = load_arm_calibration(CALIB_PATH)
    # Clamp each joint to its usable degree window; skip out-of-range joints.
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped

    if not targets:
        print("ERROR: all joints out of range -- nothing to drive", file=sys.stderr)
        return 1

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()

    torque_on = False
    print(f"Connecting on {cfg.arm.port}; driving to '{name}' ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        # STS3215 movement-speed register is "Goal_Velocity" (reg 46), not "Profile_Velocity".
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
        follower.bus.enable_torque()
        torque_on = True
        follower.send_action({f"{j}.pos": v for j, v in targets.items()})
        time.sleep(_SETTLE_S)
        print(f"Holding '{name}' under torque: {targets}")
        input("Press Enter to return home and release torque... ")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- returning home")
    finally:
        # Always return to the centered home before releasing torque (see
        # park_home_and_release): avoids the arm dropping from the held pose.
        if follower.is_connected:
            if torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, home, vel)
                print("Home reached; torque off.")
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
