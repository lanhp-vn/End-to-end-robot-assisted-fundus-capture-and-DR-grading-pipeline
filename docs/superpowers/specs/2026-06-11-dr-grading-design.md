# DR-Grading Inference Pipeline — Design Spec

**Date:** 2026-06-11
**Status:** Approved (brainstorming) — pending implementation plan
**Author:** Claude (brainstorming session)

---

## 1. Goal

Grade diabetic-retinopathy (DR) severity (0–4) from fundus photos using the
RETFound ViT-Large checkpoint fine-tuned on APTOS2019
(`references/AIML-models/APTOS2019/checkpoint-best.pth`). Primary input is the
Optomed Aurora captures landing in `media_outputs/fundus_images/*.JPG`.

This is a **research / educational** tool. It is **not a medical device** and
must never be used for clinical diagnosis. That disclaimer ships in every output.

### Phasing (user decision: "both, phased")

- **Phase 1 (this spec):** a standalone batch CLI built around a reusable,
  load-once inference module (`DRGrader`). No changes to the capture demo.
- **Phase 2 (separate future effort, not in this spec):** wire the same
  `DRGrader` into `grab_trigger_capture` to grade each new capture inline. The
  Phase-1 module API is designed to make this a thin call, but Phase 2 is out of
  scope here.

---

## 2. Verified facts (from checkpoint + RETFound source inspection)

These were confirmed empirically, not assumed:

| Fact | Value | Source |
|---|---|---|
| Checkpoint top-level keys | `model`, `optimizer`, `epoch`, `scaler`, `args` | `torch.load` inspection |
| Best epoch | 27 | checkpoint `args`/`epoch` |
| Architecture | `vit_large_patch16`, embed_dim 1024, depth 24, heads 16 | checkpoint `args` |
| Input size | **224** (not 256) | checkpoint `args.input_size`; `pos_embed` shape `(1,197,1024)` = 196 patches + cls = 14×14 @ patch-16 |
| Global pool | `True` | checkpoint `args.global_pool` |
| Classes | 5 (`head.weight` shape `(5,1024)`) | checkpoint tensors |
| Norm layers | **`fc_norm.*` present, `norm.*` ABSENT** | checkpoint tensors — see §7 risk |
| Model tensor count | 296 | checkpoint inspection |
| Eval transform | `Resize(256, bicubic)` → `CenterCrop(224)` → `ToTensor` → `Normalize(IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD)` | `RETFound/util/datasets.py:50-58` (crop_pct = 224/256 = 0.875 → size = 256) |
| Label map | `anodr`=0 No DR, `bmilddr`=1 Mild, `cmoderatedr`=2 Moderate, `dseveredr`=3 Severe, `eproliferativedr`=4 Proliferative | `ImageFolder` alphabetical, `RETFound/util/datasets.py:11` |
| APTOS test split | 1,100 labelled images (542/111/300/58/89 across grades 0–4) | dataset enumeration |
| Aurora JPG dims | 2368×1776 RGB, fundus disc centered on near-black background | cv2 inspection |
| Env present | torch 2.10.0+cpu, torchvision 0.25.0+cpu, safetensors 0.7.0; CUDA False | venv check |
| Env missing | **timm** (must add), **scikit-learn** (add to dev) | venv check |

ImageNet constants: mean `[0.485, 0.456, 0.406]`, std `[0.229, 0.224, 0.225]`.

---

## 3. Architecture & file layout

Follows the three-layer convention (`docs/conventions/01-module-layering.md`),
mirroring `fundus_camera` / `system_camera`.

```
src/arm101_hand/
├── fundus_analysis/                  # DEVICE layer (owns the heavy model resource)
│   ├── __init__.py                   # public API: DRGrader, GradeResult, DR_LABELS
│   ├── preprocess.py                 # circle-crop (cv2) + eval transform (resize 256 → crop 224 → ImageNet norm)
│   ├── model.py                      # build ViT-L/16 (timm) + load slim safetensors weights
│   └── grader.py                     # DRGrader: load-once; grade(image|path) → GradeResult  (Phase-2-ready)
├── config/fundus_analysis_config.py  # PRIMITIVE layer — pydantic schema
├── data/fundus_analysis_config.yaml  # runtime operator config (paths, confidence bands, crop params)
└── scripts/dr_grade.py               # APPLICATION layer — console script `arm101-dr-grade`

scripts/fundus_analysis/
├── export_weights.py                 # one-time: checkpoint-best.pth → models/retfound_aptos2019_vitl16.safetensors
└── aptos_eval.py                     # full APTOS test-split validation (1,100 images, prints metrics)

models/                               # NEW top-level dir, gitignored — slim weights (~1.2 GB)
media_outputs/fundus_analysis/        # output JSON sidecars (gitignored like the rest of media_outputs)
tests/unit/                           # host tests, no weights needed (test_fundus_analysis_*.py)
```

### Layer responsibilities

- **`preprocess.py` (primitive-ish, pure functions):** `circle_crop(bgr) ->
  (cropped_bgr, CropInfo)` and `build_eval_transform() -> torchvision transform`.
  No model, no I/O beyond the array passed in.
- **`model.py`:** `build_model() -> nn.Module` (5-class ViT-L/16) and
  `load_weights(model, safetensors_path) -> None`. No image logic.
- **`grader.py`:** `DRGrader` — constructs the model + transform once
  (`__init__`), exposes `grade(image_or_path) -> GradeResult`. This is the unit
  Phase 2 reuses. `GradeResult` is a frozen dataclass / pydantic model carrying
  grade, label, probabilities, confidence, crop info, model id.
- **`scripts/dr_grade.py`:** batch driver — enumerate, skip-or-force, call
  `DRGrader.grade`, write sidecars, print table.

### Dependency changes (`pyproject.toml`)

- Add `timm` to `[project.dependencies]`, pinned to a tested version
  (target `timm>=1.0,<1.1`; exact pin chosen during implementation against the
  weight-loading test in §7).
- Add `scikit-learn` to the `dev` optional group (metrics for `aptos_eval.py` only).
- `safetensors`, `torch`, `torchvision` already satisfied transitively via lerobot.
- The `opencv-python` override (lines 22-28) is **untouched**.

---

## 4. Inference data flow (`arm101-dr-grade`)

For each `media_outputs/fundus_images/*.JPG` (skip if a `.dr.json` sidecar
already exists, unless `--force`):

1. **Circle-crop** (`preprocess.circle_crop`): grayscale → threshold near-black
   background → bounding box of the largest bright contour → square crop with a
   small margin. If no plausible disc is found (degenerate / blank frame), fall
   back to a center-square crop and set `crop.fallback = true`.
2. **Eval transform:** the *exact* validated transform — `Resize(256, bicubic)`
   → `CenterCrop(224)` → `ToTensor` → ImageNet normalize. (cv2 BGR is converted
   to RGB PIL before the torchvision transform.)
3. **Model:** built once per run via timm, weights from slim safetensors,
   `torch.inference_mode()`, CPU.
4. **Result:** `softmax` over 5 logits → grade = argmax; label from `DR_LABELS`;
   confidence band HIGH ≥ 0.80 / MEDIUM ≥ 0.65 / LOW otherwise (thresholds in YAML).

### CLI surface (minimal)

```
arm101-dr-grade [--input PATH] [--force] [--limit N]
```

- `--input` — alternate file or directory (default: `media_outputs/fundus_images/`).
- `--force` — re-grade images that already have a sidecar.
- `--limit N` — grade only the first N (smoke testing).
- Missing weights file → clear error pointing at `scripts/fundus_analysis/export_weights.py`.
- One unreadable image is reported in the table and skipped; the batch continues.

---

## 5. Outputs

### 5a. JSON sidecar — `media_outputs/fundus_analysis/<image-stem>.dr.json`

Separate from the camera's own `<image>.JPG.json`; never overwrites it.

```json
{
  "source_image": "20260611T085050Z_DCIM_P0001_IM0115EY.JPG",
  "grade": 2,
  "label": "Moderate",
  "confidence": "MEDIUM",
  "probabilities": {"No DR": 0.04, "Mild": 0.18, "Moderate": 0.71, "Severe": 0.05, "Proliferative": 0.02},
  "crop": {"method": "circle", "box": [310, 96, 2058, 1844], "fallback": false},
  "model": {"checkpoint": "retfound_aptos2019_vitl16.safetensors", "sha256_8": "a1b2c3d4", "arch": "vit_large_patch16_224"},
  "preprocess_version": "1",
  "graded_at_utc": "2026-06-11T11:42:03Z",
  "disclaimer": "Research/educational use only. Not a medical device; not for clinical diagnosis."
}
```

- `model.sha256_8` ties a result to the exact weights used.
- `preprocess_version` lets stale results be detected when the crop/transform changes.

### 5b. Console summary table

End-of-run table: `filename · grade · label · confidence · top-prob`, then a
one-line summary (`N graded, M skipped, K circle-crop fell back`) and the disclaimer.

### Privacy

No network calls anywhere in the grading path — fully local. Documented in the
module docstring and CLAUDE.md.

---

## 6. Validation & testing

### 6a. APTOS test-split validation — `scripts/fundus_analysis/aptos_eval.py`

Heavy, run manually (not in CI). Runs the **same `DRGrader` model + transform**
over all 1,100 `references/.../APTOS2019/test/{anodr,...}` images, maps
folder → label, and reports overall accuracy, per-class precision/recall/F1,
confusion matrix, and quadratic-weighted kappa. ~20–50 min CPU; run in background.

This is the **correctness gate**: accuracy near the published RETFound APTOS
figure proves weights + transform + label order + `global_pool` are wired right
before any Aurora photo is trusted. Measured metrics get recorded in CLAUDE.md.

> Note: APTOS test images are already AutoMorph-cropped, so the eval **bypasses
> `circle_crop`** and feeds them straight to the transform. The eval therefore
> validates *model + transform*. The `circle_crop` step is validated separately
> by eyeballing a few annotated Aurora crops during the hardware run, and by the
> host unit tests in §6b.

### 6b. Host unit tests (`uv run pytest -m 'not hardware'`, no weights)

- `circle_crop` returns a square box on a synthetic disc-on-black image; sets
  `fallback=true` on an all-black frame.
- transform output tensor is `[3, 224, 224]`.
- pydantic config schema validation (defaults load, bad values rejected).
- label-map correctness and confidence-band boundary values (0.80, 0.65 edges).
- softmax → grade/argmax logic on synthetic logits.

Model loading and real inference are **hardware-gated** (need the 1.2 GB file),
reusing the existing `@pytest.mark.hardware` marker (semantics: "needs a heavy
local resource not present in CI").

---

## 7. Implementation risks

### 7a. `fc_norm` vs `norm` state-dict key match (PRIMARY RISK)

RETFound's `global_pool=True` ViT (`models_vit.py:14-23`) **deletes `self.norm`
and adds `self.fc_norm`**. The checkpoint confirms this: `fc_norm.*` present,
`norm.*` absent.

**Plan:** build timm `vit_large_patch16_224` with `global_pool='avg'` (recent
timm uses `fc_norm` + `norm = Identity` in that mode → no parameterized `norm`
keys) and attempt `load_state_dict(strict=True)` as a built-in correctness gate.

**Fallback (decided in advance):** if timm's key set does not match exactly, the
implementation vendors RETFound's `RETFound_mae` subclass (`models_vit.py:11-51`,
~40 lines, depends only on timm's base `VisionTransformer`) into `model.py`. This
is guaranteed to match because it is the exact training-time class. The choice is
driven by the TDD weight-loading test, not guessed. Either way the public
`build_model()` API is unchanged.

This is why "Approach A (timm.create_model)" was chosen with eyes open: it is the
least code *if* keys match, and the test tells us immediately if they don't.

### 7b. Domain shift (ACCURACY CAVEAT)

The model was fine-tuned on EyePACS-sourced APTOS images. Optomed Aurora handheld
captures differ in camera, FOV, and illumination. Even with circle-crop matching,
expect some accuracy drop on real captures vs. the test split. The per-class
probabilities and confidence bands exist precisely so a human can judge when to
distrust a prediction. This reinforces "research/educational, not diagnostic."

### 7c. Weight provenance & IL-2

`references/` is read-only (IL-2). The 3.64 GB checkpoint is ~2/3 discarded
optimizer state. `export_weights.py` extracts `checkpoint['model']` **once** into
gitignored `models/retfound_aptos2019_vitl16.safetensors` (~1.2 GB). The CLI
loads only the slim safetensors (pickle-free, ~3× faster load). `references/`
stays pristine; `models/` and `media_outputs/` are the only new write targets,
both gitignored. The torch.load in `export_weights.py` uses `weights_only=True`
with `argparse.Namespace` allowlisted (the checkpoint embeds the fine-tune args).

---

## 8. Iron-law compliance

- **IL-2** — `references/` read-only: only read for inspection + one-time weight
  export to an out-of-tree gitignored dir. RETFound code is *adapted/vendored*
  into `src/` if the fallback in §7a triggers, never imported from `references/`.
- **IL-5** — config YAML in-tree (`src/arm101_hand/data/fundus_analysis_config.yaml`),
  never written at runtime. Weights and results live in gitignored dirs.
- **IL-7** — checkpoint provenance, label map, and validation metrics get one
  canonical home in CLAUDE.md; pointers elsewhere.
- IL-1/3/4/6 — no hardware bus, no motors, no cross-device coupling: N/A.

---

## 9. Claude / workspace setup (user-selected)

1. **Pyright LSP plugin** — install `pyright-lsp` from `claude-plugins-official`
   for live Python type diagnostics while writing the module.
2. **CLAUDE.md orientation notes** — add `fundus_analysis/`, the two scripts, and
   `models/` to the directory tree and command list; record checkpoint provenance
   (RETFound MAE ViT-L/16, fine-tuned APTOS2019, epoch 27, `input_size=224`,
   `global_pool=True`) and the validation metrics once measured.
3. **Local ML-inference skill** — a small `.claude/skills/` skill capturing the
   reusable pattern: torch checkpoint hygiene (`weights_only`, allowlisting
   `argparse.Namespace`, extracting `checkpoint['model']`), slim safetensors
   export, and the timm ViT load recipe — for future ML work in this workspace.

Honest scope note: most of `references/Anthropic/` targets the Claude **API**
(cloud), not local torch inference, so it is intentionally **not** wired in. The
three items above are the genuinely useful subset.

---

## 10. Out of scope (YAGNI)

- Phase 2 capture integration (separate effort; API is ready for it).
- Fine-tuning / training (inference only).
- GPU / quantization / ONNX (CPU is adequate: ViT-L ≈ 2–5 s/image on the i7-12700H,
  ~40 GB RAM).
- Auto-downloading weights from HuggingFace (we have the local checkpoint).
- Watch/daemon mode (batch on demand is enough).
```
