"""Unit tests for ``arm101_hand.hand.controller.HandController`` (no hardware).

A ``FakeRustypot`` Protocol-shaped fake replaces the ``Scs0009PyController``
so we exercise every command path — constructor injection by direct
assignment to ``_controller``, plus a ``connect_to_bus`` test that
monkey-patches ``sys.modules['rustypot']`` to capture the lazy import.

Tests cover (per ``04-testing-verification.md``):

- Even-ID inversion + ``middle_pos`` add on the send path (parametrized
  per-servo so the asymmetry is visible).
- Inverse mapping on the read path returning YAML frame.
- ``disable_torque_all`` cuts torque on every servo even when one raises.
- ``error`` signal emitted on simulated I/O fault.
- ``isinstance(controller, DeviceProto)`` at runtime via
  ``runtime_checkable``.
- ``drain_queue`` clears the pending deque.
- ``send_servo_target`` (logical frame) writes goal_position equal to the
  servo-frame radians produced by ``degrees_to_servo_radians``.
"""

from __future__ import annotations

import math
import sys
from typing import Any

import pytest

from arm101_hand.config.calibration import (
    FingerCalibration,
    HandCalibration,
    ServoCalibration,
)
from arm101_hand.gui.safety import DeviceProto
from arm101_hand.hand.controller import HandController
from arm101_hand.hand.kinematics import degrees_to_servo_radians


# Predictable middle_pos table — each servo gets a distinct value so an
# even-ID inversion bug shows up as a wrong servo number, not a numerical
# coincidence. Servo IDs follow IL-3.
def _calibration() -> HandCalibration:
    return HandCalibration(
        com_port="COMTEST",
        baudrate=1_000_000,
        timeout=0.5,
        speed=3,
        fingers={
            "index": FingerCalibration(
                servo_1=ServoCalibration(id=1, middle_pos=10.0),
                servo_2=ServoCalibration(id=2, middle_pos=-5.0),
            ),
            "middle": FingerCalibration(
                servo_1=ServoCalibration(id=3, middle_pos=20.0),
                servo_2=ServoCalibration(id=4, middle_pos=-15.0),
            ),
            "ring": FingerCalibration(
                servo_1=ServoCalibration(id=5, middle_pos=30.0),
                servo_2=ServoCalibration(id=6, middle_pos=-25.0),
            ),
            "thumb": FingerCalibration(
                servo_1=ServoCalibration(id=7, middle_pos=40.0),
                servo_2=ServoCalibration(id=8, middle_pos=-35.0),
            ),
        },
    )


class FakeRustypot:
    """Protocol-shaped fake of ``rustypot.Scs0009PyController``.

    Records every call. ``raise_on`` lets a test trigger a failure mid-sequence
    (the controller surfaces those via the ``error`` signal).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.raise_on: set[tuple[str, int]] = set()  # e.g., {("write_torque_enable", 4)}
        self.position_by_id: dict[int, float] = dict.fromkeys(range(1, 9), 0.0)
        self.load_by_id: dict[int, int] = dict.fromkeys(range(1, 9), 0)
        self.temp_by_id: dict[int, int] = dict.fromkeys(range(1, 9), 25)
        self.voltage_by_id: dict[int, int] = dict.fromkeys(range(1, 9), 50)  # u8 in 0.1 V

    def _trip(self, op: str, sid: int) -> None:
        if (op, sid) in self.raise_on:
            raise RuntimeError(f"simulated {op} fault on sid={sid}")

    def write_torque_enable(self, sid: int, enable: int) -> None:
        self.calls.append(("write_torque_enable", (sid, enable)))
        self._trip("write_torque_enable", sid)

    def write_goal_speed(self, sid: int, speed: int) -> None:
        self.calls.append(("write_goal_speed", (sid, speed)))
        self._trip("write_goal_speed", sid)

    def write_goal_position(self, sid: int, radians: float) -> None:
        self.calls.append(("write_goal_position", (sid, radians)))
        self._trip("write_goal_position", sid)

    # Per ``references/rustypot/CLAUDE.md``: ``read_<reg>(id) -> list`` (a
    # single-element list, symmetric with ``sync_read_<reg>``'s multi-id list
    # shape). The fake mirrors that contract so M3's controller tests catch
    # any regression where we forget to unwrap.
    def read_present_position(self, sid: int) -> list[float]:
        self.calls.append(("read_present_position", (sid,)))
        self._trip("read_present_position", sid)
        return [float(self.position_by_id[sid])]

    def read_present_load(self, sid: int) -> list[int]:
        self.calls.append(("read_present_load", (sid,)))
        self._trip("read_present_load", sid)
        return [int(self.load_by_id[sid])]

    def read_present_temperature(self, sid: int) -> list[int]:
        self.calls.append(("read_present_temperature", (sid,)))
        self._trip("read_present_temperature", sid)
        return [int(self.temp_by_id[sid])]

    def read_present_voltage(self, sid: int) -> list[int]:
        self.calls.append(("read_present_voltage", (sid,)))
        self._trip("read_present_voltage", sid)
        return [int(self.voltage_by_id[sid])]


def _make_connected_controller() -> tuple[HandController, FakeRustypot]:
    """Construct the controller, then short-circuit ``connect_to_bus`` by
    assigning the fake directly. Exercises the post-connect code paths
    without monkeypatching the rustypot module."""
    ctl = HandController(_calibration())
    fake = FakeRustypot()
    ctl._controller = fake
    ctl._port = "COMTEST"
    return ctl, fake


# -----------------------------------------------------------------------------
# Tests — every assertion carries a ``desc`` per §3.2.
# -----------------------------------------------------------------------------


def test_is_connected_reflects_internal_handle_state() -> None:
    ctl = HandController(_calibration())
    assert ctl.is_connected() is False, "fresh controller is not connected"
    ctl._controller = FakeRustypot()
    assert ctl.is_connected() is True, "after handle assigned, is_connected True"
    ctl._controller = None
    assert ctl.is_connected() is False, "after handle dropped, is_connected False"


def test_isinstance_device_proto_at_runtime() -> None:
    ctl = HandController(_calibration())
    assert isinstance(ctl, DeviceProto), "HandController structurally satisfies DeviceProto"
    # Sanity: the placeholder also satisfies it (already covered in safety
    # tests, but good to assert here so the contract is explicit on this side).
    assert ctl.name == "hand", "DeviceProto requires .name attribute"


# | sid | yaml_deg | mp     | expected_radians_arg                 | description                     |
@pytest.mark.parametrize(
    "sid,yaml_deg,mp,desc",
    [
        (1, 90.0, 10.0, "odd: mp + yaml_deg, no inversion"),
        (2, -90.0, -5.0, "even: mp + yaml_deg, YAML pre-inverted matches servo frame"),
        (3, 0.0, 20.0, "odd at zero deflection: mp only"),
        (4, 0.0, -15.0, "even at zero deflection: mp only"),
        (8, -110.0, -35.0, "thumb-even at extreme: mp + yaml_deg sums correctly"),
    ],
)
def test_send_pose_writes_goal_position_in_yaml_frame(
    sid: int, yaml_deg: float, mp: float, desc: str
) -> None:
    ctl, fake = _make_connected_controller()
    ctl.send_pose({str(sid): yaml_deg})
    write_calls = [c for c in fake.calls if c[0] == "write_goal_position"]
    assert len(write_calls) == 1, f"{desc}: exactly one position write per servo; got {write_calls}"
    actual_sid, actual_rad = write_calls[0][1]
    expected_rad = math.radians(mp + yaml_deg)
    assert actual_sid == sid, f"{desc}: writes to expected servo id"
    assert math.isclose(actual_rad, expected_rad, abs_tol=1e-9), (
        f"{desc}: expected {expected_rad} rad, got {actual_rad}"
    )


# | sid | servo_radians | mp    | expected_yaml_deg | description                                |
@pytest.mark.parametrize(
    "sid,servo_radians,mp,expected_yaml_deg,desc",
    [
        (1, math.radians(100.0), 10.0, 90.0, "odd: yaml = rad2deg(rad) - mp"),
        (2, math.radians(-95.0), -5.0, -90.0, "even: yaml frame, servo angle below middle"),
        (3, math.radians(20.0), 20.0, 0.0, "odd at calibrated middle → 0 yaml"),
        (8, math.radians(-145.0), -35.0, -110.0, "thumb-even at extreme"),
    ],
)
def test_read_positions_returns_yaml_frame(
    sid: int,
    servo_radians: float,
    mp: float,
    expected_yaml_deg: float,
    desc: str,
) -> None:
    ctl, fake = _make_connected_controller()
    fake.position_by_id[sid] = servo_radians
    out = ctl.read_positions()
    assert math.isclose(out[str(sid)], expected_yaml_deg, abs_tol=1e-6), (
        f"{desc}: expected {expected_yaml_deg} yaml-deg, got {out[str(sid)]}"
    )


# | sid | deg_rel_logical | mp   | expected_radians                   | description                |
@pytest.mark.parametrize(
    "sid,deg_rel_logical,mp,desc",
    [
        (1, 90.0, 10.0, "odd, logical close = +90 → servo +90 + mp = +100"),
        (2, 90.0, -5.0, "even, logical close = +90 → servo -90 + mp = -95"),
        (4, 30.0, -15.0, "even, slight close → servo -30 + mp = -45"),
        (5, -10.0, 30.0, "odd, slight open → servo -10 + mp = +20"),
    ],
)
def test_send_servo_target_uses_logical_frame_with_inversion(
    sid: int, deg_rel_logical: float, mp: float, desc: str
) -> None:
    ctl, fake = _make_connected_controller()
    ctl.send_servo_target(sid, deg_rel_logical, 4)
    pos_writes = [c for c in fake.calls if c[0] == "write_goal_position"]
    speed_writes = [c for c in fake.calls if c[0] == "write_goal_speed"]
    assert len(pos_writes) == 1, f"{desc}: one position write; got {pos_writes}"
    assert len(speed_writes) == 1, f"{desc}: one speed write; got {speed_writes}"
    actual_sid, actual_rad = pos_writes[0][1]
    expected_rad = degrees_to_servo_radians(sid, deg_rel_logical, mp)
    assert actual_sid == sid, f"{desc}: writes to expected servo id"
    assert math.isclose(actual_rad, expected_rad, abs_tol=1e-9), (
        f"{desc}: kinematics applied; expected {expected_rad}, got {actual_rad}"
    )
    assert speed_writes[0][1] == (sid, 4), f"{desc}: speed write carries (sid, speed)"


def test_set_velocity_writes_goal_speed_on_every_servo() -> None:
    ctl, fake = _make_connected_controller()
    ctl.set_velocity(2)
    speed_writes = [c[1] for c in fake.calls if c[0] == "write_goal_speed"]
    assert len(speed_writes) == 8, f"one write per servo; got {speed_writes}"
    sids_written = sorted(sid for sid, _ in speed_writes)
    assert sids_written == [1, 2, 3, 4, 5, 6, 7, 8], f"covers all 8 servos; got {sids_written}"
    speeds = {speed for _, speed in speed_writes}
    assert speeds == {2}, f"speed value is the configured park velocity; got {speeds}"


def test_disable_torque_all_writes_zero_on_every_servo_even_when_one_raises() -> None:
    ctl, fake = _make_connected_controller()
    fake.raise_on.add(("write_torque_enable", 4))  # one bad servo mid-sequence
    ctl.disable_torque_all()
    torque_writes = [c[1] for c in fake.calls if c[0] == "write_torque_enable"]
    assert len(torque_writes) == 8, f"all 8 attempted despite one raising; got {torque_writes}"
    enables = {enable for _, enable in torque_writes}
    assert enables == {0}, f"every attempt sets torque OFF; got {enables}"


def test_drain_queue_clears_pending_deque() -> None:
    ctl, _fake = _make_connected_controller()
    # Push a few items without dispatching: bypass the slot path so the
    # deque still has stale entries when drain_queue runs.
    ctl._pending.append((1, 50.0, 3))
    ctl._pending.append((2, -30.0, 3))
    assert len(ctl._pending) == 2, "preconditions: deque has buffered targets"
    ctl.drain_queue()
    assert len(ctl._pending) == 0, "drain_queue clears the buffered target deque"


def test_drain_queue_is_safe_when_disconnected() -> None:
    ctl = HandController(_calibration())
    assert ctl.is_connected() is False, "preconditions: not connected"
    ctl.drain_queue()  # must not raise
    ctl.disable_torque_all()  # must not raise even when no controller


def test_send_pose_raises_when_not_connected() -> None:
    ctl = HandController(_calibration())
    with pytest.raises(RuntimeError, match="not connected"):
        ctl.send_pose({"1": 0.0})
    with pytest.raises(RuntimeError, match="not connected"):
        ctl.read_positions()


def test_poll_state_emits_logical_frame_pos_load_temp_voltage() -> None:
    ctl, fake = _make_connected_controller()
    # Set distinct readings so we can verify the dict shape end to end.
    fake.position_by_id[1] = math.radians(100.0)  # mp=10, expected logical_deg=+90 (odd)
    fake.position_by_id[2] = math.radians(-95.0)  # mp=-5, expected logical_deg=+90 (even, inverted)
    fake.load_by_id[1] = 42
    fake.temp_by_id[1] = 47
    fake.voltage_by_id[1] = 50  # → 5.0 V

    received: list[dict] = []
    ctl.state_changed.connect(received.append)
    ctl.poll_state()

    assert len(received) == 1, f"one state_changed emission; got {len(received)}"
    state = received[0]
    assert set(state.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}, "state covers all 8 servos"
    assert math.isclose(state[1]["pos_deg"], 90.0, abs_tol=1e-6), (
        f"odd-id position emitted in logical frame; got {state[1]['pos_deg']}"
    )
    assert math.isclose(state[2]["pos_deg"], 90.0, abs_tol=1e-6), (
        f"even-id position emitted in logical frame (inverted); got {state[2]['pos_deg']}"
    )
    assert state[1]["load_pct"] == 42.0, f"load_pct passes through; got {state[1]['load_pct']}"
    assert state[1]["temp_c"] == 47.0, f"temp_c passes through; got {state[1]['temp_c']}"
    assert math.isclose(state[1]["voltage_v"], 5.0, abs_tol=1e-6), (
        f"voltage_v scales raw u8 by 0.1; got {state[1]['voltage_v']}"
    )


def test_poll_state_emits_error_signal_on_io_failure() -> None:
    ctl, fake = _make_connected_controller()
    fake.raise_on.add(("read_present_position", 3))

    states: list[dict] = []
    errors: list[str] = []
    ctl.state_changed.connect(states.append)
    ctl.error.connect(errors.append)
    ctl.poll_state()

    assert len(states) == 0, "state_changed not emitted on I/O failure"
    assert any("sid=3" in e for e in errors), f"error mentions failing servo; got {errors}"


def test_send_servo_target_emits_error_on_io_failure() -> None:
    ctl, fake = _make_connected_controller()
    fake.raise_on.add(("write_goal_position", 5))

    errors: list[str] = []
    ctl.error.connect(errors.append)
    ctl.send_servo_target(5, 30.0, 3)

    assert any("sid=5" in e for e in errors), f"error mentions failing servo; got {errors}"


def test_disconnect_from_bus_emits_disconnected_and_drops_handle() -> None:
    ctl, fake = _make_connected_controller()
    received: list[str] = []
    ctl.disconnected.connect(received.append)

    ctl.disconnect_from_bus()

    torque_off = [c for c in fake.calls if c[0] == "write_torque_enable"]
    assert len(torque_off) == 8, f"torque-off attempted for all 8 on disconnect; got {torque_off}"
    assert all(enable == 0 for _, (_, enable) in torque_off), "torque-off writes 0"
    assert ctl.is_connected() is False, "handle dropped after disconnect"
    assert received == ["user"], f"disconnected('user') emitted; got {received}"


def test_connect_to_bus_lazily_imports_rustypot_and_enables_torque(monkeypatch) -> None:
    """Connect path test: monkey-patch ``rustypot`` in ``sys.modules`` so the
    function-scope import inside ``connect_to_bus`` returns our fake."""
    fake = FakeRustypot()

    class _StubModule:
        @staticmethod
        def Scs0009PyController(serial_port: str, baudrate: int, timeout: float):  # noqa: N802 — match real API
            fake.calls.append(("__init__", (serial_port, baudrate, timeout)))
            return fake

    monkeypatch.setitem(sys.modules, "rustypot", _StubModule)

    ctl = HandController(_calibration())
    ports: list[str] = []
    ctl.connected.connect(ports.append)
    ctl.connect_to_bus("COMTEST", 1_000_000, 0.5)

    assert ctl.is_connected() is True, "controller attached to fake handle"
    assert ports == ["COMTEST"], f"connected emitted with port; got {ports}"
    init_calls = [c for c in fake.calls if c[0] == "__init__"]
    assert init_calls == [("__init__", ("COMTEST", 1_000_000, 0.5))], (
        f"rustypot constructor called with provided args; got {init_calls}"
    )
    torque_on = [c[1] for c in fake.calls if c[0] == "write_torque_enable"]
    assert len(torque_on) == 8, f"torque enabled on every servo at connect; got {torque_on}"
    assert all(enable == 1 for _, enable in torque_on), "torque-enable writes 1 on connect"


def test_connect_to_bus_failure_emits_error_and_leaves_disconnected(monkeypatch) -> None:
    class _BrokenModule:
        @staticmethod
        def Scs0009PyController(*args, **kwargs):  # noqa: N802 — match real API
            raise OSError("port busy")

    monkeypatch.setitem(sys.modules, "rustypot", _BrokenModule)

    ctl = HandController(_calibration())
    errors: list[str] = []
    connected: list[str] = []
    ctl.error.connect(errors.append)
    ctl.connected.connect(connected.append)
    ctl.connect_to_bus("COMBROKEN", 1_000_000, 0.5)

    assert ctl.is_connected() is False, "stays disconnected after failed connect"
    assert connected == [], f"connected NOT emitted on failure; got {connected}"
    assert any("port busy" in e for e in errors), f"error surfaces underlying message; got {errors}"
