"""Interactive keyboard jog for the SO-ARM101 follower (manual per-motor positioning).

Select a joint (1-5) and nudge it in degrees (clamped to its calibrated range). Toggle
torque to hand-pose the arm, home a single joint, or save the current pose to
src/arm101_hand/data/arm_config.yaml (drivable later by set_pose.py).

Controls (torque ON to move):
  1..5          select joint (shoulder_pan shoulder_lift elbow_flex wrist_flex wrist_roll)
  Up / Down     jog active joint + / - step (deg), clamped to calibrated range
  [ / ]         shrink / grow jog step (jog_step_min..jog_step_max from arm_config.yaml)
  h             home active joint to its default-home value (arm_config.yaml poses.home)
  t             toggle torque (off = hand-pose by hand; on = resync + hold)
  s             save current pose to src/arm101_hand/data/arm_config.yaml (prompts for a name)
  q / Ctrl+C    on quit, prompts: 'h' to return home first, or Enter to release torque in
                place and exit (never auto-homes; if torque is OFF, disconnects in place)

Windows-only: uses msvcrt.getwch for raw key reads. Pure jog logic is in
arm101_hand.robots.arm_jog (unit-tested); this file only does I/O.

IL-3: motors 1-5. IL-4: single bus owner; torque released on exit. IL-5: never writes
so101_follower.json (reads it for the clamp range).
"""

from __future__ import annotations

import msvcrt
import sys

from _common import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

from arm101_hand.config import ArmPose, load_arm_config, save_arm_config
from arm101_hand.robots.arm_jog import (
    ARM_JOINTS,
    apply_action,
    format_status,
    initial_state,
    key_to_action,
)
from arm101_hand.robots.calibration_summary import degree_bounds, load_arm_calibration


def read_key() -> str:
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _hold_at(follower, cursors: dict[str, float], vel: int) -> None:
    """Set goal=cursors at gentle velocity, then enable torque (no-jump hold)."""
    follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
    follower.send_action({f"{j}.pos": cursors[j] for j in ARM_JOINTS})
    follower.bus.enable_torque()


def _save_pose(follower) -> None:
    """Prompt for a name and save current LIVE present pose to arm_config.yaml."""
    try:
        name = input("\n  save pose as (name, blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        print("  save cancelled")
        return
    # Load-modify-save: preserve connection/safety/tuning, only update poses.
    from arm101_hand.config import ArmConfig

    config = load_arm_config(ARM_CONFIG_PATH) if ARM_CONFIG_PATH.is_file() else ArmConfig()
    config.poses[name] = ArmPose(**_present_degrees(follower))
    save_arm_config(ARM_CONFIG_PATH, config)
    print(f"  saved '{name}' -> {ARM_CONFIG_PATH}")


def main() -> int:
    cfg = load_arm_app_config()
    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)
    bounds = {j: degree_bounds(calib[j].range_min, calib[j].range_max) for j in ARM_JOINTS}
    home = load_home_degrees()

    follower = build_follower(cfg, use_degrees=True)
    vel = gentle_velocity(cfg)

    load_warn: int = cfg.tuning.load_warn

    print(__doc__)
    print(f"Connecting on {cfg.connection.port} ...")
    state = None
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        cursors = _present_degrees(follower)
        _hold_at(follower, cursors, vel)
        state = initial_state(
            cursors,
            home,
            step_min=cfg.tuning.jog_step_min,
            step_max=cfg.tuning.jog_step_max,
            step_default=cfg.tuning.jog_step_default,
            step_increment=cfg.tuning.jog_step_increment,
        )
        print(format_status(state, follower.bus.sync_read("Present_Load")))

        while True:
            action = key_to_action(read_key())
            state, effect = apply_action(state, action, bounds)
            if effect == "quit":
                break
            if effect == "toggle_torque":
                if state.torque_on:
                    state.cursors = _present_degrees(follower)
                    _hold_at(follower, state.cursors, vel)
                    print("  torque ON (holding current pose)")
                else:
                    follower.bus.disable_torque()
                    print("  torque OFF -- hand-pose the arm; press t to re-hold")
            elif effect == "save":
                _save_pose(follower)
            elif effect == "move":
                follower.send_action({f"{state.active}.pos": state.cursors[state.active]})
            elif action in ("jog_up", "jog_down", "home_active") and not state.torque_on:
                print("  (torque OFF -- press t to enable before jogging)")

            loads = follower.bus.sync_read("Present_Load") if state.torque_on else {}
            print(format_status(state, loads))
            high = [j for j, v in loads.items() if abs(v) >= load_warn]
            if high:
                print(f"  WARNING: high load on {high}")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        # Never auto-home on quit. confirm_and_release offers 'h' (drive home first) or
        # Enter (release in place), then always releases torque.
        if follower.is_connected:
            confirm_and_release(
                follower, bool(state is not None and state.torque_on), home, vel, tuning=cfg.tuning
            )
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
