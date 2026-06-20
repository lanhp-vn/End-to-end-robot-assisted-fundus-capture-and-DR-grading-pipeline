# Arc Auto-Trigger — CV-driven Aurora capture from on-screen alignment — Design Spec

**Date:** 2026-06-19
**Status:** Approved (brainstorming) — pending implementation plan
**Author:** Claude (brainstorming session)
**Builds on:** `docs/superpowers/specs/2026-06-11-dr-grading-phase2-inline-design.md` (the analysis demo this clones), `grab_trigger_capture` capture cycle, `system_camera` ROI + focus work (committed `ee6e70e`).

---

## 1. Goal

Add an **automated trigger** for the robot-driven Aurora capture: watch the arm-mounted USB camera's view of the Optomed Aurora screen, detect the two on-screen alignment **arcs**, and when they turn **green** (aligned / correct working distance), automatically run the existing capture cycle (hand presses the shutter → Pictor pull) instead of the operator pressing SPACE.

The Aurora shows two arcs (left + right of the circular live view). They are **red** when misaligned and **green** when aligned. Green is the "shoot now" cue.

This stays a **research / educational** tool. It is **not a medical device** and must never be used for clinical diagnosis (the cloned demo keeps the same DR-grading disclaimers).

### Scope

- IN: a pure color-based arc detector; a pure auto-trigger lifecycle state machine; a new `auto_trigger` config block; a way for the live preview to hand the latest ROI frame + a status overlay to the caller; a **new demo** cloned from `grab_trigger_capture_analysis.py` with a manual ⇄ auto mode toggle; host unit tests for the pure pieces.
- OUT: the 2K single-stream camera simplification (separate optional spec); any change to the capture/pull protocol, the arm/hand motion code, the DR-grading math, `pyproject.toml` deps, or `references/`. The two existing demos (`grab_trigger_capture.py`, `grab_trigger_capture_analysis.py`) are left **untouched** — this is additive.

### Key decisions (from brainstorming)

1. Ready signal = **green present** (positive), not merely "red gone" — robust against a blank/off/obscured screen.
2. Ready rule = **either** arc band reads green (one arc can read faint, as seen in the sample frames). An optional `require_no_red` knob (default off) can tighten this later.
3. Re-arm = after a capture, require the arcs to return to **red** before the next auto-fire, plus a cooldown.
4. Config home = `system_camera_config.yaml` (it is all arm-cam-driven).
5. Delivery = a **new script cloned from the analysis demo** (keeps `g` DR grading), not a modification of the existing demos.

---

## 2. CV approach (settled by reference research)

Deep-dive into the vendored OpenCV submodules (`references/computer-vision/opencv`, `opencv_contrib`, `opencv-python`) concluded: **HSV color masking per arc-region + pixel counting; no shape detection.** Citations:

- HSV `cvtColor(BGR→HSV)` + `inRange` color masking — pattern in `references/computer-vision/opencv/samples/python/camshift.py:81`; HSV/Lab reference `references/computer-vision/opencv/modules/imgproc/doc/colors.markdown`.
- **Red wraps hue 0/180** → two `inRange` masks OR'd. Green is one band; keep its upper hue ≤ ~90 so the screen's **blue UI icons** (hue ~105–120) do not leak.
- Light `morphologyEx(MORPH_OPEN)` with a 3×3 `getStructuringElement` (`references/computer-vision/opencv/samples/python/morphology.py`).
- `countNonZero` per region for coverage — ~0.1–1 ms/frame at 640×480.
- **Rejected as over-engineering for fixed-geometry partial arcs:** `HoughCircles` (needs near-full circumference — `references/computer-vision/opencv/modules/imgproc/src/hough.cpp`), `fitEllipse` (needs 5+ non-collinear points — `.../shapedescr.cpp`), contrib `ximgproc.findEllipses`/`EdgeDrawing` (built for complete ellipses in clutter), and any ML.

Sample-frame observations that shape the detector: green reads **teal-ish and can be desaturated/faint**; the centre fundus view has strong **white-blue glare**; the side columns carry blue/white UI icons. Mitigations: (a) classify only inside the **two arc sub-bands** (not the whole ROI) to dodge the centre glare and the UI columns; (b) a **saturation floor** rejects the white glare; (c) green hue ≤ ~90 avoids the blue icons.

---

## 3. Architecture — pure CV + pure lifecycle, thin preview seam

The capture thread is the sole cv2-window owner (`preview.py` threading contract). The detector and the trigger lifecycle are **pure** (no cv2 window, time injected) so host tests exercise them. The only preview change is a thread-safe **frame snapshot** (so the main-thread auto loop can read the latest ROI frame to detect on) plus an optional **status-text overlay** (so the operator sees the detector's verdict on-screen). Detection runs on the **main loop** (via the snapshot), never on the capture thread — so the fire decision, which drives the hand, stays on the same thread as the motion calls.

Both new modules live in `system_camera` (a camera/CV concern; no cross-layer import — `01-module-layering.md §2`). The new demo (application layer) imports them plus the existing capture cycle.

---

## 4. New + modified files

### New (in `arm101_hand`, importable by host tests)

- **`src/arm101_hand/system_camera/arc_detector.py`** — pure color detection:
  - `AlignmentState` (frozen dataclass): `left: str`, `right: str` (each `"RED"|"GREEN"|"NONE"`), `ready: bool`, plus `left_green`/`left_red`/`right_green`/`right_red` coverage fractions for the HUD/debug.
  - `detect(roi_bgr, cfg: AutoTriggerConfig) -> AlignmentState` — one `cvtColor` to HSV, classify each arc band.
  - `classify_band(hsv_band, cfg) -> tuple[str, float, float]` — union the red bands and the green bands via `inRange` + `bitwise_or`, `MORPH_OPEN`, `countNonZero / band_area`; `GREEN` if green-fraction ≥ threshold and ≥ red-fraction, else `RED` if red-fraction ≥ threshold, else `NONE`.
  - Reuses `Roi.crop` for the two band sub-rectangles (regions stored ref-relative, like `AURORA_SCREEN_ROI`).
- **`src/arm101_hand/system_camera/auto_trigger.py`** — pure lifecycle state machine (clock injected):
  - `AutoTriggerState` (dataclass): `phase` (`WAIT_GREEN`/`STABILIZING`/`COOLDOWN`/`WAIT_RED`), `green_since`, `fired_at`.
  - `arm() -> AutoTriggerState` (fresh `WAIT_GREEN`).
  - `update(state, alignment, now, cfg) -> tuple[AutoTriggerState, bool]` — returns the next state and a one-shot `should_fire`.
- **`tests/unit/test_arc_detector.py`**, **`tests/unit/test_auto_trigger.py`** — host tests, synthetic frames + injected clock.

### Modified

- **`src/arm101_hand/system_camera/preview.py`** — add to `WebcamPreview`:
  - `latest_frame() -> np.ndarray | None` — thread-safe copy of the most recent **ROI-cropped** frame (the capture thread stores it under a lock each loop after the ROI resize); `None` before the first frame.
  - `set_status_text(text: str, color: tuple[int,int,int]) -> None` — thread-safe; the capture thread overlays it (top-left, like the existing `REC` overlay) so the operator sees mode + alignment verdict. Display-only, drawn after the clean write.
- **`src/arm101_hand/system_camera/__init__.py`** — export `arc_detector`/`auto_trigger` public names (`AlignmentState`, `detect`, `AutoTriggerState`, `arm`, `update`).
- **`src/arm101_hand/config/system_camera_config.py`** + **`.../data/system_camera_config.yaml`** — `schema_version` 4 → 5; add the `auto_trigger` block (see §7).
- **`tests/unit/test_system_camera_config.py`** — `auto_trigger` defaults, schema 5, yaml loads the block.
- **`CLAUDE.md`** + root **`README.md`** — via the doc_update skill (new demo + diagnostics-tree/tech-debt note).

### New script (application layer)

- **`scripts/demos/grab_auto_trigger_analysis.py`** — cloned from `grab_trigger_capture_analysis.py` (see §5).

### Unchanged

`scripts/demos/grab_trigger_capture.py`, `scripts/demos/grab_trigger_capture_analysis.py`, the capture/pull protocol, `fundus_analysis`, motion code, `pyproject.toml` (cv2 + numpy already present), `references/`.

---

## 5. New demo flow (`grab_auto_trigger_analysis.py`)

Cloned from the analysis demo, so it keeps the Pictor connection, the staged grab (`run_grab_demo`), the per-patient `turn_shots` model, `g` grading + combined panel + sidecars, and the USB preview. Two changes:

### 5a. Manual ⇄ auto mode (the arming)

- Starts in **MANUAL** (no surprise movements — the hand never auto-presses until you deliberately switch).
- `m` toggles **MANUAL ⇄ AUTO**. Entering AUTO is the explicit arm: it calls `auto_trigger.arm()` and prints the armed banner. Leaving AUTO disarms.
- **SPACE stays a manual override in both modes**; `r`, `[`/`]`, `g`, `q`/Ctrl+C behave as in the analysis demo.

### 5b. Non-blocking loop

The analysis demo blocks on `msvcrt.getwch`. The new demo polls non-blockingly (`msvcrt.kbhit`, like `usb_camera_capture.py`) so it can interleave key handling with detection. Each iteration:
1. Drain pending keys (mode toggle, SPACE, `g`, `r`, `[`/`]`, quit).
2. In AUTO, at most every `detect_interval_s`: `frame = preview.latest_frame()`; if not `None`, `alignment = detect(frame, scfg.auto_trigger)`; `state, fire = update(state, alignment, now, scfg.auto_trigger)`; push `set_status_text` (verdict, colored). If `fire`: run the capture cycle.
3. If `preview is None` (camera unavailable): AUTO is unavailable — print once and stay manual (detection needs the live ROI frame).

### 5c. Shared capture cycle

Extract the existing "fire one capture cycle" body (baseline filelist → press/hold/release → pull → validate → save → append to `turn_shots` → `show_still`) into `_fire_capture_cycle(...)`, called by **both** SPACE (manual) and the auto trigger. Identical capture behavior; only the trigger source differs.

### 5d. After an auto capture

On a successful pull the shot is appended to `turn_shots` (as today) and the state machine enters `COOLDOWN` then `WAIT_RED`. Grading stays **manual** (`g`) — the auto trigger automates *capturing*, not grading, matching the brainstormed flow ("image received → ready for next detection"). On quit with un-analyzed shots: print the count, **no** auto-grade, **no** motion.

---

## 6. Lifecycle state machine (`auto_trigger.update`)

Phases and transitions (cfg = `auto_trigger`):

- **WAIT_GREEN** — `alignment.ready` → set `green_since = now`, phase `STABILIZING`.
- **STABILIZING** — not `ready` → back to `WAIT_GREEN` (`green_since=None`); else `now - green_since ≥ stable_seconds` → **fire** (`should_fire=True`), `fired_at = now`, phase `COOLDOWN`.
- **COOLDOWN** — `now - fired_at ≥ cooldown_seconds` → phase `WAIT_RED` if `require_red_between` else `WAIT_GREEN`. (The capture cycle itself blocks for seconds; cooldown is a floor on top of that.)
- **WAIT_RED** — a red observation (`alignment.left == "RED" or alignment.right == "RED"`) → phase `WAIT_GREEN` (re-armed for the next patient/eye).

`ready` is computed in the detector: `(left == GREEN or right == GREEN)` and, if `require_no_red`, also no band red. `should_fire` is True for exactly one tick. Re-entering AUTO mode calls `arm()` (resets to `WAIT_GREEN`).

---

## 7. Config — `auto_trigger` block in `system_camera_config.yaml`

`SystemCameraConfig` (schema_version 5) gains `auto_trigger: AutoTriggerConfig` (nested, `extra="forbid"`, defaults so existing configs still validate). New models:

- `HsvBand(h_lo, s_lo, v_lo, h_hi, s_hi, v_hi: int)` — OpenCV 8-bit HSV (H 0–180, S/V 0–255), each `ge=0`.
- `ArcRegion(x, y, w, h: int, ref_w=640, ref_h=480)` — mirrors `Roi`; converted to `Roi` for cropping.
- `AutoTriggerConfig`:
  - `left_arc: ArcRegion`, `right_arc: ArcRegion` — initial estimates (left ≈ x60,y110,w70,h190; right ≈ x420,y110,w70,h190 @ 640×480), **tuned against the saved red + green frames in commit 1**.
  - `red_bands: list[HsvBand]` (default two: 0–10 and 170–180, S/V floor ~80), `green_bands: list[HsvBand]` (default one: H 40–90, S floor ~40 for faint teal, V floor ~60).
  - `coverage_threshold: float = 0.04` (`ge=0, le=1`) — fraction of band pixels that must match a color.
  - `morph_kernel: int = 3` (`ge=1`).
  - `stable_seconds: float = 1.0` (`gt=0`), `cooldown_seconds: float = 3.0` (`ge=0`), `detect_interval_s: float = 0.2` (`gt=0`).
  - `require_red_between: bool = True`, `require_no_red: bool = False`.

The HSV/region/threshold defaults are tuned against the four committed-as-throwaway sample frames (red + green, both resolutions) during implementation; final values land in the data YAML with comments. IL-5: the block is hand-editable and never written at runtime.

---

## 8. Testing (host, `uv run pytest -m 'not hardware'`, no camera)

`test_arc_detector.py` (synthetic BGR frames — fill the left/right band rectangles with known green/red/white BGR pixels):
- both bands green → `left==right=="GREEN"`, `ready True`.
- both red → both `"RED"`, `ready False`.
- one green + one red → `ready True` (either-green rule); with `require_no_red=True` → `ready False`.
- white-glare fill (low saturation) → `"NONE"`, `ready False` (saturation floor rejects it).
- coverage just below / above `coverage_threshold` flips `NONE`↔color.

`test_auto_trigger.py` (injected `now`):
- green for < `stable_seconds` → no fire; ≥ `stable_seconds` → fires once (one-shot).
- `ready` drops mid-`STABILIZING` → resets, no fire.
- after fire → `COOLDOWN` until `cooldown_seconds`, then `WAIT_RED`; stays un-fireable while green persists until a red is seen → re-arms.
- `require_red_between=False` → returns straight to `WAIT_GREEN` after cooldown.

`test_system_camera_config.py`: `auto_trigger` defaults (regions, bands, timings, flags), `schema_version == 5`, data YAML loads the block.

The real sample frames live under `media_outputs/` (gitignored), so they are tuning aids, not CI fixtures — tests synthesize frames. `latest_frame`/`set_status_text` are thread glue validated by the hardware run, not unit tests. ruff + mypy clean.

---

## 9. Iron-law compliance

- **IL-1 / IL-3 / IL-6** — no bus, motor-ID, or cross-device structural change; the auto trigger reuses the existing index-finger press cycle, arm+hand stay atomic in the cloned demo. N/A otherwise.
- **IL-2** — `references/` read-only: the OpenCV submodules were only *read* for research.
- **IL-4** — single camera owner: detection reads `WebcamPreview.latest_frame()`; it never opens a second capture on the cam.
- **IL-5** — config in-tree (`system_camera_config.yaml`, never written at runtime); captures/sidecars → gitignored `media_outputs/`.
- **IL-7** — the auto-trigger capability is documented once (CLAUDE.md demo + diagnostics tree + tech-debt; README points at it).
- **No surprise movements** — default MANUAL; AUTO is an explicit deliberate toggle; even armed it fires only on a stable green; SPACE stays manual; quit releases torque (inherited from `run_grab_demo`).

---

## 10. Commits (atomic, TDD order)

1. `feat(system_camera): arc_detector — HSV red/green alignment-arc classification` (+ tests, synthetic frames)
2. `feat(system_camera): auto_trigger lifecycle state machine` (+ tests)
3. `feat(config): auto_trigger config block (schema v5)` (+ tests + data YAML; tune HSV/region/threshold defaults against the saved red + green frames)
4. `feat(system_camera): WebcamPreview latest_frame + status-text overlay`
5. `feat(demos): grab_auto_trigger_analysis — auto/manual arc-triggered capture`
6. `docs: refresh CLAUDE.md/README for the arc auto-trigger demo`

---

## 11. Out of scope (YAGNI)

- The 2K single-stream camera simplification (separate optional spec).
- Auto-grading on fire (grading stays manual `g`, as in the analysis demo).
- Shape/ellipse/Hough arc detection, ML, contrib modules (rejected in §2).
- Patient metadata / IDs; a CLI flag for every threshold (YAML edit suffices).
- Driving the 2 modes from anything but the keyboard toggle.
