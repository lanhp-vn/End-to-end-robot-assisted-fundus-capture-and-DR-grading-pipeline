from arm101_hand.config.system_camera_config import AutoTriggerConfig
from arm101_hand.system_camera.arc_detector import AlignmentState
from arm101_hand.system_camera.auto_trigger import WAIT_CLEAR, WAIT_RED, arm, update

_CFG = AutoTriggerConfig(stable_seconds=1.0, cooldown_seconds=3.0)
_RED = AlignmentState(True, True, 0.5, 0.5)
_CLEAR = AlignmentState(False, False, 0.0, 0.0)


def _run(states, t0=0.0, dt=0.5):
    st, fired, t = arm(), [], t0
    for a in states:
        st, f = update(st, a, t, _CFG)
        fired.append(f)
        t += dt
    return st, fired


def test_no_fire_without_red_gate():
    # clear from the start (blank/aligned screen) must never fire
    st, fired = _run([_CLEAR] * 10)
    assert not any(fired)
    assert st.phase == WAIT_RED


def test_fires_on_red_then_clear_held_stable():
    # both_red (gate) -> both_clear held >= stable_seconds (1.0s, dt 0.5 -> 3 clears) -> one fire
    _, fired = _run([_RED, _CLEAR, _CLEAR, _CLEAR])
    assert fired == [False, False, False, True]


def test_red_gate_advances_to_wait_clear():
    # the gate (both_red) advances WAIT_RED -> WAIT_CLEAR without firing
    st, fire = update(arm(), _RED, 0.0, _CFG)
    assert st.phase == WAIT_CLEAR and fire is False


def test_requires_regate_after_cooldown():
    # after a fire, staying clear must NOT fire again until red is seen again
    seq = [_RED, _CLEAR, _CLEAR, _CLEAR] + [_CLEAR] * 10
    _, fired = _run(seq)
    assert sum(fired) == 1


def test_red_flicker_during_stabilizing_resets():
    # clear, clear, RED (flicker), clear... the stable window restarts; first window broken
    _, fired = _run([_RED, _CLEAR, _RED, _CLEAR, _CLEAR, _CLEAR])
    assert sum(fired) == 1  # only fires after an uninterrupted stable window post-flicker
