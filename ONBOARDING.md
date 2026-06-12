# Team Onboarding — AmazingHand + SO-ARM101 Follower

This guide takes a senior engineer from a bare Windows 10/11 PC to a calibrated, running arm + hand + camera + DR-grading setup. Follow it top to bottom. Every claim here is grounded in the repo's own docs; where something is planned or not yet implemented, it says so.

Audience: an engineer with the physical hardware (see [docs/BOM.md](docs/BOM.md)) who wants to run the calibration, diagnostic, and demo scripts on their own machine.

---

## 1. What this project is

A unified Python workspace that calibrates and controls two robotic devices in one process: a LeRobot SO-ARM101 follower arm (5 Feetech STS3215 servos, the gripper physically removed) with a Pollen Robotics AmazingHand bionic hand (8 Feetech SCS0009 servos) mounted where the gripper was. The arm side subclasses LeRobot's `SOFollower` to skip the absent gripper motor; the hand side drives `rustypot` directly. Optional add-ons: an Optomed Aurora fundus camera (robot-triggered capture over Wi-Fi), an arm-mounted USB system camera, and local diabetic-retinopathy (DR) grading inference. This is research/educational tooling, not a product and not a medical device.

### SAFETY FIRST — IL-1 voltage isolation (read before powering anything)

> WARNING: The 5 V hand bus and the 12 V arm bus must NEVER share power rails. Connecting a 5 V SCS0009 servo to the 12 V rail destroys it instantly. This is destructive-if-wrong, so it comes before everything else.

- Hand bus: 5 V (plus or minus 0.25 V), 8 x Feetech SCS0009, Seeed XIAO bus servo driver. Dedicated 5 V / at least 3 A supply.
- Arm bus: 12 V (plus or minus 0.6 V), 5 x Feetech STS3215, Waveshare bus servo adapter (A). Dedicated 12 V / at least 3 A supply.
- Use two separate PSUs and two separate cables. Label each PSU on the bench. Route the cables so they cannot be swapped under fatigue.
- The Seeed XIAO driver's front jumper MUST be removed to expose UART directly; otherwise the bus is back-driven through the on-board regulator.
- Logic side of both USB-TTL adapters is 3.3 V.

Full rule and current budgets: [docs/conventions/00-iron-laws.md](docs/conventions/00-iron-laws.md) IL-1 and [docs/BOM.md](docs/BOM.md).

---

## 2. Prerequisites

- Windows 10 or 11. Developed on Windows 11 Home, build 22635.
- Python 3.12. The version is pinned in [.python-version](.python-version). The python.org installer or the Microsoft Store build both work.
- The uv package manager. Install per [https://github.com/astral-sh/uv](https://github.com/astral-sh/uv). uv provisions the virtualenv and runs every command.
- git for Windows.
- The physical hardware. Do not restate the table here; see [docs/BOM.md](docs/BOM.md) for the full bill of materials, per-bus current budgets, and the reference host PC spec.

NOTE: There is no discrete GPU on the reference host. ViT-L DR inference runs on CPU (roughly 2 to 5 seconds per image). That is expected and fine for inference.

---

## 3. Clone and submodules (read this carefully)

> WARNING: Do NOT run `git clone --recurse-submodules`. It will FAIL for teammates. The submodule `references/optomed-camera/nous_vclient` points to a PRIVATE repo (`github.com/kim7tin/nous_vclient`) that most teammates cannot access, and `.gitmodules` also lists many large reference submodules (OpenCV, MediaPipe, RETFound, and more) you do not need to get started.

Clone normally, then initialize only what you actually need.

```powershell
git clone <this-repo-url>
cd AmazingHand-ARM101-Follower
```

### 3.1 Required: the lerobot submodule

`uv sync` builds lerobot as an editable path dependency (`pyproject.toml`: `lerobot = { path = "references/lerobot", editable = true }`). Without this submodule present, `uv sync` fails. Initialize just it:

```powershell
git submodule update --init --depth 1 references/lerobot
```

This is exactly what CI does (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).

### 3.2 Optional reference submodules

Every other entry in [.gitmodules](.gitmodules) is read-only reference material (IL-2) and is not needed to run the calibration, diagnostic, or demo scripts. Initialize one only if you specifically want to read it, for example:

```powershell
git submodule update --init references/AmazingHand
git submodule update --init references/rustypot
```

### 3.3 Private submodule: nous_vclient

`references/optomed-camera/nous_vclient` is a PRIVATE repo. If you need the Optomed Aurora client source as reference, request access from the repo owner, then init it explicitly. If you cannot get access, skip it — the Aurora capture code that ships in `src/arm101_hand/fundus_camera/` does not require this submodule to be checked out.

### 3.4 DR-grading checkpoint (NOT a submodule)

The DR-grading model checkpoint lives at `references/AIML-models/APTOS2019/checkpoint-best.pth`. This path is NOT a git submodule and the checkpoint is git-ignored — it will not arrive with any clone or `git submodule update`. You must obtain the `APTOS2019` directory (checkpoint plus the AutoMorph-preprocessed test split) out-of-band from a teammate or shared storage, and place it at `references/AIML-models/APTOS2019/`. Provenance is documented in [scripts/fundus_analysis/README.md](scripts/fundus_analysis/README.md).

You only need this if you intend to run DR grading (section 10). The arm, hand, and camera scripts do not need it.

---

## 4. Environment setup

From the repo root, with at least the lerobot submodule initialized:

```powershell
uv sync
```

This provisions `.venv/` from [pyproject.toml](pyproject.toml): `lerobot[feetech]` (editable from the submodule), the `rustypot` wheel, numpy, pyyaml, pydantic, `opencv-python` (full HighGUI build), and `timm`. Roughly 60 packages install, including `torch` — the first sync takes a few minutes.

NOTE: You do NOT need to activate the venv. Prefix every command with `uv run`. Add dev tooling (ruff, mypy, pytest, scikit-learn) with `uv sync --extra dev`.

The `pyproject.toml` `[tool.uv] override-dependencies` line intentionally drops lerobot's `opencv-python-headless` pin so the full `opencv-python` wheel is the sole `cv2` provider (the live camera preview needs a HighGUI window). Do not remove that override.

---

## 5. Discover COM ports and set operator config

Each USB-TTL bridge enumerates as a Windows COM port. Find them:

```powershell
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
```

On the reference host the AmazingHand CH343 bridge is `COM18`; the SO-ARM101 bridge gets a separate number. Map each bridge to its COM number by plugging one in at a time. LeRobot's interactive helper also works (unplug, Enter, replug, Enter — the diff is your port):

```powershell
uv run python scripts/diagnostics/motors/find_port.py
```

Operator config (COM ports, safety thresholds, motion tuning, named poses) lives in two in-tree YAML files (IL-5):

- Arm: `src/arm101_hand/data/arm_config.yaml` (`arm.port` and friends).
- Hand: `src/arm101_hand/data/hand_config.yaml` (`connection.port`, baudrate, timeout).

Edit the `port` fields to match your discovered COM numbers before running motor scripts.

---

## 6. AmazingHand calibration

Five ordered steps. Step 1 is a one-time ID burn done with the Feetech Windows debug tool FD.exe (`references/FeetechServo/.../FD.exe`); every SCS0009 ships as ID 1, so each servo is programmed individually to its canonical ID (1 to 8, odd-right / even-left per finger — IL-3). Mis-assigning odd/even inverts a finger's Close/Open and calibration cannot rescue it.

After the ID burn, the Python flow (run each with `uv run python`):

```powershell
uv run python scripts/calibration/amazing_hand/motor_reset.py    # drive each finger to 0 deg, then fit horns
uv run python scripts/calibration/amazing_hand/middle_calib.py   # dial in per-servo middle offsets
uv run python scripts/calibration/amazing_hand/range_calib.py    # measure per-finger base/side motion limits
uv run python scripts/calibration/amazing_hand/finger_test.py    # per-finger audit through the full envelope
uv run python scripts/calibration/amazing_hand/full_hand_test.py # end-to-end fist -> open -> per-finger demo
```

All hand calibration state is measurement-only and lives in `scripts/calibration/amazing_hand/hand_calib_values.yaml` (v3 schema). Connection and motion settings stay in `hand_config.yaml`, not in the calib file.

NOTE: `range_calib.py` is Windows-only (uses `msvcrt` raw arrow-key reads) — run it in a real PowerShell or cmd console, not Git Bash, WSL, or an IDE run pane.

Full depth, including the FD.exe ID-burn procedure, key controls, and a troubleshooting table, is in [scripts/calibration/amazing_hand/README.md](scripts/calibration/amazing_hand/README.md).

---

## 7. SO-ARM101 follower calibration

> WARNING: Use `arm101-calibrate-follower`, NOT `lerobot-calibrate`. The upstream entry point does not know our `so101_follower_no_gripper` subclass exists and will fail with an unknown-robot-type error.

```powershell
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower
```

Replace `COM<X>` with the arm's COM number from section 5. The calibration JSON lands in-tree at `scripts/calibration/so_arm101/<id>.json` (for the example above, `so101_follower.json`). The `SO101FollowerNoGripperConfig` subclass defaults `calibration_dir` to that path, so the upstream `~/.cache/huggingface/lerobot/...` location is never used (IL-5). The arm has motor IDs 1 to 5 (shoulder to wrist); ID 6 is physically absent (IL-3).

Motion and verify helpers (none of which write the calibration JSON):

```powershell
uv run python scripts/calibration/so_arm101/sweep.py <joint|all>   # range-verify sweep to endpoints
uv run python scripts/calibration/so_arm101/set_pose.py home        # drive to a named pose and hold
uv run python scripts/calibration/so_arm101/jog.py                  # keyboard-jog joints; save named poses
uv run python scripts/calibration/so_arm101/capture_pose.py         # hand-pose the arm, capture present degrees
```

NOTE: No script auto-returns the arm to home on exit — that would be a surprise movement. On quit they prompt: type `h` to return home first, or Enter to release torque in place.

Full depth and per-script detail: [scripts/calibration/so_arm101/README.md](scripts/calibration/so_arm101/README.md).

---

## 8. Diagnostics

Read-only or pre-flight checks. Close any other COM-port holder first (IL-4).

```powershell
uv run python scripts/diagnostics/motors/scan.py --device arm        # ping arm motors 1-5 (or --device hand)
uv run python scripts/diagnostics/motors/show_calib.py --device arm  # dump calibration (--live adds present pos)
uv run python scripts/diagnostics/fundus_camera/aurora_probe.py      # read-only Aurora reachability + filelist
uv run python scripts/diagnostics/system_camera/usb_camera_probe.py  # USB system-cam smoke test (live window + 'r')
```

Recommended motor order: `scan` (all motors respond?) then `show_calib` (numbers sane?) before any motion script.

---

## 9. Demos

Each reads calibration and config; none write either. Run with `uv run python`.

- `scripts/demos/grab_sequence.py` — staged arm + hand grab; 'h' on exit reverses the whole sequence.
- `scripts/demos/grab_toggle.py` — grab, then SPACE toggles the index finger in and out like a button.
- `scripts/demos/grab_trigger_capture.py` — live ROI-zoomed system-cam window ('r' records); SPACE presses the Aurora shutter and auto-pulls the fundus image to `media_outputs/`.
- `scripts/demos/grab_trigger_capture_analysis.py` — same as above plus inline DR grading: 'g' grades the patient turn and shows a combined results panel beside the image. `--no-grade` runs capture-only.

---

## 10. Optional: Optomed Aurora camera and DR grading

### 10.1 Aurora camera prerequisites

The Pictor Wi-Fi API cannot set these — configure them on the device, with the Aurora on the same Wi-Fi as the host:

- Still imaging mode.
- Quick imaging ON.
- Optomed Client closed. The Pictor API allows a single client connection at a time; `aurora_probe.py` counts as a client, so after running it, give the camera a few seconds to free the slot before launching a demo.

Pulled fundus images and recordings land under `media_outputs/` (git-ignored — never commit medical images).

### 10.2 DR grading flow

> WARNING: DR grading is a research/educational tool only. It is NOT a medical device and NOT for clinical diagnosis. The model was fine-tuned on APTOS2019 images; Optomed Aurora handheld captures differ in camera, field of view, and illumination, so real-capture accuracy can fall below the validation numbers.

Prerequisite: the APTOS2019 checkpoint directory is in place (section 3.4).

```powershell
uv run python scripts/fundus_analysis/export_weights.py   # one-time: slim weights -> models/ (~1.2 GB, git-ignored)
uv run python scripts/fundus_analysis/aptos_eval.py        # optional: validate on the APTOS test split
uv run arm101-dr-grade                                      # grade media_outputs/fundus_images/*.JPG
```

`export_weights.py` reads the read-only `checkpoint-best.pth` (IL-2, never modified) and writes `models/retfound_aptos2019_vitl16.safetensors` (about 1.21 GB, git-ignored). `arm101-dr-grade` writes `<image>.dr.json` sidecars to `media_outputs/fundus_analysis/` and prints a summary table; re-runs skip already-graded images unless you pass `--force`.

Model and provenance: RETFound MAE ViT-L/16 (`vit_large_patch16_224`, `global_pool=avg`, `input_size=224`, 5 classes, best epoch 27). The downloaded APTOS2019 checkpoint is credited to the RETFound benchmark checkpoint listing: [rmaphoh/RETFound `BENCHMARK.md`](https://github.com/rmaphoh/RETFound/blob/main/BENCHMARK.md). Validated test-split accuracy 0.8373, quadratic-weighted kappa 0.9062. Labels: 0 No DR, 1 Mild, 2 Moderate, 3 Severe, 4 Proliferative. Full metrics and run order: [scripts/fundus_analysis/README.md](scripts/fundus_analysis/README.md).

---

## 11. Development workflow

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on Ubuntu + Python 3.12, initializes only the lerobot submodule, then runs four checks. Run the same four locally before pushing:

```powershell
uv run ruff format .              # CI runs `ruff format --check .`
uv run ruff check .
uv run mypy src
uv run pytest -m 'not hardware'
```

Hardware-gated tests (`-m hardware`) need the physical buses and never run in CI — run them only at the bench. A second workflow, `update-submodules.yml`, weekly bumps the reference submodules to upstream tips (excluding lerobot and the private nous_vclient).

Read the conventions before contributing, starting with the Iron Laws — everything else is an application of them:

- [docs/conventions/00-iron-laws.md](docs/conventions/00-iron-laws.md) — read first.
- [docs/conventions/](docs/conventions/) — module layering, code style, testing, git workflow, documentation protocol, KISS.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `could not open port 'COM<X>'` or silent stall | Another process holds the bus (IL-4) | Close FD.exe, serial monitors, stale Python sessions; confirm the COM number |
| Wrong COM number | Bridge enumerated differently | Re-run the PnP query or find_port.py; update the port in arm_config.yaml / hand_config.yaml |
| Aurora "single client" / connection refused | Optomed Client open, or a probe still holds the slot | Close Optomed Client; wait a few seconds after aurora_probe.py before launching a demo |
| `uv sync` fails on lerobot build | lerobot submodule not initialized | Run `git submodule update --init --depth 1 references/lerobot` then re-sync |
| DR grading: weights not found | export_weights.py not run, or checkpoint missing | Place APTOS2019 dir (section 3.4), then run export_weights.py |
| Submodule clone 404 / access denied | Private nous_vclient, or recursive clone of all submodules | Clone normally; init only what you need; request access for nous_vclient if required |
| `KeyError: 'so101_follower_no_gripper'` | Ran lerobot-calibrate instead of the wrapper | Use `uv run arm101-calibrate-follower ...` |
| `ModuleNotFoundError: No module named 'yaml'` | Bare `python` instead of `uv run python` | Prefix with `uv run`; the package is pyyaml, the import is yaml |
| range_calib.py keys do nothing | Run in Git Bash / WSL / IDE pane (no raw key forwarding) | Run in a real PowerShell or cmd console |
| One servo of a pair unresponsive, or finger inverted | Duplicate or swapped motor ID | Re-burn IDs with FD.exe (hand) or check arm IDs 1-5; see calibration READMEs |
