"""Tests for the seeded package arm_config.yaml data file."""

from __future__ import annotations

from pathlib import Path

from arm101_hand.config import load_arm_config

_PACKAGE_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "arm_config.yaml"

_EXPECTED_JOINTS = {"shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"}


def test_seeded_yaml_loads_clean() -> None:
    cfg = load_arm_config(_PACKAGE_DATA)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert "home" in cfg.poses, "seeded YAML has the home pose"


def test_home_pose_has_all_five_joints() -> None:
    cfg = load_arm_config(_PACKAGE_DATA)
    assert set(cfg.poses["home"].as_dict()) == _EXPECTED_JOINTS, "home pose defines all five joints"


def test_connection_port_is_com20() -> None:
    cfg = load_arm_config(_PACKAGE_DATA)
    assert cfg.connection.port == "COM20", "arm port matches BOM"
