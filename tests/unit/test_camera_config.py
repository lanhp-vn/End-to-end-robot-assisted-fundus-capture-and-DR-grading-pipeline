from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import CameraConfig, load_camera_config

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "camera_config.yaml"


def test_defaults():
    cfg = CameraConfig()
    assert cfg.connection.message_port == 8000
    assert cfg.connection.discovery_port == 3000
    assert cfg.capture.hold_seconds == 3.0
    assert cfg.capture.dcim_root == "\\DCIM"
    assert cfg.capture.fundus_dir == "fundus_images"


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        CameraConfig.model_validate({"connection": {"bogus": 1}})


def test_data_yaml_loads():
    cfg = load_camera_config(_DATA)
    assert cfg.connection.message_port == 8000
    assert cfg.capture.stable_polls >= 1
