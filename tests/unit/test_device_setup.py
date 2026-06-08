# tests/unit/test_device_setup.py
from arm101_hand.scripts import device_setup


def test_calib_path_points_at_so_arm101_json():
    assert device_setup.CALIB_PATH.name == "so101_follower.json"
    assert device_setup.CALIB_PATH.parent.name == "so_arm101"


def test_arm_config_path_is_package_data():
    assert device_setup.ARM_CONFIG_PATH.parts[-3:] == ("arm101_hand", "data", "arm_config.yaml")


def test_exports_builders():
    for name in ("build_raw_bus", "build_follower", "load_arm_app_config", "FOLLOWER_ID"):
        assert hasattr(device_setup, name)
