from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import HandConfig, load_hand_config, save_hand_config


def test_defaults():
    cfg = HandConfig()
    assert cfg.connection.port == "COM18"
    assert cfg.connection.baudrate == 1_000_000
    assert cfg.tuning.speeds.open == 5


def test_pose_per_finger_round_trip(tmp_path: Path):
    cfg = HandConfig(
        poses={"grab": {"index": [70, 2], "middle": [59, -19], "ring": [64, -14], "thumb": [59, -59]}}
    )  # type: ignore[arg-type]
    out = tmp_path / "hand_config.yaml"
    save_hand_config(out, cfg)
    reloaded = load_hand_config(out)
    assert reloaded.poses["grab"].by_finger()["index"] == [70, 2]
    assert reloaded.poses["grab"].thumb == [59, -59]


def test_pose_pair_must_be_length_2(tmp_path: Path):
    out = tmp_path / "bad.yaml"
    out.write_text(
        "poses:\n  x:\n    index: [1]\n    middle: [1,2]\n    ring: [1,2]\n    thumb: [1,2]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_hand_config(out)
