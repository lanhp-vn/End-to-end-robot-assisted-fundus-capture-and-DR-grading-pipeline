# System-camera arc redness: LAB a* coverage (replacing HSV bands)

**Date:** 2026-06-24
**Status:** design — approved direction, scoped to detection rework
**Supersedes (detection metric only):** the HSV-red-band sweep in
`2026-06-23-system-camera-red-sweep-robustness` and the red-only gate metric in
`2026-06-23-system-camera-deskew-circle-redgate`. The arc *geometry*, deskew ROI, auto-trigger
lifecycle, and calibrate_view capture flow from those designs are unchanged.

## Problem

The arc auto-trigger classifies each Aurora alignment arc as RED (misaligned) by **HSV red
coverage**: the fraction of an arc box matching a red HSV band (`AutoTriggerConfig.red_bands`),
thresholded at `coverage_threshold`. During real use this fails on exactly the frames that matter.

When the patient's eye is at the eye cup, the fundus is brightly illuminated and the thin red arcs
wash toward white. Measured on 45 labelled bench frames captured 2026-06-24
(`media_outputs/arc_calib/`, 34 red / 11 clear), the offline replay
(`arc_sweep_replay.py`) **excluded 19 of 34 red frames as "transitional"** and the live detector
read ~0 coverage on them — even though they are correctly captured, correctly labelled, misaligned
(red-arc) frames. Two failure shapes:

- **both-blank** (e.g. `[5]`): both arc boxes clip to `V=255`, 0 % red-hue pixels.
- **asymmetric** (e.g. `[8]`, `[16]`, `[18]`): one arc vivid, the other washed to ~0.

Root cause (evidence, not guess): near white, **HSV saturation collapses toward 0**, so no red HSV
band can separate a pale-red arc from a blown-white background without also matching white. The red
pixels that survive *are* the right hue (measured 160–169, inside the band) — there are simply too
few of them (0–0.3 % of the box vs 6 %+ on easy frames). This is **not camera over-exposure** (the
brightness is genuine screen content — the illuminated eye); it is a colour-space limitation. In ~10
of the 19 frames one arc has *literally zero* red-dominant pixels, so threshold tuning alone cannot
recover them.

The exclusion mechanism then *hides* this: valid frames are silently dropped as "transitional"
instead of revealing that detection does not work in the operational condition.

## Requirement

AUTO must detect the misaligned/RED state **even while the eye is at the cup and the fundus washes
the arcs out** (operator decision: option (a)). Detection must still cleanly separate red
(misaligned) from clear (aligned/green) frames.

## Approach: LAB a* coverage

Replace "fraction of arc-box pixels inside an HSV red band" with **"fraction of arc-box pixels whose
LAB a\* ≥ cutoff."** The a\* axis (green→red) stays sensitive to a faint red tint over white where
HSV saturation is dead, and the *green* aligned arcs sit on a\*'s negative side, so the red-vs-green
distinction the whole system depends on is preserved.

### Feasibility evidence (same 45 frames)

`cov = fraction of arc-box pixels with a* ≥ 134` (a\* neutral = 128 in OpenCV 8-bit LAB):

| group | the arc that must clear the bar |
|---|---|
| all 34 RED frames (incl. all 19 currently excluded) | weakest-arc cov **min 0.0170**, median ≈ 0.12 |
| all 11 CLEAR frames | strongest-arc cov **max 0.0068** |

Clean gap: clear ceiling 0.68 % vs red floor 1.70 %. A coverage threshold ≈ 1.1 % classifies **all
45 frames correctly on both arcs — 0 exclusions, 0 misclassifications.** (A *local-contrast* a\*
metric — pixels above the box's own median — was tested and rejected: clear frames reach 0.24 there.
The **absolute** a\* cutoff is the discriminator.)

## Changes

### 1. Detector — `src/arm101_hand/system_camera/arc_detector.py`
- Add `arc_redness_mask(lab_band, a_star_min) -> mask`: `(lab_band[..., 1] >= a_star_min)` as a
  uint8 mask. (Exposed so the calibrate_view confirm view can tint the same pixels.)
- `red_coverage(...)`: build the mask via `arc_redness_mask`, `MORPH_OPEN(morph_kernel)`, return the
  nonzero fraction. Its input is now a **LAB** arc crop (param renamed `hsv_band` → `lab_band`).
- `detect(roi_bgr, cfg)`: `cv2.cvtColor(roi_bgr, COLOR_BGR2LAB)` once, crop each arc, classify RED at
  `cov >= cfg.coverage_threshold`. `AlignmentState` (incl. `left_cov/right_cov` for the HUD),
  `both_red`/`both_clear`, the gate, and the whole `auto_trigger` state machine are **unchanged**.
- Delete `_band_mask`.

### 2. Config — `src/arm101_hand/config/system_camera_config.py` + the yaml
- `AutoTriggerConfig`: drop `red_bands: list[HsvBand]`; add
  `a_star_min: int = Field(default=134, ge=128, le=255)`. `coverage_threshold` stays (semantics now
  a\* coverage; new default `0.01`). `morph_kernel`, timing fields unchanged.
- Remove the `HsvBand` model (no remaining user once all call sites migrate). Keep `RoiBox` /
  `ArcRegion`.
- Bump `SystemCameraConfig.schema_version` 7 → 8.
- `src/arm101_hand/data/system_camera_config.yaml`: replace the `auto_trigger.red_bands:` block with
  `a_star_min: <swept>`, update `coverage_threshold`, set `schema_version: 8`. (Re-derived by the
  sweep, not hand-tuned — IL-5.)

### 3. Calibration sweep — `src/arm101_hand/system_camera/calibration.py`
- **Delete** the HSV hue-anchoring machinery: `_RED_PRIOR`, `_prior_mask`, `_bands_from_points`,
  `sample_red_band`, `_pooled_red_anchor`, `_hsv_map`.
- `SweepResult`: replace `red_bands: list[HsvBand]` with `a_star_min: int`. Other fields
  (`coverage_threshold`, `separable`, `unsatisfied`, `case_detections`, `excluded_transitional`)
  unchanged.
- `sweep_red_detection(cases, left_arc, right_arc, ...)`: grid over candidate `a_star_min`
  (e.g. `range(128, 150)`); for each, compute per-arc a\* coverage for every case via the runtime
  `detect`/`arc_redness_mask` path (so calibration matches runtime exactly); pool clean-red-arc vs
  clear-arc coverages; `t = pick_threshold(red_pool, clear_pool, floor=...)`; score by
  `(clean_correct, -n_transitional, margin)`; keep the best `(a_star_min, t)`. `pick_threshold`
  reused unchanged.
- Transitional-exclusion logic (`_transitional = min(lc, rc) < blank_eps`, `blank_eps=0.008`)
  **kept** — it is sound for genuine one-arc-mid-transition frames. In a\* units it no longer fires
  on the 45 bench frames (weakest red arc 0.0170 > 0.008), so **the 19 frames stop being excluded**.
- `write_calibration_values(...)`: `red_bands` param → `a_star_min: int`; writer emits the scalar
  `a_star_min` into `auto_trigger` instead of the `red_bands` list.

### 4. Migrate the remaining call sites (keep everything running)
Mechanical updates so existing tools still function with the new metric — **not** the new
post-calibration report (that is the deferred part below):
- `scripts/calibration/system_camera/calibrate_view.py`: `_sweep` builds
  `AutoTriggerConfig(a_star_min=...)`; `_confirm` tints via `arc_redness_mask` on a LAB frame
  instead of `_band_mask`; `_report_sweep` prints `a*_min` instead of band lines; `_save_calib_case`
  sidecar stores `a_star_min` (int) instead of `red_bands` (list). (Old sidecars remain loadable —
  `load_arc_cases` only reads `expected` + arc geometry, never `red_bands`.)
- `scripts/diagnostics/system_camera/arc_sweep_replay.py`: `_bands_str` → an `a*_min=… thr=…`
  string; `build_write_kwargs` `red_bands` → `a_star_min`; `format_sweep_report` prints `a*_min`.
- `scripts/diagnostics/system_camera/usb_camera_arc_debug.py`: HUD/sidecar fields switch from
  `red_bands` to `a_star_min`.

### 5. Tests + validation
- Update synthetic-numpy unit tests for the new metric/sweep/schema:
  `tests/unit/test_system_camera_calibration.py`, `test_arc_sweep_replay.py`,
  `test_arc_debug_sidecar.py`, `test_system_camera_config.py`, and any detector test. Synthetic
  frames now set a LAB a\* signal (a red-dominant patch in the arc box) rather than an HSV hue.
- Real-evidence validation: re-run `arc_sweep_replay.py` over the existing 45 frames with the new
  metric. **Acceptance: 0 excluded, 45/45 correct, separable = True.**
- CI gate: `ruff format --check`, `ruff check`, `mypy src`, `pytest -m 'not hardware'` all green.

## Risks & caveats (carry into validation, do not wave away)
1. **Fit-on-test.** The clean 45-frame split is measured on the same frames the cutoff was read
   from. The 2.5× margin is reassuring but real confidence needs fresh captures via calibrate_view.
2. **Reddish real fundus.** A genuine patient retina is orange-red; if it fills an arc box it could
   push a\* above the cutoff and false-trigger RED. The arc boxes sit at the screen *edges* (little
   fundus there) and the bench clear frames stayed ≤ 0.0068, but this **must be validated on real
   patient captures** before trusting AUTO in the clinic. (A local-contrast guard was tried and
   failed; if false positives appear, revisit with a background-relative metric or tighter boxes.)
3. **White-balance / lighting drift** moves a\* neutral; the sweep re-derives `a_star_min` per
   recalibration to compensate (same role the HSV floors played).

## Out of scope (deferred follow-ups)
- **Part 4 — calibrate_view ↔ replay full-corpus report** (the original request): extract
  `load_arc_cases` / `LabeledCase` / `format_sweep_report` into the device layer and have
  calibrate_view print a per-case verdict over *all* saved frames after writing. Includes the
  undecided semantics question (evaluate-the-written-config vs re-sweep-from-scratch). Separate spec.
- **CLAUDE.md / README doc refresh** (the arc-detection description, diagnostics list, config block)
  via `/doc_update` once this lands.
- **Exposure control** — explicitly rejected: the brightness is genuine screen content, not camera
  gain, so locking exposure neither fixes detection nor is warranted.

## Rejected alternatives
- **Keep HSV, lower the S floor** — white has S→0; no floor separates pale-red from white.
- **Treat it as a hue shift** — measured arc hue is 160–169 (inside the band); the arcs are not
  shifted, just too sparse. A wider band does not help.
- **Lower `coverage_threshold` only (keep HSV)** — ~10 frames have an arc at literally 0 HSV-red
  pixels; nothing to threshold.
- **Plain BGR redness `R − max(G,B)`** — a simpler candidate (no colourspace convert) but not
  validated; LAB a\* is the principled red–green axis and separated cleanly, so it is the chosen
  path. BGR remains a fallback if a\* shows real-capture trouble.
