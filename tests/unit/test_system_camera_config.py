from pathlib import Path

import pytest
from pydantic import ValidationError

from arm101_hand.config import SystemCameraConfig, load_system_camera_config

_DATA = Path(__file__).resolve().parents[2] / "src" / "arm101_hand" / "data" / "system_camera_config.yaml"


def test_defaults():
    cfg = SystemCameraConfig()
    assert cfg.enabled is True
    assert cfg.camera_index == 0
    assert cfg.backend == "auto"
    assert cfg.record_dir == "media_outputs/camera_recordings"
    assert cfg.fps is None


def test_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"bogus": 1})


def test_invalid_backend_rejected():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"backend": "v4l2"})


def test_data_yaml_loads():
    cfg = load_system_camera_config(_DATA)
    assert cfg.camera_index == 1  # the arm cam on this PC
    assert cfg.backend == "auto"
    assert cfg.enabled is True
