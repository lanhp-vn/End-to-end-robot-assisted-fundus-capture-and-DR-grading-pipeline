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
    schema_version: int = 1
    enabled: bool = Field(default=True, description="open the preview window when the demo runs")
    camera_index: int = Field(default=0, ge=0, description="USB camera index (the arm-mounted cam)")
    backend: Literal["auto", "dshow"] = Field(
        default="auto", description="cv2 capture backend; 'auto' = platform default (MSMF on Windows)"
    )
    window_title: str = Field(default="Arm cam (Optomed view)", description="preview window title")
    record_dir: str = Field(
        default="media_outputs/camera_recordings", description="repo-relative clip folder"
    )
    fps: float | None = Field(
        default=None, gt=0, description="force writer fps; null = camera-reported (fallback 20)"
    )


def load_system_camera_config(path: Path) -> SystemCameraConfig:
    """Parse and validate ``system_camera_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SystemCameraConfig.model_validate(raw)
