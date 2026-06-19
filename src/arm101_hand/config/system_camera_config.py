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


class SystemCameraConfig(BaseModel):
    """Arm-mounted USB observation camera: live preview window (cv2) + record toggle."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 4
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
    still_width: int | None = Field(
        default=None,
        ge=1,
        description="full-res STILL width in px -- usb_camera_capture briefly switches the open "
        "capture up to this for one SPACE grab, then back to the stream res; null = the camera's max",
    )
    still_height: int | None = Field(
        default=None, ge=1, description="full-res still height in px; null = request the camera's max"
    )


def load_system_camera_config(path: Path) -> SystemCameraConfig:
    """Parse and validate ``system_camera_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SystemCameraConfig.model_validate(raw)
