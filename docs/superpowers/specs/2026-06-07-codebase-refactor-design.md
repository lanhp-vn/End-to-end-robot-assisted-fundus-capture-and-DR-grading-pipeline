# Codebase Refactor — GUI Removal, Dual-Device Diagnostics, Hand Rename

**Date:** 2026-06-07
**Status:** Approved (design)
**Scope:** Three independent workstreams plus a final documentation pass.

---

## 1. Motivation

The unified PySide6 GUI is unused: the jog scripts (`so_arm101/jog.py`,
`amazing_hand/jog.py`) already cover interactive control and pose capture for
both devices. Per KISS/YAGNI (`docs/conventions/07-kiss-simplicity.md`), the GUI
layer and its `PySide6` dependency are removed.

Two adjacent cleanups ride along:

1. The read-only diagnostics (`scan`, `show_calib`, `find_port`) currently live
   under `so_arm101/` and only work for the arm. They become **dual-device**,
   selected by `--device arm|hand`, and move to a device-neutral home.
2. The AmazingHand calibration scripts have verbose, inconsistent names
   (`AmazingHand_MiddlePos_FingerCalib.py`) and sit in a folder named
   `AmazingHand/` while the arm uses `so_arm101/`. They are renamed to short
   snake_case in a folder named after the device model.

All three are mechanical refactors: **no behavioral change to existing
device control**, only relocation, renaming, deletion, and new hand branches in
the two dual-device diagnostics.

---

## 2. Non-Goals

- No new device-control features. Jog/calibration behavior is unchanged.
- No `app_config.yaml` schema change (see §3.4 — orphaned fields are left in
  place because `safe_park.park_velocity_arm` is still used by arm scripts).
- No teleop / policy / dataset work.
- `references/**` is never modified (IL-2). `references/AmazingHandControl/` is a
  vendored submodule, distinct from our `scripts/calibration/AmazingHand/`.
- Historical records under `docs/superpowers/**` and
  `docs/plans/01-unified-gui-spec.md` are **not** rewritten (see §6).

---

## 3. Workstream 1 — Remove the GUI

### 3.1 Delete

| Path | Reason |
|---|---|
| `src/arm101_hand/gui/**` | Entire GUI layer (app, main_window, hand_panel, widgets, safety, sequence_player, pose_manager). |
| `src/arm101_hand/scripts/unified_gui.py` | GUI console-script entry. |
| `src/arm101_hand/hand/controller.py` | Qt `QObject` bus driver. Jog/calib scripts drive `rustypot.Scs0009PyController` directly; nothing in `scripts/` imports it. `hand/__init__.py` does not re-export it. |
| `tests/unit/test_hand_controller_mock.py` | Tests the removed controller. |
| `tests/unit/test_sequence_parser.py` | Tests the removed `sequence_player`. |
| `tests/unit/test_safe_park.py` | Tests the removed `safety.SafePark`. |
| `tests/unit/test_safety_thresholds.py` | Tests the removed `safety` thresholds. |

### 3.2 `pyproject.toml`

- Remove the `PySide6>=6.6,<6.9` dependency.
- Remove the `arm101-gui = "arm101_hand.scripts.unified_gui:main"` console script.
- Regenerate `uv.lock` (`uv lock` / `uv sync`).

### 3.3 `conftest.py`

No change required. `tests/conftest.py` imports only `pytest` — it registers the
`hardware` marker and `--port` option, with no Qt/GUI coupling. Deleting the GUI
tests does not affect collection of the surviving suite.

### 3.4 `app_config.py` — deliberately untouched

After removal these fields have no consumer: `window.*`,
`safe_park.park_velocity_hand`, `safe_park.arm_pose`/`hand_pose`,
`hand.default_speed`, and the GUI-only `safety` poll fields. They are **left in
place**:

- `safe_park.park_velocity_arm` is still read by `so_arm101/_common.gentle_velocity`.
- `safety.temp_warn_c` / `voltage_warn_pct` are still read by `scan.py` (both devices).
- Pruning the schema risks breaking arm scripts and is out of scope.

Orphaned fields are recorded as a future cleanup, not acted on here.

### 3.5 `tests/hardware/test_hand_real.py` — inspect during implementation

This hardware test references both the (removed) controller/GUI and the calib
YAML name. During implementation:

- If it imports `HandController`, re-point it to drive `Scs0009PyController`
  directly (mirroring `amazing_hand/jog.py`) **or** trim the controller-specific
  cases.
- Update the YAML reference per §5.

---

## 4. Workstream 2 — Dual-device diagnostics

### 4.1 New location

Create `scripts/diagnostics/` (top-level sibling of `scripts/calibration/`).
Move into it: `scan.py`, `show_calib.py`, `find_port.py`. After the move,
`so_arm101/` keeps only device-specific scripts (`jog.py`, `set_pose.py`,
`sweep.py`, `capture_pose.py`, `_common.py`, and the calibration JSON).

### 4.2 Shared-glue placement

The `from _common import …` pattern only resolves because a script's own
directory is `sys.path[0]`; it breaks once `scan.py`/`show_calib.py` live in a
different folder. Resolution:

- **Promote the shared constructors** into a new application-layer helper:
  `src/arm101_hand/scripts/device_setup.py`. It exports the device/bus builders
  and path constants currently in `so_arm101/_common.py`:
  `build_raw_bus`, `build_follower`, `load_arm_app_config`, `APP_CONFIG_PATH`,
  `ARM_CONFIG_PATH`, `CALIB_PATH`, `FOLLOWER_ID`, `gentle_velocity`,
  `load_home_degrees`. Path constants are recomputed from the repo root
  (`device_setup.py` is at `src/arm101_hand/scripts/`, repo root = `parents[3]`;
  `CALIB_PATH` becomes `repo_root / "scripts" / "calibration" / "so_arm101" / "so101_follower.json"`).
- `so_arm101/_common.py` **re-imports** those names from `device_setup` and keeps
  the **motion helpers** (`_drive_home`, `confirm_and_release`) locally — only the
  staying arm scripts use them. Net effect: `sweep.py`, `set_pose.py`,
  `capture_pose.py`, `jog.py` change **zero lines** (their `from _common import …`
  still resolves the same names).
- `scripts/diagnostics/scan.py` / `show_calib.py` import builders **directly**
  from `arm101_hand.scripts.device_setup` (application→device is a legal
  downward import per `01-module-layering.md`). No `diagnostics/_common.py`, no
  `sys.path` hacks, no duplication.

### 4.3 `find_port.py`

Relocated unchanged. Stays device-agnostic (wraps `lerobot-find-port`, which
enumerates **all** serial ports). No `--device` flag.

### 4.4 `scan.py --device arm|hand`

`--device` is **required** (argparse `choices=["arm","hand"]`, no default — an
explicit choice prevents pinging the wrong bus).

- `--device arm` — unchanged behavior: 5× STS3215 over a raw `FeetechMotorsBus`
  (`build_raw_bus`); port from `app_config.arm.port`; per-motor pos/load/voltage/
  temp; 12 V nominal; thresholds from `app_config.safety`; non-zero exit if any
  of motors 1–5 is silent.
- `--device hand` — **new**: 8× SCS0009 over `rustypot.Scs0009PyController`.
  - Port/baud/timeout from the hand calib YAML (`com_port`, `baudrate`,
    `timeout`) — the source every hand script already trusts (not
    `app_config.hand`, which is orphaned post-GUI).
  - rustypot has no `ping`; liveness probe = per-servo
    `read_present_position(sid)` wrapped in try/except, reusing the `_scalar`
    list-unwrap idiom from `hand/controller.py`. A servo that raises or returns
    nothing is reported missing.
  - For responders, report load/temp/voltage (decavolt→volt via `* 0.1`,
    matching the controller). Compare temp to `app_config.safety.temp_warn_c`;
    compare voltage to **5 V** nominal (IL-1, 5 V hand rail) using
    `voltage_warn_pct`.
  - Torque is never enabled. Non-zero exit if any of servos 1–8 is silent.

### 4.5 `show_calib.py --device arm|hand`

`--device` required; existing `--live` flag retained.

- `--device arm` — unchanged: read `so101_follower.json`, print per-joint
  id/homing/min/max/span/midpoint; `--live` opens the bus read-only and prints
  present degrees vs. mid.
- `--device hand` — **new**: read the hand calib YAML via
  `load_hand_calibration`; print per-finger `servo_1`/`servo_2` ids +
  `middle_pos` and the `base`/`side` DOF limits. `--live` opens
  `Scs0009PyController` read-only and prints each servo's present position in
  degrees (logical frame, via `servo_radians_to_degrees`) vs. its `middle_pos`.
  Never enables torque.

---

## 5. Workstream 3 — Rename the hand calibration assets

### 5.1 Renames

| From | To |
|---|---|
| `scripts/calibration/AmazingHand/` (folder) | `scripts/calibration/amazing_hand/` |
| `AmazingHand_MotorReset.py` | `motor_reset.py` |
| `AmazingHand_MiddlePos_FingerCalib.py` | `middle_calib.py` |
| `AmazingHand_RangeCalib.py` | `range_calib.py` |
| `AmazingHand_FingerTest.py` | `finger_test.py` |
| `AmazingHand_FullHand_Test.py` | `full_hand_test.py` |
| `AmazingHand_SetPose.py` | `set_pose.py` |
| `jog.py` | *(unchanged)* |
| `AmazingHand_calib_values.yaml` | `hand_calib_values.yaml` |

New canonical calibration path (IL-5):
`scripts/calibration/amazing_hand/hand_calib_values.yaml`.

### 5.2 Update every live reference

- Each renamed script's `YAML_PATH = SCRIPT_DIR / "…"` and any sibling-script
  references in its docstring.
- `jog.py`'s `YAML_PATH` (folder + filename both change) and the `REPO_ROOT`
  `parents[2]` calc is unaffected (depth unchanged).
- src docstrings naming the YAML or folder: `config/calibration.py`,
  `config/__init__.py`, `hand/kinematics.py`, `hand/pose_jog.py`,
  `hand/range_calib.py`.
- Active tests: `tests/unit/test_calibration.py`,
  `tests/unit/test_hand_config_save.py`, `tests/unit/test_hand_kinematics.py`,
  `tests/hardware/test_hand_real.py`.
- The folder `README.md` (procedure references the script names + YAML).

### 5.3 Rename a git submodule-excluded file

`AmazingHand_calib_values.yaml` is tracked in-tree (IL-5). The rename uses
`git mv` so history follows.

---

## 6. Documentation updates

**Living docs — updated in this refactor** (IL-7 single-source-of-truth):

- `CLAUDE.md` — directory tree, Common workflows (all three workstreams), IL-5
  path, Don'ts, tech-debt (drop "unified GUI" line; note removed GUI). Done via
  the final `/doc_update` pass.
- `README.md`, `data/README.md` — GUI mentions removed; workflow commands updated.
- `docs/conventions/00-iron-laws.md` — **IL-5** hand calibration path.
- `docs/conventions/02–06` — any references to the old script/folder/YAML names
  or the GUI.
- `.claude/agents/iron_law_audit.md`, `.claude/skills/iron-law-audit/SKILL.md`,
  `.claude/skills/ruleset-update/SKILL.md` — old-path references.

**Historical records — left as-is** (point-in-time; rewriting them is
misleading):

- `docs/superpowers/specs/**`, `docs/superpowers/plans/**`
- `docs/plans/01-unified-gui-spec.md` (documents a now-removed component; not
  retro-edited).

---

## 7. Verification

After each workstream (and again at the end):

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```

`mypy` baseline noise (`import yaml` `import-untyped`) is pre-existing and not
introduced here.

Hardware smoke (manual, when a bus is attached):

```powershell
uv run python scripts/diagnostics/scan.py --device arm
uv run python scripts/diagnostics/scan.py --device hand
uv run python scripts/diagnostics/show_calib.py --device arm
uv run python scripts/diagnostics/show_calib.py --device hand
```

---

## 8. Commit plan (IL-6)

Three logical commits, each green on lint/type/test:

1. **`refactor: remove unused PySide6 GUI layer`** — Workstream 1 (cross-device:
   touches shared `hand/` + tests + deps).
2. **`refactor(diagnostics): dual-device scan/show_calib/find_port`** —
   Workstream 2 (cross-device: arm + hand). Includes the `device_setup.py`
   promotion + `_common.py` re-export.
3. **`refactor(hand): snake_case calibration scripts + folder/YAML rename`** —
   Workstream 3 (hand-only; uses `git mv`).

Final: `/doc_update` commit refreshing `CLAUDE.md` / `README.md`.

IL-6 note: each commit is internally atomic for the devices it touches; none
splits a single cross-device change across commits.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `test_hand_real.py` imports the removed controller → import error. | §3.5: inspect and re-point/trim during implementation; covered by `pytest -m 'not hardware'` collection check (hardware tests still collect). |
| Path recomputation in `device_setup.py` wrong (`CALIB_PATH`, repo root). | Unit-import smoke + `show_calib.py --device arm` (reads `so101_follower.json`) exercises the path. |
| Missed reference to old YAML/folder name. | Post-rename grep for `AmazingHand_calib_values`, `calibration/AmazingHand`, `AmazingHand_*` across `scripts/ src/ docs/ tests/ .claude/` (excluding `references/`). |
| rustypot hand-scan probe semantics differ from Feetech `ping`. | §4.4: try/except around `read_present_position`; documented as the rustypot-equivalent liveness check. |
```
