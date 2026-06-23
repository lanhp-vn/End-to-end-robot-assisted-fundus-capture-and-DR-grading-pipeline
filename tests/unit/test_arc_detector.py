import numpy as np

from arm101_hand.config.system_camera_config import AutoTriggerConfig, RoiBox
from arm101_hand.system_camera.arc_detector import AlignmentState, detect

_RED = (0, 0, 255)


def _cfg() -> AutoTriggerConfig:
    # arcs at the left/right thirds of an 800x480 reference frame
    return AutoTriggerConfig(
        left_arc=RoiBox(x=40, y=160, w=120, h=160, ref_w=800, ref_h=480),
        right_arc=RoiBox(x=640, y=160, w=120, h=160, ref_w=800, ref_h=480),
        coverage_threshold=0.2,
    )


def test_alignmentstate_properties():
    assert AlignmentState(True, True, 0.5, 0.5).both_red is True
    assert AlignmentState(False, False, 0.0, 0.0).both_clear is True
    assert AlignmentState(True, False, 0.5, 0.0).both_red is False
    assert AlignmentState(True, False, 0.5, 0.0).both_clear is False


def test_detect_both_red():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)
    frame[160:320, 40:160] = _RED
    frame[160:320, 640:760] = _RED
    s = detect(frame, _cfg())
    assert s.both_red is True


def test_detect_both_clear_on_blank():
    frame = np.zeros((480, 800, 3), dtype=np.uint8)  # nothing red
    s = detect(frame, _cfg())
    assert s.both_clear is True
