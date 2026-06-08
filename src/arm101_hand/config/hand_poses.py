"""Pydantic schema for ``data/hand_config.yaml`` (named poses).

Position units: degrees relative to each servo's calibrated ``middle_pos``,
even-ID values pre-inverted in the list (see spec §5.5). The schema only
verifies structural shape — 8 ints per pose. Range/calibration validation
happens at the application layer where the loaded ``HandCalibration`` is
available.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

POSITIONS_LEN = 8


class HandPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[int] = Field(min_length=POSITIONS_LEN, max_length=POSITIONS_LEN)


class HandPoseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    poses: dict[str, HandPose] = Field(default_factory=dict)


def load_hand_poses(path: Path) -> HandPoseConfig:
    """Parse and validate ``data/hand_config.yaml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandPoseConfig.model_validate(raw)


def save_hand_poses(path: Path, config: HandPoseConfig) -> None:
    """Write a ``HandPoseConfig`` to YAML atomically (tmp + ``os.replace``).

    The whole model is dumped, so every pose survives a load-modify-save
    round-trip. Used by ``jog.py`` and any pose-saving script.
    """
    payload = config.model_dump(mode="python")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
