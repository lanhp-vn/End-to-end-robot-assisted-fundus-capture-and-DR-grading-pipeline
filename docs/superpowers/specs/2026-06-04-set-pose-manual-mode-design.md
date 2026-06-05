# Design — Manual angle-entry mode for `set_pose.py`

**Date:** 2026-06-04
**Scope:** Arm-only (SO-ARM101). Add an interactive manual angle-entry mode to the existing
`set_pose.py`, plus a small unit-tested pure helper. No new script.

---

## 1. Problem

Today the only ways to position the arm are:
- `jog.py` — incremental keyboard nudging (good for fine-tuning, slow for "go to these exact
  numbers").
- `set_pose.py <name>` — drive to a *saved* pose by name.

There is no way to type exact target degrees per joint and drive there (e.g. to set a precise
`home`). The user wants manual numeric entry.

## 2. Decision

Extend `set_pose.py` rather than add a second script. `set_pose.py`'s responsibility is
*"drive the arm to a target pose and hold, with safe release"* — the **source** of the pose
(a saved name vs. typed numbers) is just input. A separate `set_angles.py` would duplicate
~80% of the machinery (connect, push calibration, clamp, gentle hold,
`park_home_and_release`) — the "same logic in two places" smell that
`docs/conventions/07-kiss-simplicity.md` warns against. One tool, two input strategies.

## 3. Design

### 3.1 Interface

| Invocation | Behavior |
|---|---|
| `set_pose.py <name>` | Drive a saved pose by name (unchanged). |
| `set_pose.py --manual` | Interactive manual entry (new). |
| `set_pose.py` (no arg) | Prompts to pick a saved pose as today; the prompt line also notes `--manual` is available. |

`--manual` is a **flag**, detected in `sys.argv`, not a pose name — so it can never collide
with a pose literally named `manual`.

### 3.2 Two modes, one shared core

`main()` branches on the flag:

- **Named mode** (existing): resolve name → clamp each joint to its degree window, *skipping*
  out-of-range joints with a warning (unchanged behavior) → connect → drive/hold → park.
- **Manual mode** (new): connect first (present positions are needed to show "cur X" and to
  use as the kept value) → read present degrees + each joint's calibrated degree window →
  prompt loop → targets → drive/hold → **offer save** → park.

Both modes converge on the same drive/hold/park tail (current lines 83–119): push calibration
to motors (IL-5: writes motors, never JSON), gentle `Goal_Velocity`, enable torque,
`send_action(targets)`, settle, hold until Enter, then `park_home_and_release` in `finally`.

To keep `set_pose.py` readable, the existing inline logic is factored into small functions
(no behavior change):
- `_named_targets(pose, calib) -> dict[str, float]` — the current clamp-and-skip loop.
- `_prompt_manual_targets(present, calib) -> dict[str, float]` — the new prompt loop.
- `_maybe_save(targets) -> None` — manual-only save prompt.
- `_drive_hold_release(follower, cfg, targets, label, vel, home, *, offer_save: bool)` — the
  shared tail, called by both modes.

### 3.3 Manual prompt loop

For each joint in canon order (`shoulder_pan, shoulder_lift, elbow_flex, wrist_flex,
wrist_roll`):

```
shoulder_pan  [cur 80.2, range -110.0..110.0]: 75
```

- **Blank** → keep the current present value.
- **A number** → clamp to the joint's `[range_min, range_max]`; if clamping changed it, print
  a warning and use the clamped value (manual entry is direct user intent, so we *clamp to the
  nearest valid value* rather than *skip* the joint — a deliberate divergence from named mode,
  which skips).
- **Non-numeric** → re-prompt that same joint (does not abort).

Every joint ends with a value (typed or kept), so manual `targets` always covers all five
motors — which is what makes the save step (an `ArmPose` requires all five) always valid.

### 3.4 Save step (manual mode only)

After the arm is holding the manual pose, before the "Press Enter to release" prompt:

```
Save this pose? name (blank = don't save): home
saved 'home' -> data/arm_config.yaml
```

If a name is given, upsert into `data/arm_config.yaml` → `poses` via `load_arm_poses` +
`save_arm_poses` (the exact path `jog._save_pose` uses). Typing `home` overwrites the home
entry. Named mode does **not** offer save (re-saving an existing named pose is pointless —
YAGNI).

### 3.5 Pure helper (the only isolated unit)

New module `src/arm101_hand/robots/arm_manual.py`:

```python
from typing import NamedTuple
from arm101_hand.robots.calibration_summary import clamp_degrees


class ManualTarget(NamedTuple):
    value: float          # degrees to drive to
    kept: bool            # True if input was blank (kept current)
    clamped: bool         # True if a typed value was clamped into range


def resolve_target(current: float, range_min: float, range_max: float, raw: str) -> ManualTarget:
    """Resolve one joint's manual input.

    Blank -> keep ``current``. Otherwise parse a float and clamp to
    ``[range_min, range_max]``. Raises ``ValueError`` on non-numeric input (the
    caller re-prompts).
    """
    text = raw.strip()
    if not text:
        return ManualTarget(current, kept=True, clamped=False)
    parsed = float(text)  # ValueError -> caller re-prompts
    value = clamp_degrees(parsed, range_min, range_max)
    return ManualTarget(value, kept=False, clamped=value != parsed)
```

Reusing `clamp_degrees` keeps the clamp identical to the rest of the scripts (DRY / mirror
existing shapes). The script's prompt loop is the thin I/O shell around this; the helper holds
the only logic worth testing without hardware.

## 4. Files

| File | Action |
|---|---|
| `scripts/calibration/so_arm101/set_pose.py` | Modify: add `--manual` mode + the four extracted functions; update docstring/usage. |
| `src/arm101_hand/robots/arm_manual.py` | Create: `ManualTarget` + `resolve_target`. |
| `src/arm101_hand/robots/__init__.py` | Modify: export `ManualTarget`, `resolve_target` if the package re-exports its symbols (match existing convention; if it doesn't re-export, skip). |
| `tests/unit/test_arm_manual.py` | Create: unit tests for `resolve_target`. |

## 5. Error handling

- Non-numeric at a joint prompt → re-prompt that joint (caught `ValueError`).
- `Ctrl-C` / EOF at any prompt → routed to the same safe `park_home_and_release` via the
  existing `finally` (IL-4: torque released, bus closed).
- Out-of-range typed value → clamped to nearest valid with a printed warning (never silently
  skipped in manual mode).
- Connection failure → same `ERROR: could not open <port>` path as today.

IL-3: motors 1–5. IL-4: single bus owner, torque released in `finally`. IL-5: pushes
calibration to motors, never writes the JSON.

## 6. Testing

`tests/unit/test_arm_manual.py` (host, no bus):
- blank input → `kept=True`, value == current.
- valid in-range number → `kept=False`, `clamped=False`, value == typed.
- above max → `clamped=True`, value == range_max.
- below min → `clamped=True`, value == range_min.
- non-numeric (`"abc"`, `"--"`) → raises `ValueError`.

Manual hardware check (documented, not automated): `set_pose.py --manual`, enter a couple of
joints, confirm it drives there, save as a throwaway name, confirm it appears in
`arm_config.yaml`, then `set_pose.py <that name>` drives back.

## 7. Out of scope (YAGNI)

- No CLI `joint=deg` arguments (interactive only, per the chosen interface).
- No save in named mode.
- No new standalone script.
- No change to `jog.py` or the pose schema.

## 8. Verification

- `uv run pytest -m 'not hardware'` — all pass incl. new `test_arm_manual.py`.
- `uv run ruff check` / `format --check` clean on the touched files.
- Manual hardware check from §6 (operator-run).

## 9. Iron Laws touched

- **IL-3 / IL-4 / IL-5** — preserved exactly (same connect/calibration/park machinery).
- **IL-7** — single tool for "set a pose"; the manual logic has one canonical home
  (`arm_manual.resolve_target`), referenced by the script.
