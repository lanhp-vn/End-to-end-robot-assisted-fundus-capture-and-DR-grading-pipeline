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
