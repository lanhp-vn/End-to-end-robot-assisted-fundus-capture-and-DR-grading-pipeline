# tests/unit/test_device_setup.py
from dataclasses import dataclass

from arm101_hand.scripts import device_setup


def test_calib_path_points_at_so_arm101_json():
    assert device_setup.CALIB_PATH.name == "so101_follower.json"
    assert device_setup.CALIB_PATH.parent.name == "so_arm101"


def test_arm_config_path_is_package_data():
    assert device_setup.ARM_CONFIG_PATH.parts[-3:] == ("arm101_hand", "data", "arm_config.yaml")


def test_exports_builders():
    for name in ("build_raw_bus", "build_follower", "load_arm_app_config", "FOLLOWER_ID"):
        assert hasattr(device_setup, name)


@dataclass
class _Cal:
    range_min: int
    range_max: int


class _FakeBus:
    """Records sync_write calls; a Goal_Position write instantly 'moves' present pos to goal."""

    def __init__(self, present: dict[str, int]):
        self.present = dict(present)
        self.writes: list[tuple[str, dict, object]] = []

    def sync_write(self, reg, values, normalize=True):
        self.writes.append((reg, dict(values), normalize))
        if reg == "Goal_Position":
            self.present.update(values)

    def sync_read(self, reg, normalize=True):
        assert reg == "Present_Position"
        return dict(self.present)

    def disable_torque(self):
        self.writes.append(("disable_torque", {}, None))


class _FakeFollower:
    def __init__(self, calibration, bus, is_connected=True):
        self.calibration = calibration
        self.bus = bus
        self.is_connected = is_connected


def _full_calib():
    return {j: _Cal(0, 4095) for j in device_setup.ARM_JOINTS}


def test_drive_arm_joints_writes_only_subset_and_converts_degrees():
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    device_setup.drive_arm_joints(
        follower, {"shoulder_pan": 90.0}, vel=600, tolerance=25, timeout_s=1.0, poll_s=0.0
    )
    goal_writes = [w for w in bus.writes if w[0] == "Goal_Position"]
    assert len(goal_writes) == 1
    _reg, values, normalize = goal_writes[0]
    assert normalize is False
    assert set(values) == {"shoulder_pan"}, "only the requested joint is commanded"
    mid = (0 + 4095) / 2
    expected = int(round(mid + 90.0 * (4096 - 1) / 360))
    assert values["shoulder_pan"] == expected
    vel_writes = [w for w in bus.writes if w[0] == "Goal_Velocity"]
    assert vel_writes and set(vel_writes[0][1]) == {"shoulder_pan"}, "velocity scoped to subset"


def test_drive_arm_joints_empty_is_noop():
    bus = _FakeBus({})
    follower = _FakeFollower({}, bus)
    device_setup.drive_arm_joints(follower, {}, vel=600)
    assert bus.writes == []


def test_drive_home_commands_all_five_joints():
    bus = _FakeBus(dict.fromkeys(device_setup.ARM_JOINTS, 0))
    follower = _FakeFollower(_full_calib(), bus)
    home = dict.fromkeys(device_setup.ARM_JOINTS, 0.0)
    device_setup._drive_home(follower, home, vel=600, timeout_s=1.0, poll_s=0.0)
    goal = [w for w in bus.writes if w[0] == "Goal_Position"][0]
    assert set(goal[1]) == set(device_setup.ARM_JOINTS), "_drive_home still drives all joints"
