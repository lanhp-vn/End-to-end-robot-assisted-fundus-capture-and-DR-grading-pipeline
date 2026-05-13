"""Device layer: ``HandController(QObject)`` driving the AmazingHand 8-servo bus.

Owns one ``rustypot.Scs0009PyController`` (the rustypot Python wheel — Rust +
pyo3 binding for SCS0009 servos). Designed to live on its own ``QThread`` so
the UI thread never blocks on bus I/O.

Two complementary call paths:

- **Qt slot path** (UI → controller, queued connections): ``connect_to_bus`` /
  ``disconnect_from_bus`` / ``send_servo_target`` / ``send_batch_targets`` /
  ``disable_torque_all_slot`` / ``poll_state``. Inputs in **logical frame**
  (positive = close, same direction for both servos in a finger pair) so
  sliders and keyboard nudges feed in directly.
- **DeviceProto path** (``SafePark`` → controller, called from worker job
  threads): ``is_connected`` / ``drain_queue`` / ``set_velocity`` /
  ``send_pose`` / ``read_positions`` / ``disable_torque_all``. Pose units in
  **YAML/servo frame** (even-ID values pre-inverted, matching
  ``data/hand_config.yaml``) so resolvers like ``MainWindow.hand_resolver``
  feed in unchanged.

A single ``threading.Lock`` (``_io_lock``) serializes both paths over the
underlying ``Scs0009PyController`` so a SafePark park sequence and a stale
slider drag never interleave a half-update on one servo. The lock is taken
in short bursts; the polling loop in ``SafePark._run_soft`` releases it
between reads so the QThread can service its own slot calls.

Per ``docs/conventions/01-module-layering.md`` §4: the constructor does NOT
open the rustypot port. ``connect_to_bus`` creates the
``Scs0009PyController`` lazily; ``disconnect_from_bus`` drops the reference.
The Rust side releases the COM handle when the wrapper is dropped.

Calibration-aware (Option B per ``docs/plans/01-unified-gui-spec.md`` §5.2):
slider/keyboard inputs and ``state_changed`` emissions are in logical frame;
internal conversion to servo-frame radians uses
``arm101_hand.hand.kinematics``.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections import deque
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from arm101_hand.config import HandCalibration
from arm101_hand.hand.kinematics import (
    degrees_to_servo_radians,
    servo_radians_to_degrees,
)

log = logging.getLogger(__name__)

SERVO_IDS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)
_VOLTAGE_DECAVOLT_TO_VOLT = 0.1  # Feetech u8 register reports volts in 0.1-V units.


def _scalar(value: Any) -> float:
    """Unwrap rustypot's per-register reads into a Python float.

    The rustypot Python bindings return ``list[T]`` from ``read_<reg>(id)``
    (a single-element list, kept symmetric with the multi-id ``sync_read_<reg>``
    shape). See ``references/rustypot/CLAUDE.md`` — "Per-register methods
    auto-generated: ``read_<reg>(id: int) -> list``". The discovery cost us a
    bench-test cycle in M3, so the unwrap is centralized here with the citation.
    """
    if isinstance(value, list | tuple):
        if not value:
            raise ValueError("rustypot read returned empty list")
        return float(value[0])
    return float(value)


class HandController(QObject):
    """One-port driver for the AmazingHand. See module docstring for frames."""

    # === Signals (UI thread consumers) ====================================
    # ``state_changed`` carries a ``dict[int, dict[str, float]]`` keyed by
    # servo id. We declare it as ``Signal(object)`` rather than ``Signal(dict)``
    # because PySide6's ``Signal(dict)`` maps to ``QVariantMap`` which requires
    # string keys — int keys silently drop. ``object`` passes the Python dict
    # through unchanged.
    state_changed = Signal(object)
    connected = Signal(str)  # port
    disconnected = Signal(str)  # reason: "user" | "error: <msg>"
    error = Signal(str)
    warning = Signal(str, str)  # severity ("warn"|"critical"), message

    # DeviceProto requires a ``name`` attribute. Class-level default; instance
    # attribute is set in ``__init__`` so isinstance checks see it.
    name: str = "hand"

    def __init__(
        self,
        calibration: HandCalibration,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.name = "hand"  # explicit instance attr for DeviceProto runtime_checkable
        self._calibration = calibration
        self._middle_pos_by_id: dict[int, float] = calibration.middle_pos_by_id()
        self._io_lock = threading.Lock()
        # ``Any`` rather than ``rustypot.Scs0009PyController`` because the wheel
        # ships no type stubs (consistent with ``mypy.ignore_missing_imports``).
        self._controller: Any | None = None
        self._port: str = ""
        # Buffered (sid, deg_rel_logical, speed) targets from the slider/keyboard
        # path. ``send_servo_target`` appends; ``_dispatch_pending`` drains under
        # the lock; ``drain_queue`` (DeviceProto) clears on E-STOP entry.
        self._pending: deque[tuple[int, float, int]] = deque()
        self._park_velocity: int | None = None

    # === DeviceProto =====================================================

    def is_connected(self) -> bool:
        return self._controller is not None

    def drain_queue(self) -> None:
        with self._io_lock:
            self._pending.clear()

    def set_velocity(self, velocity: int) -> None:
        """Set goal_speed on every servo before the park ``send_pose`` call.

        Spec §7.5 step 2: park motion uses ``safe_park.park_velocity_hand``
        (gentle, default 2 on the 1-5 hand scale), not the user's last
        commanded speed.
        """
        with self._io_lock:
            self._park_velocity = velocity
            if self._controller is None:
                return
            for sid in SERVO_IDS:
                try:
                    self._controller.write_goal_speed(sid, velocity)
                except Exception as e:  # pragma: no cover — surfaced via signal in tests
                    self.error.emit(f"set_velocity sid={sid} failed: {e}")
                    return

    def send_pose(self, pose: dict[str, float]) -> None:
        """Write goal_position for the keys in ``pose``.

        ``pose`` keys are stringified servo IDs (``"1".."8"``); values are in
        **YAML/servo frame** (even-ID pre-inverted). The frame matches
        ``MainWindow.hand_resolver``'s output, so ``SafePark`` plugs in with
        no conversion at the call site.
        """
        with self._io_lock:
            if self._controller is None:
                raise RuntimeError("hand controller not connected")
            for sid_str, yaml_deg in pose.items():
                sid = int(sid_str)
                mp = self._middle_pos_by_id[sid]
                # YAML frame: servo_deg = mp + yaml_deg (no even-ID inversion;
                # the YAML already pre-inverts so visual symmetry holds).
                radians = float(np.deg2rad(mp + yaml_deg))
                self._controller.write_goal_position(sid, radians)

    def read_positions(self) -> dict[str, float]:
        """Return per-servo positions in **YAML/servo frame**, keyed by str(sid).

        Matches the frame that ``send_pose`` accepts so ``SafePark``'s
        tolerance check ``abs(positions[k] - target[k])`` is well-defined.
        """
        with self._io_lock:
            if self._controller is None:
                raise RuntimeError("hand controller not connected")
            out: dict[str, float] = {}
            for sid in SERVO_IDS:
                mp = self._middle_pos_by_id[sid]
                radians = _scalar(self._controller.read_present_position(sid))
                # Inverse of send_pose's mapping: yaml_deg = rad2deg(rad) - mp.
                out[str(sid)] = float(np.rad2deg(radians)) - mp
            return out

    def disable_torque_all(self) -> None:
        """Best-effort torque-off across all 8 servos. Idempotent.

        Clears the pending deque first so any queued slider drag can't
        re-enable motion after the cut. Per-servo writes are wrapped in
        try/except — one bad servo doesn't prevent the others from cutting.
        """
        with self._io_lock:
            if self._controller is None:
                return
            self._pending.clear()
            for sid in SERVO_IDS:
                try:
                    self._controller.write_torque_enable(sid, 0)
                except Exception as e:
                    log.warning("disable_torque_all sid=%d failed: %s", sid, e)

    # === Qt slots (UI thread → QThread, queued connections) ==============

    @Slot(str, int, float)
    def connect_to_bus(self, port: str, baudrate: int, timeout: float) -> None:
        """Open the rustypot controller on ``port`` and enable torque.

        Idempotent on re-entry: if already connected, disconnects first.
        Emits ``connected(port)`` on success or ``error(message)`` on failure.
        """
        from rustypot import Scs0009PyController  # noqa: PLC0415 — keep import lazy

        with self._io_lock:
            if self._controller is not None:
                # Drop the old handle before opening a new one (IL-4: single owner).
                try:
                    for sid in SERVO_IDS:
                        with contextlib.suppress(Exception):
                            self._controller.write_torque_enable(sid, 0)
                finally:
                    self._controller = None
                    self._port = ""
            try:
                self._controller = Scs0009PyController(
                    serial_port=port,
                    baudrate=baudrate,
                    timeout=timeout,
                )
                for sid in SERVO_IDS:
                    self._controller.write_torque_enable(sid, 1)
            except Exception as e:
                self._controller = None
                self._port = ""
                self.error.emit(f"connect failed: {e}")
                return
            self._port = port
        self.connected.emit(port)

    @Slot()
    def disconnect_from_bus(self) -> None:
        """Cut torque on every servo and drop the controller handle.

        Always emits ``disconnected("user")`` so the UI status badge flips
        even if the per-servo torque-off raised on the way down (IL-4 — we
        still need the COM port released).
        """
        with self._io_lock:
            if self._controller is None:
                return
            self._pending.clear()
            try:
                for sid in SERVO_IDS:
                    try:
                        self._controller.write_torque_enable(sid, 0)
                    except Exception as e:
                        log.warning("disconnect torque-off sid=%d failed: %s", sid, e)
            finally:
                self._controller = None
                self._port = ""
        self.disconnected.emit("user")

    @Slot(int, float, int)
    def send_servo_target(self, servo_id: int, deg_rel_logical: float, speed: int) -> None:
        """Buffer a single-servo target and immediately drain the deque.

        ``deg_rel_logical`` is in **logical frame** — what sliders and the
        keyboard handler emit. Conversion to servo-frame radians (even-ID
        inversion + middle_pos add) happens in ``_dispatch_pending``.
        """
        with self._io_lock:
            self._pending.append((servo_id, float(deg_rel_logical), int(speed)))
        self._dispatch_pending()

    @Slot(object)
    def send_batch_targets(self, targets: dict) -> None:
        """Buffer ``{sid: (deg_rel_logical, speed)}`` and drain.

        Used by the SequencePlayer for pose-step playback. Same frame as
        ``send_servo_target``.
        """
        with self._io_lock:
            for sid, payload in targets.items():
                deg_rel, speed = payload
                self._pending.append((int(sid), float(deg_rel), int(speed)))
        self._dispatch_pending()

    @Slot()
    def disable_torque_all_slot(self) -> None:
        """Qt slot wrapper around ``disable_torque_all`` for queued invocation."""
        self.disable_torque_all()

    @Slot()
    def poll_state(self) -> None:
        """Read pos/load/temp/voltage from all 8 servos and emit ``state_changed``.

        Position output is in **logical frame** (same as the slider) so panel
        live readouts match the slider track. SafePark uses ``read_positions``
        (YAML frame) for arrival checks — these are two different frames for
        two different consumers, both correct.
        """
        with self._io_lock:
            if self._controller is None:
                return
            state: dict[int, dict[str, float]] = {}
            for sid in SERVO_IDS:
                try:
                    radians = _scalar(self._controller.read_present_position(sid))
                    load_raw = _scalar(self._controller.read_present_load(sid))
                    temp_c = _scalar(self._controller.read_present_temperature(sid))
                    voltage_raw = _scalar(self._controller.read_present_voltage(sid))
                except Exception as e:
                    self.error.emit(f"poll failed sid={sid}: {e}")
                    return
                mp = self._middle_pos_by_id[sid]
                deg_logical = servo_radians_to_degrees(sid, radians, mp)
                state[sid] = {
                    "pos_deg": deg_logical,
                    "load_pct": float(abs(int(load_raw))),
                    "temp_c": float(temp_c),
                    "voltage_v": float(voltage_raw) * _VOLTAGE_DECAVOLT_TO_VOLT,
                }
        self.state_changed.emit(state)

    # === internals =======================================================

    def _dispatch_pending(self) -> None:
        """Drain the ``_pending`` deque under the lock, one I/O write per item.

        Holding the lock across the rustypot write keeps a concurrent
        ``drain_queue`` / ``send_pose`` strictly ordered relative to whatever
        is currently being written. Worst case: one stale slider drag commits
        before SafePark's ``send_pose`` overwrites it — harmless because
        ``send_pose`` writes the same servo right after.
        """
        while True:
            with self._io_lock:
                if not self._pending or self._controller is None:
                    return
                sid, deg_rel_logical, speed = self._pending.popleft()
                mp = self._middle_pos_by_id[sid]
                radians = degrees_to_servo_radians(sid, deg_rel_logical, mp)
                try:
                    self._controller.write_goal_speed(sid, speed)
                    self._controller.write_goal_position(sid, radians)
                except Exception as e:
                    self.error.emit(f"send sid={sid} failed: {e}")
                    return
