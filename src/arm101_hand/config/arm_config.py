"""Pydantic schema for ``src/arm101_hand/data/arm_config.yaml``.

The single operator tuning surface for the SO-ARM101: connection, diagnostics
safety thresholds, motion tuning knobs, and named poses (degrees per joint,
lerobot ``use_degrees`` frame). Measured calibration stays in so101_follower.json.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ArmConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: str = Field(default="COM20", description="SO-ARM101 12V bus COM port")


class ArmSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temp_warn_c: float = Field(default=50.0, ge=0.0, le=100.0, description="servo coil temp warn (°C)")
    voltage_warn_pct: float = Field(
        default=5.0, ge=0.0, le=100.0, description="warn when bus V deviates this % from 12V nominal"
    )


class ArmTuning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    park_velocity: int = Field(
        default=600, ge=0, le=4095, description="gentle home-move Goal_Velocity (raw units)"
    )
    jog_step_min: float = Field(default=1.0, gt=0, description="min jog step (deg)")
    jog_step_max: float = Field(default=15.0, gt=0, description="max jog step (deg)")
    jog_step_default: float = Field(default=5.0, gt=0, description="initial jog step (deg)")
    jog_step_increment: float = Field(default=1.0, gt=0, description="+/- jog step adjust (deg)")
    load_warn: int = Field(default=600, ge=0, description="jog high-load warning (raw)")
    load_stall: int = Field(default=600, ge=0, description="sweep stall threshold (raw)")
    sweep_margin_default: float = Field(
        default=90.0, gt=0, lt=100, description="sweep stop margin from endpoint (deg)"
    )
    settle_sweep_s: float = Field(default=1.2, ge=0, description="sweep dwell after target (s)")
    settle_set_pose_s: float = Field(default=2.0, ge=0, description="set_pose settle after reach (s)")
    settle_capture_s: float = Field(default=0.5, ge=0, description="capture_pose settle (s)")
    home_tolerance_steps: int = Field(default=25, ge=0, description="home-reach tolerance (raw steps)")
    home_timeout_s: float = Field(default=8.0, ge=0, description="home-move timeout (s)")
    home_poll_s: float = Field(default=0.05, gt=0, description="home-reach poll interval (s)")


class ArmPose(BaseModel):
    """One arm pose: degrees per motor, all five required."""

    model_config = ConfigDict(extra="forbid")
    shoulder_pan: float
    shoulder_lift: float
    elbow_flex: float
    wrist_flex: float
    wrist_roll: float

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class ArmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    connection: ArmConnection = Field(default_factory=ArmConnection)
    safety: ArmSafety = Field(default_factory=ArmSafety)
    tuning: ArmTuning = Field(default_factory=ArmTuning)
    poses: dict[str, ArmPose] = Field(default_factory=dict)


def load_arm_config(path: Path) -> ArmConfig:
    """Parse and validate ``arm_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ArmConfig.model_validate(raw)


_ARM_SAVE_HEADER = (
    "# arm_config.yaml -- SO-ARM101 operator config (single tuning surface).\n"
    "# connection/safety/tuning are hand-editable; 'poses' is written by jog.py /\n"
    "# capture_pose.py. Comments are regenerated on save -- see data/README.md for\n"
    "# what each tuning knob means. 'home' is the default parking / safe-park pose.\n"
)


def save_arm_config(path: Path, config: ArmConfig) -> None:
    """Write an ``ArmConfig`` to YAML atomically (tmp + ``os.replace``).

    Dumps the whole model so a load-modify-save (e.g. saving one pose) preserves
    connection/safety/tuning. ``sort_keys=False`` keeps field order.
    """
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_ARM_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
