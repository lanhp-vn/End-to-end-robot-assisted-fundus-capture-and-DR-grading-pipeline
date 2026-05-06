"""Unit tests for ``arm101_hand.gui.safety.ThresholdEvaluator`` (spec §7.1, §7.2).

Tests are pure-logic — no Qt, no hardware, no clock. They exercise:

- Single-sample temperature classification at each boundary.
- The 5-sample rolling window: critical only fires when *every* sample in a
  full window is ≥ critical (so a single noisy spike can't auto-disable).
- Per-motor independence: escalating one servo's window doesn't infect another.
- Single-sample voltage classification for both buses (5 V hand, 12 V arm).
- Voltage has no rolling window — a wiring-fault recovery flips immediately.
"""

from __future__ import annotations

import pytest

from arm101_hand.config import AppConfig
from arm101_hand.config.app_config import Safety
from arm101_hand.gui.safety import ThresholdEvaluator


def _build_safety(
    *,
    temp_warn: float = 50.0,
    temp_critical: float = 65.0,
    voltage_warn_pct: float = 5.0,
    voltage_critical_pct: float = 10.0,
) -> Safety:
    cfg = AppConfig()
    cfg.safety.temp_warn_c = temp_warn
    cfg.safety.temp_critical_c = temp_critical
    cfg.safety.voltage_warn_pct = voltage_warn_pct
    cfg.safety.voltage_critical_pct = voltage_critical_pct
    return cfg.safety


def _make_evaluator(**kw: float) -> ThresholdEvaluator:
    return ThresholdEvaluator(
        safety=_build_safety(**kw),
        bus_nominal_voltages={"hand": 5.0, "arm": 12.0},
    )


# | reading | expected | description                                                |
@pytest.mark.parametrize(
    "reading,expected,desc",
    [
        (40.0, "ok", "below warn → ok"),
        (49.99, "ok", "just below warn → ok"),
        (50.0, "warn", "exactly at warn → warn"),
        (60.0, "warn", "between warn and critical → warn"),
        (65.0, "warn", "single critical-level reading suppressed by window → warn"),
        (80.0, "warn", "high single reading still warn until window fills"),
        (-5.0, "ok", "negative sensor read treated as below warn → ok"),
    ],
)
def test_temp_single_sample_classification(reading: float, expected: str, desc: str) -> None:
    ev = _make_evaluator()
    assert ev.record_temp("hand", "1", reading) == expected, desc


def test_temp_rolling_window_suppresses_single_critical_spike() -> None:
    ev = _make_evaluator()
    # Four cool reads, then one critical-level spike — the window is full but
    # only the latest sample is ≥ critical, so we only escalate to warn.
    for c in [25.0, 25.0, 25.0, 25.0]:
        ev.record_temp("hand", "1", c)
    spike = ev.record_temp("hand", "1", 80.0)
    assert spike == "warn", f"single 80°C spike → warn (suppressed); got {spike}"
    # A subsequent cool read keeps us at warn (latest below warn) → ok.
    cooled = ev.record_temp("hand", "1", 25.0)
    assert cooled == "ok", f"latest cool reading → ok; got {cooled}"


def test_temp_sustained_critical_window_escalates_to_critical() -> None:
    ev = _make_evaluator()
    last: str = "ok"
    for c in [66.0, 67.0, 68.0, 69.0, 70.0]:
        last = ev.record_temp("hand", "1", c)
    assert last == "critical", f"5 sustained ≥critical readings escalate; got {last}"
    # A single sub-warn read drops the worst-of-window → ok.
    cooled = ev.record_temp("hand", "1", 30.0)
    assert cooled == "ok", f"latest cool reading drops severity; got {cooled}"
    # And a single warn-level reading keeps us at warn (not critical).
    warmed = ev.record_temp("hand", "1", 55.0)
    assert warmed == "warn", f"single warn reading after recovery → warn; got {warmed}"


def test_temp_window_is_per_motor_and_per_device() -> None:
    ev = _make_evaluator()
    for c in [66.0, 67.0, 68.0, 69.0, 70.0]:
        ev.record_temp("hand", "1", c)
    other_motor = ev.record_temp("hand", "2", 25.0)
    assert other_motor == "ok", f"servo 2 has its own window → ok; got {other_motor}"
    other_device = ev.record_temp("arm", "1", 25.0)
    assert other_device == "ok", f"arm-1 has its own window → ok; got {other_device}"
    # Original servo still escalated.
    again = ev.record_temp("hand", "1", 66.0)
    assert again == "critical", f"hand-1 still critical; got {again}"


# | device | observed | expected | description                                |
@pytest.mark.parametrize(
    "device,observed,expected,desc",
    [
        ("hand", 5.00, "ok", "5 V hand exactly nominal → ok"),
        ("hand", 4.80, "ok", "5 V hand 4 % low → still inside warn band"),
        ("hand", 4.74, "warn", "5 V hand 5.2 % low → warn"),
        ("hand", 4.49, "critical", "5 V hand 10.2 % low → critical"),
        ("hand", 5.55, "critical", "5 V hand 11 % high → critical"),
        ("arm", 12.00, "ok", "12 V arm exactly nominal → ok"),
        ("arm", 11.39, "warn", "12 V arm 5.1 % low → warn"),
        ("arm", 13.21, "critical", "12 V arm 10.1 % high → critical"),
        ("arm", 10.79, "critical", "12 V arm 10.1 % low → critical"),
    ],
)
def test_voltage_single_sample_classification(device: str, observed: float, expected: str, desc: str) -> None:
    ev = _make_evaluator()
    assert ev.record_voltage(device, observed) == expected, desc


def test_voltage_no_rolling_window_resets_immediately() -> None:
    ev = _make_evaluator()
    bad = ev.record_voltage("hand", 4.40)
    assert bad == "critical", f"bad single sample → critical; got {bad}"
    good = ev.record_voltage("hand", 5.00)
    assert good == "ok", f"good sample after bad resets immediately; got {good}"
    # And another bad sample re-escalates instantly.
    bad_again = ev.record_voltage("hand", 5.55)
    assert bad_again == "critical", f"bad again → critical; got {bad_again}"


def test_voltage_unknown_device_returns_ok() -> None:
    ev = _make_evaluator()
    unknown = ev.record_voltage("unknown", 0.1)
    assert unknown == "ok", f"unknown device returns ok; got {unknown}"
    # Registered devices still classify correctly afterwards.
    hand_ok = ev.record_voltage("hand", 5.00)
    assert hand_ok == "ok", f"hand still classifies; got {hand_ok}"
