# AmazingHand + SO-ARM101 Follower

Unified Python workspace for calibrating and controlling a **LeRobot SO-ARM101 follower arm** with the gripper replaced by a **Pollen Robotics AmazingHand** bionic hand. Single-PC development on Windows 11 — no embedded board, no cross-compile.

## What it is

- A 5-DoF arm + 4-finger bionic hand sharing one Python process.
- Two independent serial buses: **5 V** for the hand (8 × Feetech SCS0009), **12 V** for the arm (5 × Feetech STS3215, no gripper).
- Calibration scripts that produce version-controlled state, ready for downstream teleop / policy work.

The arm side is a thin layer over [`huggingface/lerobot`](https://github.com/huggingface/lerobot) — we subclass `SOFollower` to skip the physically-absent gripper motor (ID 6) and register the new type as `so101_follower_no_gripper`. The hand side uses [`pollen-robotics/rustypot`](https://github.com/pollen-robotics/rustypot) directly through its Python wheel.

## Hardware

Full bill of materials, current budgets, and host PC spec live in [`docs/BOM.md`](docs/BOM.md). Quick summary:

| Role | Part | Qty | Bus |
|---|---|---|---|
| Arm actuators | Feetech STS3215 (12 V) | 5 (IDs 1–5) | Waveshare Bus Servo Adapter (A) |
| Arm power | 12 V / 3 A DC | 1 | — |
| Hand actuators | Feetech SCS0009 (5 V) | 8 (IDs 1–8) | Seeed XIAO Bus Servo Driver |
| Hand power | 5 V / 3 A DC | 1 | — |
| USB↔TTL bridge (hand) | Waveshare CH343 (VID 1A86 / PID 55D3) | 1 | enumerates as `COM18` on the dev host |
| USB↔TTL bridge (arm) | Waveshare adapter (CH343 / FT232 typical) | 1 | discover with PnP query below |

**The 5 V and 12 V rails must never share** — mixing destroys SCS0009 servos instantly. See `docs/conventions/00-iron-laws.md` IL-1.

## Host setup

You'll need:

- **Windows 10/11** (developed on Windows 11 Home build 22635).
- **Python 3.12** (declared in `.python-version`; the Microsoft Store install or `https://www.python.org/downloads/` is fine).
- **`uv`** package manager — install per [`https://github.com/astral-sh/uv`](https://github.com/astral-sh/uv).

From a fresh clone:

```powershell
git clone --recurse-submodules <this-repo-url>
cd AmazingHand-ARM101-Follower
uv sync
```

`uv sync` provisions `.venv/` from `pyproject.toml` (lerobot[feetech] editable from the submodule, rustypot wheel, numpy, pyyaml, pydantic, opencv-python, timm). About 60 packages get installed including `torch` — first sync takes a few minutes.

You do **not** need to activate the venv. Use `uv run <command>` for everything.

## First commands

### 1. Discover your COM ports

```powershell
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
```

Note the COM number for each USB↔TTL bridge. On the dev host the AmazingHand bridge is `COM18`; the SO-ARM101 bridge gets a separate number.

Alternatively, lerobot's interactive port helper (device-agnostic — lists all ports):

```powershell
uv run python scripts/diagnostics/motors/find_port.py
```

### 2. AmazingHand finger calibration

Full procedure (5 steps: ID burn → 0° reset → middle-position tuning → range-limit calibration → audit) is in [`scripts/calibration/amazing_hand/README.md`](scripts/calibration/amazing_hand/README.md). After the first ID-burn step (one-time, with FD.exe), the Python flow is:

```powershell
uv run python scripts/calibration/amazing_hand/motor_reset.py
uv run python scripts/calibration/amazing_hand/middle_calib.py
uv run python scripts/calibration/amazing_hand/range_calib.py
uv run python scripts/calibration/amazing_hand/finger_test.py
```

All state lives in `scripts/calibration/amazing_hand/hand_calib_values.yaml`.

### 3. SO-ARM101 follower calibration

Full procedure in [`scripts/calibration/so_arm101/README.md`](scripts/calibration/so_arm101/README.md). Quick form:

```powershell
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower
```

Replace `COM<X>` with the COM number from step 1. The output JSON goes to `scripts/calibration/so_arm101/so101_follower.json` — the `SO101FollowerNoGripperConfig` subclass defaults `calibration_dir` to that in-tree location, so the upstream `~/.cache/huggingface/lerobot/...` path is never used (IL-5). Pass `--robot.calibration_dir=<path>` if you want to override.

> **Important.** Use `arm101-calibrate-follower` (defined in `pyproject.toml`), not `lerobot-calibrate`. The upstream entry point doesn't know our `so101_follower_no_gripper` subclass exists.

Once calibrated, two kinds of read-only / motion helpers are available. Dual-device diagnostics live under `scripts/diagnostics/motors/`: `scan.py --device arm|hand` (pre-flight bus health check) and `show_calib.py --device arm|hand [--live]` (dump the calibration, `--live` compares present positions). Arm-specific motion helpers stay alongside the runner under `scripts/calibration/so_arm101/`: `sweep.py` (range-verify sweep to the calibrated endpoints), `set_pose.py` (drive to a named pose and hold), `jog.py` (keyboard-jog each joint in degrees within its calibrated range, saving named poses to `src/arm101_hand/data/arm_config.yaml`), and `capture_pose.py` (hand-pose the arm, capture present degrees, save). None of these write `so101_follower.json` — `arm101-calibrate-follower` stays the only thing that does (IL-5). Per-script detail, including the full key controls for `jog.py`, is in [`scripts/calibration/so_arm101/README.md`](scripts/calibration/so_arm101/README.md) §6.

```powershell
uv run python scripts/diagnostics/motors/scan.py --device arm
uv run python scripts/diagnostics/motors/show_calib.py --device arm
uv run python scripts/calibration/so_arm101/jog.py
```

### 4. Robot-triggered fundus capture (optional)

Two cameras are involved: the **Optomed Aurora** *fundus* camera (captures patient retinal images, over Wi-Fi/Pictor) and an arm-mounted USB *system* camera that films the Aurora's screen. With the Aurora on the same Wi-Fi, the hand presses its dual-action shutter and the workspace auto-pulls the freshly-captured image over the Pictor protocol; the demo also opens a live window for the system camera, zoomed to a fixed region of interest on the Aurora's screen (press `r` to record that zoomed feed). After each successful pull the captured image pops up in its own window and stays until the next shutter press.

```powershell
uv run python scripts/diagnostics/fundus_camera/aurora_probe.py        # read-only Aurora reachability + status + filelist
uv run python scripts/diagnostics/system_camera/usb_camera_probe.py    # system-cam smoke test: live window + 'r' record (no motors/Aurora)
uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py  # preview the fixed deskewed 4:3 ROI zoom of the Aurora screen (no motors/Aurora)
uv run python scripts/diagnostics/system_camera/usb_camera_focus_probe.py  # sweep + fine-tune the manual focus value (needs --backend dshow; no motors/Aurora)
uv run python scripts/diagnostics/system_camera/usb_camera_arc_debug.py  # arc-detection debug: stages the arm+hand grab, draws the arc boxes on the deskewed ROI; SPACE saves failing RED/clear cases to media_outputs/arc_debug/ (motors; no Aurora)
uv run python scripts/demos/grab_trigger_capture.py                    # live ROI-zoomed system-cam window; SPACE presses the shutter, image lands in media_outputs/fundus_images/ and pops up until the next capture
```

When the system camera's **resolution or lighting** changes, re-derive its view calibration — the Aurora **screen ROI** (a deskewed 4:3 crop carrying a rotation angle), the two symmetric alignment-**arc bands**, and the **red HSV color band** — with an interactive guided capture from **3 frames** (white startup screen → red arcs → bright/aligned screen → confirm → write). The bright frame fits the camera circle and places mirror-symmetric arc bands on its edges; detection is red-only (an arc is RED or not). It writes `src/arm101_hand/data/system_camera_config.yaml` (keeping a `.bak`); see [`scripts/calibration/system_camera/README.md`](scripts/calibration/system_camera/README.md):

```powershell
uv run python scripts/calibration/system_camera/calibrate_view.py                       # live guided capture
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files WHITE RED BRIGHT  # re-detect on saved frames
```

Camera prerequisites (the API cannot set these — do it on the device): **Still imaging** mode, **Quick imaging ON**, and **Optomed Client closed** (the Pictor API allows a single client connection — `aurora_probe` counts too, so if you just ran it, give the camera a few seconds to free the slot before launching the demo). NOTE: the Aurora sleeps its screen after 2 minutes idle and powers off after 10 minutes idle (fixed timers, no software keep-alive); keep it off the charging cradle, keep gaps under ~10 minutes, and press a physical control button (the shutter counts) to wake it — see `CLAUDE.md` §7 for the bench finding. The system-cam preview uses the full `opencv-python` wheel, and locks the camera to a fixed manual focus (`autofocus: false` / `focus: 600` in `system_camera_config.yaml`) so the Aurora screen stays sharp instead of autofocus hunting — this requires `backend: dshow` (only DSHOW drives this camera's focus motor); the value was found with `usb_camera_focus_probe.py`. Pulled fundus images + their JSON sidecars and any system-cam recordings save under `media_outputs/` (git-ignored — never commit medical images).

### 5. Diabetic-retinopathy grading (optional)

Grade the severity of diabetic retinopathy (0 = No DR through 4 = Proliferative) on captured fundus photos using a **RETFound** Vision Transformer (ViT-L/16) fine-tuned on the APTOS2019 dataset. This runs **fully locally and offline** — no image or result leaves the machine. It is a research/educational tool, **not a medical device, and not for clinical diagnosis**.

Full procedure is in [`scripts/fundus_analysis/README.md`](scripts/fundus_analysis/README.md). The flow is a one-time weight export, an optional accuracy validation, then grading:

```powershell
uv run python scripts/fundus_analysis/export_weights.py     # one-time: slim weights -> models/ (~1.2 GB, git-ignored)
uv run python scripts/fundus_analysis/aptos_eval.py         # optional: validate accuracy on the APTOS test split
uv run arm101-dr-grade                                       # grade media_outputs/fundus_images/*.JPG
```

`arm101-dr-grade` writes a `<image>.dr.json` sidecar (predicted grade, per-class probabilities, confidence band, crop region, model hash) to `media_outputs/fundus_analysis/` and prints a summary table. Re-runs skip already-graded images unless you pass `--force`. The slim weights come from the read-only checkpoint under `references/AIML-models/APTOS2019/` (IL-2); the export never modifies it. Credit/source for the downloaded APTOS2019 checkpoint: [rmaphoh/RETFound `BENCHMARK.md`](https://github.com/rmaphoh/RETFound/blob/main/BENCHMARK.md).

You can also grade **inline during capture** instead of running the batch CLI afterward:

```powershell
uv run python scripts/demos/grab_trigger_capture_analysis.py
```

This runs the same robot-triggered capture as `grab_trigger_capture` but, per patient turn, grades every captured shot when you press `g` and shows a combined results panel (grade, label, confidence, per-class probabilities, disclaimer) beside the most-severe image in the popup — writing the same `<image>.dr.json` sidecars as `arm101-dr-grade`. If the slim weights are missing it still captures and pops up images, with the panel noting grading is unavailable. Pass `--no-grade` to run capture-only; `inline_grading` and `captures_per_patient` in `src/arm101_hand/data/fundus_analysis_config.yaml` set the defaults.

Finally, the capture can fire **automatically** instead of on SPACE:

```powershell
uv run python scripts/demos/grab_auto_trigger_analysis.py
```

This is the analysis demo plus a computer-vision auto-trigger: the arm-mounted USB camera watches the Optomed Aurora's two on-screen alignment arcs (which are **red** when misaligned), and the hand auto-presses the shutter — no keypress — after both arcs go red (misaligned) and then go **not-red** (aligned) and hold stable. Press `m` to arm AUTO (it starts in MANUAL, SPACE-only); each shot re-requires a fresh red → not-red transition, so re-aligning captures another shot of the same patient; `g` grades them all; `n` advances to the next patient. Detection is local, red-only HSV color masking on a fixed deskewed region of each arc — the arc bands, red color band, threshold, and timing live in the `auto_trigger` block of `src/arm101_hand/data/system_camera_config.yaml` and are tuned against your screen with `calibrate_view.py` (design in [`docs/superpowers/specs/2026-06-23-system-camera-deskew-circle-redgate-design.md`](docs/superpowers/specs/2026-06-23-system-camera-deskew-circle-redgate-design.md)).

## Repo layout

```
src/arm101_hand/        # device + application layer (subclass + console scripts + fundus_analysis DR grader)
scripts/calibration/    # AmazingHand + SO-ARM101 calibration runners + system_camera/ view calibration (calibrate_view)
scripts/diagnostics/    # motors/ (scan / show_calib / find_port) + fundus_camera/ (aurora_probe / aurora_wiredump) + system_camera/ (usb_camera_probe / usb_camera_capture / usb_camera_roi_preview / usb_camera_focus_probe / usb_camera_arc_debug)
scripts/demos/          # runnable demos (grab_sequence: staged grab; grab_toggle: + index-finger toggle; grab_trigger_capture: live system-cam window + Aurora shutter press + auto-pull; grab_trigger_capture_analysis: + inline DR grading + results panel; grab_auto_trigger_analysis: + CV auto-trigger on the green alignment arcs)
scripts/fundus_analysis/ # DR-grading ops: export_weights (slim weight export) + aptos_eval (validation)
models/                 # git-ignored slim model weights exported from references/ checkpoints
docs/BOM.md             # bill of materials, host PC spec
docs/conventions/       # 00 Iron Laws → 07 KISS
references/             # vendored git submodules — read-only (IL-2)
```

Layering rationale: [`docs/conventions/01-module-layering.md`](docs/conventions/01-module-layering.md).

## Development checks

Before pushing, run the same gate CI enforces on every push and pull request:

```powershell
uv run ruff format .        # CI runs `ruff format --check .`
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on Ubuntu + Python 3.12 and initializes only the `lerobot` submodule. Hardware-gated tests (`-m hardware`) never run in CI — they need the physical buses.

## Conventions

Project-wide rules are in [`docs/conventions/`](docs/conventions/README.md). Read `00-iron-laws.md` first — everything else is an application of those.

## License

This repository's own code is unlicensed (default copyright). The submodules under `references/` carry their own licenses (Apache-2.0, MIT, BSD variants — see each submodule's `LICENSE`).
