"""``arm101-arm-torque`` console-script: toggle torque on SO-ARM101 motors 1-5.

Interactive one-shot: prompt for ``e`` (enable) or ``d`` (disable), open the arm
bus, write ``Torque_Enable`` accordingly, release the port. Reads the arm port
from ``data/app_config.yaml`` so callers don't hard-code ``COM20``.

IL-3: motor 6 (gripper) is omitted — physically absent.
IL-4: opens the arm bus briefly and releases it; nothing else may hold the port.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from arm101_hand.config import load_app_config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_APP_CONFIG_PATH = _REPO_ROOT / "data" / "app_config.yaml"

_ARM_MOTOR_IDS: tuple[tuple[str, int], ...] = (
    ("shoulder_pan", 1),
    ("shoulder_lift", 2),
    ("elbow_flex", 3),
    ("wrist_flex", 4),
    ("wrist_roll", 5),
)


def main() -> int:
    # Imported lazily so ``--help`` (future) wouldn't pay the lerobot import cost.
    from lerobot.motors import Motor, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus

    try:
        cfg = load_app_config(_APP_CONFIG_PATH)
    except FileNotFoundError:
        print(f"app_config.yaml missing at {_APP_CONFIG_PATH}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print(f"app_config.yaml failed validation:\n{e}", file=sys.stderr)
        return 1

    motors = {name: Motor(motor_id, "sts3215", MotorNormMode.DEGREES) for name, motor_id in _ARM_MOTOR_IDS}
    motor_ids = [m for _, m in _ARM_MOTOR_IDS]
    bus = FeetechMotorsBus(port=cfg.arm.port, motors=motors)

    print(f"Opening arm bus on {cfg.arm.port} ...")
    bus.connect()
    try:
        while True:
            try:
                choice = input("Torque [e]nable / [d]isable (q to quit): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()  # newline after ^C / EOF
                break
            if choice == "e":
                bus.enable_torque()
                print(f"Torque ENABLED on motors {motor_ids}.")
            elif choice == "d":
                bus.disable_torque()
                print(f"Torque DISABLED on motors {motor_ids}.")
            elif choice == "q":
                break
            elif choice == "":
                # Empty Enter = nudge, not quit. Re-prompt.
                continue
            else:
                print(f"Unrecognized: {choice!r} — type e, d, or q.")
    finally:
        # Release the port without re-toggling torque (we just set the state
        # the user asked for — don't let the bus undo it on disconnect).
        bus.disconnect(disable_torque=False)
        print(f"Bus closed on {cfg.arm.port}. Torque state preserved.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
