# Design — `capture_pose.py` (hand-pose capture by demonstration)

**Date:** 2026-06-04
**Scope:** Arm-only (SO-ARM101). A new dedicated script to capture the arm's current pose by
hand and save it as a named pose. Supersedes the rejected numeric-entry design
(`2026-06-04-set-pose-manual-mode-*.md`, deleted).

---

## 1. Problem

The operator wants to define a pose (e.g. a new `home`) by physically moving the arm to it
and recording the motor degrees — not by typing numbers and not by incremental jogging.
`jog.py` can do this (`t` torque-off, hand-pose, `s` save) but it's a heavier keyboard-jog
tool; the operator wants a minimal, single-purpose "limp → pose by hand → save" tool.

## 2. Decision

A new standalone script `scripts/calibration/so_arm101/capture_pose.py`. It is **not** folded
into `set_pose.py`: capture shares only *connect* and *save* with `set_pose`, and has no
drive/clamp/hold-to-target logic — mixing "record a pose" into the "drive to a pose" tool
would blur responsibilities. Dedicated script is the KISS choice
(`docs/conventions/07-kiss-simplicity.md`: one responsibility per unit).

## 3. Design

### 3.1 Behavior (state machine)

1. Load `app_config.yaml`, build follower (DEGREES mode), connect.
2. Push on-file calibration to the motors so present positions read back in the recorded
   degree frame (IL-5: writes the MOTORS, never the JSON). Set a gentle `Goal_Velocity`.
3. **Capture loop:**
   1. **Torque OFF** — the arm goes limp so the operator can move it by hand.
   2. Prompt: `Move the arm to the pose by hand, then press Enter to capture (Ctrl+C to quit)...`
   3. On **Enter** → read present degrees of all 5 motors (`get_observation()`). Then hold the
      captured pose without a jump: write `Goal_Position = present`, **then torque ON**
      (mirrors `jog`'s no-jump hold). Echo the captured degrees.
   4. **Save prompt:** `Save as (name, blank = don't save): ` → if named, upsert into
      `data/arm_config.yaml` → `poses` via `load_arm_poses` + `save_arm_poses` (the path
      `jog`/`set_pose` use). Typing an existing name (e.g. `home`) overwrites it silently.
   5. **Menu:** `[h] return home & capture another   [q] return home & quit` (typed line,
      re-prompts until `h` or `q`).
      - Both first call `park_home_and_release(follower, home, vel)` — drive to the configured
        home, *then* release torque, so the arm never drops from the held pose.
      - `h` → loop back to step 3.1 (limp again for the next capture).
      - `q` → break out and exit.

### 3.2 Exit / safety paths

- **Ctrl-C / EOF at the capture prompt** (torque already off): break the loop; the `finally`
  just disconnects (arm stays limp where the operator is holding it — nothing to park).
- **Ctrl-C / EOF at the save or menu prompt:** treated as cancel of that step (`q`/skip);
  the held pose is parked home and released before exit.
- **`finally`:** if connected — if torque is on, `park_home_and_release`; else `disable_torque`;
  then `disconnect`. (IL-4: single bus owner, torque always released; bus closed.)
- IL-3: motors 1–5.

### 3.3 Functions (all in `capture_pose.py`)

- `_present_degrees(follower) -> dict[str, float]` — `{joint: degrees}` from `get_observation()`
  (same helper shape as `set_pose`/`jog`).
- `_maybe_save(pose) -> None` — name prompt + upsert into `arm_config.yaml` `poses`
  (blank/EOF = skip). `pose` always has all 5 joints, so `ArmPose(**pose)` is always valid.
- `_menu() -> str` — re-prompt until `h` or `q`; EOF/Ctrl-C returns `q`.
- `main() -> int` — the connect + capture-loop + `finally` described above.

### 3.4 Reuse (no duplication)

- Connect / velocity / home / safe park: `_common.build_follower`, `gentle_velocity`,
  `load_arm_app_config`, `load_home_degrees`, `park_home_and_release`, `ARM_CONFIG_PATH`.
- Pose persistence: `arm101_hand.config` `ArmPose`, `ArmPoseConfig`, `load_arm_poses`,
  `save_arm_poses`.
- Joint canon: `arm101_hand.robots.calibration_summary.ARM_JOINTS`.
- No calibration *clamp* is used (we record present positions; nothing to clamp), so
  `load_arm_calibration` / `clamp_degrees` are **not** imported.

## 4. Files

| File | Action |
|---|---|
| `scripts/calibration/so_arm101/capture_pose.py` | Create. |
| `scripts/calibration/so_arm101/README.md` | Modify: add a `capture_pose.py` row to the §6 helper table + an example. |
| `CLAUDE.md` | Modify: add a `capture_pose.py` line to the §4 workflow block. |
| `docs/superpowers/specs/2026-06-04-set-pose-manual-mode-design.md` | Delete (rejected approach). |
| `docs/superpowers/plans/2026-06-04-set-pose-manual-mode.md` | Delete (rejected approach). |

## 5. Testing

No new pure logic worth isolating — building an `ArmPose` from the present dict is trivial and
the save path is already covered by `tests/unit/test_arm_poses_save.py`. Adding a unit module
here would be over-building (YAGNI). Verification:

- `uv run python -c "import ast; ast.parse(open('scripts/calibration/so_arm101/capture_pose.py').read())"` → parses.
- Imports resolve (helpers + config symbols exist).
- `uv run ruff check` / `format --check` clean on the new file.
- `uv run pytest -m 'not hardware'` — no regressions.
- **Operator hardware check** (interactive, COM20): run it, hand-pose the arm, Enter, confirm
  it holds; save as a throwaway name; confirm it lands in `arm_config.yaml`; press `h`, confirm
  it homes and re-prompts; capture again; press `q`, confirm it homes, releases, exits. Then
  `set_pose.py <name>` drives back to a captured pose.

## 6. Out of scope (YAGNI)

- No numeric angle entry (rejected).
- No single-keypress (`msvcrt`) input — `h`/`q` are typed lines.
- No changes to `set_pose.py`, `jog.py`, or the pose schema.
- No re-read/confirm step beyond the echo before the save prompt.

## 7. Iron Laws touched

- **IL-3 / IL-4 / IL-5** — preserved: motors 1–5; single bus owner with torque released in
  `finally`; calibration pushed to motors, JSON never written.
- **IL-7** — capture logic has one canonical home (`capture_pose.py`); pose persistence reuses
  the existing single `poses` store.
