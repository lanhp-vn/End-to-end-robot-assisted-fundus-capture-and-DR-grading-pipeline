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
    cm = confusion_matrix(y_true, y_pred)
    for row in cm:
        print("  " + " ".join(f"{v:4d}" for v in row))


if __name__ == "__main__":
    main()
