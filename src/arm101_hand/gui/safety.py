"""Safety subsystem: ``SafePark`` orchestrator + ``ThresholdEvaluator`` + ``SafetyPoller`` skeleton.

Application layer (per ``docs/conventions/01-module-layering.md``). Imports from
``arm101_hand.config`` (primitive) and depends on a structural ``DeviceProto``
contract — never on a concrete device implementation. The future
``HandController`` (M3) and ``ArmWorker`` (M4) satisfy ``DeviceProto`` without
importing it.

See ``docs/plans/01-unified-gui-spec.md`` §7 for the design contract:

- §7.1 / §7.2 — ``ThresholdEvaluator``: 5-sample rolling window for temp,
  single-sample percent-deviation for voltage.
- §7.3 — soft vs. hard E-STOP.
- §7.5 — six-step safe-park sequence (drain → set velocity → send pose →
  poll arrival within tolerance OR timeout → disable torque → log).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from PySide6.QtCore import QObject, QThread, Signal

from arm101_hand.config import AppConfig
from arm101_hand.config.app_config import Safety

Level = Literal["info", "warn", "error"]
Mode = Literal["soft", "hard"]
Reason = Literal["user", "temp_critical", "voltage_critical", "app_exit"]
Severity = Literal["ok", "warn", "critical"]

TEMP_WINDOW_SIZE = 5


# -----------------------------------------------------------------------------
# DeviceProto + NullDevice
# -----------------------------------------------------------------------------


@runtime_checkable
class DeviceProto(Protocol):
    """Contract that ``SafePark`` drives. Pose units are caller-defined
    (calibration-aware degrees for the hand, physical degrees for the arm); the
    only requirement is that ``send_pose`` keys match the keys ``read_positions``
    returns so the tolerance check is well-defined.
    """

    name: str

    def is_connected(self) -> bool: ...
    def drain_queue(self) -> None: ...
    def set_velocity(self, velocity: int) -> None: ...
    def send_pose(self, pose: dict[str, float]) -> None: ...
    def read_positions(self) -> dict[str, float]: ...
    def disable_torque_all(self) -> None: ...


class NullDevice:
    """Placeholder used before a real controller is registered.

    Always reports ``is_connected() = False`` so ``SafePark`` short-circuits
    to a "no device" log line without ever calling the action methods. M3 and
    M4 swap this out for ``HandController`` / ``ArmWorker``.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def is_connected(self) -> bool:
        return False

    def drain_queue(self) -> None:
        raise RuntimeError(f"NullDevice({self.name!r}).drain_queue called — should be short-circuited")

    def set_velocity(self, velocity: int) -> None:
        raise RuntimeError(f"NullDevice({self.name!r}).set_velocity called")

    def send_pose(self, pose: dict[str, float]) -> None:
        raise RuntimeError(f"NullDevice({self.name!r}).send_pose called")

    def read_positions(self) -> dict[str, float]:
        raise RuntimeError(f"NullDevice({self.name!r}).read_positions called")

    def disable_torque_all(self) -> None:
        raise RuntimeError(f"NullDevice({self.name!r}).disable_torque_all called")


# -----------------------------------------------------------------------------
# SafePark orchestrator
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _DeviceRegistration:
    device: DeviceProto
    pose_resolver: Callable[[], dict[str, float] | None]
    pose_name: str
    park_velocity: int
    range_check: Callable[[dict[str, float]], list[str]]


ExecutorFn = Callable[[list[Callable[[], None]]], None]
LogFn = Callable[[str, Level], None]


def _format_event(device_name: str, mode: Mode, reason: Reason, outcome: str) -> str:
    return f"[{device_name}] safe-park: {outcome} (mode={mode}, reason={reason})"


def _default_executor(jobs: list[Callable[[], None]]) -> None:
    """Spawn one daemon thread per job and return immediately.

    Daemon so a hung device doesn't block process exit. Callers don't ``join``;
    completion is tracked via ``SafePark._busy_count``.
    """
    for job in jobs:
        threading.Thread(target=job, daemon=True).start()


class SafePark:
    """Orchestrate park-then-disable on E-STOP. Plain Python class (no Qt
    parent) so unit tests don't need a ``QApplication``. Production wires its
    ``log`` callback to a thread-safe ``Signal.emit`` on ``MainWindow``.
    """

    def __init__(
        self,
        config: AppConfig,
        log: LogFn,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep_step_s: float = 0.05,
        executor: ExecutorFn | None = None,
    ) -> None:
        self._safety = config.safety
        self._log = log
        self._now = now
        self._sleep_step_s = sleep_step_s
        self._executor = executor or _default_executor
        self._devices: list[_DeviceRegistration] = []
        self._busy_lock = threading.Lock()
        self._busy_count = 0

    def register_device(
        self,
        device: DeviceProto,
        *,
        pose_resolver: Callable[[], dict[str, float] | None],
        pose_name: str,
        park_velocity: int,
        range_check: Callable[[dict[str, float]], list[str]] = lambda _: [],
    ) -> None:
        self._devices.append(
            _DeviceRegistration(
                device=device,
                pose_resolver=pose_resolver,
                pose_name=pose_name,
                park_velocity=park_velocity,
                range_check=range_check,
            )
        )

    def is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy_count > 0

    def engage_soft(self, reason: Reason) -> None:
        self._engage("soft", reason)

    def engage_hard(self, reason: Reason) -> None:
        self._engage("hard", reason)

    # --- internals ------------------------------------------------------

    def _engage(self, mode: Mode, reason: Reason) -> None:
        with self._busy_lock:
            # Soft is rejected during an in-flight park (spec §7.5 "second
            # critical event during a park → ignored"). Hard always proceeds —
            # see "user can press hard E-STOP at any time to override".
            if self._busy_count > 0 and mode == "soft":
                self._log(
                    f"safe-park: ignored (busy, mode=soft, reason={reason})",
                    "warn",
                )
                return
            if not self._devices:
                return
            self._busy_count += len(self._devices)
        jobs = [self._make_job(reg, mode, reason) for reg in self._devices]
        self._executor(jobs)

    def _make_job(
        self,
        reg: _DeviceRegistration,
        mode: Mode,
        reason: Reason,
    ) -> Callable[[], None]:
        def job() -> None:
            try:
                if mode == "hard":
                    self._run_hard(reg, reason)
                else:
                    self._run_soft(reg, reason)
            finally:
                with self._busy_lock:
                    self._busy_count -= 1

        return job

    def _run_hard(self, reg: _DeviceRegistration, reason: Reason) -> None:
        device = reg.device
        if not device.is_connected():
            self._log(_format_event(device.name, "hard", reason, "no device"), "info")
            return
        device.drain_queue()
        device.disable_torque_all()
        self._log(
            _format_event(device.name, "hard", reason, "disabled torque (no park)"),
            "warn",
        )

    def _run_soft(self, reg: _DeviceRegistration, reason: Reason) -> None:
        device = reg.device
        if not device.is_connected():
            self._log(_format_event(device.name, "soft", reason, "no device"), "info")
            return

        if not self._safety.safe_park.enabled:
            device.drain_queue()
            device.disable_torque_all()
            self._log(
                _format_event(device.name, "soft", reason, "disabled torque (safe-park disabled)"),
                "warn",
            )
            return

        pose = reg.pose_resolver()
        if pose is None:
            device.drain_queue()
            device.disable_torque_all()
            self._log(
                _format_event(device.name, "soft", reason, f"pose missing: {reg.pose_name!r}"),
                "error",
            )
            return

        out_of_range = reg.range_check(pose)
        if out_of_range:
            device.drain_queue()
            device.disable_torque_all()
            self._log(
                _format_event(
                    device.name,
                    "soft",
                    reason,
                    f"pose {reg.pose_name!r} out of range: {', '.join(out_of_range)}",
                ),
                "error",
            )
            return

        # Spec §7.5 steps 1-5.
        device.drain_queue()
        device.set_velocity(reg.park_velocity)
        device.send_pose(pose)
        started = self._now()
        deadline = started + self._safety.safe_park.park_timeout_s
        tolerance = self._safety.safe_park.arrival_tolerance_deg

        parked = False
        while True:
            positions = device.read_positions()
            if all(
                abs(positions.get(motor, float("inf")) - target) <= tolerance
                for motor, target in pose.items()
            ):
                parked = True
                break
            if self._now() >= deadline:
                break
            if self._sleep_step_s > 0:
                time.sleep(self._sleep_step_s)

        elapsed = self._now() - started
        device.disable_torque_all()
        if parked:
            self._log(
                _format_event(device.name, "soft", reason, f"parked in {elapsed:.2f}s"),
                "info",
            )
        else:
            self._log(
                _format_event(device.name, "soft", reason, f"timed out at {elapsed:.2f}s, disabled torque"),
                "warn",
            )


# -----------------------------------------------------------------------------
# ThresholdEvaluator + SafetyPoller skeleton
# -----------------------------------------------------------------------------


@dataclass
class ThresholdEvaluator:
    """Pure-logic warn/critical classifier for temperature + voltage samples.

    Temperature uses a per-motor 5-sample rolling window so a single noisy
    reading doesn't trigger auto-disable (spec §7.1). Voltage classifies on
    a single sample — bus over/undervolt is a wiring fault, not heat, so
    averaging would just mask the alert (§7.2).
    """

    safety: Safety
    bus_nominal_voltages: dict[str, float]
    window_size: int = TEMP_WINDOW_SIZE
    _temp_windows: dict[tuple[str, str], deque[float]] = field(default_factory=dict, init=False, repr=False)

    def record_temp(self, device: str, motor: str, c: float) -> Severity:
        key = (device, str(motor))
        window = self._temp_windows.setdefault(key, deque(maxlen=self.window_size))
        window.append(c)
        # Critical only escalates when EVERY sample in a full window is at-or-above
        # the critical threshold — that's how the rolling window suppresses spikes.
        if len(window) == self.window_size and all(s >= self.safety.temp_critical_c for s in window):
            return "critical"
        latest = window[-1]
        if latest >= self.safety.temp_warn_c:
            return "warn"
        return "ok"

    def record_voltage(self, device: str, observed_v: float) -> Severity:
        nominal = self.bus_nominal_voltages.get(device)
        if nominal is None or nominal <= 0:
            return "ok"
        deviation_pct = abs(observed_v - nominal) / nominal * 100.0
        if deviation_pct >= self.safety.voltage_critical_pct:
            return "critical"
        if deviation_pct >= self.safety.voltage_warn_pct:
            return "warn"
        return "ok"


class SafetyPoller(QThread):
    """1 Hz background poller for both buses.

    Skeleton in M2 — the ``run`` body is a no-op. M3 / M4 add the actual
    ``read_positions`` / ``read_temps`` calls once ``HandController`` and
    ``ArmWorker`` exist; the threshold evaluator is already unit-tested here
    so the integration step is just plumbing.
    """

    severity_changed = Signal(str, str, str)  # device, kind ("temp"|"voltage"), severity

    def __init__(
        self,
        evaluator: ThresholdEvaluator,
        poll_period_s: float,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._evaluator = evaluator
        self._period_s = poll_period_s
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:  # noqa: D401 — Qt API
        # M2 stub. M3 will iterate over registered controllers and feed samples
        # into ``self._evaluator``, emitting ``severity_changed`` on transitions.
        return
