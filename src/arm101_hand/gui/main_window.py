"""Main window shell: header (status badges + E-STOP), tab widget, activity log.

Per spec §4.3, the header is **outside** the tab widget so it stays visible
from any tab. ``Esc`` / ``Shift+Esc`` are window-level shortcuts that drive
the same soft / hard signals as clicking the button.

Milestone 3 — first hardware contact:

- Hand bus brought up on its own ``QThread`` via ``HandController``.
- ``HandPanel`` replaces the M1 placeholder ``QLabel`` and drives the
  controller through queued signals.
- On ``HandController.connected``: ``SafePark.replace_device("hand", controller)``
  so soft E-STOP starts actually parking the hand. On ``disconnected``: swap
  back to ``NullDevice("hand")``.
- The arm tab still shows the M1 placeholder until M4 lands.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from arm101_hand.config import (
    AppConfig,
    load_arm_poses,
    load_hand_calibration,
    load_hand_poses,
)
from arm101_hand.gui.hand_panel import HandPanel
from arm101_hand.gui.safety import Mode, NullDevice, SafePark, ThresholdEvaluator
from arm101_hand.gui.widgets import ActivityLog, EStopButton, StatusBadge
from arm101_hand.hand.controller import HandController

# Repo root: <root>/src/arm101_hand/gui/main_window.py → parents[3] = <root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
HAND_CONFIG_PATH = _REPO_ROOT / "data" / "hand_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"
HAND_CALIBRATION_PATH = (
    _REPO_ROOT / "scripts" / "calibration" / "AmazingHand" / "AmazingHand_calib_values.yaml"
)


class MainWindow(QMainWindow):
    """Top-level window. Holds the header, tab widget, and activity log."""

    # Cross-thread log emitter. ``SafePark`` may dispatch device jobs on worker
    # threads (default executor in production); routing log lines through this
    # signal — connected to ``ActivityLog.append`` via ``Qt.QueuedConnection`` —
    # keeps the actual ``QTextEdit`` writes on the main thread.
    log_event = Signal(str, str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("arm101-gui — Unified Manual Control")
        self.resize(config.window.width, config.window.height)

        self._hand_badge = StatusBadge("Hand")
        self._arm_badge = StatusBadge("Arm")
        self._estop = EStopButton()
        self._tabs = QTabWidget()
        self._log = ActivityLog()

        # Calibration is required for the hand controller's frame conversions.
        self._hand_calibration = load_hand_calibration(HAND_CALIBRATION_PATH)
        self._threshold_eval = ThresholdEvaluator(
            safety=config.safety,
            bus_nominal_voltages={"hand": 5.0, "arm": 12.0},
        )

        # Controllers live on their own QThreads; bus I/O never touches the UI thread.
        self._hand_thread = QThread(self)
        self._hand_thread.setObjectName("HandControllerThread")
        self._hand_controller = HandController(self._hand_calibration)
        self._hand_controller.moveToThread(self._hand_thread)
        self._hand_thread.start()

        self._build_layout()
        self._wire_log_signal()
        self._safe_park = self._build_safe_park()
        self._wire_estop()
        self._install_shortcuts()
        self._wire_hand_controller()
        self._restore_window_state()

        self._log.append("arm101-gui started — Milestone 3 (hand bus on COM18 once connected).", "info")

    # === wiring ===========================================================

    def _build_layout(self) -> None:
        header = QHBoxLayout()
        header.addWidget(self._hand_badge)
        header.addSpacing(12)
        header.addWidget(self._arm_badge)
        header.addStretch(1)
        header.addWidget(self._estop)

        self._hand_panel = HandPanel(self._config, HAND_CONFIG_PATH)
        arm_placeholder = QLabel("Arm panel — coming in Milestone 4.")
        arm_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arm_placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self._tabs.addTab(self._hand_panel, "Hand")
        self._tabs.addTab(arm_placeholder, "Arm")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(self._log)
        self.setCentralWidget(central)

    def _wire_log_signal(self) -> None:
        self.log_event.connect(self._log.append, Qt.ConnectionType.QueuedConnection)

    def _build_safe_park(self) -> SafePark:
        # Eager YAML load: a malformed pose file should fail at startup, not
        # the first time the user hits Esc.
        hand_poses = load_hand_poses(HAND_CONFIG_PATH)
        arm_poses = load_arm_poses(ARM_CONFIG_PATH)

        sp = SafePark(config=self._config, log=self.log_event.emit)

        hand_pose_name = self._config.safety.safe_park.hand_pose

        def hand_resolver() -> dict[str, float] | None:
            pose = hand_poses.poses.get(hand_pose_name)
            if pose is None:
                return None
            # Servo IDs 1..8 stringified — same key convention HandController
            # uses for ``send_pose`` / ``read_positions``.
            return {str(i + 1): float(deg) for i, deg in enumerate(pose.positions)}

        sp.register_device(
            NullDevice("hand"),
            pose_resolver=hand_resolver,
            pose_name=hand_pose_name,
            park_velocity=self._config.safety.safe_park.park_velocity_hand,
        )

        arm_pose_name = self._config.safety.safe_park.arm_pose

        def arm_resolver() -> dict[str, float] | None:
            pose = arm_poses.quick_poses.get(arm_pose_name) or arm_poses.poses.get(arm_pose_name)
            if pose is None:
                return None
            return pose.as_dict()

        sp.register_device(
            NullDevice("arm"),
            pose_resolver=arm_resolver,
            pose_name=arm_pose_name,
            park_velocity=self._config.safety.safe_park.park_velocity_arm,
        )

        return sp

    def _wire_estop(self) -> None:
        self._estop.soft_pressed.connect(lambda: self._on_estop_pressed("soft"))
        self._estop.hard_pressed.connect(lambda: self._on_estop_pressed("hard"))

    def _install_shortcuts(self) -> None:
        soft = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        soft.setContext(Qt.ShortcutContext.WindowShortcut)
        soft.activated.connect(self._estop.trigger_soft)

        hard = QShortcut(QKeySequence("Shift+Esc"), self)
        hard.setContext(Qt.ShortcutContext.WindowShortcut)
        hard.activated.connect(self._estop.trigger_hard)

    def _wire_hand_controller(self) -> None:
        # Panel → controller (queued to cross thread boundaries).
        QC = Qt.ConnectionType.QueuedConnection  # noqa: N806 — local readability
        self._hand_panel.connect_requested.connect(self._hand_controller.connect_to_bus, QC)
        self._hand_panel.disconnect_requested.connect(self._hand_controller.disconnect_from_bus, QC)
        self._hand_panel.target_changed.connect(self._hand_controller.send_servo_target, QC)
        self._hand_panel.batch_targets_requested.connect(self._hand_controller.send_batch_targets, QC)
        self._hand_panel.disable_torque_requested.connect(self._hand_controller.disable_torque_all_slot, QC)

        # Controller → main window (auto-detected as queued since they live on
        # different threads).
        self._hand_controller.connected.connect(self._on_hand_connected)
        self._hand_controller.disconnected.connect(self._on_hand_disconnected)
        self._hand_controller.error.connect(self._on_hand_error)
        self._hand_controller.state_changed.connect(self._on_hand_state_changed)

    def _restore_window_state(self) -> None:
        # Active tab: "hand" → 0, "arm" → 1.
        self._tabs.setCurrentIndex(0 if self._config.window.active_tab == "hand" else 1)
        self._log.set_visible(self._config.window.log_panel_visible)

    # === handlers ==========================================================

    def _on_estop_pressed(self, mode: Mode) -> None:
        # Auto-open the log panel so the user sees the safe-park outcomes.
        self._log.set_visible(True)
        if mode == "soft":
            self._safe_park.engage_soft("user")
        else:
            self._safe_park.engage_hard("user")

    def _on_hand_connected(self, port: str) -> None:
        self._safe_park.replace_device("hand", self._hand_controller)
        self._hand_panel.on_connection_state(True)
        self._hand_badge.set_state(severity="ok", port=port)
        self.log_event.emit(f"hand: connected on {port}", "info")

    def _on_hand_disconnected(self, reason: str) -> None:
        self._safe_park.replace_device("hand", NullDevice("hand"))
        self._hand_panel.on_connection_state(False)
        self._hand_badge.set_state(severity="disconnected")
        self.log_event.emit(f"hand: disconnected ({reason})", "info")

    def _on_hand_error(self, message: str) -> None:
        self._hand_badge.set_state(severity="warn", port=self._hand_badge.text())
        self.log_event.emit(f"hand: {message}", "error")

    def _on_hand_state_changed(self, state: dict) -> None:
        # Push to the panel for live readouts.
        self._hand_panel.on_state_changed(state)
        # Aggregate to a header severity using the existing ThresholdEvaluator.
        worst: str = "ok"
        max_temp: float | None = None
        sample_voltage: float | None = None
        for sid, sample in state.items():
            temp_severity = self._threshold_eval.record_temp("hand", str(sid), float(sample["temp_c"]))
            voltage_severity = self._threshold_eval.record_voltage("hand", float(sample["voltage_v"]))
            for s in (temp_severity, voltage_severity):
                if s == "critical":
                    worst = "critical"
                elif s == "warn" and worst != "critical":
                    worst = "warn"
            if max_temp is None or sample["temp_c"] > max_temp:
                max_temp = float(sample["temp_c"])
            sample_voltage = float(sample["voltage_v"])  # last one wins; bus-shared
        self._hand_badge.set_state(
            severity=worst,  # type: ignore[arg-type]  # ThresholdEvaluator returns the right Literal
            port=self._config.hand.port,
            voltage_v=sample_voltage,
            temp_c_max=max_temp,
        )

    # === lifecycle =========================================================

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        # Best-effort: cut torque and stop the controller's thread before exit.
        with contextlib.suppress(Exception):
            self._hand_controller.disable_torque_all()
        self._hand_thread.quit()
        self._hand_thread.wait(2000)
        super().closeEvent(event)
