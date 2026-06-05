from arm101_hand.config import DofLimits
from arm101_hand.hand.pose_jog import (
    FINGERS,
    HandJogState,
    apply_action,
    key_to_action,
)

LIMITS = {
    "index": DofLimits(base_min=-20, base_max=70, side_min=-40, side_max=35),
    "middle": DofLimits(base_min=-35, base_max=65, side_min=-20, side_max=15),
    "ring": DofLimits(base_min=-35, base_max=65, side_min=-25, side_max=20),
    "thumb": DofLimits(base_min=-40, base_max=100, side_min=-55, side_max=50),
}


def test_key_to_action_map():
    assert key_to_action("2") == "select_middle"
    assert key_to_action("UP") == "base+"
    assert key_to_action("H") == "home_all"
    assert key_to_action("z") is None


def test_select_changes_active():
    state = apply_action(HandJogState(), "select_thumb", LIMITS)
    assert state.active == "thumb"


def test_base_clamps_to_calibrated_max():
    state = HandJogState(active="index", step=15)
    for _ in range(20):  # would reach 300 unclamped
        state = apply_action(state, "base+", LIMITS)
    assert state.fingers["index"][0] == 70  # index base_max


def test_side_clamps_to_calibrated_min():
    state = HandJogState(active="middle", step=15)
    for _ in range(20):
        state = apply_action(state, "side-", LIMITS)
    assert state.fingers["middle"][1] == -20  # middle side_min


def test_step_bounds():
    state = HandJogState(step=1)
    state = apply_action(state, "step-", LIMITS)
    assert state.step == 1  # STEP_MIN
    state = HandJogState(step=15)
    state = apply_action(state, "step+", LIMITS)
    assert state.step == 15  # STEP_MAX


def test_home_active_only():
    state = HandJogState(active="index")
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "select_thumb", LIMITS)
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "home", LIMITS)  # homes thumb only
    assert state.fingers["thumb"] == (0, 0)
    assert state.fingers["index"][0] > 0


def test_home_all():
    state = HandJogState(active="index")
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "select_thumb", LIMITS)
    state = apply_action(state, "base+", LIMITS)
    state = apply_action(state, "home_all", LIMITS)
    assert all(state.fingers[f] == (0, 0) for f in FINGERS)


def test_save_and_quit_are_state_noops():
    state = HandJogState(active="ring")
    assert apply_action(state, "save", LIMITS) == state
    assert apply_action(state, "quit", LIMITS) == state
