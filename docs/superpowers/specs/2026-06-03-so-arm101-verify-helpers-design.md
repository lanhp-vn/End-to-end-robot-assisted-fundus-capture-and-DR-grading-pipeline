# SO-ARM101 Verify/Audit Helper Scripts — Design

**Date:** 2026-06-03
**Status:** Approved (design), pending implementation plan
**Scope:** Add verify/audit helper scripts for the SO-ARM101 follower under `scripts/calibration/so_arm101/`.

---

## 1. Problem & goal

The arm's actual calibration is already handled by lerobot: `arm101-calibrate-follower`
wraps lerobot's interactive `calibrate()`, which captures homing offsets + per-joint
ranges and wrote the populated `scripts/calibration/so_arm101/so101_follower.json`
(all 5 joints have real `homing_offset` / `range_min` / `range_max`).

What's missing is the *verify/audit* layer — the arm analog of the AmazingHand's
`FingerTest` / `SetPose`. There is currently **no arm-motion code anywhere in `src/`**
(a grep for `Goal_Position` / `send_action` / `Profile_Velocity` in `src/arm101_hand`
returns nothing; the GUI drives only the hand, and the `safety.safe_park` arm path in
`app_config.yaml` is referenced but unimplemented). These scripts will therefore be the
**first arm-motion code in the repo**.

**Goal:** add four standalone scripts that let an operator sanity-check the 5 arm
servos and the calibration against the real mechanism, keeping lerobot's `calibrate()`
as the single source of truth (these scripts never write `so101_follower.json`, IL-5).

## 2. Non-goals (YAGNI)

- **No re-implementation of calibration math.** lerobot's `calibrate()` remains canonical.
- **No teleop / policy / dataset code.**
- **No GUI integration.** These are CLI scripts; GUI arm support is separate future work.
- **No new console-script entries.** Run via `uv run python scripts/calibration/so_arm101/<name>.py`,
  matching the AmazingHand suite's self-contained style and the user's stated location.

## 3. Architecture

Four standalone scripts in `scripts/calibration/so_arm101/`, plus two pieces of shared
code:

```
scripts/calibration/so_arm101/
├── scan.py          # read-only bus health check (raw bus)
├── sweep.py         # per-joint range-verification sweep (RANGE_M100_100)
├── set_pose.py      # drive to a named pose and hold (DEGREES)
├── show_calib.py    # pretty-print + optional live compare (DEGREES, read-only)
├── _common.py       # shared script setup: load arm.port, build robot config, print helpers
├── find_port.py     # (existing) COM-port discovery
├── so101_follower.json  # (existing) canonical calibration — READ ONLY to these scripts
└── README.md        # (existing) extended with a "Verify/audit helpers" section

src/arm101_hand/robots/
└── calibration_summary.py   # pure, testable calibration math (no bus)

tests/unit/
└── test_arm_calibration_summary.py   # unit tests for the pure math
```

### 3.1 Source-of-truth inputs (reused, not reinvented — IL-7)

- **Arm port:** `data/app_config.yaml` → `arm.port` (via `arm101_hand.config.load_app_config`).
- **Velocity:** `data/app_config.yaml` → `arm.velocity_profile_default` (runtime) and
  `safety.safe_park.park_velocity_arm` (gentle). Motion scripts use the gentle value.
- **Named poses:** `data/arm_config.yaml` → `quick_poses` (`zero` / `home` / `rest`),
  loaded via `arm101_hand.config.arm_poses.load_arm_poses`. `set_pose.py` invents no poses.
- **Calibration:** `scripts/calibration/so_arm101/so101_follower.json`, auto-loaded by
  `SO101FollowerNoGripper` because the Robot base sets
  `calibration_fpath = calibration_dir / f"{id}.json"` and the subclass defaults
  `calibration_dir` in-tree. Scripts construct the robot with `id="so101_follower"`.

## 4. The key technical decision — normalization mode per script

The calibration JSON stores **raw 12-bit encoder steps**, not degrees. Interpretation
depends on `MotorNormMode` (see `references/lerobot/.../motors_bus.py` `_normalize` /
`_unnormalize`):

- **`RANGE_M100_100`**: `range_min` ↔ −100, `range_max` ↔ +100, with built-in clamp to
  [−100, +100]. Driving to a calibrated endpoint is just commanding ±100 — inherently
  bounded and safe.
- **`DEGREES`**: value is degrees relative to the range *midpoint*
  (`(val − mid)·360/(res−1)`, `mid = (min+max)/2`); **no range clamp** in `_unnormalize`.
  Matches `arm_config.yaml` and the GUI convention.

**Decision (approved):** split by purpose.

| Script        | `use_degrees` | Norm mode         | Rationale                                         |
| ------------- | ------------- | ----------------- | ------------------------------------------------- |
| `sweep.py`    | `False`       | `RANGE_M100_100`  | endpoints are exactly ±100, clamped & safe        |
| `set_pose.py` | `True`        | `DEGREES`         | poses in `arm_config.yaml` are degrees            |
| `show_calib.py` | `True`      | `DEGREES`         | live display in degrees vs calibrated mid         |
| `scan.py`     | n/a (raw bus) | n/a               | reads raw registers; no normalization needed      |

Each script constructs its own robot/bus, so each selects its own mode independently.

## 5. The four scripts

### 5.1 `scan.py` — read-only bus health check

- Uses a **raw `FeetechMotorsBus`** (motors 1–5 dict), **not** the robot wrapper, so a
  dead/missing motor is *reported* rather than crashing `configure()`. (`robot.connect()`
  writes config registers to every motor and would hard-fail on a non-responding one.)
- Pings each ID 1–5; for responders reads `Present_Position`, `Present_Load`,
  `Present_Voltage`, `Present_Temperature` (all present in the Feetech table).
- **Torque stays OFF** the entire time. Read-only.
- Prints a per-motor table; flags any missing IDs and any reading outside the
  `safety` thresholds in `app_config.yaml` (temp/voltage) as a soft warning.
- Exit code non-zero if any of the 5 motors did not respond (useful as a pre-check).

### 5.2 `sweep.py` — per-joint range-verification sweep

- `SO101FollowerNoGripper`, `use_degrees=False` → `RANGE_M100_100`.
- CLI: a joint name (`shoulder_pan`…`wrist_roll`) or `all`; optional `--margin` (default
  90, clamped to ≤ 99) so the sweep stops just shy of the mechanical stop rather than
  driving into it.
- `connect(calibrate=False)` (skips lerobot's interactive calibrate prompt; calibration
  dict is already loaded for normalization).
- **Command ordering (prevents snap):**
  1. Set a gentle `Profile_Velocity` (`park_velocity_arm`) on all addressed motors.
  2. Enable torque.
  3. Move to **mid-range (0 in M100_100) first** — the arm's current pose is arbitrary
     and possibly extended, so the first excursion goes to neutral, not to an endpoint.
  4. Sweep `0 → +margin → −margin → 0` for the selected joint(s).
- Monitors `Present_Load` between steps; on high load prints a warning and backs off one
  step (mirrors the hand's `RangeCalib` stall guard).
- **`finally`: disable torque** and disconnect. Torque is never left on after exit.
- Arm analog of `AmazingHand_FingerTest.py`.

### 5.3 `set_pose.py` — drive to a named pose and hold

- `SO101FollowerNoGripper`, `use_degrees=True` → `DEGREES`.
- CLI: pose name from `arm_config.yaml` `quick_poses` (`zero` / `home` / `rest`), or
  prompt if omitted. Loaded via `load_arm_poses` — **no invented poses** (IL-7).
- Clamps each joint's degree target to the calibrated degree range (derived from the
  calibration via `calibration_summary`); a value outside range is **warned and skipped**
  for that joint, matching the behavior `arm_config.yaml` already documents for the GUI.
- Gentle `Profile_Velocity`, enable torque, command the pose, hold under torque until
  the operator presses Enter, then **release torque in `finally`**.
- Arm analog of `AmazingHand_SetPose.py`.

### 5.4 `show_calib.py` — calibration dump + optional live compare

- Read-only. Default mode: parse `so101_follower.json` via `calibration_summary` and
  print a per-joint table: `id`, `homing_offset`, `range_min`, `range_max`, computed
  **degree span** = `(range_max − range_min)·360/(res−1)`, and **midpoint** (steps).
- `--live`: `SO101FollowerNoGripper`, `use_degrees=True`, `connect(calibrate=False)`
  (torque off after `configure()` since it runs inside `torque_disabled()`), read
  `Present_Position` per joint and show current degrees vs the calibrated mid. **No
  motion, no torque enable.**

## 6. Shared / testable code

### 6.1 `src/arm101_hand/robots/calibration_summary.py` (pure)

- `load_arm_calibration(path) -> dict[str, JointCalib]` — parse the JSON
  (`yaml.safe_load`/`json`), validate the 5 expected joints and required keys.
- `degree_span(range_min, range_max, resolution=4096) -> float` — `(max−min)·360/(res−1)`.
- `midpoint_steps(range_min, range_max) -> float`.
- Pure, no serial. Unit-tested in `tests/unit/test_arm_calibration_summary.py`
  (known JSON in → known spans/midpoints out; rejects malformed/missing joints).

### 6.2 `scripts/calibration/so_arm101/_common.py` (script glue)

- `arm_port() -> str` — load `app_config.yaml`, return `arm.port` (with a clear error if
  the file is missing/invalid).
- `build_follower(use_degrees: bool) -> SO101FollowerNoGripper` — construct the config
  with the resolved port + `id="so101_follower"` + the requested norm mode.
- `gentle_velocity() -> int` — `safety.safe_park.park_velocity_arm` from `app_config.yaml`.
- Shared table/print formatting used by `scan.py` and `show_calib.py`.
- Not pure (touches config/robot construction), so not unit-tested; kept thin so the
  testable logic lives in `calibration_summary`.

## 7. Safety — Iron Laws mapping

- **IL-3 (motor ID canon):** all scripts address motors 1–5 only; ID 6 is never built
  into any motor dict.
- **IL-4 (COM-port discipline):** each script opens the bus, does its work, and releases
  it; motion scripts disable torque + disconnect in `finally`. README reiterates
  single-owner (close GUI / FD.exe / stale sessions first).
- **IL-5 (calibration in version control):** these scripts **read** `so101_follower.json`
  and never write it. The canonical calibration is produced only by
  `arm101-calibrate-follower`.
- **Torque hygiene:** read-only scripts (`scan`, `show_calib`) never enable torque;
  motion scripts (`sweep`, `set_pose`) use gentle velocity, route through mid-range
  first, monitor `Present_Load`, and always release torque on exit.

## 8. Testing & verification

- **Unit (`-m 'not hardware'`):** `test_arm_calibration_summary.py` covers `degree_span`,
  `midpoint_steps`, and `load_arm_calibration` (valid + malformed inputs). No bus.
- **Manual hardware check** (documented in README, operator-run): `scan` reports all 5
  motors; `show_calib` matches the JSON; `sweep` reaches endpoints with no buzz/stall;
  `set_pose home/rest/zero` parks cleanly.
- `uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest -m 'not hardware'`
  all green before completion.

## 9. Documentation

- Extend `scripts/calibration/so_arm101/README.md` with a **"Verify/audit helpers"**
  section: purpose, run commands, the norm-mode split rationale, safety notes, and a
  one-line summary — paralleling the AmazingHand README's per-script documentation.
- Run `doc_update` afterward to refresh the top-level `CLAUDE.md` workflows block and
  `README.md`.

## 10. Decisions locked in

1. **Norm-mode split:** `RANGE_M100_100` for `sweep.py`; `DEGREES` for `set_pose.py` /
   `show_calib.py`. (Approved.)
2. **Shared-helper split:** `_common.py` for script setup glue; `src/.../calibration_summary.py`
   for testable calibration math. (Approved.)
