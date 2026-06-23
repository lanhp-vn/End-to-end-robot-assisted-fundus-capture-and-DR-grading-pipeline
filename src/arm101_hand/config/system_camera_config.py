"""Pydantic schema for ``src/arm101_hand/data/system_camera_config.yaml`` (primitive layer).

Operator config for the arm-mounted USB observation camera (the host webcam that films the
Optomed Aurora's screen) -- live cv2 preview window + recording. Hand-editable; never written
by runtime code (IL-5). Distinct from the Aurora fundus camera, configured in ``fundus_config.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class HsvBand(BaseModel):
    """An inclusive OpenCV HSV threshold band (8-bit: H 0-180, S/V 0-255)."""

    model_config = ConfigDict(extra="forbid")
    h_lo: int = Field(ge=0, le=180)
    s_lo: int = Field(ge=0, le=255)
    v_lo: int = Field(ge=0, le=255)
    h_hi: int = Field(ge=0, le=180)
    s_hi: int = Field(ge=0, le=255)
    v_hi: int = Field(ge=0, le=255)


class RoiBox(BaseModel):
    """A pixel rectangle measured against a reference frame size (mirrors system_camera.Roi).

    Used for the screen ROI (``SystemCameraConfig.screen_roi``) and the auto-trigger arc regions.
    """

    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1)
    h: int = Field(ge=1)
    ref_w: int = Field(default=640, ge=1)
    ref_h: int = Field(default=480, ge=1)


ArcRegion = RoiBox  # back-compat alias: existing imports/configs keep working


class AutoTriggerConfig(BaseModel):
    """Arc-based auto-trigger detection + lifecycle tuning (grab_auto_trigger_analysis demo)."""

    model_config = ConfigDict(extra="forbid")
    left_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=60, y=110, w=70, h=190))
    right_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=420, y=110, w=70, h=190))
    red_bands: list[HsvBand] = Field(
        min_length=1,  # >=1 band required: classification needs a colour to match (no degenerate config)
        default_factory=lambda: [
            HsvBand(h_lo=0, s_lo=80, v_lo=80, h_hi=10, s_hi=255, v_hi=255),
            HsvBand(h_lo=170, s_lo=80, v_lo=80, h_hi=180, s_hi=255, v_hi=255),
        ],
    )
    green_bands: list[HsvBand] = Field(
        min_length=1,  # >=1 band required (see red_bands)
        default_factory=lambda: [HsvBand(h_lo=40, s_lo=40, v_lo=60, h_hi=90, s_hi=255, v_hi=255)],
    )
    coverage_threshold: float = Field(default=0.04, ge=0.0, le=1.0)
    morph_kernel: int = Field(default=3, ge=1)
    stable_seconds: float = Field(default=1.0, gt=0.0)
    cooldown_seconds: float = Field(default=3.0, ge=0.0)
    detect_interval_s: float = Field(default=0.2, gt=0.0)
    require_clear_between: bool = True
    require_no_red: bool = False


class SystemCameraConfig(BaseModel):
    """Arm-mounted USB observation camera: live preview window (cv2) + record toggle."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 6
    enabled: bool = Field(default=True, description="open the preview window when the demo runs")
    camera_index: int = Field(default=0, ge=0, description="USB camera index (the arm-mounted cam)")
    backend: Literal["auto", "dshow"] = Field(
        default="auto", description="cv2 capture backend; 'auto' = platform default (MSMF on Windows)"
    )
    autofocus: bool = Field(
        default=True,
        description="let the camera drive autofocus; set false to lock the manual 'focus' value. "
        "Manual focus only takes effect on the 'dshow' backend (MSMF ignores it)",
    )
    focus: int | None = Field(
        default=None,
        ge=0,
        description="manual lens position (UVC VCM range ~0..1023); null = don't touch the focus "
        "position. Found with usb_camera_focus_probe.py; needs autofocus=false + backend=dshow",
    )
    screen_roi: RoiBox = Field(
        default_factory=lambda: RoiBox(x=60, y=75, w=196, h=147),
        description="ROI that zooms preview/recording/detection onto just the Aurora screen. "
        "ref_w/ref_h = the resolution it was calibrated at (runtime rescale is then identity). "
        "Re-derived by scripts/calibration/system_camera/calibrate_view.py. Default = legacy "
        "AURORA_SCREEN_ROI",
    )
    auto_trigger: AutoTriggerConfig = Field(default_factory=AutoTriggerConfig)
    window_title: str = Field(default="Arm cam (Optomed view)", description="preview window title")
    record_dir: str = Field(
        default="media_outputs/camera_recordings", description="repo-relative clip folder"
    )
    fps: float | None = Field(
        default=None, gt=0, description="force writer fps; null = camera-reported (fallback 20)"
    )
    fourcc: str = Field(
        default="MJPG",
        min_length=4,
        max_length=4,
        description="capture pixel format (4-char FOURCC); MJPG is the only way UVC cams expose "
        "their high-res modes -- YUY2 is uncompressed and capped low",
    )
    width: int | None = Field(
        default=None,
        ge=1,
        description="LIVE STREAM width in px (preview + recording); keep it low for a smooth feed. "
        "null = request the camera's max (the driver clamps a large request to its largest mode)",
    )
    height: int | None = Field(
        default=None, ge=1, description="live stream height in px; null = request the camera's max"
    )


def load_system_camera_config(path: Path) -> SystemCameraConfig:
    """Parse and validate ``system_camera_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SystemCameraConfig.model_validate(raw)
