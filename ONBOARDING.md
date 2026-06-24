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
- `scripts/demos/grab_auto_trigger_analysis.py` — computer-vision AUTO variant: watches the Aurora's two alignment arcs (red when misaligned), fires after both arcs go red then clear (aligned) and hold stable, pulls the fundus image, and optionally grades the patient turn. It starts in MANUAL; press `m` to arm AUTO.

---

## 10. Optional: Optomed Aurora camera and DR grading

### 10.1 Aurora camera prerequisites

The Pictor Wi-Fi API cannot set these — configure them on the device, with the Aurora on the same Wi-Fi as the host:

- Still imaging mode.
- Quick imaging ON.
- Optomed Client closed. The Pictor API allows a single client connection at a time; `aurora_probe.py` counts as a client, so after running it, give the camera a few seconds to free the slot before launching a demo.

Pulled fundus images and recordings land under `media_outputs/` (git-ignored — never commit medical images).

### 10.2 How computer vision provides AUTO trigger

The one-sentence mental model is:

> The arm-mounted USB camera is the robot's eyes: it watches the Aurora's alignment arcs, computer vision decides when they indicate correct alignment, the robot's index finger physically presses the shutter, and the actual retinal JPEG is then downloaded from the Aurora over Wi-Fi.

#### Two cameras, with two different jobs

Do not confuse the arm-mounted USB **observation camera** with the Optomed Aurora **fundus camera**. The integration uses both, but their images have different purposes:

```text
VISUAL DECISION PATH

Aurora screen ──filmed by──> USB observation camera ──frames──> OpenCV
 alignment arcs                                                │
                                                               └──> aligned / not

PHYSICAL CAPTURE AND IMAGE PATH

ready ──> robot index finger presses Aurora shutter ──> Aurora takes retinal photo
                                                               │
                                                               └──Wi-Fi──> saved JPEG
```

The USB frame is used only to decide **when to press**. It is not saved as the diagnostic retinal image. The actual fundus JPEG comes from the Aurora through `src/arm101_hand/fundus_camera/`.

#### Component map

The integration is intentionally divided into small parts:

| Component | Responsibility |
|---|---|
| `scripts/demos/grab_auto_trigger_analysis.py` | Orchestrates keys, patients, vision decisions, the physical press, Aurora download, display, and grading |
| `system_camera/preview.py` | Owns the USB camera and OpenCV windows on a background thread; supplies the newest clean frame |
| `system_camera/roi.py` | Crops a fixed rectangle around the Aurora screen and scales coordinates to the actual frame size |
| `system_camera/arc_detector.py` | Classifies each arc as `RED` or not-red by LAB a* coverage; returns per-arc coverage plus `both_red`/`both_clear` |
| `system_camera/auto_trigger.py` | Debounces the result: wait for both arcs red (gate), then both clear, stabilize, fire once, cool down, re-require red |
| `data/system_camera_config.yaml` | Stores the camera, crop, a* cutoff, timing, and re-arm tuning |
| `fundus_camera/` | Lists and downloads the new retinal image from the Aurora after the shutter press |

The detector and trigger lifecycle do not own the camera, robot, keyboard, or filesystem. They accept values and return values, which keeps the computer-vision decision logic testable without hardware.

#### End-to-end flow

Once the staged grab has placed the arm and hand around the Aurora, the AUTO path is:

```text
USB camera captures a room-scale BGR frame
              │
              ▼
Crop the fixed Aurora-screen rectangle
              │
              ▼
Resize that screen crop to a normalized 640 x 480 frame
              │
              ▼
Inspect only the left-arc and right-arc rectangles
              │
              ▼
Convert BGR pixels to LAB and compute each arc's a* red coverage
              │
              ▼
AlignmentState(left_red, right_red, coverage values)
              │
              ▼
Auto-trigger gate: both arcs red, then both clear held stable
              │
              ▼
One-tick FIRE signal
              │
              ▼
Snapshot Aurora file list → press → hold → release
              │
              ▼
Poll file list → identify new file → download and validate JPEG
              │
              ▼
Save + display + add to current patient turn → optionally grade with `g`
```

#### 1. Screen crop and coordinate systems

The raw USB image contains the robot, room, camera body, and Aurora screen. Most of that is irrelevant to alignment detection. The live screen crop is `screen_roi` in `system_camera_config.yaml` — a **deskewed 4:3 crop** carrying a rotation `angle` so a slightly tilted screen comes out upright; `AURORA_SCREEN_ROI` in `system_camera/roi.py` is only the fallback default:

```python
Roi(x=60, y=75, w=196, h=147, ref_w=640, ref_h=480)  # roi.py fallback; the live value lives in config
```

The preview thread crops that region (rotating it upright when `angle` is non-zero) and resizes it to the 640 x 480 detection reference. This is a digital zoom onto the screen:

```text
Raw USB frame                         Normalized vision frame
┌──────────────────────────┐          ┌──────────────────────────┐
│ room                     │          │                          │
│    ┌────────────┐        │  crop +  │   enlarged Aurora screen│
│    │ Aurora     │        │  resize  │   ( retinal view )      │
│    │ screen     │        │ ───────> │   left arc   right arc  │
│    └────────────┘        │          │                          │
└──────────────────────────┘          └──────────────────────────┘
```

The `left_arc` and `right_arc` coordinates in `system_camera_config.yaml` are measured inside this **normalized 640 x 480 screen frame**, not inside the raw USB frame. `Roi.for_frame()` rescales and clamps rectangles if the incoming resolution changes.

The two arc boxes are exact tuning values: they live in the `auto_trigger` block of `system_camera_config.yaml` and are re-derived (dragged by hand on the deskewed ROI) by `calibrate_view.py`, so they are not pinned here.

Use `scripts/diagnostics/system_camera/usb_camera_roi_preview.py` when the physical camera mount or screen framing changes. Validate the outer screen crop first; tune the inner arc rectangles only after the screen crop is stable.

#### 2. How a frame becomes RED or not-red

OpenCV supplies the normalized image in BGR format. `arc_detector.detect()` converts it once to LAB color space and reads the **a\*** channel:

```text
L  = lightness
a* = position on the green (low) <-> red (high) axis; 128 is neutral
b* = position on the blue <-> yellow axis
```

LAB a* is used because the alignment arcs wash toward white when the patient's eye is at the cup and the fundus is brightly lit. In HSV the saturation of those pale arcs collapses toward zero, so no red HSV band can separate a pale-red arc from blown-out white. The a* axis stays positive for even a faint red tint, so a washed arc is still detected. Detection is red-only: the aligned screen reads greenish and unreliable, so only red is thresholded.

For each arc rectangle the detector does one thing:

```text
Crop arc rectangle
       │
       ▼
a* mask: pixels with a* >= a_star_min ──despeckle──> red coverage fraction
       │
       ▼
coverage >= coverage_threshold ? ──yes──> RED
       │ no
       ▼
                                          not-red
```

A mask is a black-and-white image: white pixels have `a* >= a_star_min` (the red-green cutoff: 128 is neutral, higher is redder) and black pixels do not. A small morphological opening removes isolated specks caused by display noise, reflections, or compression. Coverage is then:

```text
matching pixels / all pixels in that arc rectangle
```

The default `a_star_min` is `134` and `coverage_threshold` is about `0.011` (just over 1%). The threshold is deliberately much lower than 50% because an alignment arc is a thin curve occupying only a small part of its bounding rectangle. Both values are re-derived per calibration by the sweep in `calibrate_view.py`, not hand-tuned.

The result is an immutable `AlignmentState` carrying the two per-arc verdicts (`left_red`, `right_red`), the two measured coverage fractions (`left_cov`, `right_cov`), and two convenience properties the gate uses: `both_red` (both arcs RED — misaligned) and `both_clear` (neither arc RED — aligned).

#### 3. Why one frame does not immediately fire

A single frame might be glare, motion blur, a display transition, or one noisy reading. The trigger also must not fire on a screen that is *already* aligned (or blank), or it would snap a useless photo the moment AUTO is armed. So `auto_trigger.update()` gates each capture on a full **misaligned -> aligned transition** and treats perception and triggering as separate decisions:

```text
            both arcs RED (the gate)
WAIT_RED ──────────────────────────────> WAIT_CLEAR
                                              │
                              both arcs not-red (aligned)
                                              ▼
                                         STABILIZING
                                              │
                          both-clear held for stable_seconds
                                              ▼
                                       FIRE + COOLDOWN
                                              │
                                  cooldown_seconds elapses
                                              ▼
                                          WAIT_RED
```

- `WAIT_RED`: armed; waiting to see **both arcs RED** (misaligned). This is the per-capture gate — a blank, menu, or already-aligned screen has no red and never passes it, so AUTO cannot false-fire.
- `WAIT_CLEAR`: the gate passed; waiting for **both arcs not-red** (aligned).
- `STABILIZING`: both arcs are clear, but the clear state must hold continuously for `stable_seconds` (currently 1.0 second). If either arc goes red again, it drops back to `WAIT_CLEAR`.
- `COOLDOWN`: fired exactly once; another fire is prohibited for `cooldown_seconds` (currently 3.0 seconds), after which it returns to `WAIT_RED` and re-requires a fresh red gate before the next shot.

The state machine returns `should_fire=True` for exactly one update. It does not leave a persistent "firing" state for the main loop to repeatedly observe.

At the configured `detect_interval_s: 0.2`, a typical successful timeline is:

```text
0.0 s  L:RED R:RED   WAIT_RED -> WAIT_CLEAR (gate passed)
0.2 s  L:clr R:RED   WAIT_CLEAR (not both clear yet)
0.4 s  L:clr R:clr   enter STABILIZING; remember start time
0.6 s  L:clr R:clr   continue waiting
0.8 s  L:clr R:clr   continue waiting
1.0 s  L:clr R:clr   continue waiting
1.4 s  L:clr R:clr   FIRE once; enter COOLDOWN
...                  physical press and image download
later  L:clr R:clr   COOLDOWN; cannot fire again
later  L:RED R:RED   cooldown done -> WAIT_RED gate re-armed for the next shot
```

#### 4. How the demo connects vision to physical capture

The integration point in the demo's main loop is intentionally small:

```python
frame = preview.latest_frame()
alignment = detect(frame, scfg.auto_trigger)
state_auto, fire = auto_trigger.update(state_auto, alignment, now, scfg.auto_trigger)
if fire:
    _fire_capture_cycle()
```

MANUAL and AUTO both call the same `_fire_capture_cycle()`:

```text
MANUAL: SPACE key ──┐
                    ├──> _fire_capture_cycle()
AUTO: vision FIRE ──┘
```

The cycle first snapshots the Aurora's current file names. It then drives the index finger onto the physical shutter, holds for the configured dwell time, reads servo loads for a pressure warning, and releases the finger. After release it polls the Aurora file list until a stable new entry appears, downloads that file over Wi-Fi, verifies the JPEG signature and byte count, saves the image and metadata, displays it, and adds it to the current patient turn.

The capture cycle is synchronous: vision-state updates pause while the finger and Aurora transaction run. The USB camera and OpenCV window remain responsive because `WebcamPreview` owns them on a separate daemon thread. When the cycle returns, the main loop resumes detection; the state machine is already in `COOLDOWN` and advances back to `WAIT_RED` according to monotonic elapsed time.

#### 5. AUTO mode and patient-level arming are separate gates

Two controls must both allow automatic capture:

```text
mode == AUTO  AND  armed == true  AND  preview exists
```

- The demo starts in `MANUAL`. Press `m` to enter AUTO and create a fresh `WAIT_RED` state. SPACE is deliberately disabled in AUTO so two trigger sources cannot compete.
- After `g` grades the current patient's accumulated shots, `armed` becomes false. A lingering aligned screen cannot accidentally create the next patient's first shot.
- Press `n` to begin the next patient, clear the previous turn, and re-arm AUTO.
- If the USB preview camera is unavailable, AUTO cannot obtain frames and the demo falls back to MANUAL.

The intended operator sequence is:

```text
Start demo → press m → arcs red (misaligned) → align (arcs clear) → capture shot
                    → move away (arcs red again) → re-align → capture another shot
                    → press g to grade patient → AUTO HOLD
                    → press n for next patient → AUTO re-armed
```

Run the AUTO demo with:

```powershell
uv run python scripts/demos/grab_auto_trigger_analysis.py
uv run python scripts/demos/grab_auto_trigger_analysis.py --no-grade  # vision + capture only
```

Keep terminal focus for `m`, `g`, `n`, `r`, SPACE, and quit controls; the OpenCV preview window displays status but does not own terminal keyboard input.

#### 6. Tuning and debugging order

Tune from the outside inward. Changing several layers at once makes failures difficult to localize:

1. Confirm the USB camera opens with `usb_camera_probe.py` and that camera index, backend, focus, and exposure produce a sharp screen.
2. Confirm `AURORA_SCREEN_ROI` consistently crops the whole screen with `usb_camera_roi_preview.py`.
3. Confirm the left and right arc rectangles cover the arcs throughout normal alignment motion.
4. Save representative RED (misaligned) and CLEAR (aligned) frames, plus glare and transition frames, before changing the a* cutoff or coverage threshold.
5. Validate color classification before tuning `stable_seconds`, cooldown, or clear-between behavior.
6. Only after vision is reliable, test the physical press and Aurora file download together.

Useful status text in the preview has the form:

```text
AUTO [STABILIZING]  L:clr R:clr
```

This separates three questions during debugging:

- **Can the camera see the screen?** Check preview, focus, and outer ROI.
- **Can the detector classify the arcs?** Check arc rectangles, a* masks, coverage, and `L`/`R` labels.
- **Why did it not fire?** Check the lifecycle phase, stabilization time, cooldown, wait-clear, patient `armed` state, and current mode.

### 10.3 DR grading flow

> WARNING: DR grading is a research/educational tool only. It is NOT a medical device and NOT for clinical diagnosis. The model was fine-tuned on APTOS2019 images; Optomed Aurora handheld captures differ in camera, field of view, and illumination, so real-capture accuracy can fall below the validation numbers.

#### Mental model: the artifact is learned memory, not the whole model

`models/retfound_aptos2019_vitl16.safetensors` is an inference-only copy of
the learned tensor values extracted from the upstream checkpoint. It is not
trained by this repository and it is not a self-contained application.

Think of the two files this way:

```text
Training checkpoint (.pth)                 Inference artifact (.safetensors)

+--------------------------------+          +--------------------------------+
| learned model tensors          |          | learned model tensors          |
| optimizer state                |  export  |                                |
| gradient-scaler state          | -------> |                                |
| epoch and training arguments   |          |                                |
+--------------------------------+          +--------------------------------+
             3.64 GB                                  1.21 GB
```

The `.pth` file is a training suitcase: it contains the model plus the state
needed to continue the original training process. The `.safetensors` file
keeps only the learned model tensors required to make predictions. This is why
the exported artifact is about two-thirds smaller and cannot resume training.

Weights and architecture are separate:

```text
Architecture = what machinery exists
               24 transformer blocks, 16 attention heads,
               1024-dimensional embeddings, and a 5-class head

Weights      = what that machinery learned
               the numerical tensor values stored in safetensors
```

Weights without matching model code are only numbers. Model code without
trained weights is correctly shaped but untrained machinery. Working inference
requires all of the following to agree:

```text
weights
  + exact ViT architecture
  + pooling behavior
  + image preprocessing
  + class ordering
  = reproducible predictions
```

#### Provenance and conversion boundary

The complete artifact path is:

```text
RETFound retinal-image pretraining
              |
              v
ViT-Large / 16 x 16 image patches
              |
              v
Fine-tuning on five-class APTOS2019
              |
              v
references/AIML-models/APTOS2019/checkpoint-best.pth
              |
              | one controlled, CPU-only export
              | retain checkpoint["model"]
              | discard training-only state
              v
296 contiguous model tensors
              |
              | serialize without Python pickle objects
              v
models/retfound_aptos2019_vitl16.safetensors
              |
              | strict-load into the matching model
              v
Local DR inference
```

The original checkpoint remains read-only under `references/` (IL-2). The
derived artifact is written to the git-ignored `models/` directory. Ordinary
grading opens only the tensor-only artifact; it does not repeatedly deserialize
the pickle-based training checkpoint.

The exporter loads the source on CPU with PyTorch's restricted
`weights_only=True` behavior. The checkpoint's known `argparse.Namespace` is
narrowly allowlisted because its saved training arguments require that class.
The code deliberately does not fall back to unrestricted pickle loading.

Before saving, every tensor is made contiguous and cloned:

```python
value.contiguous().clone()
```

This gives each exported tensor a simple independent storage block and avoids
shared-storage or non-contiguous views that are unsuitable for predictable
safetensors serialization.

#### How the matching architecture was established

The architecture was determined from checkpoint arguments and tensor shapes,
not merely inferred from the filename:

| Property | Verified value |
|---|---|
| Model | ViT-Large, patch size 16 |
| Input | 224 x 224 pixels |
| Embedding dimension | 1024 |
| Transformer depth | 24 blocks |
| Attention heads | 16 |
| Output classes | 5 |
| Pooling | Average pooling |
| Classification head | `(5, 1024)` |
| Model tensors | 296 |
| Best upstream epoch | 27 |

The positional embedding has shape `(1, 197, 1024)`, which acts as an
architectural fingerprint:

```text
224-pixel image / 16-pixel patch = 14 patches per side

14 x 14 = 196 image-patch tokens
196 + 1 class token = 197 total tokens
```

Average pooling is significant. The checkpoint contains `fc_norm.*` parameters
and not the `norm.*` parameters produced by token pooling. Constructing the
model with `global_pool="avg"` gives an exact 296-of-296 key match. This choice
reproduces the network that was fine-tuned; it is not a stylistic preference.

At runtime the network is reconstructed as:

```python
timm.create_model(
    "vit_large_patch16_224",
    num_classes=5,
    global_pool="avg",
)
```

The weights are then loaded with `strict=True`. Every expected tensor name and
shape must match, with no missing or unexpected tensors:

```text
artifact tensor name and shape == model slot name and shape
```

The exporter's 296-tensor count is an early sanity check. Strict loading is the
stronger compatibility gate: two incompatible models could contain the same
number of tensors while using different names or shapes.

#### Why preprocessing is part of model correctness

The model expects images in the same numerical form used during RETFound
evaluation. The runtime pipeline is:

```text
Fundus image
     |
     v
For raw Aurora images, isolate the bright circular fundus region
     |
     v
Resize shortest side to 256 with bicubic interpolation
     |
     v
Center-crop to 224 x 224
     |
     v
Convert pixels to a PyTorch tensor
     |
     v
Normalize with ImageNet RGB mean and standard deviation
     |
     v
ViT-L/16 inference
```

The normalization constants are:

```text
mean = (0.485, 0.456, 0.406)
std  = (0.229, 0.224, 0.225)
```

Correct weights with incorrect preprocessing can still produce plausible
numbers, but those numbers no longer represent the behavior that was
validated. For this reason preprocessing is treated as part of the model
contract, not as display cleanup.

Aurora captures commonly include a large near-black border around the retinal
disc. The additional circle crop prevents that irrelevant border from
occupying most of the limited 224 x 224 input. If no plausible disc is found,
the runtime uses a center-square fallback and records that fallback in the
result sidecar.

#### Output labels and result interpretation

The classification head emits five values without embedding human-readable
label names. Their positions follow the alphabetical APTOS `ImageFolder`
directory order:

| Output index | Upstream folder | Meaning |
|---:|---|---|
| 0 | `anodr` | No DR |
| 1 | `bmilddr` | Mild |
| 2 | `cmoderatedr` | Moderate |
| 3 | `dseveredr` | Severe |
| 4 | `eproliferativedr` | Proliferative |

This ordering is a correctness requirement. A model can load successfully and
still report the wrong disease name if output indices are mapped to labels in a
different order.

#### Behavioral validation and traceability

Serialization proves that a file was written. Strict loading proves that its
tensors fit the reconstructed architecture. End-to-end evaluation additionally
checks preprocessing and label interpretation:

```text
1,100 labelled APTOS test images
              |
              v
same preprocessing + same model builder + same safetensors loader
              |
              v
predicted grade compared with known folder label
```

The recorded held-out results are:

- Accuracy: `0.8373`
- Quadratic-weighted kappa: `0.9062`

Quadratic-weighted kappa is useful because DR grades are ordered: an error
between adjacent grades is penalized less than confusing No DR with
Proliferative DR. These measurements support correct pipeline wiring on the
APTOS source domain. They do not establish clinical performance on Aurora
captures.

The known artifact identity is:

```text
Size:    1,213,255,180 bytes
SHA-256: 6ffa4e53ec752186323f0d9f9c4efb9328ff11414f6e57269cd0871f5eb4e2c8
Short:   6ffa4e53
```

Each `.dr.json` result records the checkpoint filename, architecture, and short
hash. A filename can be reused for different bytes; the hash ties a prediction
to the exact artifact that produced it.

The artifact intentionally does not contain Python model code, preprocessing
instructions, label names, provenance, metrics, optimizer state, or clinical
calibration. Those remain explicit, reviewable dependencies in this
repository.

#### Run order

Prerequisite: the APTOS2019 checkpoint directory is in place (section 3.4).

```powershell
uv run python scripts/fundus_analysis/export_weights.py   # one-time: slim weights -> models/ (~1.2 GB, git-ignored)
uv run python scripts/fundus_analysis/aptos_eval.py        # optional: validate on the APTOS test split
uv run arm101-dr-grade                                      # grade media_outputs/fundus_images/*.JPG
```

`export_weights.py` reads the read-only `checkpoint-best.pth` (IL-2, never modified) and writes `models/retfound_aptos2019_vitl16.safetensors` (about 1.21 GB, git-ignored). `arm101-dr-grade` writes `<image>.dr.json` sidecars to `media_outputs/fundus_analysis/` and prints a summary table; re-runs skip already-graded images unless you pass `--force`.

The downloaded APTOS2019 checkpoint is credited to the RETFound benchmark checkpoint listing: [rmaphoh/RETFound `BENCHMARK.md`](https://github.com/rmaphoh/RETFound/blob/main/BENCHMARK.md). Implementation details, metrics, and operational notes are maintained in [scripts/fundus_analysis/README.md](scripts/fundus_analysis/README.md).

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
