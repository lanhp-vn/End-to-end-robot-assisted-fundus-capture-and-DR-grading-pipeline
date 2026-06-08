"""Grab demo: stage the SO-ARM101 into its ``grab`` pose, close the AmazingHand,
hold both under torque, and reverse the whole thing on exit.

Forward sequence (each stage finishes -- position-confirmed for the arm -- before
the next begins):
  1. shoulder_pan -> base_max (its calibrated upper bound)
  2. shoulder_lift + elbow_flex + wrist_flex + wrist_roll -> grab (simultaneous)
  3. shoulder_pan -> grab
  4. hand fingers -> grab

The two devices live on separate buses (arm = 12 V STS3215, hand = 5 V SCS0009)
on different COM ports, so both are held open at once -- no shared rail (IL-1) and
one owner per bus (IL-4).

On exit:
  * plain Enter / Ctrl+C / EOF -> release BOTH in place (no movement); the arm will
    sag from a raised pose -- you are warned at the prompt.
  * 'h' -> run the sequence in REVERSE (4 -> 1): open the hand, then shoulder_pan ->
    base_max, then the four posture joints -> home, then shoulder_pan -> home. The
    arm waits for each stage to finish before the next. Torque on BOTH buses is always
    cut at the end, even if a leg fails partway through.

Poses come from version-controlled config (IL-5; never written here):
  * arm  -> src/arm101_hand/data/arm_config.yaml ``poses['grab']`` / ``poses['home']``
  * hand -> src/arm101_hand/data/hand_config.yaml ``poses['grab']`` (``open`` is built-in)

Usage:
  uv run python scripts/demos/grab_sequence.py
"""

from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

from rustypot import Scs0009PyController

from arm101_hand.config import load_arm_config, load_hand_calibration, load_hand_config
from arm101_hand.hand import resolve_hand_pose_targets
from arm101_hand.hand.protocol import SERVO_SYNC_S
from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    clamp_degrees,
    degree_bounds,
    load_arm_calibration,
)
from arm101_hand.scripts.device_setup import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    confirm_and_release,
    drive_arm_joints,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
)

# scripts/demos/grab_sequence.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"

ARM_POSE = "grab"
HAND_POSE = "grab"
HAND_OPEN_POSE = "open"
PAN_JOINT = "shoulder_pan"
# Everything except the base pan -- moved together in forward step 2 / reverse step R2.
POSTURE_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
_HAND_SETTLE_S = 1.0


def _build_arm_plan() -> tuple[dict[str, float], float]:
    """Return (clamped ``grab`` targets per joint, shoulder_pan base_max in degrees).

    Grab degrees are clamped to each joint's calibrated window (an out-of-range joint is
    warned and skipped). ``base_max`` is shoulder_pan's calibrated upper degree bound.
    """
    poses = load_arm_config(ARM_CONFIG_PATH).poses
    if ARM_POSE not in poses:
        raise SystemExit(f"arm pose {ARM_POSE!r} not in {ARM_CONFIG_PATH} (poses)")
    pose = poses[ARM_POSE].as_dict()
    calib = load_arm_calibration(CALIB_PATH)
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: arm {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped
    if not targets:
        raise SystemExit("ERROR: all arm joints out of range -- nothing to drive")
    pan = calib[PAN_JOINT]
    base_max_deg = degree_bounds(pan.range_min, pan.range_max)[1]
    return targets, base_max_deg


def _drive_hand(c: Scs0009PyController, targets: dict[int, float], speed: int) -> None:
    """Enable torque and command every servo to its wire-radians target at ``speed``."""
    for sid in targets:
        c.write_torque_enable(sid, 1)
    for sid in sorted(targets):
        c.write_goal_speed(sid, speed)
        time.sleep(SERVO_SYNC_S)
    for sid in sorted(targets):
        c.write_goal_position(sid, targets[sid])
        time.sleep(SERVO_SYNC_S)


def main() -> int:
    # Resolve everything before touching hardware -- fail fast, leave no bus open.
    arm_targets, base_max_deg = _build_arm_plan()

    hand_calib = load_hand_calibration(HAND_CALIB_PATH)
    hand_cfg = load_hand_config(HAND_CONFIG_PATH) if HAND_CONFIG_PATH.is_file() else None
    if hand_cfg is None or HAND_POSE not in hand_cfg.poses:
        raise SystemExit(f"hand pose {HAND_POSE!r} not in {HAND_CONFIG_PATH} (poses)")
    hand_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_POSE)
    # Open targets are built-in (limits-derived); used only on the 'h' (reverse) exit.
    hand_open_targets = resolve_hand_pose_targets(hand_calib, hand_cfg, HAND_OPEN_POSE)

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()
    arm_tuning = cfg.tuning
    # The home_* tuning (tolerance/timeout/poll) governs every staged arm move here, not just
    # homing -- the grab stages use the same position-wait, so we reuse it rather than add new keys.
    wait_kw = {
        "tolerance": arm_tuning.home_tolerance_steps,
        "timeout_s": arm_tuning.home_timeout_s,
        "poll_s": arm_tuning.home_poll_s,
    }

    # Per-stage joint subsets (degrees). Built from arm_targets so a skipped joint drops out.
    posture_grab = {j: arm_targets[j] for j in POSTURE_JOINTS if j in arm_targets}
    posture_home = {j: home[j] for j in POSTURE_JOINTS}
    pan_grab = {PAN_JOINT: arm_targets[PAN_JOINT]} if PAN_JOINT in arm_targets else {}
    pan_base_max = {PAN_JOINT: base_max_deg}
    pan_home = {PAN_JOINT: home[PAN_JOINT]}

    hand = Scs0009PyController(
        serial_port=hand_cfg.connection.port,
        baudrate=hand_cfg.connection.baudrate,
        timeout=hand_cfg.connection.timeout,
    )

    def _staged_reverse() -> None:
        """Mirror of the forward grab (steps 4 -> 1), run when the user types 'h' on exit.

        The hand opens first and then holds 'open' under torque while the arm reverses; both
        torques are cut by the caller (confirm_and_release for the arm, the outer finally for
        the hand) after this returns.
        """
        print(f"  [R4] opening hand on {hand_cfg.connection.port} ...")
        _drive_hand(hand, hand_open_targets, hand_cfg.tuning.speeds.open)
        time.sleep(_HAND_SETTLE_S)
        print(f"  [R3] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [R2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> home ...")
        drive_arm_joints(follower, posture_home, vel, **wait_kw)
        print("  [R1] shoulder_pan -> home ...")
        drive_arm_joints(follower, pan_home, vel, **wait_kw)

    arm_torque_on = False
    try:
        # ---- Arm leg: connect, push on-file calibration, enable torque ----
        print(f"Connecting arm on {cfg.connection.port} ...")
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open arm port {cfg.connection.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.enable_torque()
        arm_torque_on = True

        # ---- Forward grab, staged (each stage position-confirmed before the next) ----
        print(f"  [1] shoulder_pan -> base_max ({base_max_deg:.1f} deg) ...")
        drive_arm_joints(follower, pan_base_max, vel, **wait_kw)
        print("  [2] shoulder_lift/elbow_flex/wrist_flex/wrist_roll -> grab ...")
        drive_arm_joints(follower, posture_grab, vel, **wait_kw)
        if not pan_grab:
            print("  WARNING: shoulder_pan out of range -- [3] is a no-op; pan stays at base_max")
        print("  [3] shoulder_pan -> grab ...")
        drive_arm_joints(follower, pan_grab, vel, **wait_kw)
        print(f"Arm holding '{ARM_POSE}': {arm_targets}")

        # ---- Hand leg: close into grab ----
        print(f"  [4] driving hand on {hand_cfg.connection.port} to '{HAND_POSE}' ...")
        _drive_hand(hand, hand_targets, hand_cfg.tuning.speed)
        time.sleep(_HAND_SETTLE_S)
        print(f"Arm + hand holding '{ARM_POSE}'/'{HAND_POSE}' under torque.")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- exiting")
    finally:
        # Release the arm first: confirm_and_release prompts 'h'/Enter (both stay gripped
        # during the prompt). 'h' runs _staged_reverse (hand opens first, then the arm
        # reverses stage by stage); plain Enter releases in place. The hand release lives
        # in an inner finally so it ALWAYS runs -- neither bus may be left torqued (IL-4).
        try:
            if follower.is_connected:
                confirm_and_release(
                    follower,
                    arm_torque_on,
                    home,
                    vel,
                    offer_home=True,
                    home_routine=_staged_reverse,
                    tuning=arm_tuning,
                )
                with contextlib.suppress(Exception):
                    follower.disconnect()
                print("Arm bus closed.")
        finally:
            print("Releasing hand torque.")
            for sid in hand_targets:
                with contextlib.suppress(Exception):
                    hand.write_torque_enable(sid, 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
