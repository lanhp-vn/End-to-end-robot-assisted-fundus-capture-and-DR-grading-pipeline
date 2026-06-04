# SO-ARM101 Keyboard Jog Tool — Design

**Date:** 2026-06-03
**Status:** Approved (design), pending implementation plan
**Scope:** Add an interactive keyboard-jog script for the SO-ARM101 follower under
`scripts/calibration/so_arm101/`, plus a small pure state machine, a separate saved-pose
file, and a `set_pose.py` consistency update.

---

## 1. Problem & goal

The fixed-pose (`set_pose.py`) and endpoint-sweep (`sweep.py`) tools assume the arm's
canned poses are appropriate. But the arm's physical setup on the table varies (base
orientation, clamping, surrounding obstacles), so the operator needs to **manually drive
each of the 5 motors from the keyboard** to position the arm for the current setup — and
optionally capture good positions as reusable named poses.

**Goal:** an interactive `jog.py` that lets the operator select a joint and nudge it in
degrees (clamped to its calibrated range), hand-pose the arm with a torque toggle, snap a
joint home, and save the current pose to a file `set_pose.py` can later drive back to.

This mirrors the AmazingHand `RangeCalib` pattern already in the repo: a `msvcrt`
raw-key loop over a **pure, unit-tested state machine**, with all bus I/O in the script.

## 2. Non-goals (YAGNI)

- **No GUI work.** The GUI is being redesigned later; these stay pure CLI tools and make
  no attempt at GUI parity. (Per explicit user direction.)
- **No teleop/policy.** Single-operator manual jog only.
- **No calibration writing.** `jog.py` never writes `so101_follower.json` (IL-5); it reads
  it for the per-joint clamp range.
- **No multi-joint simultaneous jog.** One active joint at a time (the others hold).

## 3. Architecture

```
src/arm101_hand/robots/
└── arm_jog.py              # NEW — pure jog state machine (no bus); unit-tested

src/arm101_hand/config/
└── arm_poses.py            # MODIFY — add save_arm_poses(path, config); unit-tested

scripts/calibration/so_arm101/
├── jog.py                  # NEW — thin msvcrt I/O loop + bus; composes arm_jog + _common
├── _common.py              # MODIFY — add ARM_JOG_POSES_PATH constant
├── set_pose.py             # MODIFY — resolve poses from quick_poses + jog-poses file
└── README.md               # MODIFY — §6 jog.py row + control table

data/
└── arm_jog_poses.yaml      # NEW (created on first save) — {schema_version, poses: {...}}

tests/unit/
├── test_arm_jog.py             # NEW — state-machine transitions
└── test_arm_poses_save.py      # NEW — save_arm_poses round-trip
```

### 3.1 Layering rationale
- **Pure logic** (`arm_jog.py`) holds all decision logic — cursor math, clamping, active-
  joint selection, step changes, key→action mapping — so it is unit-testable with no bus.
- **I/O shell** (`jog.py`) only does `msvcrt` reads, bus calls, and printing.
- **Persistence** (`save_arm_poses`) lives in the config layer next to `load_arm_poses`,
  validated by the existing `ArmPoseConfig` schema.

## 4. The pure state machine — `src/arm101_hand/robots/arm_jog.py`

```python
ARM_JOINTS  # imported from calibration_summary (the 5 names, in order)

JOG_STEP_MIN = 1.0
JOG_STEP_MAX = 15.0
JOG_STEP_DEFAULT = 5.0

@dataclass
class ArmJogState:
    cursors: dict[str, float]   # per-joint target in degrees (from calibrated mid)
    active: str                 # currently selected joint name
    step: float                 # jog increment in degrees
    torque_on: bool

# Actions returned by key_to_action:
#   "select:<joint>" | "jog_up" | "jog_down" | "step_down" | "step_up"
#   "home_active" | "toggle_torque" | "save" | "quit" | None
def key_to_action(key: str) -> str | None: ...

# Apply an action. `bounds` maps joint -> (lo_deg, hi_deg) from calibration_summary.
# Returns (new_state, effect) where effect tells the I/O layer what to do:
#   "move"          -> command state.cursors[active] to the bus
#   "toggle_torque" -> flip torque (I/O does the bus work, then calls resync)
#   "save"          -> persist current pose
#   "quit"          -> exit
#   None            -> redraw only (e.g. step change, no-op)
def apply_action(
    state: ArmJogState, action: str | None, bounds: dict[str, tuple[float, float]]
) -> tuple[ArmJogState, str | None]: ...

def format_status(state: ArmJogState, loads: dict[str, int]) -> str: ...
```

Behavioural rules (all pure, all unit-tested) — each lists the `effect` returned:
- `jog_up`/`jog_down`: adjust `cursors[active]` by `±step`, **clamped** to `bounds[active]`;
  effect `"move"`.
- `home_active`: set `cursors[active] = 0.0` (calibrated mid); effect `"move"`.
- `step_down`/`step_up`: scale `step` within `[JOG_STEP_MIN, JOG_STEP_MAX]`; effect `None`
  (redraw only).
- `select:<joint>`: switch `active` (cursor unchanged); effect `None` (redraw only — nothing
  moves on a bare selection).
- `toggle_torque`: flip `state.torque_on`; effect `"toggle_torque"`.
- `save`: effect `"save"`. `quit`: effect `"quit"`.
- Jog/home while `torque_on is False`: no cursor change, effect `None` (the I/O layer prints
  an "enable torque first (t)" hint).

## 5. The I/O script — `scripts/calibration/so_arm101/jog.py`

`use_degrees=True` (DEGREES mode), so a cursor of `0.0` is the calibrated midpoint and
clamps come from `calibration_summary.degree_bounds`.

### 5.1 Entry sequence (no-jump start)
1. `build_follower(cfg, use_degrees=True)`; clean `ConnectionError/OSError` handling.
2. `connect(calibrate=False)`.
3. `bus.write_calibration(follower.calibration)` — align motor frame to file (writes
   motors, not JSON — IL-5).
4. Read present positions (degrees) via `get_observation()` → initial `cursors`.
5. `bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_JOINTS, gentle_velocity(cfg)))`.
6. `send_action(cursors)` (Goal_Position = present) **then** `enable_torque()` →
   holds in place, no jump. `state.torque_on = True`.

### 5.2 Loop
Read a key (`msvcrt`, arrows normalized like `RangeCalib.read_key`), map via
`key_to_action`, apply via `apply_action`, then act on the effect:
- `"move"` → `send_action({active.pos: cursors[active]})` (only the active joint moves).
- `"toggle_torque"` → if now off: `disable_torque()` (arm limp, hand-pose it). If now on:
  re-read present → `cursors`, `sync_write Goal_Velocity`, `send_action(cursors)`,
  `enable_torque()`.
- `"save"` → temporarily leave raw-key mode, `input()` a pose name, read **live** present
  degrees for all joints, upsert into `arm_jog_poses.yaml`, confirm.
- `"quit"` → break.
After each move, read per-joint `Present_Load` and print `format_status`; warn on high load.

### 5.3 Exit (finally)
- If `is_connected`:
  - If `torque_on`: `park_home_and_release(follower, vel)` (return all joints to 0, then
    torque off) — the shared convention from `sweep`/`set_pose`.
  - Else (torque toggled off → arm is hand-posed): just `disconnect()` in place; **do not**
    re-energize and drive home.
  - `disconnect()`; print "Bus closed."

### 5.4 Controls
| Key | Action |
|-----|--------|
| `1`–`5` | select active joint (shoulder_pan … wrist_roll) |
| `↑` / `↓` | jog active joint **+ / − step** (deg), clamped to calibrated range |
| `[` / `]` | shrink / grow step (1–15°, starts 5°) |
| `h` | home active joint to 0 (calibrated mid) |
| `t` | toggle torque (off = hand-pose; on = resync + hold) |
| `s` | save current pose to `arm_jog_poses.yaml` (prompts for a name) |
| `q` / `Ctrl+C` | exit — return home then release (or disconnect in place if torque off) |

## 6. Persistence — `save_arm_poses` + `arm_jog_poses.yaml`

`data/arm_jog_poses.yaml` is a fresh file these scripts own (no comment-preservation
concern — that's why a separate file was chosen over rewriting `arm_config.yaml`):

```yaml
schema_version: 1
poses:
  <name>:
    shoulder_pan: 12.0
    shoulder_lift: -30.0
    elbow_flex: 45.0
    wrist_flex: 0.0
    wrist_roll: 0.0
```

Add to `src/arm101_hand/config/arm_poses.py`:

```python
def save_arm_poses(path: Path, config: ArmPoseConfig) -> None:
    """Write an ArmPoseConfig to YAML atomically (tmp file + os.replace).

    Used for the jog-pose file (data/arm_jog_poses.yaml). model_dump + safe_dump;
    sort_keys=False. No comment preservation (fresh, code-owned file).
    """
```

- `jog.py` save flow: `load_arm_poses(ARM_JOG_POSES_PATH)` if the file exists else an empty
  `ArmPoseConfig()`; set `config.poses[name] = ArmPose(**live_degrees)`; `save_arm_poses`.
- Validated by the existing `ArmPose`/`ArmPoseConfig` pydantic models (all 5 joints
  required, degrees).

## 7. `set_pose.py` update

Resolve a pose name from **both** sources so jog-saved poses are drivable:
- `arm_config.yaml` `quick_poses` (existing: zero/home/rest), then
- `arm_jog_poses.yaml` `poses` (if the file exists).

Merge into one name→ArmPose dict (quick_poses first; jog poses add to it). The prompt's
available-names list reflects the merged set. Everything else in `set_pose.py` is
unchanged (clamp, drive, hold, return-home-then-release).

## 8. Safety — Iron Laws

- **IL-3:** motors 1–5 only; ID 6 never addressed.
- **IL-4:** single bus owner; torque released on every exit (or left intentionally off in
  hand-pose mode); clean COM-port error.
- **IL-5:** `so101_follower.json` is read-only here. `arm_jog_poses.yaml` is a separate
  code-owned file; `arm_config.yaml` is never written.
- **No-jump start** (goal=present before torque-on) and gentle `Goal_Velocity` throughout.
- Jog clamped to the calibrated range so a single keypress can't command past a stop.

## 9. Testing & verification

- **Unit (`-m 'not hardware'`):**
  - `tests/unit/test_arm_jog.py` — `key_to_action` mapping; `apply_action` for jog±/clamp
    at bounds/select/step min-max/home/torque-toggle/jog-blocked-when-torque-off;
    `format_status`.
  - `tests/unit/test_arm_poses_save.py` — `save_arm_poses` → `load_arm_poses` round-trip
    (including upsert into an existing file), atomic-write behaviour.
- **Manual hardware** (operator, documented): jog each joint, clamp stops at the calibrated
  ends, `t` hand-pose then re-hold, `h` homes the active joint, `s` saves a pose,
  `set_pose.py <saved>` drives back to it, `q` returns home and releases.
- `ruff` + `mypy src` + `pytest -m 'not hardware'` green (mypy covers `arm_jog.py` and the
  `save_arm_poses` addition in `src/`; scripts are ruff-only).

## 10. Documentation

- `scripts/calibration/so_arm101/README.md` §6: add a `jog.py` row to the table, the
  control table, and a note that saved poses live in `data/arm_jog_poses.yaml` and are
  drivable by `set_pose.py`.
- `doc_update` afterward for CLAUDE.md workflows + root README.

## 11. Decisions locked in

1. **Separate saved-pose file** `data/arm_jog_poses.yaml` (not a rewrite of
   `arm_config.yaml`) — protects that file's header comments.
2. **Return-home-then-release on exit** (reusing `_common.park_home_and_release`), except
   when torque was toggled off (then disconnect in place).
3. **Degrees-from-current, clamped** to the calibrated range; no-jump start.
4. **Extras included:** torque toggle (hand-pose), per-joint home key, save pose.
5. **Ignore the GUI** — CLI-only; no GUI-parity constraints.

## 12. Addendum (2026-06-03) — configurable default-home pose

Supersedes the original "home = all joints at calibrated mid (0)" assumption in §4/§5.
Released-torque-at-mid put the arm sticking out, from which it drops to its folded rest
anyway. Instead, **"home" is now a configurable default-home pose** = the arm's natural
folded rest, captured from hardware.

- **Storage:** `data/arm_config.yaml` → `quick_poses.home` (degrees), hand-edited to the
  captured values (comments preserved). Editable; drivable as `set_pose.py home`.
- **`_common.load_home_degrees()`** reads `quick_poses.home` (falls back to all-zeros).
- **`park_home_and_release(follower, home, vel)`** now takes the home pose (degrees),
  converts each joint to raw encoder steps via `follower.calibration`, and writes raw
  `Goal_Position` (`normalize=False`) — norm-agnostic, so the shared helper works for
  `sweep` (RANGE_M100_100) and `set_pose`/`jog` (DEGREES) alike.
- **All three motion scripts** (sweep, set_pose, jog) load the home and pass it to
  `park_home_and_release`, returning there before torque-off.
- **`ArmJogState` gains a `home` dict**; `initial_state(cursors, home)`; jog's `h` key
  (`home_active`) snaps the active joint to `home[active]` (clamped to bounds) instead of 0.
- Captured initial values: `{shoulder_pan: 2.1, shoulder_lift: -104.8, elbow_flex: 90.9,
  wrist_flex: -102.9, wrist_roll: 0.0}` (degrees from calibrated mid; wrist_flex clamped
  from -103.6 into range).
