from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import ArmConfig, load_arm_config, save_arm_config


def test_defaults_match_seed():
    cfg = ArmConfig()
    assert cfg.connection.port == "COM20"
    assert cfg.safety.temp_warn_c == 50.0
    assert cfg.tuning.park_velocity == 600
    assert cfg.tuning.jog_step_default == 5.0


def test_round_trip_preserves_all_sections(tmp_path: Path):
    cfg = ArmConfig(
        poses={
            "home": {"shoulder_pan": 1, "shoulder_lift": 2, "elbow_flex": 3, "wrist_flex": 4, "wrist_roll": 5}
        }
    )  # type: ignore[arg-type]
    cfg.tuning.load_warn = 555
    out = tmp_path / "arm_config.yaml"
    save_arm_config(out, cfg)
    reloaded = load_arm_config(out)
    assert reloaded.tuning.load_warn == 555
    assert reloaded.poses["home"].as_dict()["wrist_roll"] == 5
    assert reloaded.connection.port == "COM20"


def test_extra_key_rejected(tmp_path: Path):
    out = tmp_path / "bad.yaml"
    out.write_text("schema_version: 1\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_arm_config(out)
