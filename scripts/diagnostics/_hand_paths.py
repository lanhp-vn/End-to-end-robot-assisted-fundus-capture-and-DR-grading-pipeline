"""Canonical hand paths for the diagnostics scripts (IL-5)."""

from __future__ import annotations

from pathlib import Path

# scripts/diagnostics/_hand_paths.py -> repo root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_CALIB_PATH = _REPO_ROOT / "scripts" / "calibration" / "amazing_hand" / "hand_calib_values.yaml"
HAND_CONFIG_PATH = _REPO_ROOT / "src" / "arm101_hand" / "data" / "hand_config.yaml"
