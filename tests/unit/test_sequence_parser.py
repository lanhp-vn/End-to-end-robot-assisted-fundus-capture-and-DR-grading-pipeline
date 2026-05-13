"""Unit tests for ``arm101_hand.gui.sequence_player.parse_step`` and
``yaml_pose_to_logical_targets`` (no Qt event loop, no controller).

Covers the two step formats from ``data/hand_config.yaml``'s ``sequences:``
block (spec §5.5) and the YAML→logical-frame conversion the player applies
before handing targets to ``HandController.send_batch_targets``.
"""

from __future__ import annotations

import pytest

from arm101_hand.config import HandPose
from arm101_hand.gui.sequence_player import (
    PoseStep,
    SleepStep,
    parse_step,
    yaml_pose_to_logical_targets,
)

# -----------------------------------------------------------------------------
# parse_step — happy-path table
# -----------------------------------------------------------------------------


# | input                                | expected_type | expected_attrs                                          | description                        |
@pytest.mark.parametrize(
    "raw,expected_type,attrs,desc",
    [
        ("SLEEP:0.5s", SleepStep, {"delay_s": 0.5}, "SLEEP with fractional seconds"),
        ("SLEEP:2s", SleepStep, {"delay_s": 2.0}, "SLEEP with integer seconds"),
        ("SLEEP:0s", SleepStep, {"delay_s": 0.0}, "SLEEP with zero delay"),
        (
            "open:3,3,3,3,3,3,3,3|2.0s",
            PoseStep,
            {"pose_name": "open", "speeds": (3, 3, 3, 3, 3, 3, 3, 3), "delay_s": 2.0},
            "uniform speeds across 8 servos",
        ),
        (
            "fist:1,2,3,4,5,6,7,1|1.5s",
            PoseStep,
            {"pose_name": "fist", "speeds": (1, 2, 3, 4, 5, 6, 7, 1), "delay_s": 1.5},
            "per-servo speeds varied",
        ),
        (
            "  middle:3,3,3,3,3,3,3,3|0.0s  ",
            PoseStep,
            {"pose_name": "middle", "speeds": (3,) * 8, "delay_s": 0.0},
            "leading/trailing whitespace tolerated",
        ),
    ],
)
def test_parse_step_happy_path(raw: str, expected_type: type, attrs: dict, desc: str) -> None:
    step = parse_step(raw)
    assert isinstance(step, expected_type), f"{desc}: parsed type"
    for k, v in attrs.items():
        assert getattr(step, k) == v, f"{desc}: attr {k} expected {v}, got {getattr(step, k)}"


# -----------------------------------------------------------------------------
# parse_step — rejection table
# -----------------------------------------------------------------------------


# | input                            | expected_substring          | description                      |
@pytest.mark.parametrize(
    "raw,err_substr,desc",
    [
        ("", "empty step", "empty string rejected"),
        ("   ", "empty step", "whitespace-only rejected"),
        ("SLEEP:1", "must end with 's'", "SLEEP missing trailing s"),
        ("SLEEP:abcs", "must be numeric", "SLEEP non-numeric delay"),
        ("SLEEP:-1s", "non-negative", "SLEEP negative delay"),
        ("middle3,3,3,3,3,3,3,3|2s", "malformed step", "missing colon between pose and speeds"),
        ("middle:3,3,3,3,3,3,3,3", "malformed step", "missing |delay separator"),
        (":3,3,3,3,3,3,3,3|2s", "missing pose name", "empty pose name"),
        ("middle:3,3,3,3,3|2s", "needs 8 speeds", "too few speeds"),
        ("middle:3,3,3,3,3,3,3,3,3|2s", "needs 8 speeds", "too many speeds"),
        ("middle:3,a,3,3,3,3,3,3|2s", "must be integers", "non-integer speed"),
        ("middle:3,3,3,3,3,3,3,3|2", "must end with 's'", "delay missing trailing s"),
        ("middle:3,3,3,3,3,3,3,3|abcs", "must be numeric", "delay not a number"),
        ("middle:3,3,3,3,3,3,3,3|-2s", "non-negative", "negative delay rejected"),
    ],
)
def test_parse_step_rejects_malformed(raw: str, err_substr: str, desc: str) -> None:
    with pytest.raises(ValueError) as ei:
        parse_step(raw)
    assert err_substr in str(ei.value), f"{desc}: error mentions {err_substr!r}; got {ei.value}"


# -----------------------------------------------------------------------------
# yaml_pose_to_logical_targets — frame conversion
# -----------------------------------------------------------------------------


def test_yaml_pose_to_logical_targets_inverts_even_ids() -> None:
    # YAML "fist" pre-inverts even-IDs: [+90, -90, +90, -90, ...].
    # Logical frame should be [+90, +90, ...] for all 8 servos.
    pose = HandPose(positions=[90, -90, 90, -90, 90, -90, 90, -90])
    speeds = (3, 3, 3, 3, 3, 3, 3, 3)
    targets = yaml_pose_to_logical_targets(pose, speeds)
    for sid, (deg_logical, _speed) in targets.items():
        assert deg_logical == 90.0, f"sid={sid} logical-frame is +90 for fist; got {deg_logical}"


def test_yaml_pose_to_logical_targets_keeps_odd_ids_unchanged() -> None:
    # Asymmetric pose so we can see odd-IDs pass through unchanged while
    # even-IDs flip sign.
    pose = HandPose(positions=[50, -30, 50, -30, 50, -30, 50, -30])
    speeds = (1, 1, 1, 1, 1, 1, 1, 1)
    targets = yaml_pose_to_logical_targets(pose, speeds)
    for sid in (1, 3, 5, 7):
        deg, _ = targets[sid]
        assert deg == 50.0, f"odd sid={sid} unchanged; got {deg}"
    for sid in (2, 4, 6, 8):
        deg, _ = targets[sid]
        assert deg == 30.0, f"even sid={sid} flipped sign; got {deg}"


def test_yaml_pose_to_logical_targets_rejects_wrong_speed_length() -> None:
    pose = HandPose(positions=[0] * 8)
    with pytest.raises(ValueError, match="length 8"):
        yaml_pose_to_logical_targets(pose, (1, 2, 3))
