# tests/unit/test_finger_io.py
from types import SimpleNamespace

from arm101_hand.config.motor_ids import FINGER_SERVO_IDS
from arm101_hand.hand.finger_io import drive_finger, read_finger


class _FakeController:
    def __init__(self):
        self.goals = {}

    def read_present_position(self, sid):
        return [0.0]  # rustypot returns a single-element list

    def write_torque_enable(self, sid, on):
        pass

    def write_goal_speed(self, sid, sp):
        pass

    def write_goal_position(self, sid, rad):
        self.goals[sid] = rad


def _block():
    lim = SimpleNamespace(base_min=-20, base_max=70, side_min=-60, side_max=60)
    return SimpleNamespace(
        limits=lim, servo_1=SimpleNamespace(middle_pos=512), servo_2=SimpleNamespace(middle_pos=512)
    )


def test_read_finger_returns_int_pair():
    base, side = read_finger(_FakeController(), "index", _block())
    assert isinstance(base, int) and isinstance(side, int)


def test_drive_finger_commands_both_index_servos():
    c = _FakeController()
    drive_finger(
        c, "index", _block(), base=30, side=0, speed=3, tolerance_rad=0.1, timeout_s=0.05, poll_s=0.0
    )
    id1, id2 = FINGER_SERVO_IDS["index"]
    assert id1 in c.goals and id2 in c.goals
