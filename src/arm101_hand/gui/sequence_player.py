"""Sequence playback for the AmazingHand pose manager (spec §5.4 / §5.5).

Two parts:

- **Pure parser** — ``parse_step`` returns ``SleepStep | PoseStep`` from the
  two formats stored in ``data/hand_config.yaml``'s ``sequences:`` block:

  - ``"<pose>:s1,s2,s3,s4,s5,s6,s7,s8|<delay>s"`` — apply a named pose with
    8 per-servo speeds (1-7 SCS0009 scale) then sleep ``delay`` seconds.
  - ``"SLEEP:<n>s"`` — sleep only.

  No Qt, no I/O — fully unit-testable. ``HandPoseConfig._steps_well_formed``
  performs a coarse shape check at YAML load; this parser does the strict
  decomposition the player actually needs.

- **`SequencePlayer(QThread)`** — walks the parsed steps. ``stop()`` exits
  cleanly without disabling torque (the hand keeps the last commanded pose,
  per spec §5.4 — the user pressed Stop, not E-STOP).

Position units in the underlying ``HandPose.positions`` array are in **YAML /
servo frame** (even-ID values pre-inverted; see
``data/hand_config.yaml`` header comments). The player converts to **logical
frame** before handing to ``HandController.send_batch_targets`` so the
controller's signal contract is consistent across the slider, keyboard, and
sequence paths.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from arm101_hand.config import HandPose
from arm101_hand.hand.kinematics import even_id_inversion

POSE_STEP_LEN = 8


@dataclass(frozen=True)
class SleepStep:
    """A pure delay; no servo command issued."""

    delay_s: float


@dataclass(frozen=True)
class PoseStep:
    """Apply a named pose with per-servo speeds, then sleep ``delay_s``."""

    pose_name: str
    speeds: tuple[int, ...]  # length-8, each 1-7 SCS0009 scale
    delay_s: float


Step = SleepStep | PoseStep


def _parse_delay(token: str, raw_line: str) -> float:
    if not token.endswith("s"):
        raise ValueError(f"step delay must end with 's': {raw_line!r}")
    body = token[:-1]
    try:
        delay = float(body)
    except ValueError as e:
        raise ValueError(f"step delay must be numeric: {raw_line!r}") from e
    if delay < 0:
        raise ValueError(f"step delay must be non-negative: {raw_line!r}")
    return delay


def parse_step(line: str) -> Step:
    """Parse a single sequence step. Raises ``ValueError`` on malformed input.

    Examples
    --------

        >>> parse_step("SLEEP:0.5s")
        SleepStep(delay_s=0.5)
        >>> parse_step("middle:3,3,3,3,3,3,3,3|2.0s")
        PoseStep(pose_name='middle', speeds=(3, 3, 3, 3, 3, 3, 3, 3), delay_s=2.0)
    """
    raw = line
    line = line.strip()
    if not line:
        raise ValueError(f"empty step: {raw!r}")

    if line.startswith("SLEEP:"):
        return SleepStep(delay_s=_parse_delay(line[len("SLEEP:") :], raw))

    if "|" not in line or ":" not in line:
        raise ValueError(f"malformed step (need '<pose>:s1,...,s8|<delay>s' or 'SLEEP:<n>s'): {raw!r}")

    head, _, tail = line.partition("|")
    delay_s = _parse_delay(tail, raw)

    pose_name, _, speeds_csv = head.partition(":")
    if not pose_name:
        raise ValueError(f"step missing pose name: {raw!r}")
    parts = speeds_csv.split(",")
    if len(parts) != POSE_STEP_LEN:
        raise ValueError(f"step needs {POSE_STEP_LEN} speeds, got {len(parts)}: {raw!r}")
    try:
        speeds = tuple(int(p) for p in parts)
    except ValueError as e:
        raise ValueError(f"step speeds must be integers: {raw!r}") from e
    return PoseStep(pose_name=pose_name, speeds=speeds, delay_s=delay_s)


def yaml_pose_to_logical_targets(
    pose: HandPose,
    speeds: tuple[int, ...],
) -> dict[int, tuple[float, int]]:
    """Build ``{sid: (deg_rel_logical, speed)}`` from a YAML pose.

    YAML positions are in servo frame (even-ID pre-inverted). The controller's
    ``send_batch_targets`` expects logical frame; ``even_id_inversion`` is
    self-inverse, so applying it converts servo→logical.
    """
    if len(speeds) != POSE_STEP_LEN:
        raise ValueError(f"speeds must be length {POSE_STEP_LEN}, got {len(speeds)}")
    targets: dict[int, tuple[float, int]] = {}
    for j, yaml_deg in enumerate(pose.positions):
        sid = j + 1
        deg_logical = even_id_inversion(sid, float(yaml_deg))
        targets[sid] = (deg_logical, int(speeds[j]))
    return targets


class SequencePlayer(QThread):
    """Worker thread that walks parsed steps against a ``HandController``.

    Uses ``threading.Event.wait`` for the per-step delay so ``stop()``
    interrupts immediately instead of running out the full sleep.
    """

    step_started = Signal(int, object)  # (index, Step)
    step_finished = Signal(int)
    sequence_complete = Signal()
    error = Signal(str)

    def __init__(
        self,
        steps: list[Step],
        controller: Any,  # HandController; ``Any`` keeps us free of the import cycle
        poses: dict[str, HandPose],
        loop: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._steps = list(steps)
        self._controller = controller
        self._poses = dict(poses)
        self._loop = bool(loop)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Cleanly exit the player after the current step's I/O.

        Does NOT disable torque — per spec §5.4, Stop leaves the hand in the
        last commanded pose; only E-STOP cuts torque.
        """
        self._stop_event.set()

    def run(self) -> None:  # noqa: D401 — Qt API
        try:
            while not self._stop_event.is_set():
                for i, step in enumerate(self._steps):
                    if self._stop_event.is_set():
                        return
                    self.step_started.emit(i, step)
                    self._run_step(step)
                    self.step_finished.emit(i)
                if not self._loop:
                    return
        finally:
            self.sequence_complete.emit()

    def _run_step(self, step: Step) -> None:
        if isinstance(step, SleepStep):
            self._stop_event.wait(step.delay_s)
            return

        # PoseStep
        pose = self._poses.get(step.pose_name)
        if pose is None:
            self.error.emit(f"unknown pose in sequence: {step.pose_name!r}")
            return
        targets = yaml_pose_to_logical_targets(pose, step.speeds)
        try:
            self._controller.send_batch_targets(targets)
        except Exception as e:
            self.error.emit(f"send_batch_targets failed: {e}")
            return
        self._stop_event.wait(step.delay_s)
