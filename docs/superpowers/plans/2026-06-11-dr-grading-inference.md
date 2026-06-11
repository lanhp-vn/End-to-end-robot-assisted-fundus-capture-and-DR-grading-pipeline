# DR-Grading Inference Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone batch CLI (`arm101-dr-grade`) that grades diabetic-retinopathy severity (0–4) on Optomed Aurora fundus photos using the RETFound ViT-L/16 checkpoint fine-tuned on APTOS2019, plus a one-time slim-weight export and a full APTOS test-split validation harness.

**Architecture:** Three-layer (`docs/conventions/01-module-layering.md`). Primitive: pydantic config (`config/fundus_analysis_config.py`) + pure preprocessing (`fundus_analysis/preprocess.py`). Device: model load + `DRGrader` (`fundus_analysis/model.py`, `grader.py`) — owns the heavy model resource, load-once, reusable by Phase 2. Application: batch CLI (`scripts/dr_grade.py`) + two operator scripts (`scripts/fundus_analysis/export_weights.py`, `aptos_eval.py`).

**Tech Stack:** Python 3.12, PyTorch 2.10 CPU, timm 1.0.27 (`vit_large_patch16_224`, `global_pool='avg'`, `strict=True` load — verified exact 296/296 key match), torchvision transforms, safetensors, OpenCV (circle-crop), pydantic v2, pytest. scikit-learn (dev) for eval metrics.

**Verified constants (do not re-derive):**
- Eval transform: `Resize(256, BICUBIC)` → `CenterCrop(224)` → `ToTensor` → `Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225])`.
- Model: `timm.create_model("vit_large_patch16_224", num_classes=5, global_pool="avg")`, then `load_state_dict(state_dict, strict=True)`.
- Checkpoint: dict with `model/optimizer/epoch/scaler/args`; `args` is an `argparse.Namespace` (allowlist for `weights_only=True`). Slim model state_dict = `checkpoint["model"]` (296 tensors, ~1.2 GB fp32).
- Labels: `{0:"No DR", 1:"Mild", 2:"Moderate", 3:"Severe", 4:"Proliferative"}` (APTOS folder alphabetical: anodr/bmilddr/cmoderatedr/dseveredr/eproliferativedr).
- Confidence: HIGH ≥0.80, MEDIUM ≥0.65, else LOW.

---

## File structure

| File | Responsibility |
|---|---|
| `src/arm101_hand/config/fundus_analysis_config.py` | pydantic schema (paths, confidence bands, crop params, label map) + YAML loader |
| `src/arm101_hand/data/fundus_analysis_config.yaml` | operator defaults (in-tree, never written at runtime) |
| `src/arm101_hand/fundus_analysis/__init__.py` | public API re-exports: `DRGrader`, `GradeResult`, `DR_LABELS` |
| `src/arm101_hand/fundus_analysis/preprocess.py` | `circle_crop()`, `CropInfo`, `build_eval_transform()`, `confidence_band()` |
| `src/arm101_hand/fundus_analysis/model.py` | `build_model()`, `load_weights()`, `WEIGHTS_FILENAME`, `EXPECTED_KEY_COUNT` |
| `src/arm101_hand/fundus_analysis/grader.py` | `DRGrader` (load-once), `GradeResult` |
| `src/arm101_hand/scripts/dr_grade.py` | console script `arm101-dr-grade` — batch driver |
| `scripts/fundus_analysis/export_weights.py` | one-time checkpoint → slim safetensors |
| `scripts/fundus_analysis/aptos_eval.py` | full APTOS test-split metrics |
| `scripts/fundus_analysis/README.md` | run order + recorded validation metrics |
| `tests/unit/test_fundus_analysis_preprocess.py` | crop / transform / confidence-band tests |
| `tests/unit/test_fundus_analysis_config.py` | config schema tests |
| `tests/unit/test_fundus_analysis_grader.py` | GradeResult serialization + softmax→grade logic (mock model) |
| `pyproject.toml` | add `timm`, dev `scikit-learn`, console-script entry, package-data |
| `.gitignore` | add `models/` |
| `CLAUDE.md` / `README.md` | doc refresh (final task) |

---

## Task 0: Dependencies & gitignore

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add timm to dependencies and scikit-learn to dev**

In `pyproject.toml`, the `[project].dependencies` list — add after the opencv line:

```toml
    "opencv-python>=4.9",  # full build (HighGUI) for the live USB-camera preview; see [tool.uv] below
    "timm>=1.0,<1.1",  # ViT-L/16 builder for RETFound DR-grading inference (exact key match verified)
```

And `[project.optional-dependencies]`:

```toml
dev = ["ruff>=0.8", "mypy>=1.10", "pytest>=8.0", "types-PyYAML>=6.0", "scikit-learn>=1.4"]
```

- [ ] **Step 2: Register the console script**

In `[project.scripts]`:

```toml
arm101-dr-grade = "arm101_hand.scripts.dr_grade:main"
```

- [ ] **Step 3: Add models/ to .gitignore**

Append to `.gitignore`:

```
# Slim model weights exported from references/ checkpoints (large, regenerable)
models/
```

- [ ] **Step 4: Sync and verify**

Run: `uv sync --extra dev`
Expected: resolves; `timm==1.0.x` and `scikit-learn` installed.

Run: `uv run python -c "import timm, sklearn, safetensors; print(timm.__version__, sklearn.__version__)"`
Expected: prints versions, no error.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore
git commit -m "build(fundus): add timm + sklearn deps, arm101-dr-grade entry, gitignore models/"
```

---

## Task 1: Config schema (primitive layer)

**Files:**
- Create: `src/arm101_hand/config/fundus_analysis_config.py`
- Create: `src/arm101_hand/data/fundus_analysis_config.yaml`
- Test: `tests/unit/test_fundus_analysis_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_fundus_analysis_config.py
import pytest
from pydantic import ValidationError

from arm101_hand.config.fundus_analysis_config import (
    DR_LABELS,
    FundusAnalysisConfig,
    load_fundus_analysis_config,
)


def test_default_config_loads_from_yaml():
    cfg = load_fundus_analysis_config()
    assert cfg.input_size == 224
    assert cfg.num_classes == 5
    assert cfg.confidence.high >= cfg.confidence.medium
    assert cfg.weights_filename.endswith(".safetensors")


def test_label_map_is_canonical_aptos_order():
    assert DR_LABELS == {
        0: "No DR",
        1: "Mild",
        2: "Moderate",
        3: "Severe",
        4: "Proliferative",
    }


def test_confidence_bands_rejects_inverted_thresholds():
    with pytest.raises(ValidationError):
        FundusAnalysisConfig(confidence={"high": 0.5, "medium": 0.8})


def test_confidence_thresholds_in_unit_interval():
    with pytest.raises(ValidationError):
        FundusAnalysisConfig(confidence={"high": 1.5, "medium": 0.8})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_config.py -v`
Expected: FAIL — `ModuleNotFoundError: arm101_hand.config.fundus_analysis_config`.

- [ ] **Step 3: Write the config schema**

```python
# src/arm101_hand/config/fundus_analysis_config.py
"""Pydantic config for DR-grading inference (primitive layer).

Loaded from ``src/arm101_hand/data/fundus_analysis_config.yaml``. Operator-editable;
never written at runtime (IL-5).
"""

from __future__ import annotations

from importlib import resources
from typing import Final

import yaml
from pydantic import BaseModel, Field, model_validator

# Canonical APTOS2019 5-class severity map (ImageFolder alphabetical order).
DR_LABELS: Final[dict[int, str]] = {
    0: "No DR",
    1: "Mild",
    2: "Moderate",
    3: "Severe",
    4: "Proliferative",
}


class ConfidenceBands(BaseModel):
    """Top-probability thresholds for the confidence label."""

    high: float = Field(0.80, ge=0.0, le=1.0)
    medium: float = Field(0.65, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> "ConfidenceBands":
        if self.high < self.medium:
            raise ValueError("confidence.high must be >= confidence.medium")
        return self


class CircleCropParams(BaseModel):
    """Parameters for the fundus circle-crop preprocessor."""

    background_threshold: int = Field(12, ge=0, le=255)
    margin_frac: float = Field(0.02, ge=0.0, le=0.25)
    min_disc_area_frac: float = Field(0.05, ge=0.0, le=1.0)


class FundusAnalysisConfig(BaseModel):
    """Top-level DR-grading config."""

    model_arch: str = "vit_large_patch16_224"
    input_size: int = 224
    num_classes: int = 5
    weights_filename: str = "retfound_aptos2019_vitl16.safetensors"
    models_dir: str = "models"
    images_dir: str = "media_outputs/fundus_images"
    output_dir: str = "media_outputs/fundus_analysis"
    preprocess_version: str = "1"
    confidence: ConfidenceBands = ConfidenceBands()
    crop: CircleCropParams = CircleCropParams()


def load_fundus_analysis_config() -> FundusAnalysisConfig:
    """Load the bundled YAML defaults into a validated config object."""
    text = resources.files("arm101_hand.data").joinpath("fundus_analysis_config.yaml").read_text()
    data = yaml.safe_load(text) or {}
    return FundusAnalysisConfig(**data)
```

- [ ] **Step 4: Write the YAML defaults**

```yaml
# src/arm101_hand/data/fundus_analysis_config.yaml
# DR-grading inference operator config. Edit by hand; never written at runtime (IL-5).
model_arch: vit_large_patch16_224
input_size: 224
num_classes: 5
weights_filename: retfound_aptos2019_vitl16.safetensors
models_dir: models
images_dir: media_outputs/fundus_images
output_dir: media_outputs/fundus_analysis
preprocess_version: "1"
confidence:
  high: 0.80
  medium: 0.65
crop:
  background_threshold: 12
  margin_frac: 0.02
  min_disc_area_frac: 0.05
```

- [ ] **Step 5: Ensure package-data ships the YAML**

Confirm `pyproject.toml` `[tool.setuptools.package-data]` includes `data/*.yaml` (it already does: `arm101_hand = ["data/*.yaml", "data/*.md"]`). No change needed; verify by reading.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fundus_analysis_config.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/arm101_hand/config/fundus_analysis_config.py src/arm101_hand/data/fundus_analysis_config.yaml tests/unit/test_fundus_analysis_config.py
git commit -m "feat(fundus): DR-grading config schema + YAML defaults"
```

---

## Task 2: Preprocessing (pure functions)

**Files:**
- Create: `src/arm101_hand/fundus_analysis/__init__.py` (minimal for now)
- Create: `src/arm101_hand/fundus_analysis/preprocess.py`
- Test: `tests/unit/test_fundus_analysis_preprocess.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_fundus_analysis_preprocess.py
import numpy as np
import torch

from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)


def _disc_on_black(h=1776, w=2368, cx=1184, cy=888, r=700) -> np.ndarray:
    """A bright fundus-like disc on a near-black background (BGR uint8)."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    yy, xx = np.ogrid[:h, :w]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    img[mask] = (180, 170, 160)
    return img


def test_circle_crop_returns_square_box_around_disc():
    img = _disc_on_black()
    cropped, info = circle_crop(img)
    assert isinstance(info, CropInfo)
    assert info.method == "circle"
    assert info.fallback is False
    h, w = cropped.shape[:2]
    assert abs(h - w) <= 2  # square
    x0, y0, x1, y1 = info.box
    assert x0 < 1184 < x1 and y0 < 888 < y1  # disc center inside box


def test_circle_crop_falls_back_on_all_black():
    img = np.zeros((1000, 1200, 3), dtype=np.uint8)
    cropped, info = circle_crop(img)
    assert info.fallback is True
    assert info.method == "center"
    h, w = cropped.shape[:2]
    assert h == w == 1000  # central square of the shorter side


def test_eval_transform_output_shape():
    t = build_eval_transform(input_size=224)
    from PIL import Image

    pil = Image.fromarray(np.full((300, 300, 3), 128, dtype=np.uint8))
    out = t(pil)
    assert isinstance(out, torch.Tensor)
    assert out.shape == (3, 224, 224)


def test_confidence_band_boundaries():
    assert confidence_band(0.80, high=0.80, medium=0.65) == "HIGH"
    assert confidence_band(0.79, high=0.80, medium=0.65) == "MEDIUM"
    assert confidence_band(0.65, high=0.80, medium=0.65) == "MEDIUM"
    assert confidence_band(0.64, high=0.80, medium=0.65) == "LOW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_preprocess.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `preprocess.py`**

```python
# src/arm101_hand/fundus_analysis/preprocess.py
"""Pure fundus preprocessing: circle-crop + eval transform + confidence band.

No model, no disk I/O beyond the array passed in. cv2 uses BGR; the transform
expects an RGB PIL image, so callers convert before transforming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from torchvision import transforms

# timm.data.constants IMAGENET_DEFAULT_MEAN / STD
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class CropInfo:
    """Where and how the fundus disc was cropped."""

    method: Literal["circle", "center"]
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in source pixels
    fallback: bool


def _center_square(img: np.ndarray) -> tuple[np.ndarray, CropInfo]:
    h, w = img.shape[:2]
    side = min(h, w)
    x0 = (w - side) // 2
    y0 = (h - side) // 2
    box = (x0, y0, x0 + side, y0 + side)
    return img[y0 : y0 + side, x0 : x0 + side], CropInfo("center", box, fallback=True)


def circle_crop(
    img_bgr: np.ndarray,
    *,
    background_threshold: int = 12,
    margin_frac: float = 0.02,
    min_disc_area_frac: float = 0.05,
) -> tuple[np.ndarray, CropInfo]:
    """Crop a tight square around the fundus disc.

    Threshold the near-black background, take the bounding box of the largest
    bright contour, expand by ``margin_frac`` of the disc size, and square it.
    Falls back to a center square if no disc large enough is found.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, background_threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _center_square(img_bgr)

    h, w = img_bgr.shape[:2]
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < min_disc_area_frac * h * w:
        return _center_square(img_bgr)

    x, y, bw, bh = cv2.boundingRect(largest)
    margin = int(margin_frac * max(bw, bh))
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(w, x + bw + margin)
    y1 = min(h, y + bh + margin)

    # square the box around its center, clamped to image bounds
    side = min(max(x1 - x0, y1 - y0), min(h, w))
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    sx0 = max(0, min(cx - side // 2, w - side))
    sy0 = max(0, min(cy - side // 2, h - side))
    box = (sx0, sy0, sx0 + side, sy0 + side)
    return img_bgr[sy0 : sy0 + side, sx0 : sx0 + side], CropInfo("circle", box, fallback=False)


def build_eval_transform(input_size: int = 224) -> transforms.Compose:
    """The exact RETFound eval transform (util/datasets.py:50-58)."""
    crop_pct = 224 / 256 if input_size <= 224 else 1.0
    resize = int(input_size / crop_pct)
    return transforms.Compose(
        [
            transforms.Resize(resize, interpolation=transforms.InterpolationMode.BICUBIC),
            transforms.CenterCrop(input_size),
            transforms.ToTensor(),
            transforms.Normalize(_IMAGENET_MEAN, _IMAGENET_STD),
        ]
    )


def confidence_band(top_prob: float, *, high: float, medium: float) -> Literal["HIGH", "MEDIUM", "LOW"]:
    """Map the top softmax probability to a confidence label."""
    if top_prob >= high:
        return "HIGH"
    if top_prob >= medium:
        return "MEDIUM"
    return "LOW"
```

- [ ] **Step 4: Write minimal `__init__.py`**

```python
# src/arm101_hand/fundus_analysis/__init__.py
"""Diabetic-retinopathy grading from fundus photos (device layer).

Local, offline inference only — no network calls. Research/educational use;
not a medical device.
"""

from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

__all__ = ["CropInfo", "build_eval_transform", "circle_crop", "confidence_band"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fundus_analysis_preprocess.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/fundus_analysis/__init__.py src/arm101_hand/fundus_analysis/preprocess.py tests/unit/test_fundus_analysis_preprocess.py
git commit -m "feat(fundus): circle-crop + eval transform + confidence band"
```

---

## Task 3: Model builder + weight loader (device layer)

**Files:**
- Create: `src/arm101_hand/fundus_analysis/model.py`

- [ ] **Step 1: Write `model.py`**

No host unit test loads real weights (1.2 GB, hardware-gated). This module is
exercised by the grader test (mock) and the hardware path. Keep it tiny and obvious.

```python
# src/arm101_hand/fundus_analysis/model.py
"""Build the RETFound ViT-L/16 and load slim safetensors weights.

Verified: timm.create_model("vit_large_patch16_224", num_classes=5,
global_pool="avg") matches the APTOS checkpoint state_dict exactly (296/296 keys),
so load_state_dict(strict=True) is a built-in correctness gate.
"""

from __future__ import annotations

from pathlib import Path

import timm
import torch
from safetensors.torch import load_file

WEIGHTS_FILENAME = "retfound_aptos2019_vitl16.safetensors"
EXPECTED_KEY_COUNT = 296


def build_model(arch: str = "vit_large_patch16_224", num_classes: int = 5) -> torch.nn.Module:
    """Construct the 5-class ViT-L/16 with global average pooling, eval mode."""
    model = timm.create_model(arch, num_classes=num_classes, global_pool="avg")
    model.eval()
    return model


def load_weights(model: torch.nn.Module, weights_path: str | Path) -> None:
    """Load slim safetensors weights with strict=True (fails loudly on mismatch)."""
    weights_path = Path(weights_path)
    if not weights_path.is_file():
        raise FileNotFoundError(
            f"Slim weights not found at {weights_path}. "
            f"Run: uv run python scripts/fundus_analysis/export_weights.py"
        )
    state_dict = load_file(str(weights_path))
    model.load_state_dict(state_dict, strict=True)
```

- [ ] **Step 2: Quick import smoke check**

Run: `uv run python -c "from arm101_hand.fundus_analysis.model import build_model; m=build_model(); print(sum(1 for _ in m.state_dict()), 'tensors')"`
Expected: prints `296 tensors`, no error.

- [ ] **Step 3: Commit**

```bash
git add src/arm101_hand/fundus_analysis/model.py
git commit -m "feat(fundus): ViT-L/16 builder + strict safetensors weight loader"
```

---

## Task 4: Slim-weight export script

**Files:**
- Create: `scripts/fundus_analysis/export_weights.py`

- [ ] **Step 1: Write `export_weights.py`**

```python
# scripts/fundus_analysis/export_weights.py
"""One-time: extract model weights from the 3.64 GB APTOS checkpoint into a slim,
pickle-free safetensors file under models/ (gitignored).

IL-2: references/ is read-only — we only READ the checkpoint. IL-5: the slim file
lands outside the tree in models/.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import torch
from safetensors.torch import save_file

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = REPO_ROOT / "references" / "AIML-models" / "APTOS2019" / "checkpoint-best.pth"
OUT_PATH = REPO_ROOT / "models" / "retfound_aptos2019_vitl16.safetensors"
EXPECTED_KEY_COUNT = 296


def main() -> None:
    parser = argparse.ArgumentParser(description="Export slim RETFound APTOS weights.")
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--force", action="store_true", help="Overwrite if the slim file exists.")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        print(f"[skip] {args.out} already exists. Use --force to overwrite.")
        return
    if not args.checkpoint.is_file():
        raise SystemExit(f"Checkpoint not found: {args.checkpoint}")

    print(f"[load] {args.checkpoint} (this reads ~3.6 GB; ~30-60 s on CPU) ...")
    with torch.serialization.safe_globals([argparse.Namespace]):
        ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    state_dict = ckpt["model"]
    if len(state_dict) != EXPECTED_KEY_COUNT:
        raise SystemExit(f"Expected {EXPECTED_KEY_COUNT} tensors, got {len(state_dict)}")

    # safetensors requires contiguous tensors and rejects shared storage.
    state_dict = {k: v.contiguous().clone() for k, v in state_dict.items()}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_file(state_dict, str(args.out))

    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    size_gb = args.out.stat().st_size / 1e9
    print(f"[done] {args.out}  ({size_gb:.2f} GB)")
    print(f"[sha256] {digest}")
    print(f"[epoch] {ckpt.get('epoch')}  [tensors] {len(state_dict)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the export (hardware/heavy — produces the 1.2 GB file)**

Run: `uv run python scripts/fundus_analysis/export_weights.py`
Expected: prints `[done] ...models\retfound_aptos2019_vitl16.safetensors (~1.2 GB)`, a sha256, and `[tensors] 296`.

- [ ] **Step 3: Verify the slim weights load into the model (strict=True)**

Run:
```
uv run python -c "from arm101_hand.fundus_analysis.model import build_model, load_weights; from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config; from pathlib import Path; cfg=load_fundus_analysis_config(); m=build_model(); load_weights(m, Path(cfg.models_dir)/cfg.weights_filename); print('strict load OK')"
```
Expected: `strict load OK` (no missing/unexpected key error).

- [ ] **Step 4: Commit (script only — models/ is gitignored)**

```bash
git add scripts/fundus_analysis/export_weights.py
git commit -m "feat(fundus): one-time slim-weight export to safetensors"
```

---

## Task 5: DRGrader + GradeResult (device layer)

**Files:**
- Create: `src/arm101_hand/fundus_analysis/grader.py`
- Modify: `src/arm101_hand/fundus_analysis/__init__.py` (export DRGrader, GradeResult, DR_LABELS)
- Test: `tests/unit/test_fundus_analysis_grader.py`

- [ ] **Step 1: Write the failing test (mock model, no real weights)**

```python
# tests/unit/test_fundus_analysis_grader.py
import numpy as np
import torch

from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis.grader import DRGrader, GradeResult


class _StubModel(torch.nn.Module):
    """Returns fixed logits favouring class 2 (Moderate)."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        n = x.shape[0]
        logits = torch.tensor([[0.1, 0.2, 3.0, 0.1, 0.05]], dtype=torch.float32)
        return logits.repeat(n, 1)


def _grader() -> DRGrader:
    cfg = load_fundus_analysis_config()
    return DRGrader(cfg, model=_StubModel())


def test_grade_array_returns_moderate():
    g = _grader()
    img = np.full((1776, 2368, 3), 130, dtype=np.uint8)
    res = g.grade_array(img, source_name="x.JPG")
    assert isinstance(res, GradeResult)
    assert res.grade == 2
    assert res.label == "Moderate"
    assert set(res.probabilities) == {"No DR", "Mild", "Moderate", "Severe", "Proliferative"}
    assert abs(sum(res.probabilities.values()) - 1.0) < 1e-5
    assert res.confidence in {"HIGH", "MEDIUM", "LOW"}


def test_grade_result_to_dict_has_required_fields_and_disclaimer():
    g = _grader()
    img = np.full((1776, 2368, 3), 130, dtype=np.uint8)
    d = g.grade_array(img, source_name="x.JPG").to_dict()
    for key in (
        "source_image",
        "grade",
        "label",
        "confidence",
        "probabilities",
        "crop",
        "model",
        "preprocess_version",
        "graded_at_utc",
        "disclaimer",
    ):
        assert key in d
    assert "not a medical device" in d["disclaimer"].lower()
    assert d["graded_at_utc"].endswith("Z")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fundus_analysis_grader.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `grader.py`**

```python
# src/arm101_hand/fundus_analysis/grader.py
"""DRGrader: load-once diabetic-retinopathy grader. Reusable by Phase 2.

Local/offline only — no network calls anywhere in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from arm101_hand.config.fundus_analysis_config import DR_LABELS, FundusAnalysisConfig
from arm101_hand.fundus_analysis.model import build_model, load_weights
from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

_DISCLAIMER = (
    "Research/educational use only. Not a medical device; not for clinical diagnosis."
)


@dataclass(frozen=True)
class GradeResult:
    """One image's grading outcome (serializes straight to the JSON sidecar)."""

    source_image: str
    grade: int
    label: str
    confidence: str
    probabilities: dict[str, float]
    crop: dict[str, object]
    model: dict[str, str]
    preprocess_version: str
    graded_at_utc: str
    disclaimer: str = _DISCLAIMER

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DRGrader:
    """Construct the model + transform once; grade many images."""

    def __init__(
        self,
        config: FundusAnalysisConfig,
        *,
        model: torch.nn.Module | None = None,
        weights_path: Path | None = None,
        model_sha8: str = "unknown",
    ) -> None:
        self._cfg = config
        self._transform = build_eval_transform(config.input_size)
        self._model_sha8 = model_sha8
        if model is not None:
            self._model = model
        else:
            self._model = build_model(config.model_arch, config.num_classes)
            wp = weights_path or (Path(config.models_dir) / config.weights_filename)
            load_weights(self._model, wp)
        self._model.eval()

    def grade_array(self, img_bgr: np.ndarray, *, source_name: str) -> GradeResult:
        cropped, info = circle_crop(
            img_bgr,
            background_threshold=self._cfg.crop.background_threshold,
            margin_frac=self._cfg.crop.margin_frac,
            min_disc_area_frac=self._cfg.crop.min_disc_area_frac,
        )
        rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
        tensor = self._transform(Image.fromarray(rgb)).unsqueeze(0)
        with torch.inference_mode():
            logits = self._model(tensor)
            probs = torch.softmax(logits, dim=1)[0]
        grade = int(torch.argmax(probs).item())
        top_prob = float(probs[grade].item())
        return GradeResult(
            source_image=source_name,
            grade=grade,
            label=DR_LABELS[grade],
            confidence=confidence_band(
                top_prob, high=self._cfg.confidence.high, medium=self._cfg.confidence.medium
            ),
            probabilities={DR_LABELS[i]: round(float(probs[i].item()), 6) for i in range(len(DR_LABELS))},
            crop=self._crop_to_dict(info),
            model={
                "checkpoint": self._cfg.weights_filename,
                "sha256_8": self._model_sha8,
                "arch": self._cfg.model_arch,
            },
            preprocess_version=self._cfg.preprocess_version,
            graded_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def grade_path(self, image_path: Path) -> GradeResult:
        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Could not read image: {image_path}")
        return self.grade_array(img, source_name=image_path.name)

    @staticmethod
    def _crop_to_dict(info: CropInfo) -> dict[str, object]:
        return {"method": info.method, "box": list(info.box), "fallback": info.fallback}
```

- [ ] **Step 4: Update `__init__.py` to export the public API**

```python
# src/arm101_hand/fundus_analysis/__init__.py
"""Diabetic-retinopathy grading from fundus photos (device layer).

Local, offline inference only — no network calls. Research/educational use;
not a medical device.
"""

from arm101_hand.config.fundus_analysis_config import DR_LABELS
from arm101_hand.fundus_analysis.grader import DRGrader, GradeResult
from arm101_hand.fundus_analysis.preprocess import (
    CropInfo,
    build_eval_transform,
    circle_crop,
    confidence_band,
)

__all__ = [
    "DRGrader",
    "GradeResult",
    "DR_LABELS",
    "CropInfo",
    "build_eval_transform",
    "circle_crop",
    "confidence_band",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_fundus_analysis_grader.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/arm101_hand/fundus_analysis/grader.py src/arm101_hand/fundus_analysis/__init__.py tests/unit/test_fundus_analysis_grader.py
git commit -m "feat(fundus): DRGrader load-once grader + GradeResult sidecar model"
```

---

## Task 6: Batch CLI (`arm101-dr-grade`)

**Files:**
- Create: `src/arm101_hand/scripts/dr_grade.py`

- [ ] **Step 1: Write `dr_grade.py`**

```python
# src/arm101_hand/scripts/dr_grade.py
"""arm101-dr-grade — batch-grade fundus photos for DR severity.

Reads media_outputs/fundus_images/*.JPG, writes <stem>.dr.json sidecars to
media_outputs/fundus_analysis/, prints a summary table. Local/offline only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from arm101_hand.config.fundus_analysis_config import load_fundus_analysis_config
from arm101_hand.fundus_analysis.grader import DRGrader

REPO_ROOT = Path(__file__).resolve().parents[3]
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def _sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _iter_images(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade fundus photos for diabetic retinopathy.")
    parser.add_argument("--input", type=Path, default=None, help="Image file or dir (default: config images_dir).")
    parser.add_argument("--force", action="store_true", help="Re-grade images that already have a sidecar.")
    parser.add_argument("--limit", type=int, default=None, help="Grade only the first N images.")
    args = parser.parse_args()

    cfg = load_fundus_analysis_config()
    input_path = args.input or (REPO_ROOT / cfg.images_dir)
    output_dir = REPO_ROOT / cfg.output_dir
    weights_path = REPO_ROOT / cfg.models_dir / cfg.weights_filename

    if not weights_path.is_file():
        print(
            f"ERROR: slim weights missing at {weights_path}\n"
            f"Run: uv run python scripts/fundus_analysis/export_weights.py",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        raise SystemExit(2)

    images = _iter_images(input_path)
    if args.limit is not None:
        images = images[: args.limit]
    if not images:
        print(f"No images found in {input_path}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading model from {weights_path.name} ...")
    grader = DRGrader(cfg, weights_path=weights_path, model_sha8=_sha8(weights_path))

    rows: list[tuple[str, str, str, str]] = []
    graded = skipped = fallback = errors = 0
    for img in images:
        sidecar = output_dir / f"{img.stem}.dr.json"
        if sidecar.exists() and not args.force:
            skipped += 1
            continue
        try:
            res = grader.grade_path(img)
        except ValueError as exc:
            errors += 1
            rows.append((img.name, "ERR", str(exc)[:40], "-"))
            continue
        sidecar.write_text(json.dumps(res.to_dict(), indent=2))
        graded += 1
        if res.crop["fallback"]:
            fallback += 1
        rows.append((img.name, str(res.grade), res.label, f"{res.confidence} {max(res.probabilities.values()):.2f}"))

    _print_table(rows)
    print(
        f"\n{graded} graded, {skipped} skipped, {fallback} circle-crop fell back, {errors} errors."
    )
    print("Research/educational use only. Not a medical device; not for clinical diagnosis.")


def _print_table(rows: list[tuple[str, str, str, str]]) -> None:
    if not rows:
        return
    headers = ("image", "grade", "label", "confidence")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the CLI end-to-end on the real captures**

Run: `uv run arm101-dr-grade`
Expected: prints `Loading model ...`, a table of the 10 captures with grades/labels/confidence, a summary line, and the disclaimer. Sidecars appear in `media_outputs/fundus_analysis/`.

- [ ] **Step 3: Verify a sidecar is well-formed**

Run: `uv run python -c "import json,glob; p=sorted(glob.glob('media_outputs/fundus_analysis/*.dr.json'))[0]; d=json.load(open(p)); print(p); print(json.dumps(d, indent=2)[:600])"`
Expected: valid JSON with all required fields and probabilities summing to ~1.

- [ ] **Step 4: Verify skip-unless-force**

Run: `uv run arm101-dr-grade`
Expected: summary shows `0 graded, 10 skipped`.
Run: `uv run arm101-dr-grade --limit 1 --force`
Expected: `1 graded`.

- [ ] **Step 5: Commit**

```bash
git add src/arm101_hand/scripts/dr_grade.py
git commit -m "feat(fundus): arm101-dr-grade batch CLI + JSON sidecars + summary table"
```

---

## Task 7: APTOS test-split validation harness

**Files:**
- Create: `scripts/fundus_analysis/aptos_eval.py`
- Create: `scripts/fundus_analysis/README.md`

- [ ] **Step 1: Write `aptos_eval.py`**

```python
# scripts/fundus_analysis/aptos_eval.py
"""Validate the DR-grading pipeline against the APTOS2019 held-out test split.

Runs the SAME model + transform as DRGrader over references/.../APTOS2019/test/.
These images are already AutoMorph-cropped, so circle-crop is bypassed (the model
+ transform are what we are validating here). Prints accuracy, per-class P/R/F1,
confusion matrix, and quadratic-weighted kappa. Heavy: ~20-50 min on CPU.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    cohen_kappa_score,
    confusion_matrix,
)

from arm101_hand.config.fundus_analysis_config import DR_LABELS, load_fundus_analysis_config
from arm101_hand.fundus_analysis.model import build_model, load_weights
from arm101_hand.fundus_analysis.preprocess import build_eval_transform

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = REPO_ROOT / "references" / "AIML-models" / "APTOS2019" / "test"
# APTOS ImageFolder alphabetical order -> class index
CLASS_DIRS = ["anodr", "bmilddr", "cmoderatedr", "dseveredr", "eproliferativedr"]


def main() -> None:
    parser = argparse.ArgumentParser(description="APTOS2019 test-split validation.")
    parser.add_argument("--limit-per-class", type=int, default=None)
    args = parser.parse_args()

    cfg = load_fundus_analysis_config()
    weights_path = REPO_ROOT / cfg.models_dir / cfg.weights_filename
    model = build_model(cfg.model_arch, cfg.num_classes)
    load_weights(model, weights_path)
    transform = build_eval_transform(cfg.input_size)

    y_true: list[int] = []
    y_pred: list[int] = []
    for label_idx, cls_dir in enumerate(CLASS_DIRS):
        files = sorted((TEST_DIR / cls_dir).glob("*.png"))
        if args.limit_per_class:
            files = files[: args.limit_per_class]
        print(f"[{cls_dir}] {len(files)} images ...")
        for f in files:
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                continue
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            tensor = transform(Image.fromarray(rgb)).unsqueeze(0)
            with torch.inference_mode():
                logits = model(tensor)
            y_pred.append(int(torch.argmax(logits, dim=1).item()))
            y_true.append(label_idx)

    names = [DR_LABELS[i] for i in range(len(DR_LABELS))]
    print("\n=== APTOS2019 test-split results ===")
    print(f"N = {len(y_true)}")
    print(f"Accuracy: {accuracy_score(y_true, y_pred):.4f}")
    print(f"Quadratic-weighted kappa: {cohen_kappa_score(y_true, y_pred, weights='quadratic'):.4f}")
    print("\n" + classification_report(y_true, y_pred, target_names=names, digits=4))
    print("Confusion matrix (rows=true, cols=pred):")
    print(np.array2string(confusion_matrix(y_true, y_pred)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run on a small subset first**

Run: `uv run python scripts/fundus_analysis/aptos_eval.py --limit-per-class 10`
Expected: runs ~50 images, prints accuracy + kappa + report without error. Accuracy should already look reasonable (most No-DR correct).

- [ ] **Step 3: Full run (background, ~20-50 min) and record metrics**

Run (background): `uv run python scripts/fundus_analysis/aptos_eval.py`
Expected: overall accuracy and quadratic-weighted kappa in the RETFound ballpark (high accuracy / kappa). Capture the numbers.

- [ ] **Step 4: Write `scripts/fundus_analysis/README.md` with run order + recorded metrics**

```markdown
# Fundus DR-grading scripts

Run order (one-time setup, then grade):

1. `uv run python scripts/fundus_analysis/export_weights.py`
   Extracts slim weights from the APTOS checkpoint into `models/` (~1.2 GB, gitignored).
2. `uv run python scripts/fundus_analysis/aptos_eval.py`
   Validates model+transform on the 1,100-image APTOS test split.
3. `uv run arm101-dr-grade`
   Grades the captures in `media_outputs/fundus_images/`.

## Recorded validation metrics (APTOS2019 test split, N=1100)

<!-- filled in from the Step 3 full run -->
- Accuracy: <value>
- Quadratic-weighted kappa: <value>
- Per-class F1: <values>
- Weights sha256[:8]: <value>
- Date: 2026-06-11
```

(Fill the `<value>` placeholders with the actual Step 3 numbers before committing.)

- [ ] **Step 5: Commit**

```bash
git add scripts/fundus_analysis/aptos_eval.py scripts/fundus_analysis/README.md
git commit -m "feat(fundus): APTOS test-split validation harness + recorded metrics"
```

---

## Task 8: Lint, type-check, full test run

**Files:** none (verification only)

- [ ] **Step 1: Format + lint**

Run: `uv run ruff format . && uv run ruff check .`
Expected: formatted; no lint errors in `src/arm101_hand/fundus_analysis/`, `scripts/fundus_analysis/`, `src/arm101_hand/scripts/dr_grade.py`. Fix any reported issues.

- [ ] **Step 2: Type-check**

Run: `uv run mypy src`
Expected: no new errors in the fundus_analysis modules. Fix any.

- [ ] **Step 3: Full host test run**

Run: `uv run pytest -m 'not hardware' -v`
Expected: all fundus_analysis unit tests pass; no regressions elsewhere.

- [ ] **Step 4: Commit any fixups**

```bash
git add -A
git commit -m "chore(fundus): ruff/mypy fixups for DR-grading modules"
```

---

## Task 9: Claude / workspace setup

**Files:**
- Create: `.claude/skills/ml-inference/SKILL.md`
- (Plugin install is a Claude Code action, not a file edit)

- [ ] **Step 1: Install Pyright LSP plugin**

In Claude Code, run: `/plugin install pyright-lsp@claude-plugins-official`
Expected: plugin installed; Python type diagnostics active. (If already present, note and skip.)

- [ ] **Step 2: Write the local ML-inference skill**

```markdown
# .claude/skills/ml-inference/SKILL.md
---
name: ml-inference
description: Use when loading a PyTorch checkpoint for local inference in this workspace — covers safe checkpoint loading (weights_only + argparse.Namespace allowlist), extracting checkpoint['model'], slim safetensors export, and the timm ViT load recipe used by fundus_analysis.
---

# Local ML inference (PyTorch / timm)

Patterns proven in `src/arm101_hand/fundus_analysis/`:

## Safe checkpoint loading
RETFound/MAE checkpoints embed an `argparse.Namespace` under `args`, so plain
`weights_only=True` fails. Allowlist exactly that class — never fall back to
`weights_only=False` on untrusted files:

    import argparse, torch
    with torch.serialization.safe_globals([argparse.Namespace]):
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
    state_dict = ckpt["model"]   # fine-tune checkpoints nest weights under 'model'

## Slim export (drop optimizer state)
Fine-tune checkpoints carry optimizer+scaler (~2/3 of the file). Export just the
model weights to safetensors (pickle-free, ~3x faster load), into a gitignored
`models/` dir — never write into read-only `references/` (IL-2):

    from safetensors.torch import save_file
    save_file({k: v.contiguous().clone() for k, v in state_dict.items()}, out)

## timm ViT load recipe
`timm.create_model("vit_large_patch16_224", num_classes=5, global_pool="avg")`
matches RETFound's global-pool ViT exactly (296/296 keys). Load with
`strict=True` so any key drift fails loudly — it doubles as a correctness gate.
Note: `global_pool="token"` does NOT match (keeps `norm.*`, drops `fc_norm.*`).

## Eval transform
Resize(256, BICUBIC) -> CenterCrop(224) -> ToTensor -> Normalize(ImageNet).
cv2 reads BGR; convert to RGB PIL before the torchvision transform.

## Privacy
Patient retinal images: keep inference fully local. No network calls, no cloud
APIs, no telemetry in the grading path.
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/ml-inference/SKILL.md
git commit -m "chore(claude): local ML-inference skill capturing the RETFound load pattern"
```

---

## Task 10: Documentation refresh (CLAUDE.md + README.md)

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Use the doc_update skill**

Invoke the `doc_update` skill to refresh CLAUDE.md + README.md. It must capture:
- New module `src/arm101_hand/fundus_analysis/` (device layer — DR grading) in the directory tree (§3).
- New config `config/fundus_analysis_config.py` + `data/fundus_analysis_config.yaml`.
- New scripts: `scripts/fundus_analysis/export_weights.py`, `aptos_eval.py`, and the `arm101-dr-grade` CLI — added to the Common Workflows section with example commands.
- New `models/` dir (gitignored, slim weights) and `media_outputs/fundus_analysis/` outputs.
- Checkpoint provenance + validation metrics: RETFound MAE ViT-L/16, fine-tuned APTOS2019, epoch 27, input_size=224, global_pool=True; record the APTOS test-split accuracy/kappa from Task 7.
- Update the tech-debt note: a second computer-vision subsystem (local torch inference) now exists alongside the system-camera preview; Phase 2 (capture integration) is the remaining DR work.

- [ ] **Step 2: Run the iron-law-audit skill on the branch diff**

Invoke `iron-law-audit`. Expected: IL-2 (references read-only — only read + slim export to models/), IL-5 (config in-tree, weights/outputs gitignored), IL-7 (provenance single-sourced in CLAUDE.md) all pass. Fix anything flagged.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(fundus): document DR-grading pipeline + provenance + validation metrics"
```

---

## Task 11: Finish the branch

- [ ] **Step 1: Final verification before completion**

Run: `uv run pytest -m 'not hardware' -q && uv run ruff check . && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Confirm the end-to-end goal artifact**

Run: `uv run arm101-dr-grade --force --limit 3`
Expected: 3 captures graded with sidecars + table — the goal script works on the real fundus images.

- [ ] **Step 3: Use the finishing-a-development-branch skill**

Invoke `superpowers:finishing-a-development-branch` to choose merge/PR/cleanup for `feat/dr-grading-inference`.

---

## Self-review notes

- **Spec coverage:** Phasing (Task 5 DRGrader is Phase-2-ready; Phase 2 itself out of scope per §1) ✓; module layout (Tasks 1-6) ✓; circle-crop (Task 2) ✓; eval transform 224 (Task 2) ✓; timm exact match strict load (Task 3, §7a resolved) ✓; slim export (Task 4) ✓; JSON sidecar + console table outputs (Tasks 5-6) ✓; APTOS validation (Task 7) ✓; host unit tests (Tasks 1,2,5) ✓; Claude setup — Pyright + CLAUDE.md notes + ml-inference skill (Tasks 9-10) ✓; privacy local-only (grader docstring + skill) ✓; disclaimer (Task 5) ✓; IL-2/5/7 (Task 10 audit) ✓.
- **Placeholder scan:** the only `<value>` placeholders are in the README metrics block, explicitly filled from the Task 7 run before its commit. No other placeholders.
- **Type consistency:** `circle_crop`/`CropInfo`/`build_eval_transform`/`confidence_band` (Task 2) used identically in `grader.py` (Task 5); `build_model`/`load_weights`/`WEIGHTS_FILENAME` (Task 3) used in Tasks 4-7; `DRGrader(cfg, weights_path=, model_sha8=)` / `grade_path` / `grade_array` / `GradeResult.to_dict` signatures consistent across Tasks 5-6; config field names (`models_dir`, `weights_filename`, `images_dir`, `output_dir`, `confidence.high/medium`, `crop.*`) consistent across Tasks 1,5,6,7.
```
