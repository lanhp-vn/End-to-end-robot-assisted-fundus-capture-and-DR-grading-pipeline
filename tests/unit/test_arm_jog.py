"""Unit tests for the pure SO-ARM101 jog state machine (no bus)."""

from __future__ import annotations

import pytest

from arm101_hand.robots.arm_jog import (
    ARM_JOINTS,
    JOG_STEP_MAX,
    JOG_STEP_MIN,
    apply_action,
    format_status,
    initial_state,
    key_to_action,
)

_BOUNDS = dict.fromkeys(ARM_JOINTS, (-100.0, 100.0))


def _state(**kw):
    base = initial_state(dict.fromkeys(ARM_JOINTS, 0.0), dict.fromkeys(ARM_JOINTS, 0.0))
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def test_initial_state_active_is_first_joint():
    home = dict.fromkeys(ARM_JOINTS, 0.0)
    home[ARM_JOINTS[1]] = -90.0
    s = initial_state(dict.fromkeys(ARM_JOINTS, 0.0), home)
    assert s.active == ARM_JOINTS[0]
    assert s.torque_on is True
    assert s.step == pytest.approx(5.0)
    assert s.home[ARM_JOINTS[1]] == pytest.approx(-90.0)


def test_key_to_action_digits_select_joints():
    assert key_to_action("1") == f"select:{ARM_JOINTS[0]}"
    assert key_to_action("5") == f"select:{ARM_JOINTS[4]}"


def test_key_to_action_arrows_and_letters():
    assert key_to_action("UP") == "jog_up"
    assert key_to_action("DOWN") == "jog_down"
    assert key_to_action("[") == "step_down"
    assert key_to_action("]") == "step_up"
    assert key_to_action("h") == "home_active"
    assert key_to_action("t") == "toggle_torque"
    assert key_to_action("s") == "save"
    assert key_to_action("q") == "quit"
    assert key_to_action("z") is None


def test_select_changes_active():
    s2, eff = apply_action(_state(), f"select:{ARM_JOINTS[2]}", _BOUNDS)
    assert s2.active == ARM_JOINTS[2]
    assert eff is None


def test_jog_up_moves_active_by_step():
    s = _state()
    s2, eff = apply_action(s, "jog_up", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(5.0)
    assert eff == "move"


def test_jog_down_moves_active_negative():
    s = _state()
    s2, _ = apply_action(s, "jog_down", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(-5.0)


def test_jog_clamps_to_bounds():
    s = _state(step=15.0)
    bounds = dict.fromkeys(ARM_JOINTS, (-10.0, 10.0))
    s2, _ = apply_action(s, "jog_up", bounds)
    assert s2.cursors[s.active] == pytest.approx(10.0)  # clamped, not 15


def test_step_up_and_down_clamp():
    s2, eff = apply_action(_state(step=JOG_STEP_MAX), "step_up", _BOUNDS)
    assert s2.step == pytest.approx(JOG_STEP_MAX)
    assert eff is None
    s3, _ = apply_action(_state(step=JOG_STEP_MIN), "step_down", _BOUNDS)
    assert s3.step == pytest.approx(JOG_STEP_MIN)


def test_home_active_goes_to_home_value():
    s = _state()
    s.cursors[s.active] = 42.0
    s.home[s.active] = -12.0
    s2, eff = apply_action(s, "home_active", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(-12.0)
    assert eff == "move"


def test_home_active_clamps_home_to_bounds():
    s = _state()
    s.home[s.active] = 999.0  # absurd home value
    s2, _ = apply_action(s, "home_active", {**_BOUNDS, s.active: (-10.0, 10.0)})
    assert s2.cursors[s.active] == pytest.approx(10.0)  # clamped to bound


def test_toggle_torque_flips_and_signals():
    s2, eff = apply_action(_state(), "toggle_torque", _BOUNDS)
    assert s2.torque_on is False
    assert eff == "toggle_torque"


def test_jog_blocked_when_torque_off():
    s = _state(torque_on=False)
    s.cursors[s.active] = 3.0
    s2, eff = apply_action(s, "jog_up", _BOUNDS)
    assert s2.cursors[s.active] == pytest.approx(3.0)  # unchanged
    assert eff is None


def test_save_and_quit_effects():
    _, eff_save = apply_action(_state(), "save", _BOUNDS)
    assert eff_save == "save"
    _, eff_quit = apply_action(_state(), "quit", _BOUNDS)
    assert eff_quit == "quit"


def test_format_status_marks_active_and_shows_torque():
    s = _state()
    line = format_status(s, dict.fromkeys(ARM_JOINTS, 0))
    assert "torque ON" in line
    assert f"*{s.active}" in line


def test_custom_step_bounds_flow_through():
    """step_max / step_increment from initial_state override module constants."""
    s = initial_state(
        dict.fromkeys(ARM_JOINTS, 0.0),
        dict.fromkeys(ARM_JOINTS, 0.0),
        step_min=1.0,
        step_max=20.0,
        step_default=10.0,
        step_increment=2.0,
    )
    assert s.step == pytest.approx(10.0)
    # step_up increases by 2.0 (custom increment), not the module-default 1.0
    s2, _ = apply_action(s, "step_up", _BOUNDS)
    assert s2.step == pytest.approx(12.0)
    # clamps at 20.0 (custom max), not module-default 15.0
    s3 = s2
    for _ in range(10):
        s3, _ = apply_action(s3, "step_up", _BOUNDS)
    assert s3.step == pytest.approx(20.0)
