"""IL-3 motor identity canon for the hand (primitive lookup tables).

Single source for the finger->servo-ID mapping. Lives in the primitive layer so
both ``config.calibration.HandCalibration.middle_pos_by_id`` and the device-layer
``hand.pose_resolver`` can use it without an upward import (01-module-layering §2).
Odd ID = servo_1, even ID = servo_2; even IDs are wire-inverted in kinematics.
"""

from __future__ import annotations

# Canonical finger order (IL-3): pointer -> pinky-side -> thumb.
FINGER_NAMES: tuple[str, ...] = ("index", "middle", "ring", "thumb")

# Each finger's two SCS0009 servo IDs, (servo_1, servo_2). IDs 1-8, odd/even per finger.
FINGER_SERVO_IDS: dict[str, tuple[int, int]] = {
    "index": (1, 2),
    "middle": (3, 4),
    "ring": (5, 6),
    "thumb": (7, 8),
}
