from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import AlignmentState
from arm101_hand.system_camera.auto_trigger import (
    COOLDOWN,
    STABILIZING,
    WAIT_CLEAR,
    WAIT_GREEN,
    arm,
    update,
)

_CFG = AutoTriggerConfig()  # stable 1.0s, cooldown 3.0s, require_clear_between True


def _al(ready, left="NONE", right="NONE"):
    return AlignmentState(left, right, ready, 0.0, 0.0, 0.0, 0.0)


def test_green_then_stable_fires_once():
    s = arm()
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 100.0, _CFG)
    assert s.phase == STABILIZING and fire is False
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 100.5, _CFG)
    assert fire is False  # 0.5s < stable_seconds
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, _CFG)
    assert fire is True and s.phase == COOLDOWN


def test_green_drops_before_stable_resets():
    s = arm()
    s, _ = update(s, _al(True, "GREEN"), 100.0, _CFG)
    s, fire = update(s, _al(False), 100.5, _CFG)
    assert s.phase == WAIT_GREEN and fire is False


def test_cooldown_then_wait_clear_then_rearm():
    s = arm()
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 100.0, _CFG)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, _CFG)
    assert fire is True and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 102.0, _CFG)  # within cooldown
    assert fire is False and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 104.0, _CFG)  # cooldown elapsed
    assert s.phase == WAIT_CLEAR and fire is False
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 105.0, _CFG)  # still green -> NOT cleared
    assert s.phase == WAIT_CLEAR and fire is False
    # Re-arm on green CLEARING -- keyed on "not ready", NOT specifically red. The screen often
    # goes blank/NONE off-eye; an explicit-red gate would stick here forever (the bug we fixed).
    s, _ = update(s, _al(False, "NONE", "NONE"), 106.0, _CFG)  # green cleared -> re-arm
    assert s.phase == WAIT_GREEN
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 106.0, _CFG)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 107.0, _CFG)
    assert fire is True  # fires again for the next shot (re-align-to-green per shot)


def test_require_clear_between_false_returns_to_wait_green():
    cfg = _CFG.model_copy(update={"require_clear_between": False})
    s = arm()
    s, _ = update(s, _al(True, "GREEN", "GREEN"), 100.0, cfg)
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 101.0, cfg)
    assert fire is True and s.phase == COOLDOWN
    s, fire = update(s, _al(True, "GREEN", "GREEN"), 104.0, cfg)
    assert s.phase == WAIT_GREEN and fire is False
