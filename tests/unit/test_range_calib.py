"""Tests for the pure jog-to-limit state machine (no hardware, no msvcrt)."""

from __future__ import annotations

import pytest

from arm101_hand.hand.range_calib import (
    JOG_BASE_MAX,
    JOG_BASE_MIN,
    JOG_SIDE_MAX,
    JOG_SIDE_MIN,
    STEP_DEFAULT,
    STEP_MAX,
    STEP_MIN,
    JogState,
    apply_action,
    format_status,
    key_to_action,
    load_warning,
)


# | raw key bytes      | expected action  | desc                       |
@pytest.mark.parametrize(
    "key,action,desc",
    [
        ("UP", "base+", "up arrow flexes (base+)"),
        ("DOWN", "base-", "down arrow extends (base-)"),
        ("RIGHT", "side+", "right arrow spreads (side+)"),
        ("LEFT", "side-", "left arrow spreads (side-)"),
        ("[", "step-", "[ shrinks step"),
        ("]", "step+", "] grows step"),
        ("1", "mark_base_min", "1 marks base_min"),
        ("2", "mark_base_max", "2 marks base_max"),
        ("3", "mark_side_min", "3 marks side_min"),
        ("4", "mark_side_max", "4 marks side_max"),
        ("h", "home", "h homes"),
        ("s", "save", "s saves"),
        ("q", "quit", "q quits"),
        ("z", None, "unmapped key is ignored"),
    ],
)
def test_key_to_action(key, action, desc):
    assert key_to_action(key) == action, desc


def test_jog_moves_cursor_by_step():
    state = JogState()
    s2, mark = apply_action(state, "base+")
    assert s2.base == STEP_DEFAULT and mark is None, "base+ advances base by the step"
    s3, _ = apply_action(s2, "side-")
    assert s3.side == -STEP_DEFAULT, "side- decreases side by the step"


def test_jog_clamps_to_safety_envelope():
    hi = JogState(base=JOG_BASE_MAX, side=JOG_SIDE_MAX, step=STEP_DEFAULT)
    s2, _ = apply_action(hi, "base+")
    assert s2.base == JOG_BASE_MAX, "base cannot exceed the jog safety max"
    s3, _ = apply_action(hi, "side+")
    assert s3.side == JOG_SIDE_MAX, "side cannot exceed the jog safety max"

    lo = JogState(base=JOG_BASE_MIN, side=JOG_SIDE_MIN, step=STEP_DEFAULT)
    s4, _ = apply_action(lo, "base-")
    assert s4.base == JOG_BASE_MIN, "base cannot go below the jog safety min"
    s5, _ = apply_action(lo, "side-")
    assert s5.side == JOG_SIDE_MIN, "side cannot go below the jog safety min"


# | start_step | action  | expected_step | desc                       |
@pytest.mark.parametrize(
    "start,action,expected,desc",
    [
        (STEP_DEFAULT, "step-", STEP_DEFAULT - 1, "step- shrinks by 1"),
        (STEP_DEFAULT, "step+", STEP_DEFAULT + 1, "step+ increments by 1"),
        (STEP_MIN, "step-", STEP_MIN, "step- floors at STEP_MIN"),
        (STEP_MAX, "step+", STEP_MAX, "step+ ceils at STEP_MAX"),
    ],
)
def test_step_changes(start, action, expected, desc):
    state = JogState(step=start)
    s2, _ = apply_action(state, action)
    assert s2.step == expected, desc


def test_home_resets_cursor():
    state = JogState(base=50, side=20, step=3)
    s2, _ = apply_action(state, "home")
    assert (s2.base, s2.side) == (0, 0), "home zeroes the cursor"
    assert s2.step == 3, "home preserves the step"


def test_mark_returns_named_limit():
    state = JogState(base=95, side=-35, step=5)
    _, mark = apply_action(state, "mark_base_max")
    assert mark == ("base_max", 95), "marking base_max captures the live base"
    _, mark = apply_action(state, "mark_side_min")
    assert mark == ("side_min", -35), "marking side_min captures the live side"


# | load1 | load2 | threshold | warns | desc                     |
@pytest.mark.parametrize(
    "l1,l2,thr,warns,desc",
    [
        (10, 10, 60, False, "low load: no warning"),
        (80, 10, 60, True, "servo 1 over threshold warns"),
        (10, 95, 60, True, "servo 2 over threshold warns"),
        (80, 95, 60, True, "both servos over threshold warns"),
    ],
)
def test_load_warning(l1, l2, thr, warns, desc):
    msg = load_warning(l1, l2, thr)
    assert (msg is not None) == warns, desc


def test_format_status_includes_cursor_and_loads():
    line = format_status(JogState(base=12, side=-7, step=3), 40, 55)
    assert "base=" in line and "side=" in line, "status shows the cursor"
    assert "12" in line and "-7" in line, "status shows live base/side values"
    assert "40" in line and "55" in line, "status shows both load readings"


def test_apply_action_respects_custom_bounds():
    """Custom keyword bounds override the module constants."""
    # base+ clamped to custom ceiling of 3.0 (module JOG_BASE_MAX is 130).
    state = JogState(base=0, side=0, step=5)
    s2, _ = apply_action(
        state,
        "base+",
        jog_base_min=-10.0,
        jog_base_max=3.0,
        jog_side_min=-10.0,
        jog_side_max=10.0,
    )
    assert s2.base == 3, "base+ is clamped to custom jog_base_max=3, not module JOG_BASE_MAX=130"

    # step+ clamped to custom ceiling of 2 (module STEP_MAX is 15).
    s3, _ = apply_action(state, "step+", step_min=1, step_max=2)
    s4, _ = apply_action(s3, "step+", step_min=1, step_max=2)
    assert s4.step == 2, "step+ is clamped to custom step_max=2, not module STEP_MAX=15"
