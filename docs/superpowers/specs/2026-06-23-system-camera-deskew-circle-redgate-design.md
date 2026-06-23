# System-Camera Deskew + Circle-Based Red-Gate Detection — Design

**Date:** 2026-06-23
**Status:** Approved (pending implementation plan)
**Supersedes:** the arc-detection geometry/logic from `2026-06-22-system-camera-view-calibration-design.md` (that feature shipped; this revises its ROI shape, arc-region method, and trigger logic based on bench findings).
**Scope:** One coherent subsystem — the arm-mounted system camera's ROI deskew + alignment-arc detection + auto-trigger lifecycle + the calibration tool that configures them.

---

## 1. Problem (bench findings driving this revision)

The shipped view-calibration (`calibrate_view.py` + `calibration.py` + `arc_detector`/`auto_trigger`) was validated on real Aurora captures and three issues surfaced:

1. **ROI shape/orientation.** The Aurora panel is **4 in TFT-LCD, 800×480 (5:3)**. The ROI used a 4:3 crop, which (a) does not match the screen and (b) distorts the circular camera view into an ellipse. The screen also sits **slightly rotated** (~−0.9° measured) in the camera frame, so axis-aligned crops are skewed.
2. **Arc regions wrong.** The right arc region (found by red-pixel bounding box) swallowed the **red battery icon** in the top-right corner, and the regions were asymmetric. The two alignment arcs are physically **symmetric** (left/right edges of the circular camera view) and **always the same colour at the same time**.
3. **Green detection unreliable.** A bright/aligned screen reads greenish overall (the disc + glare streaks), so green-coverage classification false-fired. The discriminative signal is **red presence**, not green.

The fix re-grounds the geometry on the true 5:3 deskewed screen + the camera circle, and the trigger on a **red→not-red gated** transition.

## 2. Goals / Non-goals

**Goals**
- Crop the screen as an **upright 5:3** region (deskew the small rotation) so the camera view is round and the arc bands are stable.
- Locate the two arc regions as **mirror-symmetric bands on the camera circle's left/right edges**, excluding the corners (battery).
- Trigger logic: **red-only** detection with a **gated fire** — a capture fires only after both arcs are seen **red** (misaligned) and then go **not-red** (aligned), held stable.
- Calibration re-derives everything from **three captures** and validates the red/not-red separation before writing.
- Deskew + new bands + logic apply at **runtime** (the live demo), not just calibration.

**Non-goals**
- No green/white-to-green positive colour band (dropped — only red is thresholded).
- No per-frame circle detection at runtime (the circle is fit once at calibration to place the stored bands; runtime uses the stored bands).
- No change to the focus/resolution/recording config.
- `opencv-contrib` not used (plain `opencv-python` only).

## 3. Detection & firing logic (the core)

**Per-arc classification** (`arc_detector`), in each arc region by HSV red coverage:
- `red` = (red coverage ≥ `coverage_threshold`). Aligned/ready = **not red**. No other colour is thresholded.
- The two arcs are symmetric and always the same colour together, so the gate/fire use **both** arcs.

`AlignmentState` (revised): `left_red: bool`, `right_red: bool`, `both_red: bool` (= left_red and right_red), `both_clear: bool` (= not left_red and not right_red), plus `left_cov: float`, `right_cov: float` for the HUD.

**Auto-trigger lifecycle** (`auto_trigger`, pure + clock-injected). Each capture requires a fresh red→not-red transition:

```
WAIT_RED ──(both_red)──▶ WAIT_CLEAR ──(both_clear)──▶ STABILIZING
   ▲                                                       │
   │                                            held stable_seconds
   └──────────── COOLDOWN ◀──(fire one capture)────────────┘
```
- **WAIT_RED**: armed; the gate. Fires nothing until `both_red` is seen (a blank / menu / already-aligned screen has no red → stays here → no false fire).
- **WAIT_CLEAR**: red gate passed; wait for `both_clear`. (If red flickers back, stay; the gate remains passed.)
- **STABILIZING**: `both_clear` held `stable_seconds` → **fire one capture** → `COOLDOWN`. If red reappears (`not both_clear`) before stable, return to **WAIT_CLEAR**.
- **COOLDOWN**: after `cooldown_seconds` → **WAIT_RED** (the next capture must see red again — natural for "next patient misaligns then aligns").

This removes the old `require_no_red` / `require_clear_between` flags (the gate subsumes them) and the green path.

## 4. ROI deskew (upright 5:3)

- **Detect the screen as a rotated rectangle** on the white startup frame: high-threshold bright mask → largest **interior** contour → `cv2.minAreaRect` → (center, size, angle). Normalise so width ≥ height and angle ∈ (−45°, 45°]. Bench: size ~408×255 (**aspect 1.599 ≈ 5:3**), angle **−0.87°**. Return the **top-3** rotated rects as ROI candidates (same interior + bright-likeness ranking as the shipped `detect_screen_rect`, now rotation-aware).
- **`screen_roi` representation:** `RoiBox` gains **`angle: float = 0.0`**. `(x, y, w, h)` = the **upright** screen box centred on the screen centre; `angle` = the deskew rotation; `ref_w/ref_h` = the calibration resolution (identity rescale at runtime).
- **Deskew crop (`Roi.crop`):** rotate the frame by `angle` about the screen centre → crop the upright `w×h` box → resize to the **5:3 detection reference 800×480** (the panel's native logical resolution). `angle == 0` → fast path (plain slice, no warp), preserving existing behaviour/perf for any axis-aligned consumer. To bound warp cost, crop a small padded axis-aligned region first, warp only that, then take the centred box.
- Runs at **calibration and runtime** (`WebcamPreview` feeds the detector the deskewed 800×480 ROI).

## 5. Circle + symmetric arc bands

- **Fit the camera circle** from the **bright/aligned** (3rd) capture's central bright disc, deskewed: bright threshold → largest central blob → tight fit (`fitEllipse` → inscribed circle, or a tight percentile threshold), giving (cx, cy, r). (`minEnclosingCircle` overshoots on glare — avoid it.)
- **Arc bands:** two **mirror-symmetric** `RoiBox` bands on the circle's left/right edges:
  - vertical extent ≈ middle 60% of the circle: `y ∈ [cy − 0.6r, cy + 0.6r]`,
  - horizontal ≈ outer ~30–35% of the radius each side: left `x ∈ [cx − r, cx − 0.65r]`, right mirrored.
  - This excludes the top corners (battery) and the top/bottom of the disc.
- Stored as `left_arc` / `right_arc` (`angle = 0`, ref 800×480). **Nudgeable** on the confirm screen via `selectROI`.

## 6. Capture flow & calibration

**Three captures** (live guided, or `--from-files WHITE RED BRIGHT`):
1. **WHITE** startup → rotated-rect screen → deskew + 5:3 → `screen_roi` (with angle). Pick 1 of 3 candidates (terminal keys) or `m` to drag.
2. **RED** (misaligned, both arcs red) → sample `red_bands`; confirm the arcs land in the bands and read **both red**.
3. **BRIGHT / ready-state** (aligned: eye close, circle bright, arcs not-red) → fit the camera circle → derive the symmetric arc bands; **validate both arcs read not-red** here.

**`calibration.py` functions:**
- `detect_screen_rects(white_bgr, *, top_n=3) -> list[RotatedRect]` — top-3 interior rotated rects `((cx,cy),(w,h),angle)`.
- `to_roi_candidate(rotated_rect, *, target_aspect=5/3) -> RoiBox` — normalise to 5:3 (expand the short side; never shrink), carry the angle.
- `deskew_crop(frame, screen_roi, out=(800,480)) -> np.ndarray` — the shared deskew (also used by `Roi.crop`).
- `fit_camera_circle(bright_ref_bgr) -> tuple[float, float, float]` — (cx, cy, r), tight.
- `arc_bands_from_circle(cx, cy, r, ref_w, ref_h) -> tuple[RoiBox, RoiBox]` — symmetric left/right bands.
- `sample_red_band(region_bgr, *, lo_pct=5, hi_pct=95) -> list[HsvBand]` — percentile-bracket the red arc pixels (1–2 bands for the 0/180 hue wrap), generous S/V floors (arcs are pale: bench red S~50 V~227 hue~170).
- `suggest_coverage_threshold(red_cov_on_red, red_cov_on_bright, *, floor=0.02) -> float` — midpoint between the red-frame coverage (high) and the bright-frame coverage (≈0), floored.
- `write_calibration_values(path, *, screen_roi, left_arc, right_arc, red_bands, coverage_threshold)` — ruamel round-trip (comment-preserving, `.bak`, pydantic-validate); writes the angle + drops the removed keys.
- The shipped `detect_arc_regions` / `sample_hsv_band(color="green")` / green priors are removed.

## 7. Config schema (`system_camera_config.py`, `schema_version` 6 → 7)

- `RoiBox`: add `angle: float = Field(default=0.0)`.
- `SystemCameraConfig.screen_roi`: unchanged type (now carries angle); default stays the legacy box (angle 0).
- `AutoTriggerConfig`:
  - **keep** `red_bands` (`min_length=1`), `left_arc`, `right_arc` (ref defaults **800×480**), `coverage_threshold`, `morph_kernel`, `stable_seconds`, `cooldown_seconds`, `detect_interval_s`.
  - **remove** `green_bands`, `require_no_red`, `require_clear_between`.
- Data YAML updated: `screen_roi` gains `angle: 0`, arc regions ref 800×480, green/flags removed, `schema_version: 7`. (Actual geometry/colour values are written by the calibration tool; the YAML carries sane placeholders.)

## 8. Device layer & consumers

- `roi.py`: `Roi` gains `angle`; `crop` deskews (fast path at 0); `roi_from_region` carries angle. `AURORA_SCREEN_ROI` stays the angle-0 fallback default.
- `arc_detector.py`: revised `AlignmentState` + `detect()` (red-only, both-arc flags). `classify_band` → red coverage only.
- `auto_trigger.py`: the §3 state machine; constants `WAIT_RED`/`WAIT_CLEAR`/`STABILIZING`/`COOLDOWN`.
- `preview.py`: `WebcamPreview` crops the deskewed ROI for `latest_frame` / detection.
- `grab_auto_trigger_analysis.py`: HUD shows per-arc `RED`/`clear` + the phase (`WAIT_RED`/`WAIT_CLEAR`/…); no green. The other 4 ROI consumers inherit deskew automatically via `roi_from_region(cfg.screen_roi)`.

## 9. Confirm UX & manual override (`calibrate_view.py`)

Terminal-key driven (as shipped). After the 3 captures + auto-detection, the confirm screen (deskewed 800×480, tiled via `imshow_fit`):
- **RED panel:** red frame + both arc bands + red mask tinted, captioned `L=RED R=RED` (expect both red).
- **READY panel:** bright frame + the fitted circle + both arc bands + red mask, captioned `L=clear R=clear` (expect both not-red).
- **Verdict line:** `gate: both RED ✓ · release: both clear ✓ -> fires on red->clear`.
- Keys: `y` write · `e` nudge (selectROI the arc bands / circle) · `r` redo · `q` quit. Writes only on `y`, after pydantic validation, with a `.bak`.

## 10. Error handling
- No interior screen rect → manual `selectROI` for the screen (with an angle of 0).
- Circle fit fails (no bright disc) → fall back to fixed symmetric fractions of the 5:3 screen; warn.
- A red arc band empty on the RED frame → warn that the arcs aren't both red (re-capture / re-align); never write degenerate bands (pydantic `min_length`).
- `--from-files` size ≠ config → warn (different resolution).
- Validation gate before write; `q` writes nothing.

## 11. Testing (host-only — synthetic numpy + clock injection; UI untested as before)
- **Schema:** `angle` default/round-trip; `aligned_bands`/`green_bands` gone; `require_*` gone; arc ref 800×480; `schema_version == 7`.
- **`Roi`:** `crop` with `angle == 0` == old slice; `angle != 0` rotates (a synthetic tilted bright rect deskews to axis-aligned); `roi_from_region` carries angle.
- **`arc_detector`:** both-red / both-clear classification on synthetic red vs non-red arc crops.
- **`auto_trigger` (key):** clock-injected — no fire without the red gate; fire exactly once on red→clear held `stable_seconds`; re-gate required after cooldown; red flicker during STABILIZING resets.
- **`calibration`:** `detect_screen_rects` returns a rotated rect with the right angle sign on a synthetic tilted rect; `to_roi_candidate` yields 5:3 + carries angle; `fit_camera_circle` centres on a synthetic disc; `arc_bands_from_circle` symmetric (right = mirror of left); `sample_red_band` brackets a known red patch + splits on hue wrap; `suggest_coverage_threshold` midpoint+floor; writer preserves comments + `.bak` + validates + writes angle.
- CI: ruff format/check, mypy(src), pytest `not hardware` all green.

## 12. Docs / memory on completion
- `CLAUDE.md` + `README.md`: note the 5:3 deskewed ROI, circle-based symmetric bands, red-gate auto-trigger; the 3-capture calibration (white/red/bright).
- DRY registry: register this spec + plan.
- Memory: update `project-system-camera-view-calibration` with the deskew/red-gate revision.
