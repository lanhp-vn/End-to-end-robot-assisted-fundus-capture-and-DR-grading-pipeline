"""Main window shell: header (status badges + E-STOP), tab widget, activity log.

Per spec §4.3, the header is **outside** the tab widget so it stays visible
from any tab. ``Esc`` / ``Shift+Esc`` are window-level shortcuts that drive
the same soft / hard signals as clicking the button.

Milestone 2 wires the ``SafePark`` orchestrator into those signals — devices
are still placeholders (``NullDevice``) so a press writes "no device" outcome
lines to the activity log without ever touching a bus. M3 / M4 swap in real
controllers via ``SafePark.register_device``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from arm101_hand.config import AppConfig, load_arm_poses, load_hand_poses
from arm101_hand.gui.safety import Mode, NullDevice, SafePark
from arm101_hand.gui.widgets import ActivityLog, EStopButton, StatusBadge

# Repo root: <root>/src/arm101_hand/gui/main_window.py → parents[3] = <root>
_REPO_ROOT = Path(__file__).resolve().parents[3]
HAND_CONFIG_PATH = _REPO_ROOT / "data" / "hand_config.yaml"
ARM_CONFIG_PATH = _REPO_ROOT / "data" / "arm_config.yaml"


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

        self._build_layout()
        self._wire_log_signal()
        self._safe_park = self._build_safe_park()
        self._wire_estop()
        self._install_shortcuts()
        self._restore_window_state()

        self._log.append("arm101-gui started — Milestone 2 (SafePark wired, no devices yet).", "info")

    def _build_layout(self) -> None:
        header = QHBoxLayout()
        header.addWidget(self._hand_badge)
        header.addSpacing(12)
        header.addWidget(self._arm_badge)
        header.addStretch(1)
        header.addWidget(self._estop)

        # Placeholder tabs — populated in M3 (hand) and M4 (arm).
        hand_placeholder = QLabel("Hand panel — coming in Milestone 3.")
        hand_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hand_placeholder.setStyleSheet("color: #888; font-size: 14px;")
        arm_placeholder = QLabel("Arm panel — coming in Milestone 4.")
        arm_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arm_placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self._tabs.addTab(hand_placeholder, "Hand")
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
            # Servo IDs 1..8 stringified — same key convention the future
            # HandController (M3) will use for ``send_pose`` / ``read_positions``.
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

    def _restore_window_state(self) -> None:
        # Active tab: "hand" → 0, "arm" → 1.
        self._tabs.setCurrentIndex(0 if self._config.window.active_tab == "hand" else 1)
        self._log.set_visible(self._config.window.log_panel_visible)

    def _on_estop_pressed(self, mode: Mode) -> None:
        # Auto-open the log panel so the user sees the safe-park outcomes.
        self._log.set_visible(True)
        if mode == "soft":
            self._safe_park.engage_soft("user")
        else:
            self._safe_park.engage_hard("user")
