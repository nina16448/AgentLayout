"""BASNet + ISNet two-stage saliency pipeline for PKU PosterLayout Occ metric.

experiment.md spec (Occlusion): "saliency map S from background image via
BASNet + ISNet (or pfpn+basnet)". PKU PosterLayout originally chains a
detection-stage SOD model (BASNet, CVPR 2019) with a refinement-stage SOD
model (ISNet on DIS, ECCV 2022). SEGA Table 3 inherits the PKU evaluator
verbatim, so to make our Occ numbers comparable to SEGA Table 3 we replay
the same two-stage SOD pipeline here.

Implementation:
  * BASNet stage uses ``creative-graphic-design/BASNet`` (Hugging Face,
    transformers AutoModel). 87M params, input 256x256, output (1, 1, 256, 256)
    sigmoid in [0, 1].
  * ISNet stage uses ``rembg`` session ``isnet-general-use`` (ISNet trained
    on DIS dataset). rembg returns a single-channel uint8 alpha mask at the
    input resolution.
  * Fuse: resize both maps to the layout canvas resolution, then take the
    per-pixel mean (BASNet + ISNet)/2. This matches the PKU author's stated
    intent of "detection followed by refinement" without committing to a
    specific cascading scheme that the paper does not document.

Returns float32 in [0, 1] of shape (H, W). On failure (model load error or
torch unavailable) raises ``RuntimeError`` instead of silently falling back;
the caller can decide whether to skip that sample.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np
from PIL import Image

_BASNET_MODEL = None
_BASNET_LOCK = threading.Lock()
_ISNET_SESSION = None
_ISNET_LOCK = threading.Lock()

_BASNET_HF_ID = "creative-graphic-design/BASNet"
_BASNET_INPUT = 256


def _load_basnet():
    """Lazy-load BASNet on first use. Thread-safe."""
    global _BASNET_MODEL
    if _BASNET_MODEL is not None:
        return _BASNET_MODEL
    with _BASNET_LOCK:
        if _BASNET_MODEL is not None:
            return _BASNET_MODEL
        try:
            import torch  # noqa: F401
            from transformers import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                f"BASNet requires torch + transformers; missing: {exc}"
            ) from exc
        import torch as _torch

        try:
            model = AutoModel.from_pretrained(
                _BASNET_HF_ID,
                trust_remote_code=True,
                dtype=_torch.float32,
            )
        except TypeError:
            # Older transformers (<4.46) still uses torch_dtype.
            model = AutoModel.from_pretrained(
                _BASNET_HF_ID,
                trust_remote_code=True,
                torch_dtype=_torch.float32,
            )
        model.eval()
        _BASNET_MODEL = model
        return _BASNET_MODEL


def _load_isnet_session():
    """Lazy-load rembg isnet-general-use session. Thread-safe."""
    global _ISNET_SESSION
    if _ISNET_SESSION is not None:
        return _ISNET_SESSION
    with _ISNET_LOCK:
        if _ISNET_SESSION is not None:
            return _ISNET_SESSION
        try:
            from rembg import new_session
        except ImportError as exc:
            raise RuntimeError(
                f"ISNet stage requires rembg; missing: {exc}"
            ) from exc
        _ISNET_SESSION = new_session("isnet-general-use")
        return _ISNET_SESSION


def _basnet_saliency(bg_rgb: np.ndarray) -> np.ndarray:
    """Run BASNet. bg_rgb is (H, W, 3) uint8. Returns native-256x256 float32 in [0, 1]."""
    import torch
    from torchvision import transforms

    model = _load_basnet()
    pil = Image.fromarray(bg_rgb).convert("RGB")
    tfm = transforms.Compose(
        [
            transforms.Resize((_BASNET_INPUT, _BASNET_INPUT)),
            transforms.ToTensor(),
        ]
    )
    x = tfm(pil).unsqueeze(0)
    with torch.no_grad():
        out = model(x)
    # BASNet returns a 7-tuple (d0, d1, ..., d6) of side-output saliency
    # maps; d0 is the primary fused output. We recursively peel until we
    # find a 4D tensor.
    sal = out
    for _ in range(5):
        if isinstance(sal, torch.Tensor):
            break
        if isinstance(sal, (list, tuple)):
            sal = sal[0]
        elif hasattr(sal, "logits"):
            sal = sal.logits
        elif hasattr(sal, "last_hidden_state"):
            sal = sal.last_hidden_state
        else:
            sal = next(iter(sal.values()))
    if not isinstance(sal, torch.Tensor):
        raise RuntimeError(f"BASNet output not a tensor after unwrap: {type(out)}")
    if sal.dim() == 4:
        sal = sal[0, 0]
    elif sal.dim() == 3:
        sal = sal[0]
    sal_np = sal.detach().cpu().float().numpy()
    sal_np = np.clip(sal_np, 0.0, 1.0).astype(np.float32)
    return sal_np


def _isnet_saliency(bg_rgb: np.ndarray) -> np.ndarray:
    """Run rembg ISNet on bg_rgb (H, W, 3) uint8. Returns (H, W) float32 in [0, 1]."""
    session = _load_isnet_session()
    pil = Image.fromarray(bg_rgb).convert("RGB")
    masks = session.predict(pil)
    if not masks:
        raise RuntimeError("rembg isnet-general-use returned no masks")
    mask_pil = masks[0]
    arr = np.array(mask_pil).astype(np.float32) / 255.0
    if arr.ndim == 3:
        arr = arr.mean(axis=-1)
    return arr.astype(np.float32)


def basnet_isnet_saliency(
    bg_rgb: np.ndarray,
    out_hw: Optional[tuple] = None,
) -> np.ndarray:
    """Two-stage BASNet + ISNet saliency.

    Args:
        bg_rgb: background image as (H, W, 3) uint8 RGB numpy array.
        out_hw: (out_h, out_w) target resolution. If None, returns native bg_rgb resolution.

    Returns:
        float32 (out_h, out_w) in [0, 1].
    """
    if bg_rgb is None:
        raise ValueError("bg_rgb is None")
    if bg_rgb.dtype != np.uint8:
        bg_rgb = bg_rgb.astype(np.uint8)
    h, w = bg_rgb.shape[:2]
    if out_hw is None:
        out_h, out_w = h, w
    else:
        out_h, out_w = int(out_hw[0]), int(out_hw[1])

    import cv2

    basnet_raw = _basnet_saliency(bg_rgb)
    basnet_resized = cv2.resize(basnet_raw, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

    isnet_raw = _isnet_saliency(bg_rgb)
    isnet_resized = cv2.resize(isnet_raw, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

    fused = (basnet_resized + isnet_resized) / 2.0
    fused = np.clip(fused, 0.0, 1.0).astype(np.float32)
    return fused


__all__ = ["basnet_isnet_saliency"]
