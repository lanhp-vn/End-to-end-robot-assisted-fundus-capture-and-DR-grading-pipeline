# Fundus DR-grading scripts

Diabetic-retinopathy severity grading (0-4) on fundus photos, using a RETFound
Vision Transformer (ViT-L/16) fine-tuned on APTOS2019. Runs fully locally and
offline. Research/educational use only — not a medical device, not for clinical
diagnosis.

Design and implementation notes: `docs/superpowers/specs/2026-06-11-dr-grading-design.md`
and `docs/superpowers/plans/2026-06-11-dr-grading-inference.md`.

## Run order

One-time setup, then grade:

1. Export the slim weights (one-time, ~1.2 GB into `models/`, git-ignored):

   ```powershell
   uv run python scripts/fundus_analysis/export_weights.py
   ```

   Reads the read-only checkpoint at `references/AIML-models/APTOS2019/checkpoint-best.pth`
   (IL-2: never modified) and extracts just the model weights to
   `models/retfound_aptos2019_vitl16.safetensors`.

2. Validate model + transform on the APTOS2019 held-out test split (optional but
   recommended before trusting it on new captures):

   ```powershell
   uv run python scripts/fundus_analysis/aptos_eval.py
   # quick check on a subset:
   uv run python scripts/fundus_analysis/aptos_eval.py --limit-per-class 10
   ```

3. Grade the captured fundus photos:

   ```powershell
   uv run arm101-dr-grade
   ```

   Writes `<image>.dr.json` sidecars to `media_outputs/fundus_analysis/` and prints
   a summary table. Re-runs skip already-graded images unless `--force`.

## Checkpoint provenance

- Model: RETFound MAE Vision Transformer, `vit_large_patch16_224`, `global_pool=avg`.
- Fine-tune: APTOS2019 five-category split, `input_size=224`, 5 classes, best epoch 27.
- Upstream credit/source: the APTOS2019 model checkpoint was downloaded from the
  RETFound project's benchmark checkpoint listing:
  [rmaphoh/RETFound `BENCHMARK.md`](https://github.com/rmaphoh/RETFound/blob/main/BENCHMARK.md).
- Source checkpoint: `references/AIML-models/APTOS2019/checkpoint-best.pth` (3.64 GB,
  includes optimizer state; the export keeps only the 296 model tensors).
- Slim weights: `models/retfound_aptos2019_vitl16.safetensors` (~1.21 GB),
  sha256[:8] = `6ffa4e53`.
- Label map: 0 = No DR, 1 = Mild, 2 = Moderate, 3 = Severe, 4 = Proliferative
  (APTOS folder alphabetical: anodr / bmilddr / cmoderatedr / dseveredr / eproliferativedr).

## Recorded validation metrics (APTOS2019 test split)

Produced by `aptos_eval.py` over the full test split. The test images are already
AutoMorph-cropped, so this validates the model + eval transform (the circle-crop
step that handles raw Aurora frames is exercised separately by the host unit tests
and by `arm101-dr-grade`).

Run: 2026-06-11, N = 1100, weights sha256[:8] = `6ffa4e53`, CPU.

- Accuracy: 0.8373
- Quadratic-weighted kappa: 0.9062

Per-class (precision / recall / F1 / support):

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| No DR | 0.9887 | 0.9723 | 0.9805 | 542 |
| Mild | 0.7105 | 0.4865 | 0.5775 | 111 |
| Moderate | 0.7051 | 0.9167 | 0.7971 | 300 |
| Severe | 0.4444 | 0.3448 | 0.3883 | 58 |
| Proliferative | 0.8036 | 0.5056 | 0.6207 | 89 |

Confusion matrix (rows = true, cols = predicted; order No DR / Mild / Moderate / Severe / Proliferative):

```text
527   10    5    0    0
  3   54   53    0    1
  2   10  275   10    3
  0    1   30   20    7
  1    1   27   15   45
```

The quadratic-weighted kappa of 0.91 is consistent with published RETFound APTOS2019
fine-tune results, confirming the checkpoint, eval transform, label order, and
global-pool configuration are wired correctly. Errors cluster on the matrix diagonal
(adjacent grades), as expected for ordinal DR severity; the minority classes (Mild,
Severe) show lower recall, the known consequence of APTOS class imbalance.

## Caveat: domain shift

The model was fine-tuned on APTOS2019 (EyePACS-sourced) images. Optomed Aurora
handheld captures differ in camera, field of view, and illumination, so real-capture
accuracy can fall below the test-split numbers above. The per-class probabilities
and confidence band in each sidecar exist so a human can judge when to distrust a
prediction. This stays a research/educational tool, not a diagnostic one.
