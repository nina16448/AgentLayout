#!/usr/bin/env python
"""Gate 1: load-and-generate smoke test for Elem2Design on Pascal (e2d env).

Loads the pinned LoRA checkpoint in 4-bit (fp16 compute, no merge), prints
device placement and VRAM, and runs one short seeded generation with a dummy
image to prove the full LLaVA path works on this GPU. No dataset access.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from infer_patched import load_model  # noqa: E402


def main() -> int:
    model_path = sys.argv[1]
    started = time.time()
    tokenizer, model, image_processor, _ = load_model(model_path, load_4bit=True, device_map="auto")
    tokenizer.pad_token_id = tokenizer.unk_token_id or 0
    model.config.use_cache = True
    model.eval()
    load_s = time.time() - started
    device = next(model.parameters()).device
    print(f"loaded in {load_s:.1f}s; lm device={device}; "
          f"vram={torch.cuda.max_memory_allocated() / 2**20:.0f}MB")

    from llava.mm_utils import tokenizer_image_token  # noqa: E402

    prompt = (
        "A poster of canvas width 600px, canvas height 1200px. element 0: <image>. "
        "Now predict the background elements: element 0: <image> ASSISTANT:"
    )
    input_ids = tokenizer_image_token(prompt, tokenizer, return_tensors="pt").unsqueeze(0).to(device)
    crop = image_processor.crop_size
    dummy = torch.zeros(2, 3, crop["height"], crop["width"]).to(device, dtype=torch.float16)
    torch.manual_seed(42)
    started = time.time()
    with torch.inference_mode():
        output_ids = model.generate(
            input_ids,
            images=[dummy],
            do_sample=True,
            temperature=0.7,
            top_p=0.95,
            pad_token_id=tokenizer.eos_token_id,
            max_new_tokens=64,
        )
    gen_s = time.time() - started
    text = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0]
    n_new = output_ids.shape[1]
    print(f"generated {n_new} tokens in {gen_s:.1f}s ({n_new / gen_s:.2f} tok/s)")
    print("sample output:", json.dumps(text[:300]))
    print(f"peak vram: {torch.cuda.max_memory_allocated() / 2**20:.0f}MB")
    print("GATE1 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
