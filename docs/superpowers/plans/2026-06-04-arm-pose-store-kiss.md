# Single-section Arm Pose Store + KISS Convention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the arm's three-bucket pose storage into one `data/arm_config.yaml` → `poses` section (mirroring the hand), and add a KISS convention doc.

**Architecture:** Remove the `quick_poses` field from `ArmPoseConfig` and delete `data/arm_jog_poses.yaml`. Every reader (`set_pose.py`, `jog.py`, `_common.py`, GUI `main_window.py`) and writer (`jog.py`, GUI) converges on `arm_config.yaml` → `poses`. `home` becomes an ordinary entry resolved by name.

**Tech Stack:** Python 3.12, pydantic v2, PyYAML, pytest, uv, ruff. Spec: `docs/superpowers/specs/2026-06-04-arm-pose-store-kiss-design.md`.

**Branch:** `refactor/arm-pose-store-kiss` (already checked out; spec already committed).

**Pre-existing uncommitted state on this branch:** the earlier `zero`/`rest` removal edits are in the working tree (in `arm_config.yaml`, `app_config.yaml`, `src/arm101_hand/config/app_config.py`, `set_pose.py`, README, `CLAUDE.md`, `test_arm_poses_schema.py`). They are prerequisites and get folded into the commits below.

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `src/arm101_hand/config/arm_poses.py` | Pose schema + load/save | Modify: drop `quick_poses`; regen-header in `save_arm_poses` |
| `data/arm_config.yaml` | The single arm pose store | Modify: single `poses` section with `home` |
| `data/arm_jog_poses.yaml` | (was) jog-saved poses | **Delete** |
| `scripts/calibration/so_arm101/_common.py` | Shared script helpers | Modify: `load_home_degrees` → `poses`; drop `ARM_JOG_POSES_PATH` |
| `scripts/calibration/so_arm101/set_pose.py` | Drive to a named pose | Modify: read `poses` only |
| `scripts/calibration/so_arm101/jog.py` | Keyboard jog + save pose | Modify: save to `arm_config.yaml` |
| `src/arm101_hand/gui/main_window.py` | Safe-park pose resolver | Modify: `poses.get(name)` |
| `tests/unit/test_arm_poses_schema.py` | Schema tests | Modify: drop `quick_poses` |
| `docs/conventions/07-kiss-simplicity.md` | KISS convention | **Create** |
| `CLAUDE.md`, `scripts/calibration/so_arm101/README.md` | Docs | Modify: pointers + drop stale refs |

---

## Task 1: Collapse the schema, seed data, and schema tests

This is one atomic change — the `extra="forbid"` schema, the seed YAML, and the schema test must move together or the seeded-YAML load fails.

**Files:**
- Modify: `src/arm101_hand/config/arm_poses.py`
- Modify: `data/arm_config.yaml`
- Delete: `data/arm_jog_poses.yaml`
- Test: `tests/unit/test_arm_poses_schema.py`

- [ ] **Step 1: Update the schema tests to the new shape (write the failing tests first)**

Replace the body of `test_seeded_yaml_loads_clean` and `test_empty_quick_poses_and_poses_accepted` in `tests/unit/test_arm_poses_schema.py`.

`test_seeded_yaml_loads_clean` becomes:

```python
def test_seeded_yaml_loads_clean() -> None:
    cfg = load_arm_poses(SEEDED_PATH)
    assert cfg.schema_version == 1, "schema_version of seeded YAML"
    assert "home" in cfg.poses, "seeded YAML has the home pose"
    home = cfg.poses["home"]
    assert home.shoulder_lift == -104.9, "home folds the shoulder back (folded storage / safe-park target)"
```

Rename `test_empty_quick_poses_and_poses_accepted` → `test_empty_poses_accepted` and drop the `quick_poses` key (it is now a forbidden extra field):

```python
def test_empty_poses_accepted() -> None:
    cfg = ArmPoseConfig.model_validate({"schema_version": 1, "poses": {}})
    assert cfg.poses == {}, "poses may be empty"


def test_quick_poses_key_now_rejected() -> None:
    with pytest.raises(ValidationError):
        ArmPoseConfig.model_validate({"schema_version": 1, "quick_poses": {}, "poses": {}})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_arm_poses_schema.py -v`
Expected: FAIL — `test_seeded_yaml_loads_clean` (current YAML still has `quick_poses`), `test_quick_poses_key_now_rejected` (schema still accepts `quick_poses`).

- [ ] **Step 3: Remove `quick_poses` from the schema and add a regenerated header to `save_arm_poses`**

In `src/arm101_hand/config/arm_poses.py`, change the `ArmPoseConfig` class:

```python
class ArmPoseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    poses: dict[str, ArmPose] = Field(default_factory=dict)
```

Replace `save_arm_poses` (and add the header constant just above it):

```python
_SAVE_HEADER = (
    "# data/arm_config.yaml -- arm poses (degrees per joint; lerobot use_degrees mode).\n"
    "# MACHINE-MANAGED: rewritten by jog.py ('s' to save) and the GUI; do not hand-edit\n"
    "# (comments are regenerated on save). 'home' is the default parking / safe-park pose.\n"
)


def save_arm_poses(path: Path, config: ArmPoseConfig) -> None:
    """Write an ``ArmPoseConfig`` to YAML atomically (tmp file + ``os.replace``).

    The file is machine-owned: a fixed header is regenerated each write and any prior
    comments are discarded. ``sort_keys=False`` keeps a stable field order.
    """
    payload = config.model_dump(mode="python")
    body = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_SAVE_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
```

Also update the module docstring line 1 from `(quick-poses + saved poses)` to `(arm poses)`.

- [ ] **Step 4: Rewrite the seed `data/arm_config.yaml` to a single `poses` section**

Replace the entire file with:

```yaml
# data/arm_config.yaml -- arm poses (degrees per joint; lerobot use_degrees mode).
# MACHINE-MANAGED: rewritten by jog.py ('s' to save) and the GUI; do not hand-edit
# (comments are regenerated on save). 'home' is the default parking / safe-park pose.
#
# Joint values are DEGREES relative to each joint's calibrated midpoint. Readers clamp
# every value to range_min..range_max from so101_follower.json and skip out-of-range
# joints. 'home' is the folded-against-base storage pose the motion scripts return to
# before releasing torque; captured from hardware on 2026-06-03.
schema_version: 1
poses:
  home:
    shoulder_pan: 80.2
    shoulder_lift: -104.9
    elbow_flex: 91.0
    wrist_flex: -102.9
    wrist_roll: -0.2
```

- [ ] **Step 5: Delete the now-unused jog-pose file**

Run: `git rm data/arm_jog_poses.yaml`
Expected: file staged for deletion. (It currently holds only `poses: {}`, so nothing is lost — see spec §5.)

- [ ] **Step 6: Run the schema tests to verify they pass**

Run: `uv run pytest tests/unit/test_arm_poses_schema.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/arm_poses.py data/arm_config.yaml tests/unit/test_arm_poses_schema.py
git commit -m "refactor(arm): single poses section in arm_config.yaml; drop quick_poses + jog file

Removes the quick_poses field and deletes data/arm_jog_poses.yaml. home is now an
ordinary entry in poses. save_arm_poses regenerates a fixed header (file is
machine-owned). Mirrors the hand's single-poses model (KISS).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Point `_common.load_home_degrees` at `poses` and drop the jog path

**Files:**
- Modify: `scripts/calibration/so_arm101/_common.py:23` and `:71-81`

- [ ] **Step 1: Remove the `ARM_JOG_POSES_PATH` constant**

Delete this line (currently `_common.py:23`):

```python
ARM_JOG_POSES_PATH = _REPO_ROOT / "data" / "arm_jog_poses.yaml"
```

- [ ] **Step 2: Update `load_home_degrees` to read `poses`**

Replace the function body (currently `_common.py:71-81`):

```python
def load_home_degrees() -> dict[str, float]:
    """Per-joint default-home target in degrees, from arm_config.yaml ``poses['home']``.

    This is the pose the motion scripts return to before releasing torque. Falls back to
    all-zeros (the calibrated mid) if the file or the ``home`` pose is absent.
    """
    if ARM_CONFIG_PATH.is_file():
        home = load_arm_poses(ARM_CONFIG_PATH).poses.get("home")
        if home is not None:
            return home.as_dict()
    return dict.fromkeys(ARM_JOINTS, 0.0)
```

- [ ] **Step 3: Verify nothing else in the file references the removed constant**

Run: `uv run python -c "import ast,sys; src=open('scripts/calibration/so_arm101/_common.py').read(); assert 'ARM_JOG_POSES_PATH' not in src, 'still referenced'; print('clean')"`
Expected: `clean`

- [ ] **Step 4: Smoke-test the helper imports and home load**

Run: `uv run python -c "import sys; sys.path.insert(0, 'scripts/calibration/so_arm101'); import _common; print('home =', _common.load_home_degrees())"`
Expected: prints `home = {'shoulder_pan': 80.2, 'shoulder_lift': -104.9, 'elbow_flex': 91.0, 'wrist_flex': -102.9, 'wrist_roll': -0.2}`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/_common.py
git commit -m "refactor(arm): load_home_degrees reads poses['home']; drop ARM_JOG_POSES_PATH

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `set_pose.py` reads `poses` only

**Files:**
- Modify: `scripts/calibration/so_arm101/set_pose.py` (imports `:31-43`, `main` `:64-73`)

- [ ] **Step 1: Drop `ARM_JOG_POSES_PATH` from the `_common` import**

In the `from _common import (...)` block, remove the `ARM_JOG_POSES_PATH,` line so the import no longer references it. The remaining names (`ARM_CONFIG_PATH`, `CALIB_PATH`, `build_follower`, `gentle_velocity`, `load_arm_app_config`, `load_home_degrees`, `park_home_and_release`) stay.

- [ ] **Step 2: Replace the pose-loading block in `main`**

Replace these lines (currently `set_pose.py:65-73`):

```python
    quick = load_arm_poses(ARM_CONFIG_PATH).quick_poses
    jog = load_arm_poses(ARM_JOG_POSES_PATH).poses if ARM_JOG_POSES_PATH.is_file() else {}
    poses = {**quick, **jog}
    if not poses:
        print(
            f"no poses defined in {ARM_CONFIG_PATH} (quick_poses) or {ARM_JOG_POSES_PATH} (poses)",
            file=sys.stderr,
        )
        return 1
```

with:

```python
    poses = load_arm_poses(ARM_CONFIG_PATH).poses
    if not poses:
        print(f"no poses defined in {ARM_CONFIG_PATH} (poses)", file=sys.stderr)
        return 1
```

- [ ] **Step 3: Update the module docstring**

Replace the "Poses come from ..." sentence near the top (currently `set_pose.py:3-5`) with:

```
Poses come from data/arm_config.yaml ``poses`` (home -- the folded storage pose -- plus any
poses saved via jog.py). Targets are in DEGREES relative to each joint's calibrated mid.
```

Also update the usage block to a single example (remove any `rest` line if present):

```
Usage:
  uv run python scripts/calibration/so_arm101/set_pose.py home
  uv run python scripts/calibration/so_arm101/set_pose.py        # prompts
```

- [ ] **Step 4: Verify it parses and resolves `home`**

Run: `uv run python -c "import sys; sys.path.insert(0,'scripts/calibration/so_arm101'); import set_pose; from _common import ARM_CONFIG_PATH; from arm101_hand.config import load_arm_poses; print('available =', sorted(load_arm_poses(ARM_CONFIG_PATH).poses))"`
Expected: `available = ['home']`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/set_pose.py
git commit -m "refactor(arm): set_pose reads arm_config.yaml poses only (no jog-file merge)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `jog.py` saves to `arm_config.yaml`

**Files:**
- Modify: `scripts/calibration/so_arm101/jog.py` (imports `:29-37`, `_save_pose` `:75-88`, docstring `:4-5`, `:13`)

- [ ] **Step 1: Drop `ARM_JOG_POSES_PATH` from the `_common` import; add `ARM_CONFIG_PATH`**

In the `from _common import (...)` block, remove `ARM_JOG_POSES_PATH,` and add `ARM_CONFIG_PATH,` (keep it alphabetical: it sorts first). The block should import `ARM_CONFIG_PATH`, `CALIB_PATH`, `build_follower`, `gentle_velocity`, `load_arm_app_config`, `load_home_degrees`, `park_home_and_release`.

- [ ] **Step 2: Point `_save_pose` at `arm_config.yaml`**

Replace the three I/O lines in `_save_pose` (currently `jog.py:85-88`):

```python
    config = load_arm_poses(ARM_JOG_POSES_PATH) if ARM_JOG_POSES_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**_present_degrees(follower))
    save_arm_poses(ARM_JOG_POSES_PATH, config)
    print(f"  saved '{name}' -> {ARM_JOG_POSES_PATH}")
```

with:

```python
    config = load_arm_poses(ARM_CONFIG_PATH) if ARM_CONFIG_PATH.is_file() else ArmPoseConfig()
    config.poses[name] = ArmPose(**_present_degrees(follower))
    save_arm_poses(ARM_CONFIG_PATH, config)
    print(f"  saved '{name}' -> {ARM_CONFIG_PATH}")
```

- [ ] **Step 3: Update the docstring references to the file**

In the module docstring, change the two mentions of `data/arm_jog_poses.yaml` (currently lines `:5` and `:13`) to `data/arm_config.yaml`. Line 13 becomes:

```
  s             save current pose to data/arm_config.yaml (prompts for a name)
```

- [ ] **Step 4: Verify it parses cleanly**

Run: `uv run python -c "import ast; ast.parse(open('scripts/calibration/so_arm101/jog.py').read()); print('parsed ok')"`
Expected: `parsed ok`

Run: `uv run python -c "src=open('scripts/calibration/so_arm101/jog.py').read(); assert 'ARM_JOG_POSES_PATH' not in src and 'arm_jog_poses' not in src, 'stale ref remains'; print('clean')"`
Expected: `clean`

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/jog.py
git commit -m "refactor(arm): jog.py saves poses into arm_config.yaml (single store)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: GUI safe-park resolver reads `poses`

**Files:**
- Modify: `src/arm101_hand/gui/main_window.py:155-159`

- [ ] **Step 1: Simplify `arm_resolver`**

Replace the resolver body (currently `main_window.py:155-159`):

```python
        def arm_resolver() -> dict[str, float] | None:
            pose = arm_poses.quick_poses.get(arm_pose_name) or arm_poses.poses.get(arm_pose_name)
            if pose is None:
                return None
            return pose.as_dict()
```

with:

```python
        def arm_resolver() -> dict[str, float] | None:
            pose = arm_poses.poses.get(arm_pose_name)
            if pose is None:
                return None
            return pose.as_dict()
```

- [ ] **Step 2: Verify no `quick_poses` references remain in `src/`**

Run: `uv run python -c "import subprocess,sys; r=subprocess.run(['git','grep','-n','quick_poses','--','src/','scripts/'],capture_output=True,text=True); print(r.stdout or 'none'); sys.exit(1 if r.stdout.strip() else 0)"`
Expected: `none` (exit 0).

- [ ] **Step 3: Run the safe-park unit tests**

Run: `uv run pytest tests/unit/test_safe_park.py -v`
Expected: PASS (these use mock resolvers; confirms no import/signature breakage).

- [ ] **Step 4: Commit**

```bash
git add src/arm101_hand/gui/main_window.py
git commit -m "refactor(arm): GUI safe-park resolver reads poses (drop quick_poses fallback)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add the KISS convention doc + pointers

**Files:**
- Create: `docs/conventions/07-kiss-simplicity.md`
- Modify: `CLAUDE.md` (§5 convention table)
- Modify: `scripts/calibration/so_arm101/README.md` is NOT the convention index — the convention pointer goes in `CLAUDE.md` and the repo `README.md` if it has a convention list (check first).

- [ ] **Step 1: Create `docs/conventions/07-kiss-simplicity.md`**

```markdown
# 07 — KISS: Keep It Simple

*Systems work best kept simple, not complex. Simplicity is harder to produce than
complexity — it is the work, not the shortcut. Less is more; simplicity wins in the
long run.*

**The rule:** Prefer the simplest design that satisfies the requirement and the Iron
Laws. When two designs both work, ship the one with fewer files, fewer concepts, and
less indirection.

## Why it pays off

- **Maintainable** — fewer moving parts to reason about.
- **Readable** — a new contributor (or future you) follows it without untangling clever patterns.
- **Debuggable** — straightforward control flow makes bugs visible. On hardware that
  moves, a bug you can *see* is a bug that does not damage a servo.
- **Extensible** — a simple foundation extends cleanly; a clever one resists change.

## In practice

1. **One responsibility per unit** — small functions/modules that each do one thing.
2. **Readability over cleverness** — write as if explaining to a less-experienced developer.
3. **No speculative abstraction (YAGNI)** — add structure when a second real case appears, not before.
4. **No premature optimization** — make it correct and clear first.
5. **Refactor toward simpler** — leave code simpler than you found it.
6. **Mirror existing shapes** — when one device already solves a problem (e.g., the
   hand's single `poses` section), the other should match rather than invent a parallel scheme.

## Smells of over-complexity

- The same data in two places with divergent readers.
- A config section nothing writes.
- A "flexible" layer with exactly one caller.
- Needing a paragraph to explain why two files exist.
```

- [ ] **Step 2: Add the pointer row to the CLAUDE.md §5 convention table**

In `CLAUDE.md`, in the "## 5. Convention files" table, add a row after the `06-documentation-protocol.md` row:

```markdown
| `docs/conventions/07-kiss-simplicity.md` | KISS — prefer the simplest design that satisfies the requirement + Iron Laws. |
```

- [ ] **Step 3: Add the pointer to the repo README convention list if one exists**

Run: `uv run python -c "import subprocess; r=subprocess.run(['git','grep','-l','docs/conventions/06','--','README.md'],capture_output=True,text=True); print(r.stdout or 'no README convention list')"`

If a README references the convention files, add a matching `07-kiss-simplicity.md` pointer line there in the same format as the surrounding rows. If the command prints `no README convention list`, skip this step.

- [ ] **Step 4: Commit**

```bash
git add docs/conventions/07-kiss-simplicity.md CLAUDE.md README.md
git commit -m "docs(conventions): add 07-kiss-simplicity + pointers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(If README.md was not modified, drop it from the `git add`.)

---

## Task 7: Refresh script docs (README + CLAUDE.md) for the single store

**Files:**
- Modify: `scripts/calibration/so_arm101/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find every remaining stale reference**

Run: `uv run python -c "import subprocess; r=subprocess.run(['git','grep','-n','-e','arm_jog_poses','-e','quick_poses','--','*.md'],capture_output=True,text=True); print(r.stdout or 'none')"`
Expected: a list of doc lines in `scripts/calibration/so_arm101/README.md` and possibly `CLAUDE.md`.

- [ ] **Step 2: Update `scripts/calibration/so_arm101/README.md`**

For each hit from Step 1 in this file:
- In the `jog.py` table row and the `s` key row, change "save current pose to `data/arm_jog_poses.yaml`" → "save current pose to `data/arm_config.yaml`".
- Replace the paragraph "Saved poses land in `data/arm_jog_poses.yaml` (a separate file from `arm_config.yaml`) and are drivable by name with `set_pose.py`, which resolves from both files." with: "Saved poses land in `data/arm_config.yaml` → `poses` and are drivable by name with `set_pose.py` (one file; `home` is the parking pose)."
- Replace any `quick_poses.home` references with `poses.home`.

- [ ] **Step 3: Update `CLAUDE.md`**

- In the directory tree / data section, remove any line mentioning `arm_jog_poses.yaml`.
- In the `jog.py` workflow comment (§4), change "saves poses to data/arm_jog_poses.yaml" → "saves poses to data/arm_config.yaml".
- Replace any remaining `quick_poses` mention with `poses`.

- [ ] **Step 4: Verify no stale references remain anywhere**

Run: `uv run python -c "import subprocess,sys; r=subprocess.run(['git','grep','-n','-e','arm_jog_poses','-e','quick_poses'],capture_output=True,text=True); print(r.stdout or 'CLEAN'); sys.exit(1 if r.stdout.strip() else 0)"`
Expected: `CLEAN` (exit 0). (The spec doc under `docs/superpowers/specs/` legitimately mentions these words historically; if it is the only hit, that is acceptable — confirm the hits are only the spec/plan files.)

- [ ] **Step 5: Commit**

```bash
git add scripts/calibration/so_arm101/README.md CLAUDE.md
git commit -m "docs(arm): point script docs at single arm_config.yaml pose store

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Full verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the full host test suite**

Run: `uv run pytest -m 'not hardware'`
Expected: all pass, no errors.

- [ ] **Step 2: Lint and format check**

Run: `uv run ruff check .`
Expected: no errors.

Run: `uv run ruff format --check .`
Expected: no files would be reformatted. (If any are flagged, run `uv run ruff format .` and amend the relevant commit.)

- [ ] **Step 3: Confirm the config still loads end-to-end**

Run: `uv run python -c "from pathlib import Path; from arm101_hand.config import load_app_config, load_arm_poses; a=load_app_config(Path('data/app_config.yaml')); p=load_arm_poses(Path('data/arm_config.yaml')); print('safe_park.arm_pose =', a.safety.safe_park.arm_pose); print('poses =', list(p.poses))"`
Expected: `safe_park.arm_pose = home` and `poses = ['home']`.

- [ ] **Step 4: Confirm the deleted file is gone and untracked**

Run: `uv run python -c "from pathlib import Path; print('exists' if Path('data/arm_jog_poses.yaml').exists() else 'gone')"`
Expected: `gone`.

- [ ] **Step 5: Final branch review**

Run: `git log --oneline main..HEAD` and `git status`
Expected: a clean tree and the sequence of commits from Tasks 1–7 plus the spec commit. Nothing uncommitted.

---

## Self-review notes (already applied)

- **Spec coverage:** schema §3.1 → Task 1; data files §3.2 → Task 1; readers/writers §3.3 → Tasks 2–5; config alignment §3.4 → verified in Task 8 Step 3; tests §3.5 → Task 1; docs §3.6 → Task 7; KISS §4 → Task 6; out-of-scope §5 honored (no migration tooling, no hand changes); verification §6 → Task 8.
- **Placeholder scan:** none — every code step shows full code; doc steps that depend on current file content use a `git grep` discovery step first.
- **Type/name consistency:** `poses` (not `quick_poses`) used uniformly; `ArmPoseConfig.poses`, `ArmPose.as_dict()`, `load_arm_poses`, `save_arm_poses`, `_SAVE_HEADER`, `ARM_CONFIG_PATH` consistent across tasks.
