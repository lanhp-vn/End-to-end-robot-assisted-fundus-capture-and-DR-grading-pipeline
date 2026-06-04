"""Pretty-print the SO-ARM101 follower calibration; optionally compare to live position.

Default mode reads only ``so101_follower.json`` -- no bus, no torque. Prints each joint's
id / homing_offset / range_min / range_max plus the computed degree span and midpoint.

``--live`` additionally opens the bus read-only (``connect(calibrate=False)``, torque stays
off) and prints each joint's present position in degrees vs the calibrated mid. It does NOT
push calibration to the motors, so the live degrees assume the motors still hold the
committed EEPROM calibration from the last ``arm101-calibrate-follower`` run.

Usage:
  uv run python scripts/calibration/so_arm101/show_calib.py
  uv run python scripts/calibration/so_arm101/show_calib.py --live

IL-5: this script never writes so101_follower.json.
"""

from __future__ import annotations

import argparse
import sys

from _common import CALIB_PATH, build_follower, load_arm_app_config

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show SO-ARM101 follower calibration.")
    parser.add_argument(
        "--live", action="store_true", help="also read present positions from the bus (read-only)"
    )
    args = parser.parse_args()

    if not CALIB_PATH.is_file():
        print(f"calibration not found at {CALIB_PATH}", file=sys.stderr)
        return 1
    calib = load_arm_calibration(CALIB_PATH)

    print(f"Calibration: {CALIB_PATH}")
    header = f"{'joint':<14}{'id':>3}{'homing':>9}{'min':>7}{'max':>7}{'span_deg':>10}{'mid_steps':>11}"
    print(header)
    print("-" * len(header))
    for joint in ARM_JOINTS:
        c = calib[joint]
        print(
            f"{joint:<14}{c.id:>3}{c.homing_offset:>9}{c.range_min:>7}{c.range_max:>7}"
            f"{degree_span(c.range_min, c.range_max):>10.2f}{midpoint_steps(c.range_min, c.range_max):>11.1f}"
        )

    if not args.live:
        return 0

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)
    print(f"\nConnecting read-only on {cfg.arm.port} (torque stays off) ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        obs = follower.get_observation()  # {f"{joint}.pos": degrees}
        print(f"\n{'joint':<14}{'present_deg':>12}{'mid_deg':>10}")
        print("-" * 36)
        for joint in ARM_JOINTS:
            present = obs.get(f"{joint}.pos", float("nan"))
            # In DEGREES mode the calibrated mid is, by definition, 0 deg.
            print(f"{joint:<14}{present:>12.2f}{0.0:>10.2f}")
    finally:
        if follower.is_connected:
            follower.disconnect()
        print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
