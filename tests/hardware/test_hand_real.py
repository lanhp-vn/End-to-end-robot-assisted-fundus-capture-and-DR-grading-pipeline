"""Hardware smoke test for the AmazingHand bus on COM18 (5 V SCS0009 × 8).

Skipped by default. Run with::

    uv run pytest -m hardware --port=COM18

Pre-flight (per IL-1 / IL-4):
- 5 V supply on the hand bus only — the 12 V arm bus stays cold.
- No other process holds COM18 (close FD.exe, serial monitors, stale Python).

The test exercises the M3 milestone's bench checkpoint: connect, poll all 8
servos, nudge the Pointer's base by +5°, wait for arrival, return to middle,
torque-off, disconnect. All bus interaction goes through ``HandController``
on its own ``QThread`` — same code path as the GUI.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QThread

from arm101_hand.config import load_hand_calibration
from arm101_hand.hand.controller import HandController

CALIBRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "calibration"
    / "AmazingHand"
    / "AmazingHand_calib_values.yaml"
)
ARRIVAL_TOLERANCE_DEG = 3.0
ARRIVAL_TIMEOUT_S = 4.0


@pytest.fixture(scope="module")
def qcore_app() -> QCoreApplication:
    """One QCoreApplication for the module so QObject signals deliver."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def _spin(app: QCoreApplication, seconds: float) -> None:
    """Pump the event loop briefly so queued slot calls fire on workers.

    Hardware tests are intentionally allowed to use ``time``-based waits
    (``04-testing-verification.md`` §3.3 forbids ``time.sleep`` in *unit*
    tests; on-bench tests run against physical motion that has its own
    settling time).
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


@pytest.mark.hardware
def test_hand_bus_smoke_test(port: str, qcore_app: QCoreApplication) -> None:
    calibration = load_hand_calibration(CALIBRATION_PATH)

    thread = QThread()
    thread.setObjectName("HandControllerHardwareTest")
    controller = HandController(calibration)
    controller.moveToThread(thread)
    thread.start()

    connected: list[str] = []
    disconnected: list[str] = []
    errors: list[str] = []
    states: list[dict] = []

    controller.connected.connect(connected.append)
    controller.disconnected.connect(disconnected.append)
    controller.error.connect(errors.append)
    controller.state_changed.connect(states.append)

    try:
        # Connect on the worker thread (queued invocation).
        controller.connect_to_bus(port, calibration.baudrate, calibration.timeout)
        _spin(qcore_app, 1.0)
        assert connected == [port], f"connected emitted with port; got {connected} (errors={errors})"
        assert controller.is_connected() is True, "is_connected True after connect"

        # Read state from all 8 servos.
        controller.poll_state()
        _spin(qcore_app, 0.5)
        assert states, f"state_changed emitted at least once; errors={errors}"
        latest = states[-1]
        assert set(latest.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}, (
            f"all 8 servos report; got {sorted(latest.keys())}"
        )
        for sid, sample in latest.items():
            assert sample["voltage_v"] > 4.0, (
                f"servo {sid} voltage_v above 4 V (5 V bus); got {sample['voltage_v']}"
            )
            assert 10.0 <= sample["temp_c"] <= 80.0, (
                f"servo {sid} temp_c within plausible range; got {sample['temp_c']}"
            )

        # Nudge Pointer (UI label, schema "index", IDs 1+2) base by +5°
        # in logical frame. Both servos move in the same direction.
        controller.send_servo_target(1, 5.0, 3)  # odd ID
        controller.send_servo_target(2, 5.0, 3)  # even ID
        _spin(qcore_app, 0.5)

        # Poll until both pointer servos report within tolerance, or timeout.
        deadline = time.monotonic() + ARRIVAL_TIMEOUT_S
        while time.monotonic() < deadline:
            controller.poll_state()
            _spin(qcore_app, 0.2)
            if states:
                last = states[-1]
                p1 = last[1]["pos_deg"]
                p2 = last[2]["pos_deg"]
                if abs(p1 - 5.0) <= ARRIVAL_TOLERANCE_DEG and abs(p2 - 5.0) <= ARRIVAL_TOLERANCE_DEG:
                    break
        else:
            pytest.fail(
                f"Pointer servos did not reach +5° within {ARRIVAL_TIMEOUT_S}s; "
                f"last state: {states[-1] if states else 'none'}"
            )

        # Return to middle.
        controller.send_servo_target(1, 0.0, 3)
        controller.send_servo_target(2, 0.0, 3)
        _spin(qcore_app, 1.5)

    finally:
        # Always tear down even if a mid-test assertion failed.
        controller.disconnect_from_bus()
        _spin(qcore_app, 1.0)
        thread.quit()
        thread.wait(2000)

    assert disconnected == ["user"], f"disconnected emitted on teardown; got {disconnected}"
    assert controller.is_connected() is False, "controller is disconnected after teardown"
    assert not errors, f"no errors during smoke test; got {errors}"
