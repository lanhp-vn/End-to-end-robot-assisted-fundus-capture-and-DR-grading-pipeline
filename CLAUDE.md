# CLAUDE.md — Agent Self-Reference

Workspace: `D:\0-Nouslogic\AmazingHand-ARM101-Follower`
Hardware: SO-ARM101 follower arm (5 STS3215 motors, gripper removed) + AmazingHand bionic hand (8 SCS0009 motors). Single-PC dev (no embedded board, no cross-compile). Loaded automatically by Claude Code.

---

## 1. Project TL;DR

A unified workspace for calibrating and controlling two robotic devices that share one Python process:

- **SO-ARM101 follower** — LeRobot's 6-DoF arm with the gripper (motor ID 6) physically removed. The AmazingHand mounts in its place. We subclass `SOFollower` to skip the missing motor and register a new robot type, `so101_follower_no_gripper`.
- **AmazingHand** — Pollen Robotics 4-finger bionic hand (4 fingers × 2 SCS0009 = 8 servos). Driven via the `rustypot` Python wheel (Rust + pyo3 binding).

Both devices live behind separate USB↔TTL bridges on different COM ports and different voltage rails (5 V hand, 12 V arm). They are coordinated only at the application layer (a future teleop / policy script).

## 2. Iron Laws TL;DR

Full text in `docs/conventions/00-iron-laws.md`. Read that file before editing.

- **IL-1 — Voltage isolation is physical safety.** 5 V hand bus and 12 V arm bus must never share rails. Mixing destroys SCS0009 instantly.
- **IL-2 — `references/` is read-only.** Five vendored submodules (lerobot, AmazingHand, FeetechServo, rustypot, FTServo_Python). Adapt by transferring into `src/` or `scripts/`.
- **IL-3 — Motor ID canon.** Hand: IDs 1–8 (odd-right/even-left per finger). Arm: IDs 1–5 shoulder→wrist. **ID 6 is physically absent on the arm.**
- **IL-4 — COM-port discipline.** Single owner per bus. Close FD.exe / serial monitors / stale Python sessions before opening.
- **IL-5 — Calibration in version control, in-tree only.** Hand: `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`. Arm: `scripts/calibration/so_arm101/<id>.json` — the `SO101FollowerNoGripperConfig` subclass defaults `calibration_dir` to that path so `~/.cache/huggingface/lerobot/...` is never written to.
- **IL-6 — Atomic cross-device commits.** Changes touching both arm and hand ship in one commit.
- **IL-7 — Documentation is single-source-of-truth.** One canonical home per fact; pointers everywhere else.

## 3. Directory tree

```
AmazingHand-ARM101-Follower/
├── pyproject.toml             # uv-managed; lerobot[feetech] + rustypot
├── uv.lock                    # committed
├── .python-version            # 3.12
├── src/arm101_hand/
│   ├── robots/                # device layer — SO-ARM101 subclass
│   ├── hand/                  # device layer — rustypot controller, kinematics, range-calib state machine
│   ├── config/                # primitive layer — pydantic schemas (calibration v2, poses, app config)
│   ├── gui/                   # application layer — PySide6 unified GUI (hand + arm)
│   └── scripts/               # application layer — console-script entries
├── scripts/
│   ├── calibration/
│   │   ├── AmazingHand/       # 5 calibration/test scripts + YAML state (v2 schema)
│   │   └── so_arm101/         # follower calibration runner
│   ├── teleop/                # planned
│   └── demos/                 # planned (FullHand_Demo etc.)
├── tests/                     # host unit tests (tests/unit) + hardware-gated (tests/hardware)
├── docs/
│   ├── BOM.md                 # bill of materials + host PC spec
│   └── conventions/           # 00–06 normative rules
├── references/                # 5 git submodules — never modify (IL-2)
└── .claude/                   # local Claude Code config
```

## 4. Common workflows

```powershell
# One-time setup
uv sync                                   # provisions .venv from pyproject.toml

# Discover COM ports (both buses)
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize

# AmazingHand calibration (full procedure in scripts/calibration/AmazingHand/README.md)
uv run python scripts/calibration/AmazingHand/AmazingHand_MotorReset.py
uv run python scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py
uv run python scripts/calibration/AmazingHand/AmazingHand_RangeCalib.py      # Step 4: per-finger DOF limits
uv run python scripts/calibration/AmazingHand/AmazingHand_FingerTest.py

# SO-ARM101 follower calibration (full procedure in scripts/calibration/so_arm101/README.md)
# Output JSON lands at scripts/calibration/so_arm101/<id>.json (subclass default).
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower

# Discover ports via lerobot's helper
uv run python scripts/calibration/so_arm101/find_port.py

# SO-ARM101 verify/audit helpers (read-only re: calibration; never write so101_follower.json — IL-5)
# Detail in scripts/calibration/so_arm101/README.md §6.
uv run python scripts/calibration/so_arm101/scan.py                       # pre-flight bus health check (torque off)
uv run python scripts/calibration/so_arm101/show_calib.py [--live]        # dump calibration; --live compares present pos
uv run python scripts/calibration/so_arm101/sweep.py <joint|all>          # range-verify sweep to endpoints (--margin 90)
uv run python scripts/calibration/so_arm101/set_pose.py home              # drive to a poses entry (home = folded storage), hold
uv run python scripts/calibration/so_arm101/jog.py                       # interactive keyboard jog; reads clamp range, saves poses to data/arm_config.yaml

# Lint / format / type-check / test
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'           # host unit tests (no bus)
```

## 5. Convention files

| File | Role |
|---|---|
| `docs/conventions/00-iron-laws.md` | **Read first.** Non-negotiable invariants. |
| `docs/conventions/01-module-layering.md` | Where code goes; the four import invariants. |
| `docs/conventions/02-code-style-python.md` | Python 3.12 style; ruff/mypy config; pydantic patterns. |
| `docs/conventions/03-code-style-shell.md` | PowerShell (primary) + Bash (rare). |
| `docs/conventions/04-testing-verification.md` | Test pyramid; `@pytest.mark.hardware` for bus-required tests. |
| `docs/conventions/05-git-workflow.md` | Branches, commit format, atomic cross-device commit rule. |
| `docs/conventions/06-documentation-protocol.md` | When to refresh CLAUDE.md/README.md; DRY registry. |
| `docs/conventions/07-kiss-simplicity.md` | KISS — prefer the simplest design that satisfies the requirement + Iron Laws. |

## 6. Don'ts (compressed)

- **Don't modify `references/**`** — IL-2. Adapt by transferring into `src/` or `scripts/`.
- **Don't share power rails between hand and arm** — IL-1. Two PSUs, two cables, labeled.
- **Don't run `lerobot-calibrate` directly for the follower** — it doesn't know `so101_follower_no_gripper`. Use `arm101-calibrate-follower`.
- **Don't open a COM port from two processes** — IL-4. Close other holders first.
- **Don't hand-edit calibration YAML/JSON while a controller is connected** — IL-5. Re-run the calibration script.
- **Don't use `yaml.load()`** — `yaml.safe_load()` only.
- **Don't `subprocess.*` without `timeout=`** — see `02-code-style-python.md` §8.
- **Don't commit secrets** — `~/.cache/huggingface/token` lives outside the repo.

## 7. Tech-debt & known limitations

- **No teleop, no policy, no dataset code.** The hand controller + GUI drive poses; no teleoperation or learned-policy path yet.
- **No CI.** `ruff` / `pytest` are local-only for now.
- **No discrete GPU.** Local ML training is CPU-bound; large-policy work needs cloud.
- **`mypy` baseline noise.** PyYAML ships no stubs, so `import yaml` lines report `import-untyped` until `types-PyYAML` is added — pre-existing, not from current work.
