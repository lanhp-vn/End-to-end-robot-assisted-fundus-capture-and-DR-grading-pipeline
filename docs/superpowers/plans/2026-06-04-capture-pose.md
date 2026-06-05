# `capture_pose.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scripts/calibration/so_arm101/capture_pose.py` — a minimal tool to record the arm's current hand-posed pose (torque off → move by hand → Enter captures + holds → save) and loop (`h`) or quit (`q`), both returning home safely.

**Architecture:** A standalone script reusing the existing `_common` connect/velocity/home/park helpers and the `arm101_hand.config` pose-persistence functions. No drive-to-target, no clamping, no numeric entry. No new pure logic, so no unit test module — verification is parse/import/ruff/pytest + an operator hardware check.

**Tech Stack:** Python 3.12, pydantic v2, pyserial via lerobot, pytest, uv, ruff. Spec: `docs/superpowers/specs/2026-06-04-capture-pose-design.md`. Branch: `refactor/arm-pose-store-kiss` (current).

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `scripts/calibration/so_arm101/capture_pose.py` | Capture current pose by hand + save; home/quit loop | Create |
| `scripts/calibration/so_arm101/README.md` | §6 helper table + example | Modify |
| `CLAUDE.md` | §4 workflow block | Modify |

---

## Task 1: Create `capture_pose.py`

**Files:**
- Create: `scripts/calibration/so_arm101/capture_pose.py`

- [ ] **Step 1: Write the script**

Create `scripts/calibration/so_arm101/capture_pose.py` with exactly this content:

```python
"""Capture the SO-ARM101's current hand-posed pose and save it to data/arm_config.yaml.

Workflow (no jogging, no numeric entry): the arm goes limp so you move it to the desired
pose by hand, then Enter captures the present motor degrees and holds the pose under torque
so it does not sag. You can save the captured degrees under a name (e.g. ``home``). Then:

  h   return to home, release torque, and capture another pose
  q   return to home, release torque, and quit

Both routes drive back to the configured home BEFORE releasing torque (see
_common.park_home_and_release), so the arm never drops under gravity from the held pose.

Usage:
  uv run python scripts/calibration/so_arm101/capture_pose.py

IL-3: motors 1-5. IL-4: single bus owner; torque released in finally. IL-5: pushes the
on-file calibration to the motors (so present positions read in the recorded degree frame)
but never writes so101_follower.json.
"""

from __future__ import annotations

import sys
import time

from _common import (
    ARM_CONFIG_PATH,
    build_follower,
    gentle_velocity,
    load_arm_app_config,
    load_home_degrees,
    park_home_and_release,
)

from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses
from arm101_hand.robots.calibration_summary import ARM_JOINTS

_SETTLE_S = 0.5


def _present_degrees(follower) -> dict[str, float]:
    obs = follower.get_observation()  # {f"{joint}.pos": degrees}
    return {j: float(obs[f"{j}.pos"]) for j in ARM_JOINTS}


def _maybe_save(pose: dict[str, float]) -> None:
    """Prompt for a name and upsert the captured pose into arm_config.yaml (blank = skip)."""
    try:
        name = input("Save as (name, blank = don't save): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("  save cancelled")
        return
    if not name:
        return
    config = load_arm_poses(ARM_CONFIG_PATH) if ARM_CONFIG_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**pose)
    save_arm_poses(ARM_CONFIG_PATH, config)
    print(f"  saved '{name}' -> {ARM_CONFIG_PATH}")


def _menu() -> str:
    """Re-prompt until the operator types h or q; EOF/Ctrl-C -> q."""
    while True:
        try:
            choice = input("[h] return home & capture another   [q] return home & quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "q"
        if choice in ("h", "q"):
            return choice
        print("  please type h or q")


def main() -> int:
    cfg = load_arm_app_config()
    follower = build_follower(cfg, use_degrees=True)  # DEGREES
    vel = gentle_velocity(cfg)
    home = load_home_degrees()

    torque_on = False
    print(f"Connecting on {cfg.arm.port} ...")
    try:
        try:
            follower.connect(calibrate=False)
        except (ConnectionError, OSError) as e:
            print(f"ERROR: could not open {cfg.arm.port}: {e}", file=sys.stderr)
            return 1
        follower.bus.write_calibration(follower.calibration)
        follower.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, vel))

        while True:
            # Limp so the operator can move the arm to the pose by hand.
            follower.bus.disable_torque()
            torque_on = False
            try:
                input("\nMove the arm to the pose by hand, then press Enter to capture (Ctrl+C to quit)... ")
            except (EOFError, KeyboardInterrupt):
                print("\n^C/EOF -- finishing")
                break

            captured = _present_degrees(follower)

            # No-jump hold: command goal=present, THEN enable torque, so it holds where posed.
            follower.send_action({f"{j}.pos": v for j, v in captured.items()})
            follower.bus.enable_torque()
            torque_on = True
            time.sleep(_SETTLE_S)
            pretty = ", ".join(f"{j} {captured[j]:.1f}" for j in ARM_JOINTS)
            print(f"Captured (holding under torque): {pretty}")

            _maybe_save(captured)

            choice = _menu()
            print("Returning to home before releasing torque ...")
            park_home_and_release(follower, home, vel)
            torque_on = False
            print("Home reached; torque off.")
            if choice == "q":
                break
    except (EOFError, KeyboardInterrupt):
        print("\n^C/EOF -- returning home")
    finally:
        if follower.is_connected:
            if torque_on:
                print("Returning to home before releasing torque ...")
                park_home_and_release(follower, home, vel)
            else:
                follower.bus.disable_torque()
            follower.disconnect()
            print("Bus closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify it parses**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/so_arm101/capture_pose.py').read()); print('parsed ok')"`
Expected: `parsed ok`

- [ ] **Step 3: Verify every imported symbol resolves**

Run: `uv run python -c "from arm101_hand.config import ArmPose, ArmPoseConfig, load_arm_poses, save_arm_poses; from arm101_hand.robots.calibration_summary import ARM_JOINTS; print('config+canon ok')"`
Expected: `config+canon ok`

Run (the `_common` helpers, via the script dir on sys.path): `uv run python -c "import sys; sys.path.insert(0, 'scripts/calibration/so_arm101'); from _common import ARM_CONFIG_PATH, build_follower, gentle_velocity, load_arm_app_config, load_home_degrees, park_home_and_release; print('_common ok')"`
Expected: `_common ok`

- [ ] **Step 4: Lint**

Run: `uv run ruff check scripts/calibration/so_arm101/capture_pose.py`
Expected: `All checks passed!`

Run: `uv run ruff format --check scripts/calibration/so_arm101/capture_pose.py`
Expected: already formatted (if not, run `uv run ruff format scripts/calibration/so_arm101/capture_pose.py` and re-check).

- [ ] **Step 5: Full host test suite (no regressions)**

Run: `uv run pytest -m 'not hardware' -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/calibration/so_arm101/capture_pose.py
git commit -m "feat(arm): capture_pose.py -- hand-pose capture by demonstration

Torque off -> move arm by hand -> Enter captures present degrees + holds the pose ->
optional save to arm_config.yaml poses -> h (home & capture again) or q (home & quit).
Both routes park home before releasing torque. Reuses _common + arm_config save path.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Document `capture_pose.py`

**Files:**
- Modify: `scripts/calibration/so_arm101/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a row to the §6 helper table in `scripts/calibration/so_arm101/README.md`**

The table has rows for `show_calib.py`, `scan.py`, `sweep.py`, `set_pose.py`, `jog.py`. Add a new row immediately after the `jog.py` row, matching the column format (`| Script | Motion? | Torque | Norm mode | Purpose |`):

```
| `capture_pose.py` | yes | off→on→off | DEGREES | Torque off so you hand-pose the arm; Enter captures the present degrees and holds; save to `data/arm_config.yaml` `poses`. `h` = home & capture another, `q` = home & quit (both park home first). |
```

- [ ] **Step 2: Add an example to the command block just below that table**

After the existing `set_pose.py home` / `jog.py` example lines in the ```powershell block, add:

```
uv run python scripts/calibration/so_arm101/capture_pose.py            # hand-pose the arm, capture + save
```

- [ ] **Step 3: Add a line to the `CLAUDE.md` §4 workflow block**

Find the SO-ARM101 verify/audit helpers block (around the `jog.py` line). Immediately after the `jog.py` line, add:

```
uv run python scripts/calibration/so_arm101/capture_pose.py               # hand-pose the arm by hand, capture present degrees, save as a pose
```

- [ ] **Step 4: Verify no stale/missing references**

Run: `uv run python -c "import subprocess; r=subprocess.run(['git','grep','-c','capture_pose','--','scripts/calibration/so_arm101/README.md','CLAUDE.md'],capture_output=True,text=True); print(r.stdout or 'NONE')"`
Expected: both files report a non-zero count.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/README.md CLAUDE.md
git commit -m "docs(arm): document capture_pose.py in README + CLAUDE.md

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Full host test suite**

Run: `uv run pytest -m 'not hardware' -q`
Expected: all pass.

- [ ] **Step 2: Lint + format on touched files**

Run: `uv run ruff check scripts/calibration/so_arm101/capture_pose.py`
Expected: `All checks passed!`

- [ ] **Step 3: Branch review**

Run: `git status` and `git log --oneline main..HEAD | head`
Expected: clean tree; the two new commits (Tasks 1 & 2) present.

- [ ] **Step 4: Operator hardware check (manual, not automated — needs the bus on COM20)**

```
uv run python scripts/calibration/so_arm101/capture_pose.py
```
1. Confirm the arm goes limp (torque off).
2. Move it to a test pose by hand, press Enter — confirm it now holds the pose under torque and prints the captured degrees.
3. Save as a throwaway name (e.g. `test1`); confirm it appears in `data/arm_config.yaml` (`uv run python -c "from pathlib import Path; from arm101_hand.config import load_arm_poses; print(list(load_arm_poses(Path('data/arm_config.yaml')).poses))"`).
4. Press `h` — confirm it drives to home, releases, and re-prompts for another capture.
5. Capture again, then press `q` — confirm it drives home, releases torque, and exits with "Bus closed."
6. Finally, `set_pose.py test1` drives back to the captured pose (proves the round-trip).

---

## Self-review notes (already applied)

- **Spec coverage:** behavior §3.1 → Task 1 `main()` capture-loop (limp → Enter → no-jump hold → save → menu → park → loop/quit); exit/safety §3.2 → the two `except (EOFError, KeyboardInterrupt)` sites + `finally`; functions §3.3 → `_present_degrees`/`_maybe_save`/`_menu`/`main`; reuse §3.4 → imports from `_common` + `arm101_hand.config` + `calibration_summary`, no `clamp`/`load_arm_calibration`; files §4 → Tasks 1–2 (the two doc-doc deletions were already done when the spec was committed); testing §5 → Task 1 parse/import/ruff + Task 3 operator check; YAGNI §6 honored (no numeric entry, typed h/q, no msvcrt, no set_pose/jog/schema changes).
- **Placeholder scan:** none — the script is given in full; doc steps quote exact insert text.
- **Type/name consistency:** `_present_degrees`, `_maybe_save`, `_menu`, `main`, `torque_on`, `captured`, `vel`, `home` consistent throughout; all imported symbols (`ARM_CONFIG_PATH`, `build_follower`, `gentle_velocity`, `load_arm_app_config`, `load_home_degrees`, `park_home_and_release`, `ArmPose`, `ArmPoseConfig`, `load_arm_poses`, `save_arm_poses`, `ARM_JOINTS`) verified to exist in their modules (same imports `set_pose.py`/`jog.py` already use).
```
