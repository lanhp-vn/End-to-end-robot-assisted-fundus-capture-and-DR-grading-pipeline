"""Generic pose-manager widget shared by the hand and arm panels (spec §5.3 / §6.3).

The widget owns nothing about the underlying YAML — it surfaces UI for
naming + the four operations (save/load/delete/rename) and emits signals
that the parent panel handles. The panel decides how to snapshot the
current state, persists via atomic tmp + ``os.replace`` (spec §8.4), then
calls back ``set_pose_names`` to refresh the list.

Built generic so M4 can reuse the same class against arm config — the
constructor only needs a ``name_validator`` callable. The hand panel passes
``arm101_hand.hand.kinematics.validate_pose_name``; the arm panel will pass
its own.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

NameValidator = Callable[[str], tuple[bool, str]]


class PoseManagerWidget(QWidget):
    """List + name input + four-button row. Stateless beyond the displayed list."""

    save_requested = Signal(str)  # pose name (validated)
    load_requested = Signal(str)
    delete_requested = Signal(str)
    rename_requested = Signal(str, str)  # old, new (both validated)

    def __init__(
        self,
        name_validator: NameValidator,
        title: str = "Poses",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._validator = name_validator

        self._title = QLabel(f"<b>{title}</b>")
        self._list = QListWidget()
        self._list.setMinimumHeight(80)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Pose name (≤50 chars, no special chars)")

        self._save_btn = QPushButton("Save current")
        self._load_btn = QPushButton("Load")
        self._delete_btn = QPushButton("Delete")
        self._rename_btn = QPushButton("Rename")

        self._save_btn.clicked.connect(self._on_save)
        self._load_btn.clicked.connect(self._on_load)
        self._delete_btn.clicked.connect(self._on_delete)
        self._rename_btn.clicked.connect(self._on_rename)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._title)
        layout.addWidget(self._list, 1)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        name_row.addWidget(self._name_input, 1)
        layout.addLayout(name_row)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._load_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addWidget(self._rename_btn)
        layout.addLayout(btn_row)

    def set_pose_names(self, names: list[str]) -> None:
        """Refresh the list. Called after the panel saves/deletes/renames."""
        previous = self._selected_name()
        self._list.clear()
        for n in sorted(names):
            self._list.addItem(n)
        if previous is not None:
            for i in range(self._list.count()):
                if self._list.item(i).text() == previous:
                    self._list.setCurrentRow(i)
                    break

    # === internals =======================================================

    def _selected_name(self) -> str | None:
        items = self._list.selectedItems()
        return items[0].text() if items else None

    def _on_selection_changed(self) -> None:
        sel = self._selected_name()
        if sel is not None:
            self._name_input.setText(sel)

    def _validate_or_warn(self, name: str) -> bool:
        ok, err = self._validator(name)
        if not ok:
            QMessageBox.warning(self, "Invalid name", err)
        return ok

    def _on_save(self) -> None:
        name = self._name_input.text().strip()
        if not self._validate_or_warn(name):
            return
        self.save_requested.emit(name)

    def _on_load(self) -> None:
        sel = self._selected_name()
        if sel is None:
            QMessageBox.information(self, "Load pose", "Select a pose first.")
            return
        self.load_requested.emit(sel)

    def _on_delete(self) -> None:
        sel = self._selected_name()
        if sel is None:
            QMessageBox.information(self, "Delete pose", "Select a pose first.")
            return
        confirm = QMessageBox.question(
            self,
            "Delete pose",
            f"Delete pose {sel!r}? This cannot be undone.",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(sel)

    def _on_rename(self) -> None:
        sel = self._selected_name()
        if sel is None:
            QMessageBox.information(self, "Rename pose", "Select a pose first.")
            return
        new = self._name_input.text().strip()
        if new == sel:
            return
        if not self._validate_or_warn(new):
            return
        self.rename_requested.emit(sel, new)
