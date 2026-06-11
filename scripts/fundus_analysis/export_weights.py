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
