# DR-Grading Phase 2 — Inline Grading in `grab_trigger_capture` — Design Spec

**Date:** 2026-06-11
**Status:** Approved (brainstorming) — pending implementation plan
**Author:** Claude (brainstorming session)
**Builds on:** `docs/superpowers/specs/2026-06-11-dr-grading-design.md` (Phase 1, merged)

---

## 1. Goal

Wire the existing, load-once `DRGrader` (Phase 1) into the `grab_trigger_capture`
demo so the fundus images the robot pulls from the Optomed Aurora are graded for
diabetic-retinopathy severity (0–4) **inline**, and show the result in a panel
**beside** the image in the existing "last capture" popup — text on a separate
panel region, never drawn over the image pixels.

This stays a **research / educational** tool. It is **not a medical device** and
must never be used for clinical diagnosis. The disclaimer ships in every output
(JSON sidecar and on-screen panel).

### Scope

- IN: per-patient-turn capture + batch grade + combined results panel + sidecars
  identical to the batch CLI; graceful degradation when weights are missing; an
  enable/disable toggle (config default + per-run CLI override).
- OUT: any change to arm/hand motion, the capture/pull protocol, the Phase-1
  grading math, `pyproject.toml` dependencies, or `references/`.

---

## 2. Reused Phase-1 API (unchanged)

- `DRGrader(config, *, weights_path=..., model_sha8=...)` — loads the ~1.2 GB
  model once in `__init__`; `.grade_array(img_bgr, *, source_name) -> GradeResult`
  grades an already-decoded BGR frame (no disk re-read).
- `GradeResult` (frozen dataclass) — `grade:int`, `label:str`, `confidence:str`,
  `probabilities:dict[str,float]` (5 entries), `crop`, `model`,
  `preprocess_version`, `graded_at_utc`, `disclaimer`, `.to_dict()`.
- `load_fundus_analysis_config() -> FundusAnalysisConfig`, `DR_LABELS`.

The demo grades the JPEG bytes it already pulled (`data`) by decoding once and
calling `grade_array`, so inline and batch grading run identical math.

---

## 3. Architecture decision — caller composes; `show_still` unchanged

The capture thread is the **sole cv2-window owner** (`preview.py` threading
contract). Two ways to draw the panel were considered:

1. **(CHOSEN) Caller composes a numpy frame, passes encoded bytes to the existing
   `show_still(bytes)`.** Composition is pure `numpy`/`cv2.putText`/`cv2.rectangle`
   — *not* cv2-window calls — so it runs on the demo's trigger thread without
   touching the cv2-owner invariant. The composite is PNG-encoded (lossless →
   crisp text) and handed to `show_still`, which already decodes-once-and-reshows.
2. (REJECTED) Extend `show_still` to take a `GradeResult` and compose on the
   capture thread. This makes `system_camera` (a generic camera device) import
   `fundus_analysis` — a same-layer peer import forbidden by
   `01-module-layering.md §2.2` — and moves composition onto a thread that host
   tests cannot exercise.

Consequence: `WebcamPreview` / `preview.py` are **not modified**. The panel logic
lives in the `arm101_hand` package (so host tests import it), not in the script.

---

## 4. New + modified files

### New (in the `arm101_hand` package — importable by host tests)

- **`src/arm101_hand/fundus_analysis/render.py`** — pure panel composition + thin
  image-codec wrappers (no model, no disk I/O):
  - `compose_summary_panel(shots, *, unavailable_reason=None, panel_width=420, display_height=720) -> np.ndarray`
    — builds the `[ most-severe image | summary panel ]` composite. Handles N≥1
    (N=1 degenerates to the single-shot view).
  - `encode_png(frame) -> bytes`, `decode_bgr(data: bytes) -> np.ndarray | None`
    — so the demo never imports cv2 directly.
  - `_most_severe(results) -> int` and `_bar_width(prob, max_px) -> int` — small
    pure helpers, directly unit-tested.
  - `GradedShot` — lightweight container (`image_bgr: np.ndarray`,
    `result: GradeResult | None`); `eq=False` to avoid ndarray-comparison.
- **`src/arm101_hand/fundus_analysis/sidecar.py`** — the shared sidecar helper
  (de-duplicates the CLI logic):
  - `weights_sha8(path) -> str` (sha256[:8] of the weights file).
  - `sidecar_path(output_dir, source_name) -> Path` → `output_dir/<stem>.dr.json`.
  - `write_sidecar(result, output_dir) -> Path` (mkdirs, writes
    `json.dumps(result.to_dict(), indent=2)`).
- **`tests/unit/test_fundus_analysis_render.py`**, **`tests/unit/test_fundus_analysis_sidecar.py`**
  — host tests, no weights.

### Modified

- **`src/arm101_hand/fundus_analysis/__init__.py`** — export
  `compose_summary_panel`, `GradedShot`, `encode_png`, `decode_bgr`,
  `weights_sha8`, `sidecar_path`, `write_sidecar`.
- **`src/arm101_hand/scripts/dr_grade.py`** — drop local `_sha8` + inline sidecar
  write; use `weights_sha8` / `sidecar_path` / `write_sidecar`. Behavior identical;
  the point is that inline and batch emit byte-identical sidecars.
- **`src/arm101_hand/config/fundus_analysis_config.py`** + **`.../data/fundus_analysis_config.yaml`**
  — add `inline_grading: bool = True` and `captures_per_patient: int = 1` (`ge=1`).
- **`scripts/demos/grab_trigger_capture.py`** — the integration (see §5).
- **`CLAUDE.md`** + root **`README.md`** — via the doc_update skill (demo
  description + Phase-2-done tech-debt note).

### Unchanged

`src/arm101_hand/system_camera/preview.py`, `pyproject.toml` (cv2/torch/timm/numpy
all present), `references/`, all motion code.

---

## 5. Demo flow (`grab_trigger_capture.py`)

### 5a. Toggle (config default + per-run override = "both")

- `cfg.inline_grading` (YAML) is the default.
- argparse adds a mutually-exclusive `--grade` / `--no-grade` (unset ⇒ use config).
- Effective: `enabled = cli_override if set else cfg.inline_grading`.
- No CLI flag for `captures_per_patient` — operator edits the YAML (per decision).

### 5b. Startup (before `run_grab_demo`, so it never affects motion timing)

- If `enabled`: print `"Loading DR-grading model (~a few seconds, ~1.2 GB)…"` and
  build `DRGrader(cfg, weights_path=..., model_sha8=weights_sha8(...))` once.
- **Graceful degradation:** weights missing or load raises ⇒ print a warning,
  `grader=None`, remember `unavailable_reason="grading unavailable — run export_weights.py"`.
  The demo still runs capture + popup; it never crashes.
- Resolve `output_dir = REPO_ROOT / cfg.output_dir`.

### 5c. Patient turn (the new loop state)

- `turn_shots: list[tuple[bytes, str]]` accumulates `(jpeg_bytes, saved_name)` for
  the current patient. Target = `cfg.captures_per_patient` (soft, drives status).
- **SPACE** = the existing capture cycle (press → hold → release → pull),
  unchanged. On a successful save: append the shot, `preview.show_still(data)`
  (RAW image, as today), and print `shot k/N captured — SPACE for another, 'g' to analyze`.
  When `k >= N`, add a `(target reached — press 'g')` hint. Extras are allowed.
- **`g` / `G`** = confirm the turn → analysis (handled in the key loop like `r`):
  - No shots yet ⇒ `"no shots captured yet"`, continue.
  - Grading disabled ⇒ `"grading disabled (capture-only) — nothing to analyze"`.
  - Else: for each shot, `decode_bgr` → if `grader`: `grade_array(img_bgr, source_name=name)`
    + `write_sidecar(result, output_dir)` (per-shot try/except → on error keep the
    image with a per-shot error note); collect `GradedShot(img_bgr, result)`.
    Print a per-shot console summary table + the disclaimer.
  - If `preview` is available: `compose_summary_panel(shots, unavailable_reason=…)`
    → `encode_png` → `preview.show_still(bytes)` (replaces the raw image; stays
    until the next capture/turn).
  - **Sidecars are written even when `preview is None`** (decision: inline + batch
    artifacts stay identical); only the popup is gated on the preview.
  - Reset `turn_shots = []`; print `"patient turn complete — next SPACE starts a new patient."`
- **`r`**, **`[` / `]`**, **`q` / Ctrl+C** behave exactly as today. On quit with
  un-analyzed shots, print `"(N shots captured but not analyzed)"` — **no**
  auto-grade, **no** motion ("no surprise movements").

### 5d. Latency

Grading runs on the trigger thread (~2–5 s/shot CPU). The live USB preview is on
its own daemon thread, so it keeps streaming during analysis. Per the user
decision, the popup updates **only when the combined panel is ready** (no
intermediate "grading…" frame).

---

## 6. Combined results panel (`compose_summary_panel`)

Composite = `hstack( most_severe_image_scaled_to_display_height , panel )`.

- **Most-severe shot** = `_most_severe`: max `grade`; tie → max top-probability;
  tie → latest shot. Its image fills the left.
- **Panel** (dark canvas, `panel_width × display_height`, text/bars via `putText`/
  `rectangle` only):
  - `GRADE {g}: {label}` — large, **color-coded by severity** (0 green → 4 red).
  - `Confidence: {HIGH|MEDIUM|LOW}`.
  - **Per-shot list** (shown only when N>1): `idx. {label} ({grade})  {top%}`, the
    most-severe row highlighted.
  - The most-severe shot's **5 horizontal probability bars** (label · % · bar),
    predicted class highlighted — the rendering style approved in brainstorming.
  - 2-line disclaimer footer.
- **Unavailable / error path:** `result=None` for a shot (or all) ⇒ that shot
  contributes its filename + the `unavailable_reason` text; the left image is the
  most recent shot. Always returns a valid composite — never raises for display.

PNG-encoded (lossless) for crisp text; `show_still` letterboxes it via the
existing `imshow_fit` (so its aspect ratio is preserved on resize).

---

## 7. Testing (host, `uv run pytest -m 'not hardware'`, no weights)

`test_fundus_analysis_render.py`:
- `compose_summary_panel` shape = `display_height × (scaled_w + panel_width) × 3`,
  dtype uint8; left region equals the scaled most-severe image (image pixels
  untouched — panel is appended, not overlaid).
- N=1 and N=3 both return valid composites; the N>1 case picks the highest grade.
- `result=None` (unavailable) path returns a valid composite carrying the reason.
- `_most_severe`: grade dominates; tie broken by top-prob then latest.
- `_bar_width`: 0 → 0, 1.0 → max_px, monotonic, clamped.
- `encode_png` → `decode_bgr` round-trips to the same shape.

`test_fundus_analysis_sidecar.py`:
- `sidecar_path` → `<stem>.dr.json` under `output_dir`.
- `write_sidecar` creates `output_dir`, writes JSON that round-trips to
  `result.to_dict()`, returns the path.
- `weights_sha8` returns the sha256[:8] of a known-content temp file.

All tests build a synthetic `GradeResult` + synthetic numpy arrays. ruff + mypy
clean. The 1.2 GB weights and real inference stay hardware-gated.

---

## 8. Iron-law compliance

- **IL-2** — `references/` read-only: untouched (grading reads only the gitignored
  slim weights in `models/`).
- **IL-5** — config stays in-tree (`fundus_analysis_config.yaml` gains two fields,
  never written at runtime); sidecars → gitignored `media_outputs/fundus_analysis/`,
  weights → gitignored `models/`.
- **IL-7** — the Phase-2 capability is documented once in CLAUDE.md (demo
  description + tech-debt), with the README pointing at it; no fact duplicated.
- **IL-1 / IL-3 / IL-4 / IL-6** — no bus, motor, COM-port, or cross-device change:
  N/A. Motion behavior is untouched ("no surprise movements" preserved).

---

## 9. Commits (atomic, TDD order)

1. `feat(fundus): shared DR sidecar helpers + refactor dr_grade CLI to use them` (+ tests)
2. `feat(fundus): combined results-panel composition + image-codec helpers` (+ tests)
3. `feat(fundus): inline_grading + captures_per_patient config fields`
4. `feat(demos): inline DR grading + combined results panel in grab_trigger_capture`
5. `docs: refresh CLAUDE.md/README for Phase-2 inline DR grading`

---

## 10. Out of scope (YAGNI)

- Patient metadata / IDs / folder grouping (a turn is just an in-memory shot batch).
- A CLI flag for `captures_per_patient` (YAML edit is enough).
- Key-cycling through per-shot panels (the combined panel + console table suffice).
- GPU / quantization, fine-tuning, watch/daemon mode (unchanged from Phase 1).
