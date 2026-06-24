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
    angle: float = Field(default=0.0, description="deskew rotation in degrees; 0 = axis-aligned crop")


ArcRegion = RoiBox  # back-compat alias: existing imports/configs keep working


class AutoTriggerConfig(BaseModel):
    """Red-only arc auto-trigger: each arc is RED (>= coverage_threshold of pixels at LAB a* >=
    a_star_min) or not. ready/fire is gated by a red -> not-red transition (see
    arm101_hand.system_camera.auto_trigger)."""

    model_config = ConfigDict(extra="forbid")
    left_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=88, y=130, w=56, h=230, ref_w=640, ref_h=480))
    right_arc: RoiBox = Field(default_factory=lambda: RoiBox(x=496, y=130, w=56, h=230, ref_w=640, ref_h=480))
    a_star_min: int = Field(
        default=134,
        ge=128,
        le=255,
        description="LAB a* cutoff: a pixel counts as red at a* >= this (128 = neutral, higher = redder)",
    )
    coverage_threshold: float = Field(
        default=0.01, ge=0.0, le=1.0, description="arc is RED at >= this fraction of pixels above a_star_min"
    )
    morph_kernel: int = Field(default=3, ge=1)
    stable_seconds: float = Field(default=1.0, gt=0.0)
    cooldown_seconds: float = Field(default=3.0, ge=0.0)
    detect_interval_s: float = Field(default=0.2, gt=0.0)


class SystemCameraConfig(BaseModel):
    """Arm-mounted USB observation camera: live preview window (cv2) + record toggle."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 8
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
