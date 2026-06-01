# AmazingHand Range Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-finger, hardware-measured DOF motion limits (`base`/`side` min/max) to the AmazingHand calibration — analogous to the SO-ARM101 follower's `range_min`/`range_max` — discovered with an arrow-key jog-to-limit script, stored in the calibration YAML (v2 schema), and consumed by the kinematics + audit scripts.

**Architecture:** Clean schema (Approach B): `config/calibration.py` grows a required `DofLimits` block per finger plus a `schema_version`; the old v1 YAML is rejected until recalibration. Pure logic (schema, kinematics clamps, jog state machine) lives in `src/arm101_hand/` and is host-unit-tested; the thin Windows-only `msvcrt` jog REPL lives in `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py` and wires the pure logic to `rustypot`. The current GUI's startup load breaks on purpose (out of scope — the GUI redesign will consume the new limits).

**Tech Stack:** Python 3.12, `uv`, pydantic v2, `numpy`, `pyyaml`, `rustypot` (SCS0009), `pytest`, `ruff`, `mypy`. Windows / PowerShell / COM-port host.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/arm101_hand/config/calibration.py` | Pydantic schema for the calib YAML | Modify — add `DofLimits`, `FingerCalibration.limits`, `schema_version`, `limits_by_finger()` |
| `src/arm101_hand/hand/kinematics.py` | Pure DOF math | Modify — clamp `base`/`side` to calibrated limits in `compose_finger`/`decompose_finger` |
| `src/arm101_hand/hand/range_calib.py` | Pure jog-to-limit state machine (no hardware, no msvcrt) | Create |
| `src/arm101_hand/hand/__init__.py` | Hand package exports | Modify — export new range_calib symbols |
| `src/arm101_hand/config/__init__.py` | Config package exports | Modify — export `DofLimits` |
| `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py` | Windows msvcrt jog REPL + rustypot + YAML write-back | Create |
| `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` | Calibration source of truth (IL-5) | Modify — bump to v2, add `limits` per finger |
| `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py` | Per-finger audit | Modify — derive poses from limits |
| `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py` | Full-hand demo | Modify — derive poses from limits |
| `scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py` | Middle-pos fine-tune | Modify — derive test cycle from limits |
| `scripts/calibration/AmazingHand/README.md` | Calibration procedure | Modify — add Step 4 (range calib), renumber |
| `tests/config/test_calibration.py` | Schema tests | Create |
| `tests/hand/test_kinematics.py` | Kinematics clamp + round-trip tests | Create |
| `tests/hand/test_range_calib.py` | Jog state-machine tests | Create |
| `pyproject.toml` | Register `hardware` pytest marker | Modify |
| `CLAUDE.md` | Agent self-reference | Modify (via doc_update skill) |

**Convention reminders:**
- Run everything with `uv run …` (resolves `.venv`).
- Tests are table-driven: tuple rows + a one-line header comment, ≥2 cases per function, every assertion carries a `{desc}` (see `docs/conventions/04-testing-verification.md` §3).
- `ruff` line-length 110; double quotes; `from __future__ import annotations` at top of new modules.
- Logical frame: `base` positive = close; `pos1 = base − side`, `pos2 = base + side`; even-ID servo already inverted by `even_id_inversion`. Stored limits are in this logical frame.

---

## Task 0: Register the `hardware` pytest marker

**Files:**
- Modify: `pyproject.toml` (the `[tool.pytest.ini_options]` block)

- [ ] **Step 1: Add the marker registration**

Open `pyproject.toml`, find:

```toml
[tool.pytest.ini_options]
# Keep collection inside our own ``tests/`` tree. The vendored submodules under
# ``references/`` ship their own conftest.py files (some define an option named
# ``--port`` that collides with ours), and they are read-only per IL-2.
testpaths = ["tests"]
```

Replace it with:

```toml
[tool.pytest.ini_options]
# Keep collection inside our own ``tests/`` tree. The vendored submodules under
# ``references/`` ship their own conftest.py files (some define an option named
# ``--port`` that collides with ours), and they are read-only per IL-2.
testpaths = ["tests"]
markers = [
    "hardware: test requires a physical bus (powered AmazingHand on a COM port)",
]
```

- [ ] **Step 2: Verify pytest collects with no marker warning**

Run: `uv run pytest -m 'not hardware' -q`
Expected: `no tests ran` (the `tests/` dir doesn't exist yet) with exit code 5 and **no** `PytestUnknownMarkWarning`. This confirms the config parses.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "test: register the 'hardware' pytest marker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 1: Schema — `DofLimits` + `FingerCalibration.limits` + `schema_version`

**Files:**
- Modify: `src/arm101_hand/config/calibration.py`
- Modify: `src/arm101_hand/config/__init__.py`
- Test: `tests/config/test_calibration.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/__init__.py` (empty) and `tests/config/__init__.py` (empty) so the test packages import cleanly, then create `tests/config/test_calibration.py`:

```python
"""Tests for the AmazingHand calibration schema (v2 with DOF limits)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arm101_hand.config import DofLimits, HandCalibration
from arm101_hand.config.calibration import load_hand_calibration


def _valid_finger(sid1: int, sid2: int) -> dict:
    return {
        "servo_1": {"id": sid1, "middle_pos": 0},
        "servo_2": {"id": sid2, "middle_pos": 0},
        "limits": {"base_min": -30, "base_max": 110, "side_min": -40, "side_max": 40},
    }


def _valid_doc() -> dict:
    return {
        "schema_version": 2,
        "com_port": "COM18",
        "baudrate": 1000000,
        "timeout": 0.5,
        "speed": 6,
        "fingers": {
            "index": _valid_finger(1, 2),
            "middle": _valid_finger(3, 4),
            "ring": _valid_finger(5, 6),
            "thumb": _valid_finger(7, 8),
        },
    }


def test_valid_doc_parses():
    cal = HandCalibration.model_validate(_valid_doc())
    assert cal.schema_version == 2, "accepts a well-formed v2 document"
    assert cal.fingers["thumb"].limits.side_max == 40, "exposes nested DOF limits"


# | mutation                              | desc                                   |
@pytest.mark.parametrize("mutate,desc", [
    (lambda d: d.pop("schema_version"),          "rejects v1 doc (no schema_version)"),
    (lambda d: d.__setitem__("schema_version", 1), "rejects schema_version < 2"),
    (lambda d: d["fingers"]["index"].pop("limits"), "rejects a finger missing limits"),
    (lambda d: d["fingers"]["index"]["limits"].__setitem__("extra", 1), "rejects unknown limit key"),
])
def test_rejects_bad_docs(mutate, desc):
    doc = _valid_doc()
    mutate(doc)
    with pytest.raises(ValidationError):
        HandCalibration.model_validate(doc)


# | base_min | base_max | side_min | side_max | desc                          |
@pytest.mark.parametrize("bmin,bmax,smin,smax,desc", [
    (110, -30, -40, 40, "rejects base_min >= base_max"),
    (-30, 110, 40, -40, "rejects side_min >= side_max"),
])
def test_limit_ordering_validated(bmin, bmax, smin, smax, desc):
    with pytest.raises(ValidationError):
        DofLimits(base_min=bmin, base_max=bmax, side_min=smin, side_max=smax)


def test_limits_by_finger_lookup():
    cal = HandCalibration.model_validate(_valid_doc())
    by_finger = cal.limits_by_finger()
    assert set(by_finger) == {"index", "middle", "ring", "thumb"}, "one entry per finger"
    assert by_finger["index"].base_max == 110, "returns the DofLimits object"


def test_loads_canonical_yaml(tmp_path):
    # The committed YAML must satisfy the v2 schema. Load it through the public loader.
    from pathlib import Path
    path = Path("scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml")
    cal = load_hand_calibration(path)
    assert cal.schema_version == 2, "committed YAML is v2"
    assert len(cal.fingers) == 4, "committed YAML has all four fingers"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/config/test_calibration.py -q`
Expected: FAIL — `ImportError: cannot import name 'DofLimits'` (the schema doesn't exist yet). The `test_loads_canonical_yaml` will also fail until Task 2 updates the YAML — that's expected; it goes green after Task 2.

- [ ] **Step 3: Implement the schema**

Replace the body of `src/arm101_hand/config/calibration.py` (keep the module docstring; update it to mention limits) with:

```python
"""Pydantic schema for ``scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml``.

Read-only consumer — the calibration scripts under
``scripts/calibration/AmazingHand/`` own writing this file. The GUI and audit
scripts import the per-servo ``middle_pos`` (for calibration-aware slider math,
see ``hand/kinematics.degrees_to_servo_radians``) and the per-finger DOF
``limits`` (``base``/``side`` min/max, logical frame) that bound motion.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical finger labels, per IL-3. The GUI displays "Pointer" instead of
# "index", but the schema-level name stays "index".
FINGER_NAMES = ("index", "middle", "ring", "thumb")


class DofLimits(BaseModel):
    """Per-finger motion envelope in the **logical** frame (degrees rel. to middle).

    ``base`` is flexion (positive = close); ``side`` is abduction/adduction.
    Matches ``hand.kinematics.compose_finger``'s ``base``/``side`` convention
    (``pos1 = base - side``, ``pos2 = base + side``, even-ID already inverted).
    """

    model_config = ConfigDict(extra="forbid")

    base_min: float  # full extension / open
    base_max: float  # full flexion / close
    side_min: float  # spread one way
    side_max: float  # spread the other

    @model_validator(mode="after")
    def _check_ordering(self) -> DofLimits:
        if self.base_min >= self.base_max:
            raise ValueError(f"base_min ({self.base_min}) must be < base_max ({self.base_max})")
        if self.side_min >= self.side_max:
            raise ValueError(f"side_min ({self.side_min}) must be < side_max ({self.side_max})")
        return self


class ServoCalibration(BaseModel):
    """One SCS0009 servo's calibrated neutral."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, le=8)
    middle_pos: float


class FingerCalibration(BaseModel):
    """The two SCS0009 servos that drive one AmazingHand finger, plus its limits."""

    model_config = ConfigDict(extra="forbid")

    servo_1: ServoCalibration
    servo_2: ServoCalibration
    limits: DofLimits


class HandCalibration(BaseModel):
    """Top-level shape of ``AmazingHand_calib_values.yaml`` (v2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=2)
    com_port: str
    baudrate: int = Field(ge=9600)
    timeout: float = Field(ge=0.0, le=5.0)
    speed: int = Field(ge=1, le=7)
    fingers: dict[str, FingerCalibration]

    def middle_pos_by_id(self) -> dict[int, float]:
        """Flat ``{servo_id: middle_pos}`` lookup for the controller layer."""
        out: dict[int, float] = {}
        for finger in self.fingers.values():
            out[finger.servo_1.id] = finger.servo_1.middle_pos
            out[finger.servo_2.id] = finger.servo_2.middle_pos
        return out

    def limits_by_finger(self) -> dict[str, DofLimits]:
        """``{finger_name: DofLimits}`` lookup for kinematics + audit scripts."""
        return {name: finger.limits for name, finger in self.fingers.items()}


def load_hand_calibration(path: Path) -> HandCalibration:
    """Parse and validate the AmazingHand calibration YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return HandCalibration.model_validate(raw)
```

Note: `schema_version` is `Field(ge=2)` with **no default** — a v1 file (which omits the key) fails with "field required", exactly the rejection we want.

- [ ] **Step 4: Export `DofLimits`**

In `src/arm101_hand/config/__init__.py`, update the calibration import line and `__all__`:

Change:
```python
from .calibration import FINGER_NAMES, HandCalibration, load_hand_calibration
```
to:
```python
from .calibration import DofLimits, FINGER_NAMES, HandCalibration, load_hand_calibration
```

And add `"DofLimits",` to the `__all__` list (keep it alphabetically near the top with the other class names, e.g. after `"AppConfig",`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/config/test_calibration.py -q`
Expected: all PASS **except** `test_loads_canonical_yaml`, which still fails (`ValidationError` — the committed YAML is still v1). That one goes green in Task 2.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/config/calibration.py src/arm101_hand/config/__init__.py tests/__init__.py tests/config/__init__.py tests/config/test_calibration.py
git commit -m "feat: add per-finger DOF limits to the hand calibration schema (v2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Migrate the committed calibration YAML to v2

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`

The `middle_pos` values below are the current committed values — keep them. The `limits` values are seed placeholders (the reference envelope); your hardware recalibration via Task 5 overwrites them.

- [ ] **Step 1: Rewrite the YAML to the v2 shape**

Replace the entire contents of `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` with:

```yaml
schema_version: 2
com_port: COM18
baudrate: 1000000
timeout: 0.5
speed: 6
fingers:
  index:
    servo_1: {id: 1, middle_pos: 30}
    servo_2: {id: 2, middle_pos: -2}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  middle:
    servo_1: {id: 3, middle_pos: -32}
    servo_2: {id: 4, middle_pos: 20}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  ring:
    servo_1: {id: 5, middle_pos: 22}
    servo_2: {id: 6, middle_pos: 0}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
  thumb:
    servo_1: {id: 7, middle_pos: 0}
    servo_2: {id: 8, middle_pos: -12}
    limits: {base_min: -30, base_max: 110, side_min: -40, side_max: 40}
```

- [ ] **Step 2: Verify the canonical-YAML test now passes**

Run: `uv run pytest tests/config/test_calibration.py -q`
Expected: ALL PASS (including `test_loads_canonical_yaml`).

- [ ] **Step 3: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml
git commit -m "feat: migrate AmazingHand calibration YAML to v2 (seed DOF limits)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Clamp `base`/`side` to calibrated limits in kinematics

**Files:**
- Modify: `src/arm101_hand/hand/kinematics.py` (`compose_finger`, `decompose_finger`)
- Test: `tests/hand/test_kinematics.py`

The new keyword-only limit params default to a wide envelope, so existing positional callers in `gui/hand_panel.py` (which pass `servo_min`/`servo_max` positionally) keep working unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/hand/__init__.py` (empty), then `tests/hand/test_kinematics.py`:

```python
"""Tests for AmazingHand pure kinematics: frame round-trips and limit clamps."""

from __future__ import annotations

import pytest

from arm101_hand.hand import compose_finger, decompose_finger, even_id_inversion


def test_spread_sign_preserved():
    # Sign guard: a positive `side` (spread one way) must survive
    # compose->decompose without flipping. Uses an in-envelope asymmetric pose
    # so the per-servo clamp ([-40, 110]) does not interfere. This is the
    # invariant the earlier sign bug violated.
    pos1, pos2 = compose_finger(20, 30)
    assert (pos1, pos2) == (-10, 50), "compose: pos1 = base - side, pos2 = base + side"
    base, side = decompose_finger(pos1, pos2)
    assert (base, side) == (20, 30), "decompose preserves +side (no sign flip)"

    pos1, pos2 = compose_finger(0, -30)
    assert (pos1, pos2) == (30, -30), "negative side spreads the other way"
    base, side = decompose_finger(pos1, pos2)
    assert (base, side) == (0, -30), "decompose preserves -side"


def test_even_id_inversion_self_inverse():
    # The logical<->servo bridge used by the calibration script. Odd unchanged,
    # even negated, applying twice is identity.
    assert even_id_inversion(1, 17) == 17, "odd id unchanged"
    assert even_id_inversion(2, 17) == -17, "even id negated"
    assert even_id_inversion(2, even_id_inversion(2, 17)) == 17, "self-inverse"


# | base | side | desc                                            |
@pytest.mark.parametrize("base,side,desc", [
    (0, 0, "neutral round-trips"),
    (90, 0, "pure flexion round-trips"),
    (-30, 0, "pure extension round-trips"),
    (40, 30, "mixed flex+spread round-trips"),
])
def test_compose_decompose_roundtrip(base, side, desc):
    pos1, pos2 = compose_finger(base, side)
    b2, s2 = decompose_finger(pos1, pos2)
    assert (b2, s2) == (base, side), desc


def test_base_clamped_to_limits():
    # base above base_max is clamped before composing.
    pos1, pos2 = compose_finger(200, 0, base_min=-30, base_max=110)
    assert (pos1, pos2) == (110, 110), "base clamped to base_max=110 (side=0 -> both servos)"
    pos1, pos2 = compose_finger(-200, 0, base_min=-30, base_max=110)
    assert (pos1, pos2) == (-30, -30), "base clamped to base_min=-30"


def test_side_clamped_to_limits():
    # side beyond side_max is clamped before composing.
    pos1, pos2 = compose_finger(0, 99, side_min=-40, side_max=40)
    assert (pos1, pos2) == (-40, 40), "side clamped to side_max=40 (pos1=base-side, pos2=base+side)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/hand/test_kinematics.py -q`
Expected: `test_base_clamped_to_limits` FAILS (no `base_min`/`base_max` kwargs yet → `TypeError`). The round-trip tests may already pass against the current implementation — that's fine; they pin the behavior we must preserve.

- [ ] **Step 3: Update `compose_finger` and `decompose_finger`**

In `src/arm101_hand/hand/kinematics.py`, replace `compose_finger` and `decompose_finger` with:

```python
def compose_finger(
    base: int,
    side: int,
    servo_min: int = -40,
    servo_max: int = 110,
    *,
    base_min: int = -110,
    base_max: int = 110,
    side_min: int = -40,
    side_max: int = 40,
) -> tuple[int, int]:
    """``(base, side)`` → ``(pos1, pos2)`` symmetric servo pair, clamped.

    Both outputs are in the **logical** frame (positive = close, same direction
    for both servos). ``base`` is clamped to ``[base_min, base_max]`` (calibrated
    flexion range) and ``side`` to ``[side_min, side_max]`` (calibrated spread)
    before composing; the result is then clamped per-servo to ``[servo_min,
    servo_max]`` as a corner-safety net (a ``(base_max, side_max)`` corner can
    still exceed one servo's stop). ``pos1`` takes ``base - side``, ``pos2``
    takes ``base + side``, so ``decompose_finger`` round-trips cleanly.

    The default limit kwargs reproduce the legacy global envelope; calibrated
    callers pass per-finger values from ``FingerCalibration.limits``.
    """
    base = clamp(base, base_min, base_max)
    side = clamp(side, side_min, side_max)
    pos1 = clamp(base - side, servo_min, servo_max)
    pos2 = clamp(base + side, servo_min, servo_max)
    return int(pos1), int(pos2)


def decompose_finger(
    pos1: int,
    pos2: int,
    servo_min: int = -40,
    servo_max: int = 110,
    side_min: int = -40,
    side_max: int = 40,
    *,
    base_min: int = -110,
    base_max: int = 110,
) -> tuple[int, int]:
    """``(pos1, pos2)`` → ``(base, side)``. Inverse of ``compose_finger``."""
    p1 = clamp(int(pos1), servo_min, servo_max)
    p2 = clamp(int(pos2), servo_min, servo_max)
    base = clamp((p1 + p2) // 2, base_min, base_max)
    side = clamp((p2 - p1) // 2, side_min, side_max)
    return int(base), int(side)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/hand/test_kinematics.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Run the full suite + lint to confirm no regression**

Run: `uv run pytest -m 'not hardware' -q && uv run ruff check src/arm101_hand/hand/kinematics.py tests/hand/test_kinematics.py`
Expected: all tests PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/hand/kinematics.py tests/hand/__init__.py tests/hand/test_kinematics.py
git commit -m "feat: clamp base/side to calibrated limits in compose/decompose_finger

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Pure jog-to-limit state machine — `hand/range_calib.py`

**Files:**
- Create: `src/arm101_hand/hand/range_calib.py`
- Modify: `src/arm101_hand/hand/__init__.py`
- Test: `tests/hand/test_range_calib.py`

This module holds everything pure about the jog REPL: the cursor state, key→action mapping, step changes, mark capture, and status/warning formatting. No `msvcrt`, no `rustypot`. The script in Task 5 wires it to hardware.

- [ ] **Step 1: Write the failing tests**

Create `tests/hand/test_range_calib.py`:

```python
"""Tests for the pure jog-to-limit state machine (no hardware, no msvcrt)."""

from __future__ import annotations

import pytest

from arm101_hand.hand.range_calib import (
    JOG_BASE_MAX,
    JOG_SIDE_MAX,
    STEP_DEFAULT,
    STEP_MAX,
    STEP_MIN,
    JogState,
    apply_action,
    key_to_action,
    load_warning,
)


# | raw key bytes      | expected action  | desc                       |
@pytest.mark.parametrize("key,action,desc", [
    ("UP",          "base+",      "up arrow flexes (base+)"),
    ("DOWN",        "base-",      "down arrow extends (base-)"),
    ("RIGHT",       "side+",      "right arrow spreads (side+)"),
    ("LEFT",        "side-",      "left arrow spreads (side-)"),
    ("[",           "step-",      "[ shrinks step"),
    ("]",           "step+",      "] grows step"),
    ("1",           "mark_base_min", "1 marks base_min"),
    ("2",           "mark_base_max", "2 marks base_max"),
    ("3",           "mark_side_min", "3 marks side_min"),
    ("4",           "mark_side_max", "4 marks side_max"),
    ("h",           "home",       "h homes"),
    ("s",           "save",       "s saves"),
    ("q",           "quit",       "q quits"),
    ("z",           None,         "unmapped key is ignored"),
])
def test_key_to_action(key, action, desc):
    assert key_to_action(key) == action, desc


def test_jog_moves_cursor_by_step():
    state = JogState()
    s2, mark = apply_action(state, "base+")
    assert s2.base == STEP_DEFAULT and mark is None, "base+ advances base by the step"
    s3, _ = apply_action(s2, "side-")
    assert s3.side == -STEP_DEFAULT, "side- decreases side by the step"


def test_jog_clamps_to_safety_envelope():
    state = JogState(base=JOG_BASE_MAX, side=JOG_SIDE_MAX, step=STEP_DEFAULT)
    s2, _ = apply_action(state, "base+")
    assert s2.base == JOG_BASE_MAX, "base cannot exceed the jog safety max"
    s3, _ = apply_action(state, "side+")
    assert s3.side == JOG_SIDE_MAX, "side cannot exceed the jog safety max"


# | start_step | action  | expected_step | desc                       |
@pytest.mark.parametrize("start,action,expected,desc", [
    (STEP_DEFAULT, "step-", STEP_DEFAULT - 1, "step- shrinks by 1"),
    (STEP_MIN,     "step-", STEP_MIN,         "step- floors at STEP_MIN"),
    (STEP_MAX,     "step+", STEP_MAX,         "step+ ceils at STEP_MAX"),
])
def test_step_changes(start, action, expected, desc):
    state = JogState(step=start)
    s2, _ = apply_action(state, action)
    assert s2.step == expected, desc


def test_home_resets_cursor():
    state = JogState(base=50, side=20, step=3)
    s2, _ = apply_action(state, "home")
    assert (s2.base, s2.side) == (0, 0), "home zeroes the cursor"
    assert s2.step == 3, "home preserves the step"


def test_mark_returns_named_limit():
    state = JogState(base=95, side=-35, step=5)
    _, mark = apply_action(state, "mark_base_max")
    assert mark == ("base_max", 95), "marking base_max captures the live base"
    _, mark = apply_action(state, "mark_side_min")
    assert mark == ("side_min", -35), "marking side_min captures the live side"


# | load1 | load2 | threshold | warns | desc                     |
@pytest.mark.parametrize("l1,l2,thr,warns,desc", [
    (10, 10, 60, False, "low load: no warning"),
    (80, 10, 60, True,  "servo 1 over threshold warns"),
    (10, 95, 60, True,  "servo 2 over threshold warns"),
])
def test_load_warning(l1, l2, thr, warns, desc):
    msg = load_warning(l1, l2, thr)
    assert (msg is not None) == warns, desc
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/hand/test_range_calib.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.hand.range_calib'`.

- [ ] **Step 3: Implement the module**

Create `src/arm101_hand/hand/range_calib.py`:

```python
"""Pure jog-to-limit state machine for AmazingHand range calibration.

No hardware, no ``msvcrt`` — this is the testable core of
``scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py``. The script reads
raw keys, maps them via ``key_to_action``, advances a ``JogState`` via
``apply_action``, then composes the live ``(base, side)`` cursor into servo
commands (using ``hand.kinematics.compose_finger`` +
``degrees_to_servo_radians``) and writes them over the bus.

Frame: ``base``/``side`` are the **logical** DOF (see ``hand.kinematics``).
During calibration the cursor roams a generous *safety* envelope
(``JOG_BASE_*`` / ``JOG_SIDE_*``) — deliberately wider than any expected
limit, because the whole point is to discover where the real stops are. The
true protections are (a) the per-servo clamp inside ``compose_finger`` at write
time and (b) the operator watching the ``present_load`` warning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Jog step bounds (degrees). Conservative default; operator widens/narrows live.
STEP_DEFAULT = 5
STEP_MIN = 1
STEP_MAX = 15

# Generous cursor safety envelope (degrees, logical frame). Wider than any real
# stored limit so the operator can jog *to* the mechanical stop and mark it.
JOG_BASE_MIN = -60
JOG_BASE_MAX = 130
JOG_SIDE_MIN = -60
JOG_SIDE_MAX = 60

# present_load magnitude above which we warn (units: SCS0009 load register,
# same scale read in HandController.poll_state). Tune on the bench.
LOAD_WARN_THRESHOLD = 60

# Raw-key → action. Arrow names are produced by the msvcrt shell in Task 5.
_KEY_ACTIONS: dict[str, str] = {
    "UP": "base+",
    "DOWN": "base-",
    "RIGHT": "side+",
    "LEFT": "side-",
    "[": "step-",
    "]": "step+",
    "1": "mark_base_min",
    "2": "mark_base_max",
    "3": "mark_side_min",
    "4": "mark_side_max",
    "h": "home",
    "s": "save",
    "q": "quit",
}

# action → (limit_name) for the four mark actions.
_MARK_TARGETS: dict[str, str] = {
    "mark_base_min": "base_min",
    "mark_base_max": "base_max",
    "mark_side_min": "side_min",
    "mark_side_max": "side_max",
}


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class JogState:
    """Immutable jog cursor: where we are and how big each step is."""

    base: int = 0
    side: int = 0
    step: int = STEP_DEFAULT


def key_to_action(key: str) -> str | None:
    """Map a normalized key token to an action name, or ``None`` if unmapped."""
    return _KEY_ACTIONS.get(key)


def apply_action(state: JogState, action: str) -> tuple[JogState, tuple[str, int] | None]:
    """Apply an action to the cursor.

    Returns ``(new_state, mark)`` where ``mark`` is ``None`` except for the four
    mark actions, which return ``(limit_name, captured_value)`` and leave the
    cursor unchanged. ``home`` / ``save`` / ``quit`` are handled by the caller;
    here ``home`` resets the cursor and ``save``/``quit`` are no-ops on state.
    """
    if action == "base+":
        return replace(state, base=_clamp(state.base + state.step, JOG_BASE_MIN, JOG_BASE_MAX)), None
    if action == "base-":
        return replace(state, base=_clamp(state.base - state.step, JOG_BASE_MIN, JOG_BASE_MAX)), None
    if action == "side+":
        return replace(state, side=_clamp(state.side + state.step, JOG_SIDE_MIN, JOG_SIDE_MAX)), None
    if action == "side-":
        return replace(state, side=_clamp(state.side - state.step, JOG_SIDE_MIN, JOG_SIDE_MAX)), None
    if action == "step+":
        return replace(state, step=_clamp(state.step + 1, STEP_MIN, STEP_MAX)), None
    if action == "step-":
        return replace(state, step=_clamp(state.step - 1, STEP_MIN, STEP_MAX)), None
    if action == "home":
        return replace(state, base=0, side=0), None
    if action in _MARK_TARGETS:
        name = _MARK_TARGETS[action]
        value = state.base if name.startswith("base") else state.side
        return state, (name, value)
    # save / quit / anything else: no state change.
    return state, None


def format_status(state: JogState, load1: int, load2: int) -> str:
    """One-line live status for the REPL."""
    return f"base={state.base:>4}  side={state.side:>4}  step={state.step:>2}  load=[{load1},{load2}]"


def load_warning(load1: int, load2: int, threshold: int = LOAD_WARN_THRESHOLD) -> str | None:
    """Return a warning string if either servo's |load| exceeds ``threshold``."""
    hot = [sid for sid, load in ((1, load1), (2, load2)) if abs(load) > threshold]
    if not hot:
        return None
    return f"WARNING: high load (servo pair index {hot}) — back off one step and mark there"
```

- [ ] **Step 4: Export the new symbols**

In `src/arm101_hand/hand/__init__.py`, add an import for the range_calib module and extend `__all__`. After the existing `from .kinematics import (...)` block, add:

```python
from .range_calib import (
    JogState,
    apply_action,
    format_status,
    key_to_action,
    load_warning,
)
```

And add these names to `__all__`:

```python
    "JogState",
    "apply_action",
    "format_status",
    "key_to_action",
    "load_warning",
```

- [ ] **Step 5: Run the tests + lint**

Run: `uv run pytest tests/hand/test_range_calib.py -q && uv run ruff check src/arm101_hand/hand/range_calib.py tests/hand/test_range_calib.py`
Expected: ALL PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/hand/range_calib.py src/arm101_hand/hand/__init__.py tests/hand/test_range_calib.py
git commit -m "feat: pure jog-to-limit state machine for hand range calibration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: The jog REPL script — `AmazingHand_RangeCalib.py`

**Files:**
- Create: `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`

This is the Windows-only (`msvcrt`) I/O shell. It is not unit-tested (interactive + hardware); its logic lives in Task 4. Verification is the on-bench checklist at the end.

- [ ] **Step 1: Write the script**

Create `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`:

```python
"""AmazingHand Range Calibration Script (Step 4) — arrow-key jog-to-limit.

Discovers per-finger DOF limits (logical-frame ``base``/``side`` min/max) on the
real hardware and writes them into ``AmazingHand_calib_values.yaml``. Run AFTER
middle-position calibration (Step 3).

Controls (torque ON the whole time):
  Up / Down     base + / -   (flex toward close / extend toward open)
  Right / Left  side + / -   (spread)
  [ / ]         shrink / grow the jog step
  1 2 3 4       mark base_min / base_max / side_min / side_max at the cursor
  h             home to (0, 0) = calibrated middle
  s             save this finger's limits to YAML
  q / Ctrl+C    save and exit (torque off)

Windows-only: uses ``msvcrt.getwch`` for raw key reads. The pure jog logic is in
``arm101_hand.hand.range_calib`` (unit-tested); this file only does I/O.
"""

import msvcrt
from pathlib import Path

import numpy as np
import yaml

from arm101_hand.hand import (
    apply_action,
    compose_finger,
    degrees_to_servo_radians,
    format_status,
    key_to_action,
    load_warning,
)
from arm101_hand.hand.range_calib import JogState
from rustypot import Scs0009PyController

SCRIPT_DIR = Path(__file__).resolve().parent
YAML_PATH = SCRIPT_DIR / "AmazingHand_calib_values.yaml"

VALID_FINGERS = ("index", "middle", "ring", "thumb")


class InlineDict(dict):
    pass


def _inline_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


yaml.SafeDumper.add_representer(InlineDict, _inline_dict_representer)


def load_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_config(path, data):
    out = {k: v for k, v in data.items() if k != "fingers"}
    out["fingers"] = {}
    for finger, block in data["fingers"].items():
        out["fingers"][finger] = {
            "servo_1": InlineDict(block["servo_1"]),
            "servo_2": InlineDict(block["servo_2"]),
            "limits": InlineDict(block["limits"]),
        }
    with open(path, "w") as f:
        yaml.safe_dump(out, f, sort_keys=False, default_flow_style=False)


def prompt_finger():
    while True:
        choice = input(f"Which finger to range-calibrate? ({'/'.join(VALID_FINGERS)}): ").strip().lower()
        if choice in VALID_FINGERS:
            return choice
        print(f"  invalid -- pick one of {VALID_FINGERS}")


def read_key():
    """Block for one key; normalize arrows to UP/DOWN/LEFT/RIGHT tokens."""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow / function-key prefix
        code = msvcrt.getwch()
        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code, "")
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def write_cursor(c, id1, id2, mp1, mp2, state, speed):
    """Compose the (base, side) cursor and command both servos."""
    pos1, pos2 = compose_finger(state.base, state.side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))


def read_loads(c, id1, id2):
    def _scalar(v):
        return int(v[0]) if isinstance(v, (list, tuple)) else int(v)

    return _scalar(c.read_present_load(id1)), _scalar(c.read_present_load(id2))


def main():
    config = load_config(YAML_PATH)
    finger = prompt_finger()
    block = config["fingers"][finger]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    speed = config["speed"]
    limits = dict(block["limits"])  # start from current stored limits

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    state = JogState()
    print(__doc__)
    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] current limits: {limits}")
    write_cursor(c, id1, id2, mp1, mp2, state, speed)

    should_save = True
    try:
        while True:
            key = read_key()
            action = key_to_action(key)
            if action is None:
                continue
            if action == "quit":
                break
            if action == "save":
                block["limits"] = InlineDict(limits)
                save_config(YAML_PATH, config)
                print(f"  saved limits {limits} for {finger}")
                continue

            state, mark = apply_action(state, action)
            if mark is not None:
                name, value = mark
                limits[name] = value
                print(f"  marked {name} = {value}")
            else:
                write_cursor(c, id1, id2, mp1, mp2, state, speed)

            load1, load2 = read_loads(c, id1, id2)
            print("  " + format_status(state, load1, load2))
            warn = load_warning(load1, load2)
            if warn:
                print("  " + warn)
    except KeyboardInterrupt:
        print("\n^C -- saving and exiting")
    finally:
        if should_save:
            block["limits"] = InlineDict(limits)
            save_config(YAML_PATH, config)
            print(f"Saved limits for {finger} to {YAML_PATH}")
        for sid in (id1, id2):
            try:
                c.write_torque_enable(sid, 0)
            except Exception as e:
                print(f"warning: failed to disable torque on {sid}: {e}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports without hardware**

Run: `uv run python -c "import ast; ast.parse(open(r'scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py').read()); print('parse OK')"`
Expected: `parse OK` (syntax check without opening the bus). Then `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py` — clean.

- [ ] **Step 3: On-bench manual verification (requires powered hand, IL-4 single-owner bus)**

Close any other COM-port holder. Then:
Run: `uv run python scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`
- Pick `index`. Confirm the finger holds at middle.
- Tap ↑ a few times — finger flexes toward close; status line updates; load shown.
- Jog to the comfortable full-close (just before any buzz), press `2` (base_max).
- ↓ to full-open, press `1` (base_min). →/← to spread extremes, press `4`/`3`.
- Press `s` — confirm `AmazingHand_calib_values.yaml` shows the new `index.limits`.
- Press `q` — finger goes limp (torque off).

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py
git commit -m "feat: add AmazingHand_RangeCalib jog-to-limit script (Step 4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Refactor `AmazingHand_FingerTest.py` to derive poses from limits

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`

Replace the hardcoded `±90`/`±30` close/open with limit-derived poses. Closed = `compose_finger(base_max, 0)`, open = `compose_finger(base_min, 0)`, converted to servo radians.

- [ ] **Step 1: Rewrite the motion helpers + main to read limits**

Replace the `close_finger`, `open_finger`, and `main` functions in `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py` with the versions below, and add the new imports at the top (next to the existing `from rustypot import Scs0009PyController`):

```python
from arm101_hand.hand import compose_finger, degrees_to_servo_radians
```

New helpers + main (everything from `def close_finger` onward in the file):

```python
def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.01)


def close_finger(c, id1, id2, mp1, mp2, limits, speed):
    # Full flexion at neutral spread.
    _send_pose(c, id1, id2, mp1, mp2, limits["base_max"], 0, speed)


def open_finger(c, id1, id2, mp1, mp2, limits, speed):
    # Full extension at neutral spread.
    _send_pose(c, id1, id2, mp1, mp2, limits["base_min"], 0, speed)


def main():
    with open(YAML_PATH, "r") as f:
        config = yaml.safe_load(f)

    finger = prompt_finger()
    block = config["fingers"][finger]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    limits = block["limits"]
    speed = config["speed"]

    c = Scs0009PyController(
        serial_port=config["com_port"],
        baudrate=config["baudrate"],
        timeout=config["timeout"],
    )
    c.write_torque_enable(id1, 1)
    c.write_torque_enable(id2, 1)

    print(f"[finger={finger}, ID_1={id1}, ID_2={id2}] limits={limits}")
    print("cycling Close <-> Open (from calibrated limits) -- Ctrl+C to stop")

    try:
        while True:
            close_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(3)
            open_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n^C -- stopping")
    finally:
        try:
            c.write_torque_enable(id1, 0)
            c.write_torque_enable(id2, 0)
        except Exception as e:
            print(f"warning: failed to disable torque: {e}")
```

The `np` import is no longer used by this file — remove the `import numpy as np` line if ruff flags it (F401).

- [ ] **Step 2: Syntax + lint check (no hardware)**

Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`
Expected: clean (fix any F401 by removing the unused `numpy` import).

- [ ] **Step 3: On-bench verification**

Run: `uv run python scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`, pick a finger.
Expected: the finger cycles close↔open using the calibrated `base_max`/`base_min`; no servo whines at either endpoint.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_FingerTest.py
git commit -m "refactor: derive FingerTest poses from calibrated limits

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Refactor `AmazingHand_FullHand_Test.py` to derive poses from limits

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`

The demo currently hardcodes `(90, -90)`, `(-35, 35)`, `(-10, 70)`, etc. Replace those with `(base, side)` values derived from each finger's limits, composed via `compose_finger` + `degrees_to_servo_radians`. The existing `_move(name, angle_1, angle_2, speed)` writes raw servo-frame angles; we replace it with a `(base, side)` mover.

- [ ] **Step 1: Replace the mover and pose functions**

In `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`:

1. Add imports near the top (next to `from rustypot import Scs0009PyController`):

```python
from arm101_hand.hand import compose_finger, degrees_to_servo_radians
```

2. Replace `_move` and the per-finger `Move_*` helpers with a single limit-aware mover:

```python
def _move(name, base, side, speed):
    block = _FINGERS[name]
    id1 = block["servo_1"]["id"]
    id2 = block["servo_2"]["id"]
    mp1 = block["servo_1"]["middle_pos"]
    mp2 = block["servo_2"]["middle_pos"]
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    time.sleep(0.0002)
    c.write_goal_speed(id2, speed)
    time.sleep(0.0002)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.005)


def _limits(name):
    return _FINGERS[name]["limits"]


def Move_Index(base, side, speed):
    _move("index", base, side, speed)


def Move_Middle(base, side, speed):
    _move("middle", base, side, speed)


def Move_Ring(base, side, speed):
    _move("ring", base, side, speed)


def Move_Thumb(base, side, speed):
    _move("thumb", base, side, speed)
```

3. Replace the pose functions (`InitialPose`, `OpenHand`, `CloseHand`, `IndexOnly`, `MiddleOnly`, `RingOnly`, `ThumbOnly`) with limit-derived versions. `base`/`side` now come from each finger's calibrated envelope:

```python
def _close(name):
    return _limits(name)["base_max"], 0


def _open(name):
    return _limits(name)["base_min"], 0


def _spread_pose(name, frac):
    # An isolation pose: nearly open, spread by ``frac`` of the side range.
    lim = _limits(name)
    side = int((lim["side_max"] if frac > 0 else lim["side_min"]) * abs(frac))
    return lim["base_min"], side


def InitialPose():
    for name, mover in _MOVERS.items():
        mover(*_open(name), MaxSpeed)


def OpenHand():
    for name, mover in _MOVERS.items():
        mover(*_open(name), MaxSpeed)


def CloseHand():
    for name, mover in _MOVERS.items():
        mover(*_close(name), CloseSpeed)


def _isolate(active):
    # Close every finger except ``active``, which sweeps its spread range.
    for name, mover in _MOVERS.items():
        if name != active:
            mover(*_close(name), MaxSpeed)
    mover = _MOVERS[active]
    mover(*_open(active), MaxSpeed)
    time.sleep(0.6)
    mover(*_spread_pose(active, 1.0), MaxSpeed)
    time.sleep(0.4)
    mover(*_spread_pose(active, -1.0), MaxSpeed)
    time.sleep(0.4)
    mover(*_open(active), MaxSpeed)


def IndexOnly():
    _isolate("index")


def MiddleOnly():
    _isolate("middle")


def RingOnly():
    _isolate("ring")


def ThumbOnly():
    _isolate("thumb")
```

4. Add the `_MOVERS` registry just after the `Move_Thumb` definition:

```python
_MOVERS = {
    "index": Move_Index,
    "middle": Move_Middle,
    "ring": Move_Ring,
    "thumb": Move_Thumb,
}
```

The module-level `import numpy as np` is now unused — remove it if ruff flags F401.

- [ ] **Step 2: Lint check**

Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`
Expected: clean (remove unused `numpy` import if flagged).

- [ ] **Step 3: On-bench verification**

Run: `uv run python scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`
Expected: fist → open → per-finger isolation → neutral, all driven from calibrated limits; no endpoint whine; torque releases on the Enter prompt / Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py
git commit -m "refactor: derive FullHand demo poses from calibrated limits

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Refactor `AmazingHand_MiddlePos_FingerCalib.py` test cycle to respect limits

**Files:**
- Modify: `scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`

This script keeps its purpose (tuning `middle_pos`), but its hardcoded `±90`/`±30` close/open cycle should derive from the finger's limits so it never drives past a calibrated stop while you tune.

- [ ] **Step 1: Replace `close_finger` / `open_finger` and thread `limits` through `main`**

Add the import near the existing rustypot import:

```python
from arm101_hand.hand import compose_finger, degrees_to_servo_radians
```

Replace `close_finger` and `open_finger`:

```python
def _send_pose(c, id1, id2, mp1, mp2, base, side, speed):
    pos1, pos2 = compose_finger(base, side)
    c.write_goal_speed(id1, speed)
    c.write_goal_speed(id2, speed)
    c.write_goal_position(id1, degrees_to_servo_radians(id1, pos1, mp1))
    c.write_goal_position(id2, degrees_to_servo_radians(id2, pos2, mp2))
    time.sleep(0.01)


def close_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits["base_max"], 0, speed)


def open_finger(c, id1, id2, mp1, mp2, limits, speed):
    _send_pose(c, id1, id2, mp1, mp2, limits["base_min"], 0, speed)
```

In `main`, after `mp2 = block["servo_2"]["middle_pos"]`, add:

```python
    limits = block["limits"]
```

and update the two call sites inside the loop:

```python
            close_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(3)
            open_finger(c, id1, id2, mp1, mp2, limits, speed)
            time.sleep(1)
```

Note: `mp1`/`mp2` are mutated by the tuning loop, and `degrees_to_servo_radians` already adds `middle_pos`, so the close/open poses track the current middle as you tune — correct. Remove the now-unused `import numpy as np` if ruff flags it.

- [ ] **Step 2: Lint check**

Run: `uv run ruff check scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`
Expected: clean.

- [ ] **Step 3: On-bench verification**

Run: `uv run python scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`, pick a finger.
Expected: the close/open cycle stays within the calibrated envelope; the `MiddlePos_1/2` prompts still adjust and persist `middle_pos`.

- [ ] **Step 4: Commit**

```bash
git add scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py
git commit -m "refactor: bound MiddlePos calib cycle by calibrated limits

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Update the calibration README (add Step 4)

**Files:**
- Modify: `scripts/calibration/AmazingHand/README.md`

- [ ] **Step 1: Insert the range-calibration step and renumber**

In `scripts/calibration/AmazingHand/README.md`:

1. After the existing **"Step 3 — Fine-tune middle positions"** section (ends just before "### Step 4 — Audit with FingerTest"), insert a new section:

```markdown
### Step 4 — Calibrate per-finger motion limits

**Script:** `scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`

Each finger's reachable envelope (how far it flexes/extends and how far it
spreads) is measured here and stored as logical-frame `base`/`side` min/max in
`AmazingHand_calib_values.yaml`. This is the hand's analog of the SO-ARM101
follower's `range_min`/`range_max` sweep. Torque stays ON throughout; you jog
the finger with the arrow keys and mark each limit.

> **Windows-only.** Uses `msvcrt` for raw arrow-key reads.

1. Close any other COM-port holder (IL-4). Run:
   `uv run python scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py`
2. Pick a finger. It holds at calibrated middle `(base=0, side=0)`.
3. Jog to each extreme and mark it:

   | Key | Action |
   | --- | --- |
   | ↑ / ↓ | flex toward close / extend toward open (`base` ±) |
   | → / ← | spread (`side` ±) |
   | `[` / `]` | shrink / grow the jog step (default 5°) |
   | `1` `2` `3` `4` | mark `base_min` / `base_max` / `side_min` / `side_max` |
   | `h` | home to `(0, 0)` |
   | `s` | save this finger's limits |
   | `q` / Ctrl+C | save and exit (torque off) |

4. **Mark just *before* the mechanical stop, not at it.** A `WARNING: high load`
   line means you've pushed into the stop — back off one step and mark there.
5. Repeat for all four fingers.
```

2. Renumber the following sections: the old **"Step 4 — Audit with FingerTest"** becomes **"Step 5 — Audit with FingerTest"**, and **"Step 5 — Full-hand demo"** becomes **"Step 6 — Full-hand demo"**.

3. Update the **"One-line summary"** at the bottom to insert range calibration between middle-pos tuning and the audit:

Change the summary sentence to:

```markdown
Burn unique IDs (FD.exe) → reset each finger's motors to 0° and install horns (`AmazingHand_MotorReset.py`) → interactively dial in per-servo offsets (`AmazingHand_MiddlePos_FingerCalib.py`) → measure per-finger motion limits (`AmazingHand_RangeCalib.py`) → audit per-finger with `AmazingHand_FingerTest.py` → run the full-hand demo (`AmazingHand_FullHand_Test.py`). All state lives in `AmazingHand_calib_values.yaml`.
```

4. In the **"Where the calibration lives"** section, update the example YAML to the v2 shape (add `schema_version: 2` at the top and a `limits: { base_min: ..., base_max: ..., side_min: ..., side_max: ... }` line under each finger), and add a bullet explaining the `limits` block.

- [ ] **Step 2: Commit**

```bash
git add scripts/calibration/AmazingHand/README.md
git commit -m "docs: add Step 4 range calibration to AmazingHand README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Full verification + refresh CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (via the doc_update skill)

- [ ] **Step 1: Run the full host test suite + lint + type-check**

Run:
```powershell
uv run pytest -m 'not hardware' -q
uv run ruff check src scripts/calibration/AmazingHand
uv run mypy src
```
Expected: all tests PASS, ruff clean, mypy clean (or only the project's pre-existing baseline notes — do not introduce new errors in the files you touched).

- [ ] **Step 2: Refresh CLAUDE.md via the doc_update skill**

Invoke the `doc_update` skill. The agent self-reference needs these corrections:
- §7 "Tech-debt": the claim that `src/arm101_hand/hand/` and `src/arm101_hand/config/` are "placeholders" is **stale** — both are populated (`controller.py`, `kinematics.py`, `range_calib.py`; `calibration.py`, `hand_poses.py`, etc.). Correct it.
- §4 "Common workflows": add the new `AmazingHand_RangeCalib.py` step (Step 4) to the AmazingHand calibration block.
- Note the calibration YAML is now **schema v2** with required per-finger `limits`, and that `tests/` now exists (so `uv run pytest -m 'not hardware'` is live).

- [ ] **Step 3: Commit the doc refresh**

```bash
git add CLAUDE.md
git commit -m "docs: refresh CLAUDE.md for hand range calibration + populated src modules

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Run the iron-law-audit skill before opening a PR**

Invoke the `iron-law-audit` skill against the branch diff. Expected: IL-5 (calibration in-tree) satisfied by the YAML; IL-2 (no `references/` edits) clean; IL-6 (hand-only, no arm coupling) clean. Address any flagged item before merge.

---

## Self-Review Notes (for the implementer)

- **Frame correctness** is the highest-risk area. Task 3's `test_asymmetric_spread_roundtrip` is the guard — if it fails, the stored `side` sign is inverted relative to the GUI/controller. Do not "fix" it by flipping a sign in the script; the logical `base`/`side` convention in `kinematics` is canonical.
- **Backward-compat is intentionally absent.** After Task 1+2, the old GUI's `HandController(calibration=…)` startup load fails on any un-migrated YAML. That is expected (Approach B); the GUI redesign owns re-wiring. Do not add a v1 fallback.
- **`compose_finger` defaults are deliberately wide** (`base_min=-110, base_max=110`) so legacy positional callers in `gui/hand_panel.py` are unaffected. Calibrated callers pass per-finger limits explicitly.
- **The jog script is not unit-tested** — its logic is in `range_calib.py` (Task 4). On-bench steps (Tasks 5–8 Step 3) are the only verification for the I/O shells; run them with a powered hand before merge.
