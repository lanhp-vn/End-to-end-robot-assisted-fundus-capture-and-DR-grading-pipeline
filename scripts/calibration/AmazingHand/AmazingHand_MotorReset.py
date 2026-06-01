"""
AmazingHand Motor Reset Script (Pre-Horn Installation)

This script is used during Step 2 of the calibration process, BEFORE attaching
the servo horns. It prompts for a finger and drives both of its servos to their
electrical 0° (center) position. The motors will hold this position under torque,
allowing you to attach the servo horns in the correct neutral orientation.
It also resets the 'middle_pos' values for that finger to 0 in the YAML file.
"""
import time
from pathlib import Path

import numpy as np
import yaml

from rustypot import Scs0009PyController


SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")


class InlineDict(dict):
    pass


def _inline_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


yaml.SafeDumper.add_representer(InlineDict, _inline_dict_representer)


def save_config(path, data):
    out = {k: v for k, v in data.items() if k != "fingers"}
    out["fingers"] = {}
    for finger, block in data["fingers"].items():
        out["fingers"][finger] = {
            "servo_1": InlineDict(block["servo_1"]),
            "servo_2": InlineDict(block["servo_2"]),
            "limits": InlineDict(block["limits"]),
        }
    with open(path, "w") as f:
        yaml.safe_dump(out, f, sort_keys=False, default_flow_style=False)


WARNING = """
======================================================================
  MOTOR RESET -- PRE-HORN-INSTALLATION
----------------------------------------------------------------------
  This script drives a servo to its 0 deg (centre) position so the
  servo horn can be attached in the correct neutral orientation.

  BEFORE CONTINUING:
    * The servo horn MUST NOT be attached to the motor shaft yet.
    * The motor must be wired, powered, and addressable on the bus.
    * Nothing should be mechanically coupled to the shaft.

  The motor will hold position under torque while you install the
  horn.  Torque is released automatically after you confirm install.
======================================================================
"""


def prompt_yes():
    answer = input("Proceed with motor reset? Type 'yes' to continue: ").strip().lower()
    return answer == "yes"


def prompt_finger():
    while True:
        raw = input(f"Which finger to reset? ({'/'.join(VALID_FINGERS)}, 'q' to quit): ").strip().lower()
        if raw in ("q", "quit", "exit"):
            return None
        if raw in VALID_FINGERS:
            return raw
        print(f"  invalid -- pick one of {VALID_FINGERS} or 'q'")


def reset_motor(c, servo_id, speed):
    c.write_torque_enable(servo_id, 1)
    c.write_goal_speed(servo_id, speed)
    c.write_goal_position(servo_id, np.deg2rad(0))
    time.sleep(1.0)
    print(f"  motor {servo_id} at 0 deg -- torque ON, holding position")


def main():
    print(WARNING)
    if not prompt_yes():
        print("Aborted.")
        return

    with open(YAML_PATH, "r") as f:
        config = yaml.safe_load(f)

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
    )
    speed = config["speed"]

    touched = set()
    try:
        while True:
            finger = prompt_finger()
            if finger is None:
                break
            block = config["fingers"][finger]
            id1 = block["servo_1"]["id"]
            id2 = block["servo_2"]["id"]
            print(f"\n--- {finger} finger: servo_1={id1}, servo_2={id2} ---")
            reset_motor(c, id1, speed)
            touched.add(id1)
            reset_motor(c, id2, speed)
            touched.add(id2)
            block["servo_1"]["middle_pos"] = 0
            block["servo_2"]["middle_pos"] = 0
            save_config(YAML_PATH, config)
            print(f"  {finger}.middle_pos -> 0, YAML saved")
    except KeyboardInterrupt:
        print("\n^C -- exiting")
    finally:
        for sid in touched:
            try:
                c.write_torque_enable(sid, 0)
            except Exception:
                pass


if __name__ == "__main__":
    main()
