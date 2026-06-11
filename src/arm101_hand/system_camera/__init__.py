"""Arm-mounted USB observation camera device layer.

The host webcam mounted on the arm, pointed at the Optomed Aurora's screen -- a live cv2
preview window with a record toggle. Distinct from the Aurora *fundus* camera (patient
retinal images) in ``arm101_hand.fundus_camera``.
"""

from .preview import WebcamPreview, open_capture

__all__ = ["WebcamPreview", "open_capture"]
