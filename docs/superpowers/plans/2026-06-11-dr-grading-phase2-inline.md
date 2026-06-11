# DR-Grading Phase 2 — Inline Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade each fundus image the robot pulls from the Optomed Aurora inline in the `grab_trigger_capture` demo, batched per patient turn, and show the result in a panel beside the image in the existing "last capture" popup.

**Architecture:** A patient turn accumulates captured shots; pressing `g` grades every shot via the Phase-1 `DRGrader`, writes one `.dr.json` sidecar each (via a new shared helper also adopted by the batch CLI), and shows one combined `[ most-severe image | summary panel ]` composite. The composite is built with pure numpy/`cv2.putText` (off the cv2-window thread) and handed to the unchanged `WebcamPreview.show_still(bytes)`, preserving the capture thread as the sole cv2 owner.

**Tech Stack:** Python 3.12, numpy, opencv-python (full build), timm/torch (Phase-1 grader, unchanged), pydantic config, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-11-dr-grading-phase2-inline-design.md`

---

## File Structure

**Create:**
- `src/arm101_hand/fundus_analysis/sidecar.py` — shared `weights_sha8` / `sidecar_path` / `write_sidecar`.
- `src/arm101_hand/fundus_analysis/render.py` — `GradedShot`, `compose_summary_panel`, `encode_png`, `decode_bgr`, and pure helpers `_most_severe` / `_bar_width` / `_scale_to_height`.
- `tests/unit/test_fundus_analysis_sidecar.py`
- `tests/unit/test_fundus_analysis_render.py`

**Modify:**
- `src/arm101_hand/fundus_analysis/__init__.py` — export the new public symbols.
- `src/arm101_hand/scripts/dr_grade.py` — use the shared sidecar helpers (drop local `_sha8` + inline write).
- `src/arm101_hand/config/fundus_analysis_config.py` — add `inline_grading` + `captures_per_patient`.
- `src/arm101_hand/data/fundus_analysis_config.yaml` — add the two fields.
- `tests/unit/test_fundus_analysis_config.py` — cover the two new fields.
- `scripts/demos/grab_trigger_capture.py` — startup grader load, patient-turn state, `g`-to-analyze, combined panel.
- `CLAUDE.md`, `README.md` — via the doc_update skill.

**Do NOT touch:** `src/arm101_hand/system_camera/preview.py`, `pyproject.toml`, `references/`, any motion code.

---

## Task 1: Shared DR sidecar helpers + refactor the batch CLI

**Files:**
- Create: `src/arm101_hand/fundus_analysis/sidecar.py`
- Create: `tests/unit/test_fundus_analysis_sidecar.py`
- Modify: `src/arm101_hand/fundus_analysis/__init__.py`
- Modify: `src/arm101_hand/scripts/dr_grade.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fundus_analysis_sidecar.py`:

```python
import hashlib
import json

from arm101_hand.fundus_analysis.grader import GradeResult
from arm101_hand.fundus_analysis.sidecar import sidecar_path, weights_sha8, write_sidecar


def _result(name: str = "img01.JPG") -> GradeResult:
    return GradeResult(
        source_image=name,
        grade=2,
        label="Moderate",
        confidence="MEDIUM",
        probabilities={"No DR": 0.04, "Mild": 0.18, "Moderate": 0.71, "Severe": 0.05, "Proliferative": 0.02},
        crop={"method": "circle", "box": [0, 0, 10, 10], "fallback": False},
        model={"checkpoint": "w.safetensors", "sha256_8": "abc12345", "arch": "vit_large_patch16_224"},
        preprocess_version="1",
        graded_at_utc="2026-06-11T00:00:00Z",
    )


def test_sidecar_path_uses_source_stem(tmp_path):
    assert sidecar_path(tmp_path, "20260611_IM0115EY.JPG") == tmp_path / "20260611_IM0115EY.dr.json"


def test_write_sidecar_creates_dir_and_round_trips(tmp_path):
    out = tmp_path / "fundus_analysis"
    res = _result("img01.JPG")
    path = write_sidecar(res, out)
    assert path == out / "img01.dr.json"
    assert json.loads(path.read_text()) == res.to_dict()


def test_weights_sha8_matches_hashlib(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"hello world")
    assert weights_sha8(f) == hashlib.sha256(b"hello world").hexdigest()[:8]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_sidecar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.fundus_analysis.sidecar'`

- [ ] **Step 3: Write the implementation**

Create `src/arm101_hand/fundus_analysis/sidecar.py`:

```python
"""Shared DR-grading sidecar helpers.

Used by both the batch CLI (``arm101-dr-grade``) and the inline demo so they emit
byte-identical ``<stem>.dr.json`` artifacts. Local/offline; no network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from arm101_hand.fundus_analysis.grader import GradeResult


def weights_sha8(path: Path) -> str:
    """sha256[:8] of the weights file — ties a sidecar to the exact weights used."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def sidecar_path(output_dir: Path, source_name: str) -> Path:
    """Where the ``<source-stem>.dr.json`` sidecar for ``source_name`` lives."""
    return output_dir / f"{Path(source_name).stem}.dr.json"


def write_sidecar(result: GradeResult, output_dir: Path) -> Path:
    """Write ``result.to_dict()`` as ``<stem>.dr.json`` under ``output_dir``; return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_path(output_dir, result.source_image)
    path.write_text(json.dumps(result.to_dict(), indent=2))
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fundus_analysis_sidecar.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Export the helpers**

Edit `src/arm101_hand/fundus_analysis/__init__.py` — add the import and `__all__` entries (keep the existing grader/preprocess imports):

```python
from arm101_hand.fundus_analysis.sidecar import sidecar_path, weights_sha8, write_sidecar
```

Add `"sidecar_path"`, `"weights_sha8"`, `"write_sidecar"` to `__all__`.

- [ ] **Step 6: Refactor the batch CLI to use the helpers**

In `src/arm101_hand/scripts/dr_grade.py`:

Remove the `import hashlib` and `import json` lines and the `_sha8` function. Add this import (after the existing `from arm101_hand.fundus_analysis.grader import DRGrader`):

```python
from arm101_hand.fundus_analysis.sidecar import sidecar_path, weights_sha8, write_sidecar
```

Change the grader construction line from:

```python
    grader = DRGrader(cfg, weights_path=weights_path, model_sha8=_sha8(weights_path))
```

to:

```python
    grader = DRGrader(cfg, weights_path=weights_path, model_sha8=weights_sha8(weights_path))
```

Change the skip-check line from:

```python
        sidecar = output_dir / f"{img.stem}.dr.json"
```

to:

```python
        sidecar = sidecar_path(output_dir, img.name)
```

Change the write line from:

```python
        sidecar.write_text(json.dumps(res.to_dict(), indent=2))
```

to:

```python
        write_sidecar(res, output_dir)
```

- [ ] **Step 7: Verify no stale `_sha8`/`json` references remain and everything imports**

Run: `uv run python -c "import arm101_hand.scripts.dr_grade as m; assert not hasattr(m, '_sha8'); print('ok')"`
Expected: `ok`

Run: `uv run ruff check src/arm101_hand/fundus_analysis/sidecar.py src/arm101_hand/scripts/dr_grade.py src/arm101_hand/fundus_analysis/__init__.py`
Expected: no errors (no unused `hashlib`/`json` imports left behind)

- [ ] **Step 8: Run the full host suite + type-check**

Run: `uv run pytest -m 'not hardware' -q`
Expected: PASS (existing tests + 3 new)

Run: `uv run mypy src`
Expected: no new errors

- [ ] **Step 9: Commit**

```bash
git add src/arm101_hand/fundus_analysis/sidecar.py src/arm101_hand/fundus_analysis/__init__.py src/arm101_hand/scripts/dr_grade.py tests/unit/test_fundus_analysis_sidecar.py
git commit -m "feat(fundus): shared DR sidecar helpers + refactor dr_grade CLI to use them"
```

---

## Task 2: Combined results-panel composition + image-codec helpers

**Files:**
- Create: `src/arm101_hand/fundus_analysis/render.py`
- Create: `tests/unit/test_fundus_analysis_render.py`
- Modify: `src/arm101_hand/fundus_analysis/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fundus_analysis_render.py`:

```python
import numpy as np

from arm101_hand.fundus_analysis.grader import GradeResult
from arm101_hand.fundus_analysis.render import (
    GradedShot,
    _bar_width,
    _most_severe,
    _scale_to_height,
    compose_summary_panel,
    decode_bgr,
    encode_png,
)


def _result(grade: int, label: str, top: float, name: str = "x.JPG") -> GradeResult:
    probs = {"No DR": 0.1, "Mild": 0.1, "Moderate": 0.1, "Severe": 0.1, "Proliferative": 0.1}
    probs[label] = top
    return GradeResult(
        source_image=name,
        grade=grade,
        label=label,
        confidence="MEDIUM",
        probabilities=probs,
        crop={"method": "circle", "box": [0, 0, 1, 1], "fallback": False},
        model={"checkpoint": "w", "sha256_8": "abc", "arch": "vit"},
        preprocess_version="1",
        graded_at_utc="2026-06-11T00:00:00Z",
    )


def _img(h: int, w: int, fill: int) -> np.ndarray:
    return np.full((h, w, 3), fill, np.uint8)


def test_bar_width_clamps_and_scales():
    assert _bar_width(0.0, 100) == 0
    assert _bar_width(1.0, 100) == 100
    assert _bar_width(0.5, 100) == 50
    assert _bar_width(2.0, 100) == 100
    assert _bar_width(-1.0, 100) == 0


def test_most_severe_prefers_higher_grade():
    rs = [_result(0, "No DR", 0.9), _result(3, "Severe", 0.4), _result(1, "Mild", 0.8)]
    assert _most_severe(rs) == 1


def test_most_severe_tie_breaks_on_top_prob_then_latest():
    assert _most_severe([_result(2, "Moderate", 0.6), _result(2, "Moderate", 0.9)]) == 1
    assert _most_severe([_result(2, "Moderate", 0.7), _result(2, "Moderate", 0.7)]) == 1


def test_compose_single_shot_shape_and_image_preserved():
    img = _img(30, 40, 100)
    shot = GradedShot(image_bgr=img, result=_result(2, "Moderate", 0.71), source_name="x.JPG")
    out = compose_summary_panel([shot], panel_width=160, display_height=120)
    assert out.dtype == np.uint8 and out.shape[0] == 120 and out.shape[2] == 3
    left = _scale_to_height(img, 120)
    assert out.shape[1] == left.shape[1] + 160
    assert np.array_equal(out[:, : left.shape[1]], left)  # image pixels never overdrawn
    panel = out[:, left.shape[1] :]
    assert np.any(panel != panel[0, 0])  # text/bars drawn on the panel


def test_compose_multishot_leads_with_most_severe_image():
    mild = GradedShot(image_bgr=_img(20, 20, 50), result=_result(0, "No DR", 0.9), source_name="a.JPG")
    severe = GradedShot(image_bgr=_img(20, 20, 200), result=_result(3, "Severe", 0.7), source_name="b.JPG")
    out = compose_summary_panel([mild, severe], panel_width=160, display_height=120)
    left = _scale_to_height(_img(20, 20, 200), 120)
    assert np.array_equal(out[:, : left.shape[1]], left)


def test_compose_unavailable_returns_valid_frame():
    shot = GradedShot(image_bgr=_img(20, 20, 80), result=None, source_name="a.JPG")
    out = compose_summary_panel(
        [shot], unavailable_reason="grading unavailable -- run export_weights.py", panel_width=160, display_height=120
    )
    assert out.dtype == np.uint8 and out.shape[0] == 120
    assert out.shape[1] == _scale_to_height(_img(20, 20, 80), 120).shape[1] + 160


def test_encode_decode_round_trip():
    frame = _img(40, 50, 123)
    out = decode_bgr(encode_png(frame))
    assert out is not None and out.shape == frame.shape and np.array_equal(out, frame)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arm101_hand.fundus_analysis.render'`

- [ ] **Step 3: Write the implementation**

Create `src/arm101_hand/fundus_analysis/render.py`:

```python
"""Pure composition of the DR results panel + thin cv2 image-codec wrappers.

No model, no network, no disk I/O. The demo composes the ``[ image | panel ]``
frame here (numpy + ``cv2.putText``/``cv2.rectangle`` — NOT cv2-window calls),
PNG-encodes it, and hands the bytes to ``WebcamPreview.show_still``. That keeps
the capture thread the sole cv2-window owner (see ``system_camera/preview.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from arm101_hand.config.fundus_analysis_config import DR_LABELS
from arm101_hand.fundus_analysis.grader import GradeResult

# Severity colours (BGR), grade 0 (No DR) -> grade 4 (Proliferative).
_GRADE_BGR: dict[int, tuple[int, int, int]] = {
    0: (0, 160, 0),
    1: (0, 200, 160),
    2: (0, 190, 230),
    3: (0, 120, 240),
    4: (40, 40, 230),
}
_PANEL_BG = (32, 32, 32)
_TEXT = (235, 235, 235)
_MUTED = (170, 170, 170)
_BAR_BG = (70, 70, 70)
_BAR_FG = (180, 180, 180)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_ABBREV = {"No DR": "No DR", "Mild": "Mild", "Moderate": "Mod", "Severe": "Severe", "Proliferative": "Prolif"}
_DISCLAIMER_LINES = ("Not a medical device.", "Research/educational use only.")


@dataclass(eq=False)
class GradedShot:
    """One captured shot + its grade (``None`` if grading was unavailable/failed)."""

    image_bgr: np.ndarray
    result: GradeResult | None
    source_name: str = ""


def _bar_width(prob: float, max_px: int) -> int:
    """Pixel width of a probability bar; clamps ``prob`` to ``[0, 1]``."""
    p = min(1.0, max(0.0, prob))
    return int(round(p * max_px))


def _most_severe(results: list[GradeResult]) -> int:
    """Index of the most-severe result: max grade, tie -> max top-prob, tie -> latest."""
    best = 0
    for i in range(1, len(results)):
        a, b = results[i], results[best]
        if (a.grade, a.probabilities[a.label]) >= (b.grade, b.probabilities[b.label]):
            best = i
    return best


def _scale_to_height(image_bgr: np.ndarray, height: int) -> np.ndarray:
    """Resize ``image_bgr`` to ``height`` px tall, preserving aspect ratio."""
    h, w = image_bgr.shape[:2]
    if h <= 0:
        return image_bgr
    scale = height / h
    new_w = max(1, int(round(w * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(image_bgr, (new_w, height), interpolation=interp)


def _put_disclaimer(panel: np.ndarray, display_height: int) -> None:
    base = display_height - 14
    for i, line in enumerate(reversed(_DISCLAIMER_LINES)):
        cv2.putText(panel, line, (16, base - i * 18), _FONT, 0.42, _MUTED, 1, cv2.LINE_AA)


def _draw_bars(panel: np.ndarray, result: GradeResult, pred_grade: int, x: int, y: int, panel_width: int) -> None:
    label_w, pct_w, gap, row_h = 86, 46, 8, 24
    bar_x = x + label_w + pct_w + gap
    bar_max = max(10, panel_width - bar_x - 16)
    for i in range(len(DR_LABELS)):
        lbl = DR_LABELS[i]
        p = result.probabilities[lbl]
        pred = i == pred_grade
        tc = _GRADE_BGR.get(pred_grade, _TEXT) if pred else _TEXT
        base = y + i * row_h
        cv2.putText(panel, _ABBREV.get(lbl, lbl), (x, base + 13), _FONT, 0.45, tc, 1, cv2.LINE_AA)
        cv2.putText(panel, f"{p * 100:3.0f}%", (x + label_w, base + 13), _FONT, 0.45, tc, 1, cv2.LINE_AA)
        cv2.rectangle(panel, (bar_x, base + 2), (bar_x + bar_max, base + 16), _BAR_BG, -1)
        w = _bar_width(p, bar_max)
        if w > 0:
            fg = _GRADE_BGR.get(pred_grade, _BAR_FG) if pred else _BAR_FG
            cv2.rectangle(panel, (bar_x, base + 2), (bar_x + w, base + 16), fg, -1)


def _panel_graded(
    graded: list[tuple[np.ndarray, GradeResult]], sev: int, n_total: int, panel_width: int, display_height: int
) -> np.ndarray:
    panel = np.full((display_height, panel_width, 3), _PANEL_BG, np.uint8)
    lead = graded[sev][1]
    gc = _GRADE_BGR.get(lead.grade, _TEXT)
    x = 16
    cv2.putText(panel, f"DR GRADE {lead.grade}", (x, 50), _FONT, 1.1, gc, 2, cv2.LINE_AA)
    cv2.putText(panel, lead.label, (x, 88), _FONT, 0.8, gc, 2, cv2.LINE_AA)
    cv2.putText(panel, f"Confidence: {lead.confidence}", (x, 120), _FONT, 0.55, _TEXT, 1, cv2.LINE_AA)
    y = 152
    if n_total > 1:
        cv2.putText(panel, f"Shots ({len(graded)}/{n_total} graded):", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA)
        y += 24
        for i, (_, r) in enumerate(graded):
            sel = i == sev
            color = _GRADE_BGR.get(r.grade, _TEXT) if sel else _TEXT
            prefix = ">" if sel else " "
            line = f"{prefix} {i + 1}. G{r.grade} {r.label} {r.probabilities[r.label] * 100:3.0f}%"
            cv2.putText(panel, line, (x, y), _FONT, 0.48, color, 1, cv2.LINE_AA)
            y += 22
        y += 12
    cv2.putText(panel, "Class probabilities:", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA)
    y += 22
    _draw_bars(panel, lead, lead.grade, x, y, panel_width)
    _put_disclaimer(panel, display_height)
    return panel


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _panel_unavailable(
    shots: list[GradedShot], reason: str | None, panel_width: int, display_height: int
) -> np.ndarray:
    panel = np.full((display_height, panel_width, 3), _PANEL_BG, np.uint8)
    x = 16
    cv2.putText(panel, "DR GRADING", (x, 50), _FONT, 0.95, _TEXT, 2, cv2.LINE_AA)
    y = 92
    for line in _wrap(reason or "grading unavailable", 30):
        cv2.putText(panel, line, (x, y), _FONT, 0.5, (40, 160, 230), 1, cv2.LINE_AA)
        y += 24
    y += 12
    if shots:
        cv2.putText(panel, f"{len(shots)} shot(s) captured:", (x, y), _FONT, 0.5, _MUTED, 1, cv2.LINE_AA)
        y += 24
        for i, shot in enumerate(shots):
            name = shot.source_name or (shot.result.source_image if shot.result else f"shot {i + 1}")
            cv2.putText(panel, f"  {i + 1}. {name[:24]}", (x, y), _FONT, 0.45, _TEXT, 1, cv2.LINE_AA)
            y += 22
    _put_disclaimer(panel, display_height)
    return panel


def compose_summary_panel(
    shots: list[GradedShot],
    *,
    unavailable_reason: str | None = None,
    panel_width: int = 420,
    display_height: int = 720,
) -> np.ndarray:
    """Compose the ``[ most-severe image | summary panel ]`` BGR frame for the popup.

    Handles ``N >= 1`` shots (``N == 1`` is the single-shot view). Never raises for
    display: with no graded shot it shows ``unavailable_reason`` beside the most
    recent shot's image.
    """
    graded: list[tuple[np.ndarray, GradeResult]] = []
    for s in shots:
        r = s.result
        if r is not None:
            graded.append((s.image_bgr, r))
    if graded:
        sev = _most_severe([r for _, r in graded])
        image = graded[sev][0]
        panel = _panel_graded(graded, sev, len(shots), panel_width, display_height)
    else:
        image = shots[-1].image_bgr if shots else np.zeros((display_height, max(1, display_height), 3), np.uint8)
        panel = _panel_unavailable(shots, unavailable_reason, panel_width, display_height)
    left = _scale_to_height(image, display_height)
    return np.hstack([left, panel])


def encode_png(frame: np.ndarray) -> bytes:
    """PNG-encode a BGR frame (lossless → crisp text) to bytes for ``show_still``."""
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("failed to PNG-encode frame")
    return buf.tobytes()


def decode_bgr(data: bytes) -> np.ndarray | None:
    """Decode JPEG/PNG bytes to a BGR array; ``None`` if the bytes are not an image."""
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fundus_analysis_render.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Export the new symbols**

Edit `src/arm101_hand/fundus_analysis/__init__.py` — add:

```python
from arm101_hand.fundus_analysis.render import (
    GradedShot,
    compose_summary_panel,
    decode_bgr,
    encode_png,
)
```

Add `"GradedShot"`, `"compose_summary_panel"`, `"decode_bgr"`, `"encode_png"` to `__all__`.

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff format src/arm101_hand/fundus_analysis/render.py tests/unit/test_fundus_analysis_render.py`
Run: `uv run ruff check src/arm101_hand/fundus_analysis/render.py src/arm101_hand/fundus_analysis/__init__.py tests/unit/test_fundus_analysis_render.py`
Run: `uv run mypy src`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/fundus_analysis/render.py src/arm101_hand/fundus_analysis/__init__.py tests/unit/test_fundus_analysis_render.py
git commit -m "feat(fundus): combined DR results-panel composition + image-codec helpers"
```

---

## Task 3: Config fields `inline_grading` + `captures_per_patient`

**Files:**
- Modify: `src/arm101_hand/config/fundus_analysis_config.py`
- Modify: `src/arm101_hand/data/fundus_analysis_config.yaml`
- Modify: `tests/unit/test_fundus_analysis_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_fundus_analysis_config.py`:

```python
def test_inline_grading_and_captures_defaults():
    cfg = load_fundus_analysis_config()
    assert cfg.inline_grading is True
    assert cfg.captures_per_patient == 1


def test_captures_per_patient_must_be_positive():
    with pytest.raises(ValidationError):
        FundusAnalysisConfig(captures_per_patient=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_config.py -v`
Expected: FAIL — `AttributeError: 'FundusAnalysisConfig' object has no attribute 'inline_grading'`

- [ ] **Step 3: Add the schema fields**

In `src/arm101_hand/config/fundus_analysis_config.py`, inside `FundusAnalysisConfig`, add these two lines immediately after `preprocess_version: str = "1"`:

```python
    inline_grading: bool = True
    captures_per_patient: int = Field(1, ge=1)
```

(`Field` is already imported.)

- [ ] **Step 4: Add the YAML defaults**

In `src/arm101_hand/data/fundus_analysis_config.yaml`, add two lines right after the `preprocess_version: "1"` line:

```yaml
inline_grading: true
captures_per_patient: 1
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fundus_analysis_config.py -v`
Expected: PASS

- [ ] **Step 6: Lint + type-check**

Run: `uv run ruff check src/arm101_hand/config/fundus_analysis_config.py`
Run: `uv run mypy src`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/fundus_analysis_config.py src/arm101_hand/data/fundus_analysis_config.yaml tests/unit/test_fundus_analysis_config.py
git commit -m "feat(fundus): inline_grading + captures_per_patient config fields"
```

---

## Task 4: Wire inline grading + combined panel into `grab_trigger_capture`

**Files:**
- Modify: `scripts/demos/grab_trigger_capture.py`

> Note: this script is integration glue over the units tested in Tasks 1–3. It is not unit-tested (it needs the arm, hand, Aurora, and 1.2 GB weights). The automated gate here is ruff + a `--help` smoke run + `mypy src` (still clean); a manual hardware acceptance test is in Step 6. The script lives in `scripts/`, which `uv run mypy src` does not type-check.

- [ ] **Step 1: Add imports**

At the top of `scripts/demos/grab_trigger_capture.py`, add `import argparse` next to the other stdlib imports. Then add these package imports alongside the existing `from arm101_hand...` block:

```python
from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis import (
    DRGrader,
    GradedShot,
    compose_summary_panel,
    decode_bgr,
    encode_png,
    weights_sha8,
    write_sidecar,
)
```

- [ ] **Step 2: Add the module-level analysis helper**

Add this function near the other module-level helpers (e.g. after `_start_preview`):

```python
def _analyze_turn(
    grader: DRGrader | None,
    shots: list[tuple[bytes, str]],
    output_dir: Path,
    preview: WebcamPreview | None,
    unavailable_reason: str,
) -> None:
    """Grade every shot of the patient turn, write a sidecar each, show the combined panel.

    Sidecars are written whenever the grader is available, even with no preview window
    (so inline and batch artifacts stay identical); only the popup is gated on ``preview``.
    """
    graded: list[GradedShot] = []
    for data, name in shots:
        img = decode_bgr(data)
        if img is None:
            print(f"  WARNING: could not decode {name} for grading -- skipping.")
            continue
        result = None
        if grader is not None:
            try:
                result = grader.grade_array(img, source_name=name)
                sidecar = write_sidecar(result, output_dir)
                print(
                    f"  graded {name}: grade {result.grade} ({result.label}), "
                    f"{result.confidence} -> {sidecar.name}"
                )
            except Exception as e:
                print(f"  WARNING: grading failed for {name}: {e}")
        graded.append(GradedShot(image_bgr=img, result=result, source_name=name))
    if not graded:
        print("  nothing to display.")
        return
    if any(s.result is not None for s in graded):
        print("  Research/educational use only. Not a medical device; not for clinical diagnosis.")
    if preview is not None:
        composite = compose_summary_panel(graded, unavailable_reason=unavailable_reason)
        preview.show_still(encode_png(composite))
        print("  (combined results shown in the popup until the next capture)")
```

- [ ] **Step 3: Parse args + load the grader at startup in `main`**

At the very top of `main()` (before `cfg = load_fundus_config(...)`), add argument parsing so `--help` works without any hardware:

```python
    parser = argparse.ArgumentParser(description="Robot-triggered Aurora capture with inline DR grading.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--grade", dest="grade", action="store_true", default=None, help="force inline DR grading ON"
    )
    group.add_argument(
        "--no-grade", dest="grade", action="store_false", default=None, help="force inline DR grading OFF (capture-only)"
    )
    args = parser.parse_args()
```

Then, after the existing `fundus_dir = _REPO_ROOT / cap.fundus_dir` line, add the grading setup:

```python
    acfg = load_fundus_analysis_config()
    grading_enabled = args.grade if args.grade is not None else acfg.inline_grading
    analysis_output_dir = _REPO_ROOT / acfg.output_dir
    grader: DRGrader | None = None
    grading_reason = "grading disabled (capture-only)"
    if grading_enabled:
        weights = _REPO_ROOT / acfg.models_dir / acfg.weights_filename
        if not weights.is_file():
            grading_reason = "grading unavailable -- run export_weights.py"
            print(f"WARNING: {grading_reason} (missing {weights})", file=sys.stderr)
        else:
            print("Loading DR-grading model (~a few seconds, ~1.2 GB) ...")
            try:
                grader = DRGrader(acfg, weights_path=weights, model_sha8=weights_sha8(weights))
                print("DR-grading model loaded.")
            except Exception as e:
                grading_reason = f"grading failed to load: {e}"
                print(f"WARNING: {grading_reason}", file=sys.stderr)
```

- [ ] **Step 4: Add patient-turn state, the `g` key, and shot accumulation in `_trigger_loop`**

`_trigger_loop` is nested in `main`, so it already closes over `preview`, `grader`, `grading_enabled`, `grading_reason`, `analysis_output_dir`, and `acfg`. Make these edits inside `_trigger_loop`:

(a) Add the turn buffer near the top of `_trigger_loop` (e.g. right after `state = TriggerState(out_base=out_base, side=side)`):

```python
        turn_shots: list[tuple[bytes, str]] = []
```

(b) Update the help/banner line printed at the start of the loop to mention `g`:

```python
        print("Trigger capture: SPACE = capture, g = analyze turn, r = record, [ / ] = press depth, q = exit")
```

(c) In the key loop, add a `g` handler immediately after the existing `r`/`R` block (the one that calls `_toggle_recording`):

```python
                if key in ("g", "G"):
                    if not grading_enabled:
                        print("  grading disabled (capture-only) -- nothing to analyze.")
                        continue
                    if not turn_shots:
                        print("  no shots captured yet -- press SPACE to capture first.")
                        continue
                    print(f"  analyzing {len(turn_shots)} shot(s) ...")
                    _analyze_turn(grader, turn_shots, analysis_output_dir, preview, grading_reason)
                    turn_shots = []
                    print("  patient turn complete -- next SPACE starts a new patient.")
                    continue
```

(d) Replace the existing success block (the lines that currently read `trigger_no["n"] += 1` ... `preview.show_still(data)` ... `(last capture shown ...)`) so the raw image still pops up but the shot is buffered for analysis. Change the tail of that block from:

```python
                    print(f"  SUCCESS: saved {saved.name} ({len(data)} bytes). Ready for next trigger.")
                    if preview is not None:
                        preview.show_still(data)  # pop up the image; stays until the next capture
                        print("  (last capture shown in its popup window until the next trigger)")
```

to:

```python
                    print(f"  SUCCESS: saved {saved.name} ({len(data)} bytes).")
                    turn_shots.append((data, saved.name))
                    if preview is not None:
                        preview.show_still(data)  # raw image while the turn is in progress
                    target = acfg.captures_per_patient
                    if len(turn_shots) >= target:
                        print(f"  shot {len(turn_shots)} captured (target {target} reached) -- press 'g' to analyze, SPACE for another.")
                    else:
                        print(f"  shot {len(turn_shots)}/{target} captured -- SPACE for another, 'g' to analyze.")
```

(e) In the `except KeyboardInterrupt:` handler at the end of `_trigger_loop`, note any un-analyzed shots (no auto-grade — "no surprise movements"):

```python
        except KeyboardInterrupt:
            print("\n^C -- leaving trigger mode")
            if turn_shots:
                print(f"  ({len(turn_shots)} shot(s) captured but not analyzed)")
```

- [ ] **Step 5: Static checks + `--help` smoke run**

Run: `uv run ruff format scripts/demos/grab_trigger_capture.py`
Run: `uv run ruff check scripts/demos/grab_trigger_capture.py`
Expected: clean (ruff F-rules catch any undefined name / unused import)

Run: `uv run mypy src`
Expected: still clean (the script is not under `src`, but the imported package symbols must resolve)

Run: `uv run python scripts/demos/grab_trigger_capture.py --help`
Expected: prints usage including `--grade` and `--no-grade`, exits 0, contacts no hardware

Run: `uv run pytest -m 'not hardware' -q`
Expected: PASS (unchanged from Task 3)

- [ ] **Step 6: Manual hardware acceptance test (operator-run; document the outcome)**

This requires the powered arm + hand, the Aurora (Still mode, Quick imaging ON, Optomed Client closed, a study selected), the USB observation cam, and exported weights. Run:

```
uv run python scripts/fundus_analysis/export_weights.py   # once, if models/ is empty
uv run python scripts/demos/grab_trigger_capture.py
```

Verify:
1. "Loading DR-grading model …" prints at startup, then "DR-grading model loaded."
2. SPACE captures; each pulled image pops up raw; status shows `shot k/1` then the "press 'g'" hint.
3. `g` grades the shot(s); the popup shows the fundus image on the left and a panel on the right with grade, label, confidence, 5 probability bars, and the disclaimer — text on the panel, never over the image.
4. A matching `<stem>.dr.json` appears in `media_outputs/fundus_analysis/`.
5. Rename `models/*.safetensors` aside and re-run: capture + popup still work, the panel reads "grading unavailable -- run export_weights.py", no crash. (Restore the weights afterward.)
6. `--no-grade` runs capture-only (no model load, raw popups only).

- [ ] **Step 7: Commit**

```bash
git add scripts/demos/grab_trigger_capture.py
git commit -m "feat(demos): inline DR grading + combined results panel in grab_trigger_capture"
```

---

## Task 5: Documentation refresh + Iron-Law audit

**Files:**
- Modify: `CLAUDE.md`, `README.md` (via doc_update skill)

- [ ] **Step 1: Refresh the top-level docs**

Invoke the **doc_update** skill. It must reflect:
- The `grab_trigger_capture` demo now grades each capture inline (per-patient turn, `g` to analyze) and shows a combined DR results panel beside the image, writing `.dr.json` sidecars identical to the batch CLI.
- The `--grade`/`--no-grade` flag + `inline_grading` / `captures_per_patient` config fields in `data/fundus_analysis_config.yaml`.
- `fundus_analysis/` now also exposes `render.py` (panel composition) and `sidecar.py` (shared sidecar/sha8 helpers).
- Tech-debt §7: Phase 2 ("wire `DRGrader` into `grab_trigger_capture`") moves from "remaining" to done; note it is still research/educational, not diagnostic.

- [ ] **Step 2: Verify docs are consistent**

Run: `uv run pytest -m 'not hardware' -q && uv run ruff check . && uv run mypy src`
Expected: all clean

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: refresh CLAUDE.md/README for Phase-2 inline DR grading"
```

- [ ] **Step 4: Iron-Law audit**

Invoke the **iron-law-audit** skill on the branch diff. Confirm IL-2 (references untouched), IL-5 (config in-tree; sidecars/weights gitignored), IL-7 (single canonical home for the Phase-2 fact). Address any finding before opening the PR.

---

## Self-Review

**Spec coverage:**
- §3 caller-composes / `show_still` unchanged → Task 2 (render) + Task 4 (demo calls `show_still(encode_png(...))`); `preview.py` untouched. ✓
- §4 new modules `render.py` / `sidecar.py` + `__init__` exports + CLI refactor + config fields → Tasks 1–3. ✓
- §5a toggle (config default + CLI override) → Task 3 (field) + Task 4 Step 3. ✓
- §5b startup load + graceful degradation → Task 4 Step 3. ✓
- §5c patient turn / SPACE raw popup / `g` analyze / reset / quit note → Task 4 Step 4. ✓
- §5c grade-even-without-preview → `_analyze_turn` writes sidecars before the `preview is not None` gate. ✓
- §6 combined panel (most-severe lead, bars, per-shot list hidden at N=1, severity colour, unavailable path) → Task 2 `_panel_graded`/`_panel_unavailable`. ✓
- §7 host tests (no weights) → Tasks 1–3 tests. ✓
- §8 Iron Laws → Task 5 Step 4. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code; commands have expected output. ✓

**Type consistency:** `GradedShot(image_bgr, result, source_name)`, `compose_summary_panel(shots, *, unavailable_reason, panel_width, display_height)`, `write_sidecar(result, output_dir)`, `weights_sha8(path)`, `sidecar_path(output_dir, source_name)`, `decode_bgr`/`encode_png` — names match across the render/sidecar tasks and the Task 4 demo call sites. ✓
