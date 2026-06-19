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


def test_resolution_fourcc_defaults():
    # width/height = the live stream resolution; still_width/still_height = the full-res grab.
    # All default to None (= request the camera's max at open time); format defaults to MJPG
    # (the only way UVC cams expose high-res modes).
    cfg = SystemCameraConfig()
    assert cfg.width is None
    assert cfg.height is None
    assert cfg.still_width is None
    assert cfg.still_height is None
    assert cfg.fourcc == "MJPG"
    assert cfg.schema_version == 3


def test_still_resolution_round_trips():
    cfg = SystemCameraConfig.model_validate({"still_width": 4000, "still_height": 3000})
    assert (cfg.still_width, cfg.still_height) == (4000, 3000)


def test_non_positive_still_resolution_rejected():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"still_width": 0})
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"still_height": -10})


def test_explicit_resolution_round_trips():
    cfg = SystemCameraConfig.model_validate({"width": 4000, "height": 3000, "fourcc": "YUY2"})
    assert (cfg.width, cfg.height, cfg.fourcc) == (4000, 3000, "YUY2")


def test_none_resolution_allowed():
    cfg = SystemCameraConfig.model_validate({"width": None, "height": None})
    assert cfg.width is None and cfg.height is None


def test_non_positive_resolution_rejected():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"width": 0})
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"height": -1})


def test_fourcc_must_be_four_chars():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"fourcc": "MJP"})
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"fourcc": "MOTION"})


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
    # Smooth live stream (640x480) decoupled from the full 12 MP still grab (4000x3000), MJPG.
    assert (cfg.width, cfg.height) == (640, 480)
    assert (cfg.still_width, cfg.still_height) == (4000, 3000)
    assert cfg.fourcc == "MJPG"
