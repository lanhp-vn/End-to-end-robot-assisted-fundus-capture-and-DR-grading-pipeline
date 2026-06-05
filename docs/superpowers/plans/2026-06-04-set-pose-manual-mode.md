# Manual angle-entry mode for `set_pose.py` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive `--manual` mode to `set_pose.py` so the operator can type exact target degrees per joint (blank = keep current), drive there, and optionally save the result as a named pose.

**Architecture:** A new pure helper `arm101_hand.robots.arm_manual.resolve_target` (host-unit-tested) parses+clamps one joint's typed input against its degree window. `set_pose.py` gains a `--manual` branch and is refactored so named mode and manual mode share one drive/hold/park tail. The pure helper takes the already-converted degree bounds `(lo, hi)` (from `calibration_summary.degree_bounds`) and clamps with `min/max` — a refinement of spec §3.5 (which drafted raw-step args) that decouples the tested logic from STS3215 resolution math.

**Tech Stack:** Python 3.12, pydantic v2, pytest, uv, ruff. Spec: `docs/superpowers/specs/2026-06-04-set-pose-manual-mode-design.md`. Branch: `refactor/arm-pose-store-kiss` (current).

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `src/arm101_hand/robots/arm_manual.py` | Pure parse/clamp of one joint's manual input | Create |
| `tests/unit/test_arm_manual.py` | Unit tests for `resolve_target` | Create |
| `scripts/calibration/so_arm101/set_pose.py` | Drive to a target pose (named OR manual) + hold + safe release | Modify (refactor + `--manual`) |
| `scripts/calibration/so_arm101/README.md` | §6 helper table — note `--manual` | Modify |
| `CLAUDE.md` | §4 workflow — note `--manual` | Modify |

---

## Task 1: Pure helper `arm_manual.resolve_target` (TDD)

**Files:**
- Create: `src/arm101_hand/robots/arm_manual.py`
- Create: `tests/unit/test_arm_manual.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_arm_manual.py`:

```python
"""Unit tests for arm101_hand.robots.arm_manual.resolve_target."""

from __future__ import annotations

import pytest

from arm101_hand.robots.arm_manual import ManualTarget, resolve_target


def test_blank_keeps_current() -> None:
    assert resolve_target(12.5, -100.0, 100.0, "") == ManualTarget(12.5, kept=True, clamped=False)


def test_blank_with_whitespace_keeps_current() -> None:
    assert resolve_target(12.5, -100.0, 100.0, "   ") == ManualTarget(12.5, kept=True, clamped=False)


def test_valid_in_range() -> None:
    assert resolve_target(0.0, -100.0, 100.0, "45") == ManualTarget(45.0, kept=False, clamped=False)


def test_value_at_bound_not_flagged_clamped() -> None:
    assert resolve_target(0.0, -100.0, 100.0, "100") == ManualTarget(100.0, kept=False, clamped=False)


def test_above_max_clamps() -> None:
    assert resolve_target(0.0, -100.0, 100.0, "150") == ManualTarget(100.0, kept=False, clamped=True)


def test_below_min_clamps() -> None:
    assert resolve_target(0.0, -100.0, 100.0, "-150") == ManualTarget(-100.0, kept=False, clamped=True)


@pytest.mark.parametrize("bad", ["abc", "--", "1.2.3", "12deg"])
def test_non_numeric_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        resolve_target(0.0, -100.0, 100.0, bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_arm_manual.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.robots.arm_manual'`.

- [ ] **Step 3: Implement the helper**

Create `src/arm101_hand/robots/arm_manual.py`:

```python
"""Pure helper for set_pose.py manual angle entry (host-unit-tested).

Resolves one joint's typed input against its degree window. No hardware, no I/O,
so the parse/clamp logic is testable without a bus -- the thin prompt loop lives
in scripts/calibration/so_arm101/set_pose.py.
"""

from __future__ import annotations

from typing import NamedTuple


class ManualTarget(NamedTuple):
    value: float  # degrees to drive to
    kept: bool  # input was blank -> kept current
    clamped: bool  # a typed value was clamped into range


def resolve_target(current: float, lo: float, hi: float, raw: str) -> ManualTarget:
    """Resolve one joint's manual input.

    Blank ``raw`` keeps ``current``. Otherwise parse a float and clamp to the
    degree window ``[lo, hi]``. Raises ``ValueError`` on non-numeric input (the
    caller re-prompts that joint).
    """
    text = raw.strip()
    if not text:
        return ManualTarget(current, kept=True, clamped=False)
    parsed = float(text)  # ValueError -> caller re-prompts
    value = min(max(parsed, lo), hi)
    return ManualTarget(value, kept=False, clamped=value != parsed)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_arm_manual.py -v`
Expected: PASS (8 tests: 6 named + 4 parametrized minus overlaps = all green).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/arm101_hand/robots/arm_manual.py tests/unit/test_arm_manual.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/robots/arm_manual.py tests/unit/test_arm_manual.py
git commit -m "feat(arm): pure resolve_target helper for manual angle entry

Blank keeps current; numeric values clamp to the joint degree window; non-numeric
raises ValueError. Host-unit-tested; no hardware. Backs set_pose.py --manual.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `--manual` mode to `set_pose.py`

The current `main()` is monolithic. Refactor it into small functions and add the manual branch. Named-mode behavior is unchanged. Replace the ENTIRE file with the content below (it is the full, final file — no partial edits).

**Files:**
- Modify: `scripts/calibration/so_arm101/set_pose.py` (full replacement)

- [ ] **Step 1: Replace `set_pose.py` with the new version**

```python
"""Drive the SO-ARM101 to a target pose (degrees) and hold it under torque until Enter.

Two ways to choose the target:
  * by name  -- a pose from data/arm_config.yaml ``poses`` (e.g. ``home``).
  * --manual -- type each joint's target degrees interactively (blank = keep current).

Named targets are clamped to each joint's usable degree window; an out-of-range value is
warned and that joint is skipped. Manual entries are clamped into range (nearest valid),
since a typed value is direct operator intent. After connect() the script pushes the
on-file calibration to the motors so degree targets physically match the recorded range
(writes the MOTORS, never the JSON -- IL-5).

In --manual mode, after the arm is holding the pose you can save it into
data/arm_config.yaml ``poses`` under a name (e.g. ``home``).

On exit (Enter, Ctrl+C, or EOF) the arm is first driven back to the configured home and
only then is torque released, so it never drops under gravity from the held pose (see
_common.park_home_and_release).

Usage:
  uv run python scripts/calibration/so_arm101/set_pose.py home
  uv run python scripts/calibration/so_arm101/set_pose.py --manual
  uv run python scripts/calibration/so_arm101/set_pose.py            # prompts for a name

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally.
"""

from __future__ import annotations

import sys
import time

from _common import (
    ARM_CONFIG_PATH,
    CALIB_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    park_home_and_release,
)

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from arm101_hand.robots.arm_manual import resolve_target
from arm101_hand.robots.calibration_summary import (
    ARM_JOINTS,
    clamp_degrees,
    degree_bounds,
    load_arm_calibration,
)

_SETTLE_S = 2.0


def _resolve_pose_name(argv: list[str], available: list[str]) -> str:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if args and args[0] in available:
        return args[0]
    if args:
        print(f"  {args[0]!r} is not a known pose; choose from {available}")
    while True:
        try:
            choice = input(f"Pose? ({'/'.join(available)}) or blank to cancel: ").strip()
        except EOFError:
            print("\nNo input -- exiting.", file=sys.stderr)
            raise SystemExit(1) from None
        if choice in available:
            return choice
        print(f"  invalid -- pick one of {available} (or run with --manual)")


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _named_targets(pose: dict[str, float], calib) -> dict[str, float]:
    """Clamp each joint to its degree window; skip (warn) any out-of-range joint."""
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        raw = pose[joint]
        clamped = clamp_degrees(raw, calib[joint].range_min, calib[joint].range_max)
        if abs(clamped - raw) > 1e-6:
            print(f"  WARNING: {joint} target {raw:.1f} deg out of range -> skipped")
            continue
        targets[joint] = clamped
    return targets


def _prompt_manual_targets(present: dict[str, float], calib) -> dict[str, float]:
    """Prompt per joint for target degrees (blank keeps current); clamp into range."""
    targets: dict[str, float] = {}
    for joint in ARM_JOINTS:
        lo, hi = degree_bounds(calib[joint].range_min, calib[joint].range_max)
        cur = present[joint]
        while True:
            raw = input(f"  {joint:13s} [cur {cur:.1f}, range {lo:.1f}..{hi:.1f}]: ")
            try:
                result = resolve_target(cur, lo, hi, raw)
            except ValueError:
                print("    not a number -- try again (blank keeps current)")
                continue
            if result.clamped:
                print(f"    out of range -- clamped to {result.value:.1f}")
            targets[joint] = result.value
            break
    return targets


def _maybe_save(targets: dict[str, float]) -> None:
    """Offer to save the held pose into arm_config.yaml ``poses`` (blank = skip)."""
    try:
        name = input("Save this pose? name (blank = don't save): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        return
    config = load_arm_poses(ARM_CONFIG_PATH) if ARM_CONFIG_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**targets)
    save_arm_poses(ARM_CONFIG_PATH, config)
    print(f"  saved '{name}' -> {ARM_CONFIG_PATH}")


def main() -> int:
    manual = "--manual" in sys.argv[1:]

    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()
    calib = load_arm_calibration(CALIB_PATH)

    targets: dict[str, float] = {}
    label = "manual"

    # Named mode resolves its target BEFORE connecting (fail fast on a bad name).
    if not manual:
        poses = load_arm_poses(ARM_CONFIG_PATH).poses
        if not poses:
            print(
                f"no poses defined in {ARM_CONFIG_PATH} (poses); use --manual to enter angles",
                file=sys.stderr,
            )
            return 1
        available = sorted(poses)
        name = _resolve_pose_name(sys.argv, available)
        targets = _named_targets(poses[name].as_dict(), calib)
        label = name
        if not targets:
            print("ERROR: all joints out of range -- nothing to drive", file=sys.stderr)
            return 1

    torque_on = False
    print(f"Connecting on {cfg.arm.port} ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)

        # Manual mode needs the live present positions to show + keep, so it prompts
        # only after a successful connect.
        if manual:
            present = _present_degrees(follower)
            print("Enter target degrees per joint (blank = keep current):")
            targets = _prompt_manual_targets(present, calib)

        # STS3215 movement-speed register is "Goal_Velocity" (reg 46), not "Profile_Velocity".
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))
        follower.bus.enable_torque()
        torque_on = True
        follower.send_action({f"{j}.pos": v for j, v in targets.items()})
        time.sleep(_SETTLE_S)
        print(f"Holding '{label}' under torque: {targets}")
        if manual:
            _maybe_save(targets)
        input("Press Enter to return home and release torque... ")
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- returning home")
    finally:
        # Always return to the configured home before releasing torque (see
        # park_home_and_release): avoids the arm dropping from the held pose.
        if follower.is_connected:
            if torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, home, vel)
                print("Home reached; torque off.")
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify it parses and imports resolve clean**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/so_arm101/set_pose.py').read()); print('parsed ok')"`
Expected: `parsed ok`

Run: `uv run python -c "from arm101_hand.robots.arm_manual import resolve_target; from arm101_hand.robots.calibration_summary import degree_bounds, clamp_degrees, load_arm_calibration, ARM_JOINTS; from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 3: Confirm `--manual` is recognized without a bus (arg parse smoke test)**

Run: `uv run python -c "import sys; sys.argv=['set_pose.py','--manual']; print('--manual' in sys.argv[1:])"`
Expected: `True`

- [ ] **Step 4: Full host test suite + lint (no regressions)**

Run: `uv run pytest -m 'not hardware' -q`
Expected: all pass.

Run: `uv run ruff check scripts/calibration/so_arm101/set_pose.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/set_pose.py
git commit -m "feat(arm): set_pose.py --manual interactive angle entry

Refactors main() into _named_targets / _prompt_manual_targets / _maybe_save /
shared drive-hold-park. --manual prompts per joint (blank keeps current), clamps to
range, drives, and can save the result into arm_config.yaml poses. Named mode unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Document `--manual`

**Files:**
- Modify: `scripts/calibration/so_arm101/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the `set_pose.py` row in `scripts/calibration/so_arm101/README.md` §6**

Find the table row that currently reads (approximately):

```
| `set_pose.py` | yes | on→off | DEGREES | Drive to a `poses` entry from `data/arm_config.yaml` (`home` — the folded storage pose) and hold until Enter. |
```

Replace it with:

```
| `set_pose.py` | yes | on→off | DEGREES | Drive to a `poses` entry from `data/arm_config.yaml` (`home` — the folded storage pose), or `--manual` to type target degrees per joint (blank = keep current) and optionally save the result. Holds until Enter. |
```

Then, in the example commands block just below the table, add a line after the existing `set_pose.py home` example:

```
uv run python scripts/calibration/so_arm101/set_pose.py --manual         # type angles, optionally save
```

- [ ] **Step 2: Update the `set_pose.py` line in `CLAUDE.md` §4**

Find (around line 86):

```
uv run python scripts/calibration/so_arm101/set_pose.py home              # drive to a poses entry (home = folded storage), hold
```

Add a second line immediately after it:

```
uv run python scripts/calibration/so_arm101/set_pose.py --manual          # type target degrees per joint; optionally save as a named pose
```

- [ ] **Step 3: Commit**

```bash
git add scripts/calibration/so_arm101/README.md CLAUDE.md
git commit -m "docs(arm): document set_pose.py --manual mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Full host test suite**

Run: `uv run pytest -m 'not hardware' -q`
Expected: all pass (includes `test_arm_manual.py`).

- [ ] **Step 2: Lint + format check on touched files**

Run: `uv run ruff check src/arm101_hand/robots/arm_manual.py tests/unit/test_arm_manual.py scripts/calibration/so_arm101/set_pose.py`
Expected: `All checks passed!`

Run: `uv run ruff format --check src/arm101_hand/robots/arm_manual.py tests/unit/test_arm_manual.py scripts/calibration/so_arm101/set_pose.py`
Expected: all already formatted.

- [ ] **Step 3: Branch review**

Run: `git status` and `git log --oneline main..HEAD | head`
Expected: clean tree; the three new commits present.

- [ ] **Step 4: Operator hardware check (manual, not automated)**

Documented for the operator (interactive, needs the bus on COM20):
```
uv run python scripts/calibration/so_arm101/set_pose.py --manual
```
Enter a value for one or two joints (blank the rest), confirm the arm drives there and holds,
save under a throwaway name (e.g. `test1`), confirm it lands in `data/arm_config.yaml`, then
`set_pose.py test1` drives back to it. Press Enter to home and release.

---

## Self-review notes (already applied)

- **Spec coverage:** interface §3.1 → Task 2 (`--manual` flag, named/no-arg unchanged); two-modes-one-core §3.2 → Task 2 (`_named_targets`/`_prompt_manual_targets` + a shared drive/hold/park tail **inlined in `main()`** rather than the spec's drafted `_drive_hold_release()` — manual mode must prompt *between* connect and drive, so one inlined sequence is clearer than threading a prompt callback through a helper; deliberate KISS refinement); prompt loop §3.3 → `_prompt_manual_targets`; save §3.4 → `_maybe_save` (manual only); pure helper §3.5 → Task 1 (refined to degree-bounds args, noted in Architecture); files §4 → Tasks 1–3; error handling §5 → re-prompt on `ValueError`, `finally` park on EOF/Ctrl-C, clamp-with-warning; testing §6 → Task 1 tests + Task 4 operator check; YAGNI §7 honored (interactive only, no save in named mode, no new script).
- **Placeholder scan:** none — every code step is complete; doc steps quote the exact target text.
- **Type/name consistency:** `ManualTarget(value, kept, clamped)` and `resolve_target(current, lo, hi, raw)` identical across Task 1 and Task 2; `_named_targets`, `_prompt_manual_targets`, `_maybe_save`, `_present_degrees`, `_resolve_pose_name` names consistent; imports (`ArmPose`, `ArmPoseConfig`, `load_arm_poses`, `save_arm_poses`, `degree_bounds`, `clamp_degrees`, `load_arm_calibration`, `ARM_JOINTS`) all exist in the cited modules.
