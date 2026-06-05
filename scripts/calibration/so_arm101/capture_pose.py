"""Capture the SO-ARM101's current hand-posed pose and save it to data/arm_config.yaml.

Workflow (no jogging, no numeric entry): the arm goes limp so you move it to the desired
pose by hand, then Enter captures the present motor degrees and holds the pose under torque
so it does not sag. You can save the captured degrees under a name (e.g. ``home``). Then:

  h   return to home, release torque, and capture another pose
  q   return to home, release torque, and quit

Both routes drive back to the configured home BEFORE releasing torque (see
_common.park_home_and_release), so the arm never drops under gravity from the held pose.

Usage:
  uv run python scripts/calibration/so_arm101/capture_pose.py

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally. IL-5: pushes the
on-file calibration to the motors (so present positions read in the recorded degree frame)
but never writes so101_follower.json.
"""

from __future__ import annotations

import sys
import time

from _common import (
    ARM_CONFIG_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    park_home_and_release,
)

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS

_SETTLE_S = 0.5


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _maybe_save(pose: dict[str, float]) -> None:
    """Prompt for a name and upsert the captured pose into arm_config.yaml (blank = skip)."""
    try:
        name = input("Save as (name, blank = don't save): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        return
    config = load_arm_poses(ARM_CONFIG_PATH) if ARM_CONFIG_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**pose)
    save_arm_poses(ARM_CONFIG_PATH, config)
    print(f"  saved '{name}' -> {ARM_CONFIG_PATH}")


def _menu() -> str:
    """Re-prompt until the operator types h or q; EOF/Ctrl-C -> q."""
    while True:
        try:
            choice = input("[h] return home & capture another   [q] return home & quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if choice in ("h", "q"):
            return choice
        print("  please type h or q")


def main() -> int:
    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()

    torque_on = False
    print(f"Connecting on {cfg.arm.port} ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))

        while True:
            # Limp so the operator can move the arm to the pose by hand.
            follower.bus.disable_torque()
            torque_on = False
            try:
                input("\nMove the arm to the pose by hand, then press Enter to capture (Ctrl+C to quit)... ")
            except (EOFError, KeyboardInterrupt):
                print("\n^C/EOF -- finishing")
                break

            captured = _present_degrees(follower)

            # No-jump hold: command goal=present, THEN enable torque, so it holds where posed.
            follower.send_action({f"{j}.pos": v for j, v in captured.items()})
            follower.bus.enable_torque()
            torque_on = True
            time.sleep(_SETTLE_S)
            pretty = ", ".join(f"{j} {captured[j]:.1f}" for j in ARM_JOINTS)
            print(f"Captured (holding under torque): {pretty}")

            _maybe_save(captured)

            choice = _menu()
            print("Returning to home before releasing torque ...")
            park_home_and_release(follower, home, vel)
            torque_on = False
            print("Home reached; torque off.")
            if choice == "q":
                break
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- returning home")
    finally:
        if follower.is_connected:
            if torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, home, vel)
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
