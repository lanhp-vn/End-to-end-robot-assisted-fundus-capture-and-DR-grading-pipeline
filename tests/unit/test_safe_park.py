"""Unit tests for ``arm101_hand.gui.safety.SafePark`` (spec §7.3, §7.5).

These tests run against an injected ``DeviceProto`` (``FakeDevice``), an
injected clock (``FakeClock``), and a synchronous executor — no Qt event loop,
no real bus, no ``time.sleep`` (per ``04-testing-verification.md`` §3.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from arm101_hand.config import AppConfig
from arm101_hand.gui.safety import NullDevice, SafePark, _format_event

# -----------------------------------------------------------------------------
# Test doubles
# -----------------------------------------------------------------------------


class FakeClock:
    """Mockable monotonic clock. ``advance(dt)`` is the only way time moves."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@dataclass
class FakeDevice:
    """Records every call. Tests tune behaviour via:

    - ``arrival_after_calls`` — which ``read_positions`` call returns the target
      pose (earlier reads return positions 100° off so the orchestrator keeps
      polling).
    - ``advance_per_read`` (paired with ``clock``) — advances the injected clock
      on each read; lets timeout tests exhaust ``park_timeout_s`` deterministically.
    - ``on_first_read`` — runs once on the first read, useful for re-entry tests.
    """

    name: str
    connected: bool = True
    target_pose: dict[str, float] = field(default_factory=dict)
    arrival_after_calls: int = 1
    clock: FakeClock | None = None
    advance_per_read: float = 0.0
    on_first_read: Callable[[], None] | None = None
    calls: list[tuple[str, object]] = field(default_factory=list)
    _read_count: int = 0

    def is_connected(self) -> bool:
        return self.connected

    def drain_queue(self) -> None:
        self.calls.append(("drain_queue", None))

    def set_velocity(self, velocity: int) -> None:
        self.calls.append(("set_velocity", velocity))

    def send_pose(self, pose: dict[str, float]) -> None:
        self.calls.append(("send_pose", dict(pose)))
        if not self.target_pose:
            self.target_pose = dict(pose)

    def read_positions(self) -> dict[str, float]:
        self._read_count += 1
        self.calls.append(("read_positions", None))
        if self._read_count == 1 and self.on_first_read is not None:
            self.on_first_read()
        if self.clock is not None and self.advance_per_read > 0:
            self.clock.advance(self.advance_per_read)
        if self._read_count >= self.arrival_after_calls:
            return dict(self.target_pose)
        return {motor: target + 100.0 for motor, target in self.target_pose.items()}

    def disable_torque_all(self) -> None:
        self.calls.append(("disable_torque_all", None))


class FakeLog:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def __call__(self, message: str, level: str) -> None:
        self.entries.append((message, level))

    def messages(self) -> list[str]:
        return [m for m, _ in self.entries]


def sync_executor(jobs: list[Callable[[], None]]) -> None:
    """Run device jobs sequentially on the calling thread."""
    for job in jobs:
        job()


def _make_safepark(
    *,
    config: AppConfig | None = None,
    clock: FakeClock | None = None,
    log: FakeLog | None = None,
) -> tuple[SafePark, FakeLog, FakeClock]:
    cfg = config or AppConfig()
    log = log or FakeLog()
    clock = clock or FakeClock()
    sp = SafePark(
        config=cfg,
        log=log,
        now=clock,
        sleep_step_s=0.0,
        executor=sync_executor,
    )
    return sp, log, clock


# -----------------------------------------------------------------------------
# Tests — every assertion carries a ``desc`` per §3.2.
# -----------------------------------------------------------------------------


def test_soft_park_happy_path_executes_full_sequence_and_logs_parked() -> None:
    sp, log, _clock = _make_safepark()
    target = {"shoulder_pan": 0.0, "shoulder_lift": -90.0}
    fake = FakeDevice(name="arm", target_pose=target, arrival_after_calls=1)
    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="rest",
        park_velocity=600,
    )

    sp.engage_soft("user")

    op_names = [c[0] for c in fake.calls]
    assert op_names[:3] == ["drain_queue", "set_velocity", "send_pose"], (
        f"steps 1-3 in spec order; got {op_names}"
    )
    assert ("set_velocity", 600) in fake.calls, "set_velocity called with park_velocity"
    assert fake.calls[-1] == ("disable_torque_all", None), (
        f"disable_torque_all is the final call; got {fake.calls[-1]}"
    )
    msgs = log.messages()
    assert any("parked in" in m for m in msgs), f"happy-path log says 'parked in'; got {msgs}"
    assert any("mode=soft" in m and "reason=user" in m for m in msgs), "log carries mode + reason"


def test_soft_park_timeout_falls_back_to_disable_torque_with_timeout_outcome() -> None:
    sp, log, clock = _make_safepark()
    target = {"j1": 0.0}
    fake = FakeDevice(
        name="hand",
        target_pose=target,
        arrival_after_calls=10_000,  # never within tolerance
        clock=clock,
        advance_per_read=1.0,  # 4 reads → 4.0 s ≥ park_timeout_s default
    )
    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="middle",
        park_velocity=2,
    )

    sp.engage_soft("temp_critical")

    assert ("disable_torque_all", None) in fake.calls, "torque disabled even on timeout"
    msgs = log.messages()
    assert any("timed out at" in m for m in msgs), f"timeout outcome logged; got {msgs}"
    assert any("reason=temp_critical" in m for m in msgs), "timeout log carries reason"
    assert any("mode=soft" in m for m in msgs), "timeout log carries mode"


def test_soft_park_missing_pose_disables_only_that_device() -> None:
    sp, log, _clock = _make_safepark()
    arm_target = {"shoulder_pan": 0.0}
    arm_dev = FakeDevice(name="arm", target_pose=arm_target, arrival_after_calls=1)
    hand_dev = FakeDevice(name="hand", target_pose={}, arrival_after_calls=1)

    sp.register_device(
        arm_dev,
        pose_resolver=lambda: dict(arm_target),
        pose_name="rest",
        park_velocity=600,
    )
    sp.register_device(
        hand_dev,
        pose_resolver=lambda: None,
        pose_name="middle",
        park_velocity=2,
    )

    sp.engage_soft("user")

    arm_ops = [c[0] for c in arm_dev.calls]
    hand_ops = [c[0] for c in hand_dev.calls]
    assert "set_velocity" in arm_ops, f"arm parks normally; got {arm_ops}"
    assert "set_velocity" not in hand_ops, f"hand skips park steps; got {hand_ops}"
    assert ("disable_torque_all", None) in hand_dev.calls, "hand still hard-disables"
    msgs = log.messages()
    assert any("[hand]" in m and "pose missing" in m and "'middle'" in m for m in msgs), (
        f"hand logs missing-pose with name; got {msgs}"
    )
    assert any("[arm]" in m and "parked in" in m for m in msgs), f"arm logs successful park; got {msgs}"


def test_soft_park_out_of_range_pose_falls_back_to_hard_disable_per_device() -> None:
    sp, log, _clock = _make_safepark()
    target = {"shoulder_pan": 0.0, "elbow_flex": 999.0}
    fake = FakeDevice(name="arm", target_pose=target, arrival_after_calls=1)

    def range_check(pose: dict[str, float]) -> list[str]:
        return [m for m, v in pose.items() if abs(v) > 180.0]

    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="rest",
        park_velocity=600,
        range_check=range_check,
    )

    sp.engage_soft("user")

    op_names = [c[0] for c in fake.calls]
    assert "send_pose" not in op_names, f"out-of-range pose never sent; got {op_names}"
    assert "disable_torque_all" in op_names, "torque disabled as fallback"
    msgs = log.messages()
    assert any("out of range" in m and "elbow_flex" in m for m in msgs), (
        f"log names the out-of-range motor; got {msgs}"
    )
    assert any("'rest'" in m for m in msgs), "log includes pose name"


def test_safe_park_disabled_short_circuits_to_torque_off() -> None:
    cfg = AppConfig()
    cfg.safety.safe_park.enabled = False
    sp, log, _clock = _make_safepark(config=cfg)

    target = {"j1": 0.0}
    fake = FakeDevice(name="hand", target_pose=target, arrival_after_calls=1)
    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="middle",
        park_velocity=2,
    )

    sp.engage_soft("user")

    op_names = [c[0] for c in fake.calls]
    assert op_names == ["drain_queue", "disable_torque_all"], (
        f"only drain + disable when safe-park disabled; got {op_names}"
    )
    assert "set_velocity" not in op_names, "no velocity write when safe-park disabled"
    assert any("safe-park disabled" in m for m in log.messages()), (
        f"log mentions disabled flag; got {log.messages()}"
    )


def test_second_event_during_park_is_ignored_and_logged() -> None:
    sp, log, _clock = _make_safepark()
    target = {"j1": 0.0}

    def reentry() -> None:
        # Re-enters while the orchestrator is still inside the soft job.
        sp.engage_soft("user")

    fake = FakeDevice(
        name="arm",
        target_pose=target,
        arrival_after_calls=2,  # parks on the 2nd read so the 1st can re-enter
        on_first_read=reentry,
    )
    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="rest",
        park_velocity=600,
    )

    sp.engage_soft("user")

    msgs = log.messages()
    ignored_count = sum("ignored (busy" in m for m in msgs)
    parked_count = sum("parked in" in m for m in msgs)
    assert ignored_count == 1, f"exactly one 'ignored (busy' line; got {msgs}"
    assert parked_count == 1, f"original park completes once; got {msgs}"


def test_hard_engage_skips_park_and_only_drains_then_disables() -> None:
    sp, log, _clock = _make_safepark()
    target = {"j1": 0.0}
    fake = FakeDevice(name="arm", target_pose=target, arrival_after_calls=1)
    sp.register_device(
        fake,
        pose_resolver=lambda: dict(target),
        pose_name="rest",
        park_velocity=600,
    )

    sp.engage_hard("user")

    op_names = [c[0] for c in fake.calls]
    assert op_names == ["drain_queue", "disable_torque_all"], (
        f"hard path is drain → disable only; got {op_names}"
    )
    assert "send_pose" not in op_names, "no pose sent on hard E-STOP"
    msgs = log.messages()
    assert any("mode=hard" in m for m in msgs), "log carries hard mode"
    assert any("disabled torque (no park)" in m for m in msgs), f"log states no-park outcome; got {msgs}"


def test_disconnected_device_emits_no_device_and_does_not_call_actions() -> None:
    sp, log, _clock = _make_safepark()
    sp.register_device(
        NullDevice("hand"),
        pose_resolver=lambda: None,
        pose_name="middle",
        park_velocity=2,
    )
    sp.register_device(
        NullDevice("arm"),
        pose_resolver=lambda: None,
        pose_name="rest",
        park_velocity=600,
    )

    sp.engage_soft("user")
    sp.engage_hard("user")

    msgs = log.messages()
    soft_no_dev = [m for m in msgs if "no device" in m and "mode=soft" in m]
    hard_no_dev = [m for m in msgs if "no device" in m and "mode=hard" in m]
    assert len(soft_no_dev) == 2, f"both devices log 'no device' on soft; got {soft_no_dev}"
    assert len(hard_no_dev) == 2, f"both devices log 'no device' on hard; got {hard_no_dev}"
    assert any("[hand]" in m for m in soft_no_dev), "hand line present"
    assert any("[arm]" in m for m in soft_no_dev), "arm line present"


def test_concurrent_independent_devices_one_failing_does_not_block_other() -> None:
    sp, log, _clock = _make_safepark()
    arm_target = {"shoulder_pan": 0.0}
    arm_dev = FakeDevice(name="arm", target_pose=arm_target, arrival_after_calls=1)
    hand_dev = FakeDevice(name="hand", target_pose={}, arrival_after_calls=1)

    sp.register_device(
        arm_dev,
        pose_resolver=lambda: dict(arm_target),
        pose_name="rest",
        park_velocity=600,
    )
    sp.register_device(
        hand_dev,
        pose_resolver=lambda: None,  # hand fails fast, arm proceeds
        pose_name="middle",
        park_velocity=2,
    )

    sp.engage_soft("user")

    arm_ops = [c[0] for c in arm_dev.calls]
    assert arm_ops == [
        "drain_queue",
        "set_velocity",
        "send_pose",
        "read_positions",
        "disable_torque_all",
    ], f"arm completes its full sequence; got {arm_ops}"
    hand_ops = [c[0] for c in hand_dev.calls]
    assert "send_pose" not in hand_ops, f"hand never sends pose; got {hand_ops}"
    assert "disable_torque_all" in hand_ops, f"hand still hard-disables; got {hand_ops}"


# | mode | reason | outcome | description |
@pytest.mark.parametrize(
    "mode,reason,outcome,desc",
    [
        ("soft", "user", "parked in 1.83s", "happy-path shape"),
        ("hard", "voltage_critical", "disabled torque (no park)", "hard outcome shape"),
        ("soft", "app_exit", "no device", "no-device shape"),
    ],
)
def test_format_event_string_shape(mode: str, reason: str, outcome: str, desc: str) -> None:
    msg = _format_event("hand", mode, reason, outcome)  # type: ignore[arg-type]
    assert msg.startswith("[hand]"), f"{desc}: device prefix; got {msg}"
    assert f"mode={mode}" in msg and f"reason={reason}" in msg, f"{desc}: mode + reason present; got {msg}"
    assert outcome in msg, f"{desc}: outcome present; got {msg}"


# -----------------------------------------------------------------------------
# replace_device — M3 swap mechanism
# -----------------------------------------------------------------------------


def test_replace_device_swaps_handle_and_preserves_resolver_and_velocity() -> None:
    sp, _log, _clock = _make_safepark()
    target = {"j1": 0.0}
    null_dev = NullDevice("hand")

    range_calls: list[dict[str, float]] = []

    def range_check(pose: dict[str, float]) -> list[str]:
        range_calls.append(pose)
        return []

    sp.register_device(
        null_dev,
        pose_resolver=lambda: dict(target),
        pose_name="middle",
        park_velocity=2,
        range_check=range_check,
    )

    real = FakeDevice(name="hand", target_pose=target, arrival_after_calls=1)
    sp.replace_device("hand", real)
    sp.engage_soft("user")

    op_names = [c[0] for c in real.calls]
    assert op_names[:3] == ["drain_queue", "set_velocity", "send_pose"], (
        f"new device receives the full park sequence; got {op_names}"
    )
    assert ("set_velocity", 2) in real.calls, f"park_velocity preserved across the swap; got {real.calls}"
    assert range_calls == [target], f"range_check preserved across the swap; called with {range_calls}"


def test_replace_device_unknown_name_raises_key_error() -> None:
    sp, _log, _clock = _make_safepark()
    sp.register_device(
        NullDevice("hand"),
        pose_resolver=lambda: None,
        pose_name="middle",
        park_velocity=2,
    )
    with pytest.raises(KeyError) as ei:
        sp.replace_device("arm", NullDevice("arm"))  # never registered
    assert "arm" in str(ei.value), f"KeyError carries the unknown name; got {ei.value}"


def test_replace_device_rejected_during_park_does_not_swap() -> None:
    sp, log, _clock = _make_safepark()
    target = {"j1": 0.0}
    original = FakeDevice(name="hand", target_pose=target, arrival_after_calls=2)

    replacement = FakeDevice(name="hand", target_pose=target, arrival_after_calls=1)

    def replace_during_park() -> None:
        sp.replace_device("hand", replacement)

    original.on_first_read = replace_during_park

    sp.register_device(
        original,
        pose_resolver=lambda: dict(target),
        pose_name="middle",
        park_velocity=2,
    )

    sp.engage_soft("user")

    assert "send_pose" not in [c[0] for c in replacement.calls], (
        f"replacement device never receives in-flight park; got {replacement.calls}"
    )
    assert any("ignored (busy)" in m for m in log.messages()), f"busy rejection logged; got {log.messages()}"


def test_replace_device_is_idempotent_across_repeated_swaps() -> None:
    sp, _log, _clock = _make_safepark()
    target = {"j1": 0.0}
    sp.register_device(
        NullDevice("hand"),
        pose_resolver=lambda: dict(target),
        pose_name="middle",
        park_velocity=2,
    )

    a = FakeDevice(name="hand", target_pose=target, arrival_after_calls=1)
    b = FakeDevice(name="hand", target_pose=target, arrival_after_calls=1)

    sp.replace_device("hand", a)
    sp.replace_device("hand", b)
    sp.replace_device("hand", a)

    sp.engage_soft("user")
    assert "send_pose" in [c[0] for c in a.calls], (
        f"final swap target receives the park; got a.calls={a.calls}"
    )
    assert "send_pose" not in [c[0] for c in b.calls], (
        f"intermediate swap target not engaged; got b.calls={b.calls}"
    )
