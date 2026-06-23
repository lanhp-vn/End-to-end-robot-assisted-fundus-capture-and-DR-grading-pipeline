"""Auto-trigger lifecycle for the red-gated Aurora capture (device layer, pure + clock-injected).

Each capture requires a fresh transition: BOTH arcs RED (misaligned -> the gate) -> BOTH arcs
not-red (aligned) held ``stable_seconds`` -> fire ONE capture -> cooldown -> re-require red. A blank
/ menu / already-aligned screen has no red, so it never passes the gate (no false fire). ``update``
is pure: (state, latest AlignmentState, monotonic ``now``, config) -> (next state, should_fire).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from arm101_hand.config.system_camera_config import AutoTriggerConfig

from .arc_detector import AlignmentState

WAIT_RED = "WAIT_RED"  # armed; waiting to see both arcs RED (the per-capture gate)
WAIT_CLEAR = "WAIT_CLEAR"  # gate passed; waiting for both arcs not-red
STABILIZING = "STABILIZING"  # both not-red; holding for stable_seconds
COOLDOWN = "COOLDOWN"  # fired; waiting out cooldown_seconds, then re-gate


@dataclass(frozen=True)
class AutoTriggerState:
    phase: str = WAIT_RED
    clear_since: float | None = None
    fired_at: float | None = None


def arm() -> AutoTriggerState:
    """Fresh state, ready to watch for the red gate (called when the operator enters AUTO mode)."""
    return AutoTriggerState()


def update(
    state: AutoTriggerState, alignment: AlignmentState, now: float, cfg: AutoTriggerConfig
) -> tuple[AutoTriggerState, bool]:
    """Advance the lifecycle; return (next_state, should_fire). ``should_fire`` is True one tick."""
    if state.phase == WAIT_RED:
        if alignment.both_red:
            return replace(state, phase=WAIT_CLEAR), False
        return state, False

    if state.phase == WAIT_CLEAR:
        if alignment.both_clear:
            return replace(state, phase=STABILIZING, clear_since=now), False
        return state, False

    if state.phase == STABILIZING:
        if not alignment.both_clear:
            return replace(state, phase=WAIT_CLEAR, clear_since=None), False
        assert state.clear_since is not None
        if now - state.clear_since >= cfg.stable_seconds:
            return replace(state, phase=COOLDOWN, fired_at=now), True
        return state, False

    # COOLDOWN
    assert state.fired_at is not None
    if now - state.fired_at >= cfg.cooldown_seconds:
        return replace(state, phase=WAIT_RED, clear_since=None, fired_at=None), False
    return state, False
