"""Round-trip tests for save_arm_poses (no bus)."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses

_POSE = {
    "shoulder_pan": 1.0,
    "shoulder_lift": -2.0,
    "elbow_flex": 3.0,
    "wrist_flex": 4.0,
    "wrist_roll": 5.0,
}


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "arm_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    loaded = load_arm_poses(path)
    assert loaded.poses["a"].as_dict() == _POSE


def test_save_upserts_into_existing(tmp_path: Path) -> None:
    path = tmp_path / "arm_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    cfg = load_arm_poses(path)
    cfg.poses["b"] = ArmPose(**{**_POSE, "wrist_roll": 9.0})
    save_arm_poses(path, cfg)
    loaded = load_arm_poses(path)
    assert set(loaded.poses) == {"a", "b"}
    assert loaded.poses["b"].as_dict()["wrist_roll"] == 9.0


def test_save_leaves_no_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "arm_poses.yaml"
    save_arm_poses(path, ArmPoseConfig(poses={"a": ArmPose(**_POSE)}))
    assert path.is_file()
    assert not (tmp_path / "arm_poses.yaml.tmp").exists()
