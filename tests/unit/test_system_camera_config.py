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
    assert cfg.schema_version == 6


def test_focus_defaults():
    # Focus is off by default in the SCHEMA (callers that want autofocus get it for free); the
    # locked manual value lives only in the data yaml. autofocus True => let the cam drive AF;
    # focus None => don't touch CAP_PROP_FOCUS.
    cfg = SystemCameraConfig()
    assert cfg.autofocus is True
    assert cfg.focus is None


def test_focus_round_trips():
    cfg = SystemCameraConfig.model_validate({"autofocus": False, "focus": 600})
    assert cfg.autofocus is False
    assert cfg.focus == 600


def test_negative_focus_rejected():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"focus": -1})


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
    # dshow is REQUIRED: manual focus only drives the VCM on the DSHOW backend (MSMF ignores it).
    assert cfg.backend == "dshow"
    assert cfg.enabled is True
    # Live stream 1600x1200 (UXGA) -> ~44 fps; ROI crop ~490x368 upscales ~1.3x to the 640x480 ref
    # (fps trade); decoupled from the full 12 MP still grab (4000x3000), MJPG.
    assert (cfg.width, cfg.height) == (1600, 1200)
    assert (cfg.still_width, cfg.still_height) == (4000, 3000)
    assert cfg.fourcc == "MJPG"
    # Manual focus locked at the value the focus probe found (autofocus off so it never breathes).
    assert cfg.autofocus is False
    assert cfg.focus == 600
    at = cfg.auto_trigger
    assert at.left_arc.w == 70 and at.right_arc.w == 70
    assert len(at.red_bands) == 2 and len(at.green_bands) == 1
    assert at.stable_seconds == 2.0  # bench-tuned in the data YAML (schema default stays 1.0)


def test_auto_trigger_defaults():
    at = SystemCameraConfig().auto_trigger
    assert at.stable_seconds == 1.0
    assert at.cooldown_seconds == 3.0
    assert at.detect_interval_s == 0.2
    assert at.require_clear_between is True
    assert at.require_no_red is False
    assert at.coverage_threshold == 0.04
    assert len(at.red_bands) == 2  # red wraps hue 0/180
    assert len(at.green_bands) == 1
    assert (at.left_arc.ref_w, at.left_arc.ref_h) == (640, 480)


def test_auto_trigger_coverage_threshold_bounds():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"coverage_threshold": 1.5}})


def test_auto_trigger_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        SystemCameraConfig.model_validate({"auto_trigger": {"bogus": 1}})


def test_arcregion_is_roibox_alias():
    from arm101_hand.config.system_camera_config import ArcRegion, RoiBox

    assert ArcRegion is RoiBox


def test_screen_roi_default_matches_legacy_constant():
    cfg = SystemCameraConfig()
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h) == (60, 75, 196, 147)
    assert (sr.ref_w, sr.ref_h) == (640, 480)


def test_screen_roi_round_trips_at_calibration_resolution():
    cfg = SystemCameraConfig.model_validate(
        {"screen_roi": {"x": 150, "y": 188, "w": 490, "h": 368, "ref_w": 1600, "ref_h": 1200}}
    )
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h, sr.ref_w, sr.ref_h) == (150, 188, 490, 368, 1600, 1200)


def test_schema_default_version_is_6():
    assert SystemCameraConfig().schema_version == 6


def test_data_yaml_has_screen_roi():
    cfg = load_system_camera_config(_DATA)
    sr = cfg.screen_roi
    assert (sr.x, sr.y, sr.w, sr.h) == (60, 75, 196, 147)
    assert (sr.ref_w, sr.ref_h) == (640, 480)
