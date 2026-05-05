"""
AmazingHand Full-Hand Test Script

This script runs a complete sequence of movements for the 4-finger AmazingHand
(right hand configuration). It cycles through:
1. Closing all fingers into a fist
2. Opening all fingers
3. Isolating and moving the Index finger
4. Isolating and moving the Middle finger
5. Isolating and moving the Ring finger
6. Isolating and moving the Thumb
7. Returning to the neutral (initial) pose

It reads calibration values from 'AmazingHand_calib_values.yaml' and is intended
to be the final end-to-end sanity check that proves the calibration is correct.
"""
import time
from pathlib import Path

import numpy as np
import yaml

from rustypot import Scs0009PyController


SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

# Demo-specific speeds, independent of YAML 'speed' (which is the calibration speed).
MaxSpeed = 7
CloseSpeed = 3


with open(YAML_PATH, "r") as f:
    _config = yaml.safe_load(f)

_FINGERS = _config["fingers"]

c = Scs0009PyController(
    serial_port=_config["com_port"],
    baudrate=_config["baudrate"],
    timeout=_config["timeout"],
)


def main():
    for finger in _FINGERS.values():
        c.write_torque_enable(finger["servo_1"]["id"], 1)
        c.write_torque_enable(finger["servo_2"]["id"], 1)

    print("Running AmazingHand full-hand demo (right hand, one cycle)...")
    try:
        CloseHand()
        time.sleep(2)

        OpenHand()
        time.sleep(1)

        IndexOnly()
        time.sleep(1.5)

        MiddleOnly()
        time.sleep(1.5)

        RingOnly()
        time.sleep(1.5)

        ThumbOnly()
        time.sleep(1.5)

        InitialPose()
        time.sleep(1)
        print("Cycle complete -- holding middle (initial) pose under torque.")
        input("Press Enter to release torque and exit... ")
    except KeyboardInterrupt:
        print("\n^C -- aborting")
    finally:
        for finger in _FINGERS.values():
            for servo_key in ("servo_1", "servo_2"):
                try:
                    c.write_torque_enable(finger[servo_key]["id"], 0)
                except Exception:
                    pass


# ---------- poses (right hand) ----------

def InitialPose():
    # Matches FingerTest's open_finger: (mp - 30, mp + 30) on the two servos.
    # This is the visually-neutral / extended pose, not the raw (mp, mp) point.
    Move_Index(-30, 30, MaxSpeed)
    Move_Middle(-30, 30, MaxSpeed)
    Move_Ring(-30, 30, MaxSpeed)
    Move_Thumb(-30, 30, MaxSpeed)


def OpenHand():
    Move_Index(-35, 35, MaxSpeed)
    Move_Middle(-35, 35, MaxSpeed)
    Move_Ring(-35, 35, MaxSpeed)
    Move_Thumb(-35, 35, MaxSpeed)


def CloseHand():
    Move_Index(90, -90, CloseSpeed)
    Move_Middle(90, -90, CloseSpeed)
    Move_Ring(90, -90, CloseSpeed)
    Move_Thumb(90, -90, CloseSpeed + 1)


def IndexOnly():
    Move_Index(-40, 40, MaxSpeed)
    Move_Middle(90, -90, MaxSpeed)
    Move_Ring(90, -90, MaxSpeed)
    Move_Thumb(90, -90, MaxSpeed)
    time.sleep(0.6)
    Move_Index(-10, 70, MaxSpeed)
    time.sleep(0.4)
    Move_Index(-70, 10, MaxSpeed)
    time.sleep(0.4)
    Move_Index(-40, 40, MaxSpeed)


def MiddleOnly():
    Move_Index(90, -90, MaxSpeed)
    Move_Middle(-40, 40, MaxSpeed)
    Move_Ring(90, -90, MaxSpeed)
    Move_Thumb(90, -90, MaxSpeed)
    time.sleep(0.6)
    Move_Middle(-10, 70, MaxSpeed)
    time.sleep(0.4)
    Move_Middle(-70, 10, MaxSpeed)
    time.sleep(0.4)
    Move_Middle(-40, 40, MaxSpeed)


def RingOnly():
    Move_Index(90, -90, MaxSpeed)
    Move_Middle(90, -90, MaxSpeed)
    Move_Ring(-40, 40, MaxSpeed)
    Move_Thumb(90, -90, MaxSpeed)
    time.sleep(0.6)
    Move_Ring(-10, 70, MaxSpeed)
    time.sleep(0.4)
    Move_Ring(-70, 10, MaxSpeed)
    time.sleep(0.4)
    Move_Ring(-40, 40, MaxSpeed)


def ThumbOnly():
    Move_Index(90, -90, MaxSpeed)
    Move_Middle(90, -90, MaxSpeed)
    Move_Ring(90, -90, MaxSpeed)
    Move_Thumb(-40, 40, MaxSpeed)
    time.sleep(0.6)
    Move_Thumb(-10, 70, MaxSpeed)
    time.sleep(0.4)
    Move_Thumb(-70, 10, MaxSpeed)
    time.sleep(0.4)
    Move_Thumb(-40, 40, MaxSpeed)


# ---------- per-finger motion helpers ----------

def Move_Index(Angle_1, Angle_2, Speed):
    _move("index", Angle_1, Angle_2, Speed)


def Move_Middle(Angle_1, Angle_2, Speed):
    _move("middle", Angle_1, Angle_2, Speed)


def Move_Ring(Angle_1, Angle_2, Speed):
    _move("ring", Angle_1, Angle_2, Speed)


def Move_Thumb(Angle_1, Angle_2, Speed):
    _move("thumb", Angle_1, Angle_2, Speed)


def _move(name, angle_1, angle_2, speed):
    block = _FINGERS[name]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    c.write_goal_speed(id1, speed)
    time.sleep(0.0002)
    c.write_goal_speed(id2, speed)
    time.sleep(0.0002)
    c.write_goal_position(id1, np.deg2rad(mp1 + angle_1))
    c.write_goal_position(id2, np.deg2rad(mp2 + angle_2))
    time.sleep(0.005)


if __name__ == "__main__":
    main()