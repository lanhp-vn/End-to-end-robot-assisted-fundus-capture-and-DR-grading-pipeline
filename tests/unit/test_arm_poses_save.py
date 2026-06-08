"""Round-trip tests for save_arm_config / load-modify-save (no bus)."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import ArmConfig, ArmPose, load_arm_config, save_arm_config

_POSE = {
    "shoulder_pan": 1.0,
    "shoulder_lift": -2.0,
    "elbow_flex": 3.0,
    "wrist_flex": 4.0,
    "wrist_roll": 5.0,
}


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "arm_config.yaml"
    cfg = ArmConfig(poses={"a": ArmPose(**_POSE)})
    save_arm_config(path, cfg)
    loaded = load_arm_config(path)
    assert loaded.poses["a"].as_dict() == _POSE


def test_save_upserts_into_existing(tmp_path: Path) -> None:
    path = tmp_path / "arm_config.yaml"
    cfg = ArmConfig(poses={"a": ArmPose(**_POSE)})
    save_arm_config(path, cfg)
    # Load-modify-save: add a second pose
    cfg2 = load_arm_config(path)
    cfg2.poses["b"] = ArmPose(**{**_POSE, "wrist_roll": 9.0})
    save_arm_config(path, cfg2)
    loaded = load_arm_config(path)
    assert set(loaded.poses) == {"a", "b"}
    assert loaded.poses["b"].as_dict()["wrist_roll"] == 9.0


def test_save_preserves_connection_and_tuning(tmp_path: Path) -> None:
    path = tmp_path / "arm_config.yaml"
    cfg = ArmConfig(poses={"a": ArmPose(**_POSE)})
    cfg.tuning.load_warn = 333
    save_arm_config(path, cfg)
    loaded = load_arm_config(path)
    assert loaded.tuning.load_warn == 333
    assert loaded.connection.port == "COM20"


def test_save_leaves_no_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "arm_config.yaml"
    cfg = ArmConfig(poses={"a": ArmPose(**_POSE)})
    save_arm_config(path, cfg)
    assert path.is_file()
    assert not (tmp_path / "arm_config.yaml.tmp").exists()
