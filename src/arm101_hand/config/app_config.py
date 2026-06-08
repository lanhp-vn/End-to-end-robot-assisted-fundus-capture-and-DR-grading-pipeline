"""Pydantic schema for ``data/app_config.yaml`` (runtime safety + connection settings).

Defaults match the seeded YAML so a ``AppConfig()`` instantiation produces the
same shape the scripts load at startup. Loaded once per run; never re-read
mid-run (per ``02-code-style-python.md`` §6).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ArmBus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: str = "COM20"


class SafePark(BaseModel):
    """Gentle velocity for safe home moves (read by ``device_setup.gentle_velocity``)."""

    model_config = ConfigDict(extra="forbid")

    park_velocity_arm: int = Field(default=600, ge=0, le=4095)


class Safety(BaseModel):
    """Diagnostics warn thresholds (read by ``scripts/diagnostics/scan.py``) + safe-park velocity."""

    model_config = ConfigDict(extra="forbid")

    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0)
    voltage_warn_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    safe_park: SafePark = Field(default_factory=SafePark)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    arm: ArmBus = Field(default_factory=ArmBus)
    safety: Safety = Field(default_factory=Safety)


def load_app_config(path: Path) -> AppConfig:
    """Parse and validate ``data/app_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
