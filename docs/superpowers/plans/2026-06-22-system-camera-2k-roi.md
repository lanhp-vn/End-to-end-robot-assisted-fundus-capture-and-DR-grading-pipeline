# System Camera 2K Stream — Real-Pixel ROI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream the arm-mounted USB system camera at 2592×1944 so the fixed ROI crops real pixels (~794×595) and downscales to the 640×480 reference — no upscale — while the preview window, detection frame, and recording stay 640×480.

**Architecture:** The change is mostly config (`width/height`). The existing `Roi.for_frame` rescaling already turns a higher stream into a bigger real-pixel crop, flipping the resize-to-reference from upscale to downscale automatically. Two small defensive helpers in `preview.py` (a window-size cap and a resolution-clamp warning) de-risk the resolution bump, and the `usb_camera_roi_preview` diagnostic is simplified to crop the native ROI from the live frame.

**Tech Stack:** Python 3.12, OpenCV (full `opencv-python` wheel), pydantic config, pytest, ruff, mypy.

## Global Constraints

- **No upscale invariant:** the chosen stream resolution must make `AURORA_SCREEN_ROI.for_frame(w, h)` produce a crop ≥ the ROI reference (640×480) in both dimensions. 2592×1944 → crop (x=243, y=304, w=794, h=595).
- **Backend stays `dshow`** (required for the manual focus VCM) and **focus stays 600** — unchanged by this work.
- **IL-5:** `system_camera_config.yaml` is hand-edited, never written by runtime code.
- **IL-2:** never modify `references/`.
- **CI must stay green:** `ruff format --check .`, `ruff check .`, `mypy src`, `pytest -m 'not hardware'`.
- **Type annotations required** on all new functions in `src/` (mypy checks `src`).
- **Branch:** all work lands on `feat/system-camera-2k-roi` (already created; spec already committed there).
- `still_width/still_height` (4000×3000) must remain untouched — `usb_camera_capture.py` still uses them.

---

### Task 1: `_fit_within` window-size cap (pure helper + wiring)

Caps the initial preview-window size so the no-ROI consumer (`usb_camera_probe`) doesn't open a window at native 2592×1944.

**Files:**
- Modify: `src/arm101_hand/system_camera/preview.py`
- Test: `tests/unit/test_preview_letterbox.py`

**Interfaces:**
- Produces: `_fit_within(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]` and module constants `_MAX_INITIAL_WIN_W = 960`, `_MAX_INITIAL_WIN_H = 720`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_preview_letterbox.py`. Change the existing import line at the top from `from arm101_hand.system_camera.preview import _letterbox` to:

```python
from arm101_hand.system_camera.preview import _fit_within, _letterbox
```

Append these tests:

```python
def test_fit_within_unchanged_when_already_fits():
    # 640x480 is under the 960x720 cap -> returned unchanged (ROI consumers unaffected).
    assert _fit_within(640, 480, 960, 720) == (640, 480)


def test_fit_within_scales_down_preserving_aspect():
    # 2592x1944 (4:3) scaled to fit 960x720 -> exactly 960x720, same aspect.
    assert _fit_within(2592, 1944, 960, 720) == (960, 720)


def test_fit_within_never_upscales():
    # A frame smaller than the cap is never enlarged.
    assert _fit_within(320, 240, 960, 720) == (320, 240)


def test_fit_within_handles_zero():
    # Degenerate size (camera not ready) returns unchanged, no divide-by-zero.
    assert _fit_within(0, 0, 960, 720) == (0, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_preview_letterbox.py -v`
Expected: FAIL — `ImportError: cannot import name '_fit_within'`.

- [ ] **Step 3: Implement `_fit_within` + constants**

In `src/arm101_hand/system_camera/preview.py`, add the constants next to `_MAX_DIM_REQUEST` (after line 48):

```python
# Cap the INITIAL preview-window size so a high-res stream (e.g. 2592x1944) doesn't open a giant
# window. Only the on-open size -- windows are WINDOW_NORMAL (resizable) + letterboxed (imshow_fit),
# so content is never distorted. The ROI consumers (640x480) are under this and unaffected.
_MAX_INITIAL_WIN_W = 960
_MAX_INITIAL_WIN_H = 720
```

Add the helper just above `def _letterbox(` (before line 208):

```python
def _fit_within(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Scale ``(w, h)`` down to fit within ``(max_w, max_h)`` preserving aspect; never upscales.

    Returns the size unchanged if it already fits (or is degenerate). Used to bound the *initial*
    window size only -- the window stays resizable.
    """
    if w <= 0 or h <= 0:
        return w, h
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_preview_letterbox.py -v`
Expected: PASS (all tests, including the existing letterbox ones).

- [ ] **Step 5: Wire the cap into `_run`**

In `src/arm101_hand/system_camera/preview.py` `_run`, replace the window-sizing block (currently lines 385-389):

```python
        disp_w = self._roi.ref_w if self._roi is not None else self.width
        disp_h = self._roi.ref_h if self._roi is not None else self.height
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL)
        if disp_w > 0 and disp_h > 0:
            cv2.resizeWindow(self._title, disp_w, disp_h)
```

with:

```python
        disp_w = self._roi.ref_w if self._roi is not None else self.width
        disp_h = self._roi.ref_h if self._roi is not None else self.height
        disp_w, disp_h = _fit_within(disp_w, disp_h, _MAX_INITIAL_WIN_W, _MAX_INITIAL_WIN_H)
        cv2.namedWindow(self._title, cv2.WINDOW_NORMAL)
        if disp_w > 0 and disp_h > 0:
            cv2.resizeWindow(self._title, disp_w, disp_h)
```

- [ ] **Step 6: Run lint, types, and the full host suite**

Run: `uv run ruff format src/arm101_hand/system_camera/preview.py tests/unit/test_preview_letterbox.py && uv run ruff check . && uv run mypy src && uv run pytest -m 'not hardware'`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/system_camera/preview.py tests/unit/test_preview_letterbox.py
git commit -m "feat(system_camera): cap initial preview-window size (_fit_within)"
```

---

### Task 2: `resolution_mismatch_warning` clamp guard (pure helper + wiring + export)

Surfaces a silent driver mode-clamp (the one real risk of the resolution bump) as a loud stderr warning, in every `WebcamPreview` consumer.

**Files:**
- Modify: `src/arm101_hand/system_camera/preview.py`
- Modify: `src/arm101_hand/system_camera/__init__.py`
- Test: `tests/unit/test_preview_letterbox.py`

**Interfaces:**
- Produces: `resolution_mismatch_warning(requested_w: int | None, requested_h: int | None, actual_w: int, actual_h: int) -> str | None` — exported from `arm101_hand.system_camera`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_preview_letterbox.py`, update the preview import line to:

```python
from arm101_hand.system_camera.preview import _fit_within, _letterbox, resolution_mismatch_warning
```

Append:

```python
def test_resolution_mismatch_none_when_equal():
    assert resolution_mismatch_warning(2592, 1944, 2592, 1944) is None


def test_resolution_mismatch_warns_when_clamped():
    msg = resolution_mismatch_warning(2592, 1944, 1920, 1080)
    assert msg is not None
    assert "2592x1944" in msg and "1920x1080" in msg


def test_resolution_mismatch_none_when_request_unspecified():
    # width/height = None means "give me the driver max" -> nothing to compare against.
    assert resolution_mismatch_warning(None, None, 1920, 1080) is None
    assert resolution_mismatch_warning(2592, None, 2592, 1944) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_preview_letterbox.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolution_mismatch_warning'`.

- [ ] **Step 3: Implement the helper + `import sys`**

In `src/arm101_hand/system_camera/preview.py`, add `import sys` to the stdlib imports (after `import math`, line 33-ish — keep alphabetical: `import math` then `import sys` then `import threading`).

Add the helper just above `def _fit_within(` :

```python
def resolution_mismatch_warning(
    requested_w: int | None, requested_h: int | None, actual_w: int, actual_h: int
) -> str | None:
    """Return a warning string if the driver negotiated a different mode than requested, else None.

    A UVC driver clamps an unsupported resolution request to its nearest mode -- which can change
    the aspect ratio and shift where the Aurora screen sits in the frame, breaking the fixed ROI.
    ``None`` for a requested dimension means "the driver's max" (nothing to compare). Pure -- the
    caller decides where to print it.
    """
    if requested_w is None or requested_h is None:
        return None
    if (actual_w, actual_h) == (requested_w, requested_h):
        return None
    return (
        f"WARNING: system camera requested {requested_w}x{requested_h} but the driver negotiated "
        f"{actual_w}x{actual_h} -- ROI framing assumes the requested aspect; verify with "
        "usb_camera_roi_preview.py."
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_preview_letterbox.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into `_run`**

In `_run`, after the resolution/fps read + `self.ok = True` (currently lines 377-380), insert the warning before the window-sizing block:

```python
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.src_fps = float(cap.get(cv2.CAP_PROP_FPS))
        self.ok = True
        warn = resolution_mismatch_warning(self._width, self._height, self.width, self.height)
        if warn:
            print(warn, file=sys.stderr)
```

- [ ] **Step 6: Export the helper**

In `src/arm101_hand/system_camera/__init__.py`, add `resolution_mismatch_warning` to the `from .preview import (...)` block and to `__all__` (alphabetical):

```python
from .preview import (
    WebcamPreview,
    grab_full_res_frame,
    imshow_fit,
    open_capture,
    read_settled_frame,
    resolution_mismatch_warning,
)
```

and in `__all__`, add `"resolution_mismatch_warning",` (keep the list sorted — between `"read_settled_frame",` and `"sharpness",`).

- [ ] **Step 7: Run lint, types, and the full host suite**

Run: `uv run ruff format src/arm101_hand/system_camera/preview.py src/arm101_hand/system_camera/__init__.py tests/unit/test_preview_letterbox.py && uv run ruff check . && uv run mypy src && uv run pytest -m 'not hardware'`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/arm101_hand/system_camera/preview.py src/arm101_hand/system_camera/__init__.py tests/unit/test_preview_letterbox.py
git commit -m "feat(system_camera): warn when the driver clamps the requested resolution"
```

---

### Task 3: Switch the stream to 2592×1944 + lock the no-upscale invariant

The substantive change. A guard test locks the `for_frame` numbers the config depends on, then the config flips.

**Files:**
- Modify: `tests/unit/test_roi.py`
- Modify: `src/arm101_hand/data/system_camera_config.yaml`

**Interfaces:**
- Consumes: `AURORA_SCREEN_ROI` and `Roi.for_frame` (existing, unchanged).

- [ ] **Step 1: Write the guard test**

In `tests/unit/test_roi.py`, update the import line from `from arm101_hand.system_camera import Roi` to:

```python
from arm101_hand.system_camera import AURORA_SCREEN_ROI, Roi
```

Append:

```python
def test_aurora_roi_downscales_at_2592x1944():
    # The 2592x1944 stream (system_camera_config.yaml) makes the ROI crop LARGER than the 640x480
    # reference, so the resize-to-reference is a DOWNSCALE -- real pixels, no upscale. This is the
    # whole point of the 2K stream; it locks the for_frame numbers the config relies on.
    x, y, w, h = AURORA_SCREEN_ROI.for_frame(2592, 1944)
    assert (x, y, w, h) == (243, 304, 794, 595)
    assert w >= AURORA_SCREEN_ROI.ref_w and h >= AURORA_SCREEN_ROI.ref_h  # no upscale in either axis
```

- [ ] **Step 2: Run it to confirm it passes (characterizes existing `for_frame`)**

Run: `uv run pytest tests/unit/test_roi.py -v`
Expected: PASS — `for_frame` already supports arbitrary resolutions; this test documents the invariant. (If it fails, the ROI constant changed — stop and reconcile before touching config.)

- [ ] **Step 3: Flip the config + rewrite the comments**

In `src/arm101_hand/data/system_camera_config.yaml`, replace the block from the `fps: null` comment through `height: 480` (currently lines 19-33):

```yaml
fps: null           # null -> camera-reported fps (fallback 20)
# This cam = IFWATER IF-USB12MP02AF (IMX362, USB 2.0): 4000x3000 max still, MJPG-only for hi-res
# (YUY2 is uncompressed + capped low). MJPG also lets the low-res stream run at a high frame rate.
#
# DECOUPLED: the LIVE stream (width/height, used by the preview + recording everywhere) is kept
# small for a smooth feed; usb_camera_capture briefly reopens the device at the STILL size for one
# SPACE grab, then resumes the stream -- so framing stays smooth while saved stills are full 12 MP.
# (MSMF can't switch resolution on an open capture, so the grab reopens rather than switching it.)
# (The demos film the Aurora screen only; they never save a USB-cam still, just the stream.)
# Set any of these to null to request the driver's max instead (it clamps a large request down).
fourcc: MJPG
width: 640          # live stream: smooth ~60 fps; matches the ROI reference size exactly
height: 480
```

with:

```yaml
fps: null           # null -> camera-reported fps (fallback 20)
# This cam = IFWATER IF-USB12MP02AF (IMX362, USB 2.0): 4000x3000 max still, MJPG-only for hi-res
# (YUY2 is uncompressed + capped low). MJPG also lets the 5 MP stream run at a usable frame rate.
#
# The LIVE stream (width/height) runs at 2592x1944 (5 MP, 4:3) -- used by the preview, recording, AND
# arc detection everywhere. The fixed ROI is ~30.6% of the frame, so at this size it crops to ~794x595
# REAL pixels and DOWNSCALES to the 640x480 ROI reference (roi.py): no upscale, real optical detail
# for detection. The preview window, recording, and detection frame all stay 640x480 (the ROI
# reference) regardless of stream size. Expect ~15 fps over USB 2.0 -- the Aurora screen is static, so
# that is smooth enough. (If 2592x1944 is unsupported or too slow on the bench, fall back to 2048x1536
# -- accepts a ~2% residual upscale.) usb_camera_capture still reopens the device at the STILL size
# (4000x3000) for its 12 MP SPACE grab, then resumes the stream; the demos never save a USB-cam still.
# (MSMF can't switch resolution on an open capture, so that grab reopens rather than switching it.)
# Set any of these to null to request the driver's max instead (it clamps a large request down).
fourcc: MJPG
width: 2592         # live stream @ 5 MP 4:3 -> ROI crop ~794x595 real px -> downscale to 640x480 ref
height: 1944
```

- [ ] **Step 4: Confirm the config still loads + invariant holds**

Run: `uv run pytest tests/unit/test_roi.py tests/unit/test_system_camera_config.py -v`
Expected: PASS — config validates against the schema (2592/1944 satisfy `ge=1`), guard test green.

- [ ] **Step 5: Run lint + full host suite**

Run: `uv run ruff check . && uv run mypy src && uv run pytest -m 'not hardware'`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_roi.py src/arm101_hand/data/system_camera_config.yaml
git commit -m "feat(system_camera): stream at 2592x1944 so the ROI crops real pixels (no upscale)"
```

---

### Task 4: Simplify `usb_camera_roi_preview` to crop the native ROI from the live frame

SPACE now crops the ROI from the already-5 MP live frame (no reopen, no freeze), plus a clamp warning so the bench-verification tool itself flags a driver clamp.

**Files:**
- Modify: `scripts/diagnostics/system_camera/usb_camera_roi_preview.py`

**Interfaces:**
- Consumes: `resolution_mismatch_warning` (Task 2), `AURORA_SCREEN_ROI`, `imshow_fit`, `open_capture` (existing).

- [ ] **Step 1: Update the imports**

In `scripts/diagnostics/system_camera/usb_camera_roi_preview.py`, replace the import block (currently lines 56-62):

```python
from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    AURORA_SCREEN_ROI,
    grab_full_res_frame,
    imshow_fit,
    open_capture,
)
```

with:

```python
from arm101_hand.config import load_system_camera_config  # noqa: E402
from arm101_hand.system_camera import (  # noqa: E402
    AURORA_SCREEN_ROI,
    imshow_fit,
    open_capture,
    resolution_mismatch_warning,
)
```

- [ ] **Step 2: Add the clamp warning after the resolution read**

Replace the two lines (currently lines 160-161):

```python
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
```

with:

```python
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    clamp_warn = resolution_mismatch_warning(cfg.width, cfg.height, width, height)
    if clamp_warn:
        print(clamp_warn, file=sys.stderr)
```

- [ ] **Step 3: Replace the SPACE handler (drop the reopen)**

Replace the entire SPACE branch (currently lines 246-280, from `if key == " ":` through `last_good = time.monotonic()  # the fresh stream cap is healthy; reset the stall clock`):

```python
            if key == " ":  # SPACE -> save the ROI zoom + the native-res ROI crop from the live frame
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # shared stem pairs both files
                if _write_image(out_dir, ts, zoom):  # the 640x480 ROI zoom shown above
                    saved += 1
                # Native-res ROI crop straight from the live frame: the stream is already 5 MP, so
                # this is as sharp as the operating path gets -- no device reopen, no preview freeze.
                if _write_image(out_dir, ts, _ROI.crop(frame)):  # ~794x595 at a 2592x1944 stream
                    saved += 1
```

- [ ] **Step 4: Update the docstring**

Replace the SPACE paragraph (currently lines 17-21):

```
Press SPACE to save the framed shot: the ROI zoom (640x480) plus a full-resolution ROI crop -- the
same screen region cropped from a 4000x3000 grab (~1225x919) -- so you keep a sharp high-res zoom of
the Aurora screen. The full-res grab reopens the device (preview freezes ~1-2 s) exactly like
``usb_camera_capture.py`` (``grab_full_res_frame``); focus is held through the reopen so the crop
stays sharp. Both files share a timestamp; the ``<w>x<h>`` suffix tells them apart.
```

with:

```
Press SPACE to save the framed shot: the ROI zoom (640x480) plus the native-resolution ROI crop
taken straight from the live frame -- at the configured 2592x1944 stream that crop is ~794x595 real
pixels, as sharp as the operating path gets, with no device reopen and no preview freeze. Both files
share a timestamp; the ``<w>x<h>`` suffix tells them apart.
```

Replace the SPACE key line (currently lines 36-38):

```
  SPACE   save TWO paired images to --out-dir: the ROI zoom roi_<ts>_640x480.jpg
          + a full-res ROI crop roi_<ts>_<w>x<h>.jpg (same <ts>)
          (preview freezes ~1-2 s while the device reopens at full res, then resumes)
```

with:

```
  SPACE   save TWO paired images to --out-dir: the ROI zoom roi_<ts>_640x480.jpg
          + the native-res ROI crop roi_<ts>_<w>x<h>.jpg (same <ts>), cropped from the live frame
```

- [ ] **Step 5: Verify lint + syntax (this is a script, not mypy-checked by CI)**

Run: `uv run ruff format scripts/diagnostics/system_camera/usb_camera_roi_preview.py && uv run ruff check scripts/diagnostics/system_camera/usb_camera_roi_preview.py && uv run python -m py_compile scripts/diagnostics/system_camera/usb_camera_roi_preview.py`
Expected: no errors; `py_compile` confirms it parses and all imports resolve at compile time. Confirm `grab_full_res_frame` no longer appears in the file: `uv run python -c "import pathlib,sys; sys.exit('grab_full_res_frame' in pathlib.Path('scripts/diagnostics/system_camera/usb_camera_roi_preview.py').read_text())"` → exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/diagnostics/system_camera/usb_camera_roi_preview.py
git commit -m "refactor(system_camera): roi_preview crops native ROI from the live frame (no reopen)"
```

---

### Task 5: Refresh stale resolution docs (BOM)

**Files:**
- Modify: `docs/BOM.md`

- [ ] **Step 1: Update the system-camera row**

In `docs/BOM.md`, in the row for the IFWATER system camera (row 10), replace the substring:

```
Live preview + recording stream at a smooth low res (`system_camera_config.yaml` `width`/`height`)
```

with:

```
Live preview + recording + arc detection stream at **2592×1944 (5 MP, 4:3)** (`system_camera_config.yaml` `width`/`height`) so the fixed ROI crops real pixels (~794×595) and downscales to the 640×480 reference with no upscale (~15 fps over USB 2.0)
```

- [ ] **Step 2: Check whether CLAUDE.md / README echo the stream resolution**

Run: `uv run python -c "import pathlib; print([f for f in ('CLAUDE.md','README.md') if '640' in pathlib.Path(f).read_text(encoding='utf-8')])"`
Expected: prints a (possibly empty) list. If either file states the stream is 640×480 (the system-camera paragraph in CLAUDE.md §7 / README), update that phrasing to 2592×1944 → 640×480-reference ROI; if neither does, no change. (Grading/ROI mentions of the 640×480 *reference* are still correct — only a claim that the camera *streams* at 640×480 is stale.)

- [ ] **Step 3: Commit**

```bash
git add docs/BOM.md CLAUDE.md README.md
git commit -m "docs: system camera streams at 2592x1944 (real-pixel ROI)"
```

(If CLAUDE.md/README were unchanged, drop them from the `git add`.)

---

### Task 6: Bench verification (HARDWARE — operator-run, not CI)

Not a code task; the hardware-gated acceptance check. Run on the bench with the system camera connected (camera index 1), Optomed Client closed.

- [ ] **Step 1: Probe — confirm the camera actually delivers 2592×1944**

Run: `uv run python scripts/diagnostics/system_camera/usb_camera_probe.py --backend dshow`
Expected: the printed "Window open: WxH" line reads **2592x1944**; **no** "requested ... but the driver negotiated ..." warning appears; the window opens at ~960×720 (capped), stays responsive 30+ s, `r` records a clip that plays back at the right speed, and `q` + Ctrl+C both tear down cleanly. If the warning fires (driver clamped) or fps is unusable → fall back to `width: 2048`, `height: 1536` in the config and re-run.

- [ ] **Step 2: ROI preview — confirm framing + sharpness + instant SPACE**

Run: `uv run python scripts/diagnostics/system_camera/usb_camera_roi_preview.py`
Expected: the "ROI zoom" window still frames just the Aurora screen (visibly sharper than before); SPACE saves `roi_<ts>_640x480.jpg` **and** `roi_<ts>_794x595.jpg` to `media_outputs/camera_captures/` instantly, with no ~1-2 s preview freeze.

- [ ] **Step 3: Auto-trigger demo — confirm detection + capture still work**

Run: `uv run python scripts/demos/grab_auto_trigger_analysis.py` (full prerequisites per its docstring).
Expected: in AUTO, the arc HUD still classifies RED/GREEN correctly, a capture fires when the arcs go green and hold, the ~15 fps preview is acceptable, and the screen stays sharp at focus 600.

- [ ] **Step 4: Record the outcome**

If all three pass, the feature is verified — note it on the branch (commit message or PR body). If Step 1 forced the 2048×1536 fallback, commit that config change with a note explaining the camera didn't expose 2592×1944.

---

## Self-Review

**Spec coverage:**
- §4.1 config flip + comments → Task 3. ✓
- §4.2 clamp-detection guard → Task 2 (helper + wiring + export). ✓
- §4.3 `_fit_within` probe-window cap → Task 1. ✓
- §4.4 roi_preview crops live frame + clamp warning + docstring + dropped `grab_full_res_frame` → Task 4. ✓
- §8 host tests (`test_roi`, `test_preview_letterbox`) → Tasks 1-3. ✓ Bench steps → Task 6. ✓
- §9 docs (BOM + CLAUDE/README check) → Task 5. ✓
- §7 fallback to 2048×1536 → noted in Task 3 config comment + Task 6 Step 1. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. ✓

**Type consistency:** `_fit_within(w, h, max_w, max_h) -> tuple[int, int]` and `resolution_mismatch_warning(requested_w, requested_h, actual_w, actual_h) -> str | None` are used with matching signatures in `_run` (Task 1/2) and in `usb_camera_roi_preview` (Task 4 calls `resolution_mismatch_warning(cfg.width, cfg.height, width, height)`). Constants `_MAX_INITIAL_WIN_W/H` defined in Task 1, used in Task 1 Step 5. Export name `resolution_mismatch_warning` matches the import in Task 4. ✓
