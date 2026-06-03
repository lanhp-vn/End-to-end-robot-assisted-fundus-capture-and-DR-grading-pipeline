"""Unit tests for the pure SO-ARM101 calibration-summary math (no bus)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    JointCalib,
    clamp_degrees,
    degree_bounds,
    degree_span,
    load_arm_calibration,
    midpoint_steps,
)

# A complete, well-ordered 5-joint calibration (mirrors so101_follower.json shape).
_GOOD = {
    "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -1918, "range_min": 736, "range_max": 3460},
    "shoulder_lift": {"id": 2, "drive_mode": 0, "homing_offset": -1987, "range_min": 816, "range_max": 3205},
    "elbow_flex": {"id": 3, "drive_mode": 0, "homing_offset": -1993, "range_min": 873, "range_max": 3092},
    "wrist_flex": {"id": 4, "drive_mode": 0, "homing_offset": -2003, "range_min": 950, "range_max": 3202},
    "wrist_roll": {"id": 5, "drive_mode": 0, "homing_offset": 1977, "range_min": 0, "range_max": 4095},
}


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "so101_follower.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_returns_all_five_joints(tmp_path: Path) -> None:
    calib = load_arm_calibration(_write(tmp_path, _GOOD))
    assert set(calib) == set(ARM_JOINTS)
    assert calib["shoulder_pan"] == JointCalib(
        id=1, drive_mode=0, homing_offset=-1918, range_min=736, range_max=3460
    )


def test_load_rejects_missing_joint(tmp_path: Path) -> None:
    bad = {k: v for k, v in _GOOD.items() if k != "wrist_roll"}
    with pytest.raises(ValueError, match="missing joints"):
        load_arm_calibration(_write(tmp_path, bad))


def test_load_rejects_missing_key(tmp_path: Path) -> None:
    bad = {**_GOOD, "elbow_flex": {"id": 3, "drive_mode": 0, "homing_offset": 0, "range_min": 100}}
    with pytest.raises(ValueError, match="missing key"):
        load_arm_calibration(_write(tmp_path, bad))


def test_degree_span_full_turn_is_360() -> None:
    # range 0..4095 over resolution 4096 -> (4095-0)*360/4095 = 360.0
    assert degree_span(0, 4095) == pytest.approx(360.0)


def test_degree_span_partial() -> None:
    # (3460-736)*360/4095
    assert degree_span(736, 3460) == pytest.approx(2724 * 360 / 4095)


def test_midpoint_steps() -> None:
    assert midpoint_steps(736, 3460) == pytest.approx(2098.0)


def test_degree_bounds_symmetric() -> None:
    lo, hi = degree_bounds(736, 3460)
    span = degree_span(736, 3460)
    assert lo == pytest.approx(-span / 2)
    assert hi == pytest.approx(span / 2)


def test_clamp_inside_is_unchanged() -> None:
    _, hi = degree_bounds(736, 3460)
    assert clamp_degrees(hi - 1.0, 736, 3460) == pytest.approx(hi - 1.0)


def test_clamp_above_is_clamped_to_hi() -> None:
    _, hi = degree_bounds(736, 3460)
    assert clamp_degrees(hi + 50.0, 736, 3460) == pytest.approx(hi)


def test_clamp_below_is_clamped_to_lo() -> None:
    lo, _ = degree_bounds(736, 3460)
    assert clamp_degrees(lo - 50.0, 736, 3460) == pytest.approx(lo)
