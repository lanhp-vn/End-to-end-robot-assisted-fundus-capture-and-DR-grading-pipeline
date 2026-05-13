"""Hand tab UI (spec §5.3) — replaces the M1 placeholder ``QLabel``.

Drives an ``arm101_hand.hand.controller.HandController`` via Qt **queued**
signals — never reaches into the rustypot wrapper directly. The controller
runs on its own ``QThread`` (set up in ``MainWindow``) so all bus I/O happens
off the UI thread.

Frame conventions, restated for the cross-file invariant:

- The two sliders per finger are in **logical frame** — positive ``base``
  is "close", positive ``side`` is finger-specific lateral offset, and both
  servos in a finger pair move in the same direction in this frame.
- Hand pose YAML files store **YAML/servo frame** values (even-ID
  pre-inverted; see ``data/hand_config.yaml`` header). Save/load convert via
  ``even_id_inversion`` (self-inverse).
- ``HandController.send_servo_target`` accepts logical-frame degrees and
  performs the conversion to servo radians internally.

Keyboard handling (panel-scope ``keyPressEvent`` per spec §5.3):

- ``1`` / ``2`` / ``3`` / ``4`` — select Ring / Middle / Pointer / Thumb.
- ``Up`` / ``Down`` — step base by ±step (Up = close = +).
- ``Right`` / ``Left`` — step side by ±step.
- ``Shift`` modifier ⇒ step × 5; ``Ctrl`` modifier ⇒ step × 10; default 1°.
- ``Q`` — fully close selected (base=110, side=0).
- ``E`` — fully open selected (base=0, side=0).
- ``C`` — center side of selected (base unchanged, side=0).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arm101_hand.config import AppConfig, HandPose, HandPoseConfig, load_hand_poses
from arm101_hand.gui.pose_manager import PoseManagerWidget
from arm101_hand.gui.widgets import LabeledSlider
from arm101_hand.hand.kinematics import (
    compose_finger,
    decompose_finger,
    even_id_inversion,
    validate_pose_name,
)

log = logging.getLogger(__name__)

# UI label → (canonical schema key, odd servo ID, even servo ID).
# IL-3: schema key stays "index" for the pointer pair (1,2); the UI label is
# "Pointer" only for human readability.
FINGER_TABLE: tuple[tuple[str, str, int, int], ...] = (
    ("Ring", "ring", 5, 6),
    ("Middle", "middle", 3, 4),
    ("Pointer", "index", 1, 2),
    ("Thumb", "thumb", 7, 8),
)
NUM_FINGERS = len(FINGER_TABLE)

BASE_MIN, BASE_MAX = 0, 110
SIDE_MIN, SIDE_MAX = -40, 40
SPEED_MIN, SPEED_MAX = 1, 5

# Even-ID-pre-inverted servo target range (the YAML's per-servo cap from
# AmazingHandControl conventions); used to gate slider math.
_SERVO_LOGICAL_MIN, _SERVO_LOGICAL_MAX = -40, 110


class _FingerRow(QGroupBox):
    """One per-finger row: base + side sliders, per-finger speed combo, live readouts."""

    target_changed = Signal(int, float, int)  # sid, deg_rel_logical, speed
    finger_clicked = Signal(int)  # finger_index 0-3

    def __init__(
        self,
        finger_index: int,
        ui_label: str,
        odd_id: int,
        even_id: int,
        default_speed: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"{ui_label} ({finger_index + 1}) — IDs {odd_id},{even_id}", parent)
        self._finger_index = finger_index
        self._odd_id = odd_id
        self._even_id = even_id

        self._base = LabeledSlider("Pos", BASE_MIN, BASE_MAX, initial=0, units="°")
        self._side = LabeledSlider("Side", SIDE_MIN, SIDE_MAX, initial=0, units="°")
        self._speed = QComboBox()
        for s in range(SPEED_MIN, SPEED_MAX + 1):
            self._speed.addItem(str(s), s)
        self._speed.setCurrentText(str(default_speed))

        # Sliders shouldn't steal arrow keys — those go to the panel's
        # ``keyPressEvent`` instead (per spec §5.3).
        self._base.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._side.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._base.value_changed.connect(self._on_value_changed)
        self._side.value_changed.connect(self._on_value_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        layout.addWidget(self._base)
        layout.addWidget(self._side)
        speed_row = QHBoxLayout()
        speed_row.addStretch(1)
        speed_row.addWidget(QLabel("Speed:"))
        speed_row.addWidget(self._speed)
        layout.addLayout(speed_row)

        self._set_selected(False)

    # --- public API used by HandPanel ---------------------------------

    @property
    def finger_index(self) -> int:
        return self._finger_index

    @property
    def odd_id(self) -> int:
        return self._odd_id

    @property
    def even_id(self) -> int:
        return self._even_id

    def base_value(self) -> int:
        return self._base.value()

    def side_value(self) -> int:
        return self._side.value()

    def speed(self) -> int:
        return int(self._speed.currentData())

    def set_speed(self, speed: int) -> None:
        self._speed.setCurrentText(str(speed))

    def set_base(self, value: int, *, emit: bool = True) -> None:
        self._set_value(self._base, value, emit=emit)

    def set_side(self, value: int, *, emit: bool = True) -> None:
        self._set_value(self._side, value, emit=emit)

    def step_base(self, delta: int) -> None:
        self.set_base(max(BASE_MIN, min(BASE_MAX, self.base_value() + delta)))

    def step_side(self, delta: int) -> None:
        self.set_side(max(SIDE_MIN, min(SIDE_MAX, self.side_value() + delta)))

    def set_live(self, base_live_deg: float | None, side_live_deg: float | None) -> None:
        self._base.set_live(base_live_deg)
        self._side.set_live(side_live_deg)

    def set_selected(self, selected: bool) -> None:
        self._set_selected(selected)

    def emit_current_targets(self) -> None:
        """Emit ``target_changed`` for both servos using the current state."""
        self._on_value_changed(0)  # the int payload is unused

    # --- internals ----------------------------------------------------

    def mousePressEvent(self, event):  # noqa: N802 — Qt API
        # Click anywhere in the group box selects this finger.
        self.finger_clicked.emit(self._finger_index)
        super().mousePressEvent(event)

    def _set_value(self, slider: LabeledSlider, value: int, *, emit: bool) -> None:
        if not emit:
            slider.set_value(value)
            return
        # value_changed is connected; setting via slider's setValue would emit
        # twice if we manually call the signal. Use the slider's internal
        # signal-blocking setter then manually re-emit.
        slider.set_value(value)
        self._on_value_changed(value)

    def _on_value_changed(self, _value: int) -> None:
        base = self.base_value()
        side = self.side_value()
        speed = self.speed()
        # Logical frame: pos1 = base - side (odd), pos2 = base + side (even).
        pos1, pos2 = compose_finger(base, side, _SERVO_LOGICAL_MIN, _SERVO_LOGICAL_MAX)
        self.target_changed.emit(self._odd_id, float(pos1), speed)
        self.target_changed.emit(self._even_id, float(pos2), speed)

    def _set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                "QGroupBox { border: 2px solid #4fd1c5; border-radius: 4px; "
                "margin-top: 8px; padding-top: 6px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
            )
        else:
            self.setStyleSheet(
                "QGroupBox { border: 1px solid #444; border-radius: 4px; "
                "margin-top: 8px; padding-top: 6px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
            )


class HandPanel(QWidget):
    """Hand tab. Owns four ``_FingerRow`` widgets and the pose manager.

    Drives a ``HandController`` exclusively through Qt signals — no direct
    method calls — so the controller can live on its own thread.
    """

    # Signals consumed by ``MainWindow`` and routed to ``HandController``
    # via ``Qt.QueuedConnection``.
    connect_requested = Signal(str, int, float)  # port, baudrate, timeout
    disconnect_requested = Signal()
    target_changed = Signal(int, float, int)  # sid, deg_rel_logical, speed
    # ``object`` rather than ``dict`` — same int-key reason as the controller's
    # ``state_changed`` (PySide6 ``Signal(dict)`` is ``QVariantMap`` which
    # requires string keys).
    batch_targets_requested = Signal(object)  # {sid: (deg_rel_logical, speed)}
    disable_torque_requested = Signal()

    def __init__(
        self,
        app_config: AppConfig,
        hand_poses_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._app_config = app_config
        self._poses_path = hand_poses_path
        self._poses: HandPoseConfig = load_hand_poses(hand_poses_path)
        self._selected_finger = 0
        self._step_default = 1
        self._step_shift = 5
        self._step_ctrl = 10

        self._fingers: list[_FingerRow] = []

        # --- top row: connect controls ---
        self._port_input = QLineEdit(app_config.hand.port)
        self._port_input.setMaximumWidth(120)
        self._port_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._connect_btn = QPushButton("Connect")
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._disconnect_btn.clicked.connect(self.disconnect_requested.emit)

        self._global_speed = QComboBox()
        for s in range(SPEED_MIN, SPEED_MAX + 1):
            self._global_speed.addItem(str(s), s)
        self._global_speed.setCurrentText(str(app_config.hand.default_speed))
        self._sync_speed_btn = QPushButton("Sync to all")
        self._sync_speed_btn.clicked.connect(self._on_sync_speed)

        self._disable_torque_btn = QPushButton("Disable hand torque")
        self._disable_torque_btn.clicked.connect(self.disable_torque_requested.emit)

        for w in (
            self._connect_btn,
            self._disconnect_btn,
            self._global_speed,
            self._sync_speed_btn,
            self._disable_torque_btn,
        ):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # --- per-finger rows ---
        for i, (ui_label, _schema_key, odd_id, even_id) in enumerate(FINGER_TABLE):
            row = _FingerRow(
                i,
                ui_label,
                odd_id,
                even_id,
                default_speed=app_config.hand.default_speed,
            )
            row.target_changed.connect(self.target_changed.emit)
            row.finger_clicked.connect(self.set_selected_finger)
            self._fingers.append(row)

        # --- pose manager (uses kinematics name validator) ---
        self._pose_manager = PoseManagerWidget(
            name_validator=validate_pose_name,
            title="Hand poses",
        )
        self._pose_manager.save_requested.connect(self._save_pose)
        self._pose_manager.load_requested.connect(self._load_pose)
        self._pose_manager.delete_requested.connect(self._delete_pose)
        self._pose_manager.rename_requested.connect(self._rename_pose)
        self._pose_manager.set_pose_names(list(self._poses.poses.keys()))

        self._build_layout()
        self.set_selected_finger(0)

    # --- public hooks invoked by MainWindow -----------------------------

    def on_state_changed(self, state: dict) -> None:
        """Update each finger's live readouts. ``state`` keys are int sids."""
        for row in self._fingers:
            odd, even = row.odd_id, row.even_id
            if odd in state and even in state:
                p1 = float(state[odd].get("pos_deg", 0.0))  # logical frame
                p2 = float(state[even].get("pos_deg", 0.0))
                base_live = (p1 + p2) / 2.0
                side_live = (p2 - p1) / 2.0
                row.set_live(base_live, side_live)
            else:
                row.set_live(None, None)

    def on_connection_state(self, connected: bool) -> None:
        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)
        if not connected:
            for row in self._fingers:
                row.set_live(None, None)

    def set_selected_finger(self, finger_index: int) -> None:
        if not (0 <= finger_index < NUM_FINGERS):
            return
        self._selected_finger = finger_index
        for i, row in enumerate(self._fingers):
            row.set_selected(i == finger_index)

    # --- layout ----------------------------------------------------------

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Port:"))
        top.addWidget(self._port_input)
        top.addWidget(self._connect_btn)
        top.addWidget(self._disconnect_btn)
        top.addSpacing(16)
        top.addWidget(QLabel("Global speed:"))
        top.addWidget(self._global_speed)
        top.addWidget(self._sync_speed_btn)
        top.addStretch(1)
        top.addWidget(self._disable_torque_btn)
        layout.addLayout(top)

        for row in self._fingers:
            layout.addWidget(row)

        layout.addWidget(self._pose_manager, 1)

    # --- key handling (panel-scope per spec §5.3) ------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt API
        key = event.key()
        modifiers = event.modifiers()
        step = self._step_size(modifiers)
        row = self._fingers[self._selected_finger]

        if key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4):
            self.set_selected_finger(int(key) - int(Qt.Key.Key_1))
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            row.step_base(+step)
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            row.step_base(-step)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            row.step_side(+step)
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            row.step_side(-step)
            event.accept()
            return
        if key == Qt.Key.Key_Q:
            row.set_base(BASE_MAX)
            row.set_side(0)
            event.accept()
            return
        if key == Qt.Key.Key_E:
            row.set_base(BASE_MIN)
            row.set_side(0)
            event.accept()
            return
        if key == Qt.Key.Key_C:
            row.set_side(0)
            event.accept()
            return
        super().keyPressEvent(event)

    def _step_size(self, modifiers: Qt.KeyboardModifier) -> int:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return self._step_ctrl
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return self._step_shift
        return self._step_default

    # --- top-row handlers ------------------------------------------------

    def _on_connect_clicked(self) -> None:
        port = self._port_input.text().strip() or self._app_config.hand.port
        self.connect_requested.emit(
            port,
            self._app_config.hand.baudrate,
            self._app_config.hand.timeout,
        )

    def _on_sync_speed(self) -> None:
        speed = int(self._global_speed.currentData())
        for row in self._fingers:
            row.set_speed(speed)
        # Re-emit current targets so the new speed applies on the next move.
        for row in self._fingers:
            row.emit_current_targets()

    # --- pose persistence -----------------------------------------------

    def _save_pose(self, name: str) -> None:
        positions = self._snapshot_positions()
        self._poses.poses[name] = HandPose(positions=positions)
        self._write_yaml()
        self._pose_manager.set_pose_names(list(self._poses.poses.keys()))

    def _load_pose(self, name: str) -> None:
        pose = self._poses.poses.get(name)
        if pose is None:
            return
        self._apply_positions(pose.positions)
        # Re-emit current targets so the bus moves to the loaded pose.
        for row in self._fingers:
            row.emit_current_targets()

    def _delete_pose(self, name: str) -> None:
        if name in self._poses.poses:
            del self._poses.poses[name]
            self._write_yaml()
            self._pose_manager.set_pose_names(list(self._poses.poses.keys()))

    def _rename_pose(self, old: str, new: str) -> None:
        if old not in self._poses.poses or new in self._poses.poses:
            return
        self._poses.poses[new] = self._poses.poses.pop(old)
        self._write_yaml()
        self._pose_manager.set_pose_names(list(self._poses.poses.keys()))

    def _snapshot_positions(self) -> list[int]:
        """Build the YAML positions array from the current sliders.

        Slider state is in logical frame; the YAML stores the servo frame
        (even-ID pre-inverted). ``even_id_inversion`` is self-inverse, so
        applying it once converts logical → servo.
        """
        out = [0] * 8
        for row in self._fingers:
            base, side = row.base_value(), row.side_value()
            pos1_logical, pos2_logical = compose_finger(base, side, _SERVO_LOGICAL_MIN, _SERVO_LOGICAL_MAX)
            out[row.odd_id - 1] = int(even_id_inversion(row.odd_id, float(pos1_logical)))
            out[row.even_id - 1] = int(even_id_inversion(row.even_id, float(pos2_logical)))
        return out

    def _apply_positions(self, positions: list[int]) -> None:
        for row in self._fingers:
            yaml_odd = float(positions[row.odd_id - 1])
            yaml_even = float(positions[row.even_id - 1])
            pos1_logical = even_id_inversion(row.odd_id, yaml_odd)
            pos2_logical = even_id_inversion(row.even_id, yaml_even)
            base, side = decompose_finger(int(pos1_logical), int(pos2_logical))
            # Block emit; we'll trigger emit_current_targets once at the end.
            row.set_base(base, emit=False)
            row.set_side(side, emit=False)

    def _write_yaml(self) -> None:
        """Atomic write to ``self._poses_path`` (tmp + os.replace, spec §8.4)."""
        payload = self._poses.model_dump(mode="python")
        tmp = self._poses_path.with_suffix(self._poses_path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        os.replace(tmp, self._poses_path)


# Module-level helper for tests / external callers needing the finger table
# without instantiating the widget. Kept thin so importing the module from a
# headless test still doesn't require a ``QApplication``.
def finger_servo_ids(ui_label: str) -> tuple[int, int] | None:
    """Return ``(odd_id, even_id)`` for an UI label, or None if unknown."""
    for label, _schema, odd, even in FINGER_TABLE:
        if label == ui_label:
            return odd, even
    return None
