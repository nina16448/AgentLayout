#!/usr/bin/env python
"""Minimally patched Elem2Design inference for the A3 external baseline.

Patches over the official ``llava/infer/infer.py`` (all documented):

1. ``--load-4bit`` uses the official ``load_pretrained_model(load_4bit=True)``
   (fp16 compute dtype — required on Pascal GPUs, which lack bf16); peft
   0.19 merges the LoRA delta into the NF4 weights via dequant/requant.
2. No ``model.to("cuda")`` in 4-bit mode (would break the quantized device
   map); tensors go to the model's real input device.
3. ``model.config.use_cache = True`` for generation (the checkpoint ships
   with the training value ``False``, which makes decoding quadratic).
4. ``--seed`` reseeds torch per (sample, turn) for reproducible sampling.
5. ``--resume``: one JSONL line per finished sample, fsync'd; already-done
   sample IDs are skipped on restart.  No per-sample retry: a failed turn
   records the exception and the sample continues to fail explicitly.
6. ``max_new_tokens`` replaces the official ``max_length=5000`` so >25-element
   prompts are not silently truncated to empty generations.

Run inside the `e2d` conda environment from the elem2design repo root.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from pathlib import Path

import torch

from llava.infer.infer import EvalArguments, LazySupervisedDataset  # noqa: E402
from crello.util import render  # noqa: E402


def load_model(model_path: str, load_4bit: bool, device_map: str):
    """Official loader; peft >= 0.7 merges LoRA into bnb 4-bit layers directly.

    The official builder passes both ``load_in_4bit`` and
    ``quantization_config``, which transformers 4.44.2 rejects, so we route
    the identical official BitsAndBytesConfig through ``**kwargs`` instead of
    setting ``load_4bit=True``.
    """
    import llava.model.builder as builder

    kwargs = {}
    if load_4bit:
        from transformers import BitsAndBytesConfig
        from transformers.models.llama import modeling_llama

        # Pascal fp16 ALU throughput is 1/64 of fp32; allow overriding the
        # bnb compute dtype (numerics change is on par with the already
        # accepted 4-bit rounding). Default stays the official float16.
        compute_dtype = getattr(torch, os.environ.get("E2D_BNB_COMPUTE_DTYPE", "float16"))
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            # Keep the multimodal modules (loaded later from mm_projector.bin)
            # and the output head in fp16 — quantizing them breaks the
            # post-load state_dict copy (packed-shape mismatch).
            llm_int8_skip_modules=["mm_projector", "vision_tower", "lm_head"],
        )
        # transformers 4.44.2 tries to normal_-init missing-weight modules even
        # when their params are already bnb-quantized uint8, which crashes.
        # Quantized params never need init, so guard on floating dtype.
        original_init = modeling_llama.LlamaPreTrainedModel._init_weights

        def _quant_safe_init(self, module):
            weight = getattr(module, "weight", None)
            if weight is not None and not torch.is_floating_point(weight):
                return
            original_init(self, module)

        modeling_llama.LlamaPreTrainedModel._init_weights = _quant_safe_init
    return builder.load_pretrained_model(
        model_path, _model_base(model_path), device_map=device_map, **kwargs
    )


def _model_base(model_path: str) -> str:
    with open(os.path.join(model_path, "adapter_config.json")) as handle:
        return json.load(handle)["base_model_name_or_path"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--data-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--image-folder", default="/")
    parser.add_argument("--load-4bit", action="store_true")
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--max-new-tokens", type=int, default=3072)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = args.output_dir / f"raw_predictions_shard{args.shard_index:02d}.jsonl"
    done_ids = set()
    if args.resume:
        # Scan every shard file so resharding never repeats a finished sample.
        for shard_file in args.output_dir.glob("raw_predictions_shard*.jsonl"):
            for line in shard_file.read_text().splitlines():
                try:
                    done_ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    pass

    tokenizer, model, image_processor, _ = load_model(
        args.model_path, args.load_4bit, args.device_map
    )
    tokenizer.pad_token_id = tokenizer.unk_token_id or 0
    model.config.use_cache = True
    model.eval()
    device = next(model.parameters()).device

    eval_args = EvalArguments(
        model_name_or_path=args.model_path,
        data_path=str(args.data_path),
        image_folder=args.image_folder,
        output_dir=str(args.output_dir),
        temperature=args.temperature,
        top_p=args.top_p,
    )
    eval_args.image_processor = image_processor
    dataset = LazySupervisedDataset(
        tokenizer=tokenizer, data_path=str(args.data_path), data_args=eval_args
    )

    image_save_path = args.output_dir / "render"
    image_save_path.mkdir(exist_ok=True)
    handle = out_jsonl.open("a")

    for idx in range(len(dataset)):
        if idx % args.shard_count != args.shard_index:
            continue
        sample, processed_images, layer_image_list = dataset[idx]
        sample_id = sample["id"]
        if sample_id in done_ids:
            continue
        started = time.time()
        record = {
            "id": sample_id,
            "shard": args.shard_index,
            "seed": args.seed,
            "predictions": "",
            "turn_errors": {},
            "canvas_width": sample["canvas_width"],
            "canvas_height": sample["canvas_height"],
        }
        gpt_dict: dict = {}
        new_images: dict = {}
        prediction = ""
        with torch.inference_mode():
            for turn_id in range(5):
                gpt_dict[turn_id] = None
                sample, processed_images, _ = dataset.__getitem__(
                    idx,
                    end_layer_index=turn_id,
                    gpt_dict=gpt_dict,
                    images=processed_images,
                    new_images=new_images,
                )
                input_ids = sample["input_ids"].unsqueeze(0).to(device)
                images = [sample["image"].to(device, dtype=torch.float16)]
                attention_mask = input_ids.ne(tokenizer.pad_token_id).to(device)
                torch.manual_seed(args.seed * 1000 + idx * 10 + turn_id)
                try:
                    output_ids = model.generate(
                        input_ids,
                        images=images,
                        attention_mask=attention_mask,
                        do_sample=True,
                        temperature=args.temperature,
                        top_p=args.top_p,
                        num_return_sequences=1,
                        pad_token_id=tokenizer.eos_token_id,
                        max_new_tokens=args.max_new_tokens,
                    )
                    output = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0].strip()
                    prediction += " ##### " + output + " $$$$$ "
                    gpt_dict[turn_id] = output
                except Exception as error:  # noqa: BLE001 — explicit failure record
                    gpt_dict[turn_id] = "{}"
                    prediction += " ##### {} $$$$$ "
                    record["turn_errors"][str(turn_id)] = (
                        f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"
                    )

                try:
                    render_image = render(
                        prediction,
                        args.image_folder,
                        sample["render_image"],
                        sample["render_text"],
                        sample["canvas_width"],
                        sample["canvas_height"],
                    )
                except Exception:  # noqa: BLE001 — official white-canvas fallback
                    from PIL import Image

                    render_image = Image.new(
                        "RGB", (sample["canvas_width"], sample["canvas_height"]), color="white"
                    )
                render_file = image_save_path / f"{sample_id}_{turn_id}.png"
                render_image.save(render_file)
                if turn_id < 4:
                    new_images = {layer_image_list[turn_id]: str(render_file)}

        record["predictions"] = prediction
        record["elapsed_s"] = round(time.time() - started, 2)
        if torch.cuda.is_available():
            record["peak_vram_mb"] = int(torch.cuda.max_memory_allocated() / 2**20)
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        errs = len(record["turn_errors"])
        print(f"[shard {args.shard_index}] {sample_id} done in {record['elapsed_s']}s"
              + (f" ({errs} turn errors)" if errs else ""), flush=True)

    handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
