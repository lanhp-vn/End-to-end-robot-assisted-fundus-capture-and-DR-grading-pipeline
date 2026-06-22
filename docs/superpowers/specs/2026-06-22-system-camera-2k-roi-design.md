# System camera 2K stream — real-pixel ROI (no upscale)

**Date:** 2026-06-22
**Status:** Design — approved for planning
**Scope:** `src/arm101_hand/system_camera/` + its config + one diagnostic. Camera-only; no arm/hand behavior.

## 1. Problem

The arm-mounted USB observation camera (IFWATER IF-USB12MP02AF, IMX362, USB 2.0) streams at **640×480**. The fixed ROI that zooms onto the Optomed Aurora's screen (`AURORA_SCREEN_ROI`) is ~30.6% of the frame, so it crops only **196×147 real pixels**, then **upscales ~10×** to the 640×480 reference (`preview.py` `_run`). Arc detection, the preview, and the recording therefore run on interpolated pixels rather than real optical detail.

We want the ROI fed real pixels — with **no upscale anywhere**.

## 2. Key insight

The ROI tracks a fixed *fraction* of the frame (the screen is always in the same place), not a fixed pixel size. `Roi.for_frame` rescales the ROI to whatever resolution the camera streams. So raising the stream resolution makes the ROI crop *larger in real pixels*; the existing resize-to-reference then becomes a **downscale** instead of an upscale.

For the crop to be ≥ 640×480 (strictly downscale-only, no upscale), the stream must be ≥ ~2090 px wide (640 ÷ 0.306). The chosen mode is **2592×1944** (4:3, 5 MP): crop ≈ **794×595** real px → downscale to 640×480, uniform (aspect 1.334 ≈ 1.333), no distortion.

## 3. Goal / non-goals

**Goal:** Stream at 2592×1944 so the ROI crop is real pixels downscaled to the 640×480 reference. Preview window, detection frame, and recording stay 640×480 (unchanged dimensions, sharper content).

**Non-goals:**
- Decoupled detection resolution (keep preview low-res, grab high-res only for detection). Rejected — more complex, fights the "no mid-stream resolution switch" constraint, and the ~15 fps hit is accepted.
- Changing the 12 MP still-grab size (`still_width/still_height`) for `usb_camera_capture.py`.
- Any arm/hand behavior.

## 4. Design

### 4.1 Config — the substantive change
`src/arm101_hand/data/system_camera_config.yaml`:
- `width: 640 → 2592`, `height: 480 → 1944`.
- Rewrite the stale comments ("kept small for a smooth feed", "matches the ROI reference size exactly") to the new rationale: 5 MP 4:3 stream so the ~30.6% ROI crops to ~794×595 real px and downscales to the 640×480 reference (no upscale); ~15 fps expected over USB 2.0; preview/recording/detection frame all stay 640×480.
- `still_width/still_height` (4000×3000) **untouched** — `usb_camera_capture.py`'s 12 MP grab still uses them.
- `schema_version` stays **5** (value change, not a structural schema change).

### 4.2 `preview.py` — clamp-detection guard
The one real risk of a resolution change is the driver silently clamping an unsupported 2592×1944 request to a different mode (possibly 16:9, which shifts where the screen sits and breaks ROI framing). `open_capture` handles *backend* fallback (dshow→default) but not *mode* clamping.

In `WebcamPreview._run`, after reading the negotiated `self.width/self.height`: if `self._width`/`self._height` were explicitly requested (not `None`) and the negotiated values differ, print a loud `WARNING` to stderr (requested vs negotiated, "ROI framing assumes the requested aspect; verify with usb_camera_roi_preview.py"). Lives in `WebcamPreview` so every consumer (both demos + `usb_camera_probe`) inherits it. It fires during `start()` (which blocks on `_ready` until after the resolution is read), so the caller sees it before proceeding. Requires adding `import sys` to `preview.py`.

### 4.3 `preview.py` — probe-window initial-size cap
With the stream at 2592×1944, the no-ROI consumer (`usb_camera_probe.py`) would open its window at native 2592×1944. Add a pure helper `_fit_within(w, h, max_w, max_h)` that scales `(w, h)` down to fit a max box, aspect-preserved, returning it unchanged if it already fits. Apply it to the initial `resizeWindow` size in `_run`, uniformly to both branches. Cap = **960×720** (module constant). The ROI branch (640×480) is under the cap → unaffected; the probe (2592×1944) opens scaled to 960×720. Windows are `WINDOW_NORMAL` (resizable) + letterboxed via `imshow_fit`, so the cap only sets the *initial* on-open size and never distorts content.

### 4.4 `scripts/diagnostics/system_camera/usb_camera_roi_preview.py` — validate at 2592×1944
The live "ROI zoom" + "full frame" windows already validate the new resolution once the config flips (they open the stream at `cfg.width/cfg.height`). The only `4000×3000` reference is the SPACE full-res grab, which reopens the device at `still_width/still_height` for a sharper crop — pointless now that the stream is already 5 MP.

Change SPACE to **crop the ROI straight from the live 2592×1944 frame** (no reopen, no ~1-2 s freeze):
- Keep saving the 640×480 ROI zoom (`roi_<ts>_640x480.jpg`).
- Save `_ROI.crop(frame)` (the native ~794×595 crop) — `_write_image` already names files by actual `w×h`, so it lands as `roi_<ts>_794x595.jpg` automatically.
- Remove the `grab_full_res_frame` call, the still-size handling, the reopen, the cap reassignment + `isOpened` recheck, the "grab returned no frame" / "below requested" notes. Drop the now-unused `grab_full_res_frame` import and the `cfg.still_width/still_height` reads. (`grab_full_res_frame` stays in the module — `usb_camera_capture.py` still uses it.)
- This tool opens via `open_capture` directly (not `WebcamPreview`), so add the same requested-vs-negotiated clamp warning here so the bench-verification tool itself flags a driver clamp. (The existing "differs from the ROI reference" note becomes always-true now that the stream ≠ 640×480; it's still accurate, leave it.)
- Update the docstring (lines 12-21, 35-39): SPACE now saves the ROI zoom + the native-res ROI crop from the live 2592×1944 frame (~794×595); remove the reopen/freeze language.

## 5. Behavior after the change (numbers)
Stream 2592×1944 → `for_frame` scale 4.05× → ROI crop **(x=243, y=304, w=794, h=595)** real px → resize **down** to 640×480 → that 640×480 frame is shown, recorded, and classified by `detect()`. Arc regions + HSV bands index the same 640×480 post-resize frame, so detection is unchanged in dimensions, sharper in content.

## 6. What stays exactly the same
`AURORA_SCREEN_ROI` (auto-scales via `for_frame`); arc regions + HSV bands + `coverage_threshold` + the auto-trigger state machine; manual focus (`focus: 600`, a lens position independent of sensor resolution); `usb_camera_capture.py`'s 12 MP still path; `fps: null` → camera-reported writer fps; the best-effort "no preview → AUTO unavailable, stay MANUAL" path.

## 7. Error handling & fallback
- Camera won't open at all → existing best-effort: `start()` returns `False`, demo continues without preview, AUTO reports unavailable and stays MANUAL.
- Driver clamps to a different/16:9 mode → the new warning surfaces it immediately.
- **Fallback if 2592×1944 is unsupported or too slow on the bench:** drop to `2048×1536` (accept a ~2% residual upscale) — pure config edit, no code revert.

## 8. Testing

**Host (CI, no hardware — ruff + mypy + pytest must stay green):**
- `tests/unit/test_roi.py`: assert `AURORA_SCREEN_ROI.for_frame(2592, 1944) == (243, 304, 794, 595)` and that the crop is ≥ 640×480 in both dims (the no-upscale property).
- `tests/unit/test_preview_letterbox.py`: add `_fit_within` cases — already-fits returns unchanged (640×480 @ 960×720), scales-down (2592×1944 → 960×720), aspect preserved.
- The print-warning is I/O — left to the bench.

**Bench (hardware-gated, operator-run):**
1. `usb_camera_probe.py --backend dshow` — negotiated resolution prints **2592×1944**, no clamp warning, streams, records, plays back at correct speed, window opens capped at 960×720.
2. `usb_camera_roi_preview.py` — ROI still frames the Aurora screen (sharper); SPACE saves `_640x480` + `_794x595` instantly with no freeze.
3. `grab_auto_trigger_analysis.py` — red/green arcs still classify correctly, a capture fires in AUTO, ~15 fps preview is acceptable, focus sharp at 600.

## 9. Docs & Iron Laws
- Update the canonical resolution rationale in the config comments (single source — IL-7).
- Refresh the stale "smooth low res" phrasing in `docs/BOM.md` (system-camera row); run `/doc_update` if `CLAUDE.md`/`README.md` echo the stream resolution.
- **IL-5** ✓ hand-edited config, never runtime-written. **IL-2** ✓ no `references/`. **IL-6** N/A camera-only, no motor change. **No-surprise-movements** N/A (no actuator change).

## 10. Files touched
- `src/arm101_hand/data/system_camera_config.yaml` — width/height + comments.
- `src/arm101_hand/system_camera/preview.py` — `import sys`; clamp warning; `_fit_within` helper + cap; constant.
- `scripts/diagnostics/system_camera/usb_camera_roi_preview.py` — SPACE crops live frame; clamp warning; docstring.
- `tests/unit/test_roi.py`, `tests/unit/test_preview_letterbox.py` — new cases.
- `docs/BOM.md` — refresh stream-resolution phrasing.
