"""BASNet + ISNet two-stage saliency pipeline for PKU PosterLayout Occ metric.

experiment.md spec (Occlusion): "saliency map S from background image via
BASNet + ISNet (or pfpn+basnet)". The public PKU PosterLayout evaluator fuses
PFPN and BASNet maps. This module instead fuses BASNet with ISNet, so its Occ
values support matched comparisons only when every method is re-evaluated by
this same pipeline. Published SEGA values remain literature references and
are not directly comparable.

Implementation:
  * BASNet stage uses ``creative-graphic-design/BASNet`` (Hugging Face,
    transformers AutoModel). 87M params, input 256x256, output (1, 1, 256, 256)
    sigmoid in [0, 1].
  * ISNet stage uses ``rembg`` session ``isnet-general-use`` (ISNet trained
    on DIS dataset). rembg returns a single-channel uint8 alpha mask at the
    input resolution.
  * Fuse: resize both maps to the layout canvas resolution, then take the
    per-pixel MAX, matching PKU eval.py's fusion operation. Detector identity
    still differs because ISNet replaces PKU's PFPN branch.

Returns float32 in [0, 1] of shape (H, W). On failure (model load error or
torch unavailable) raises ``RuntimeError`` instead of silently falling back;
the caller can decide whether to skip that sample.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import os
import threading
from importlib import metadata
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

_BASNET_MODEL = None
_BASNET_LOCK = threading.Lock()
_BASNET_RUNTIME_IDENTITY = None
_ISNET_SESSION = None
_ISNET_LOCK = threading.Lock()
_ISNET_RUNTIME_IDENTITY = None

_BASNET_HF_ID = "creative-graphic-design/BASNet"
_BASNET_INPUT = 256
_BASNET_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "configuration_basnet.py",
    "modeling_basnet.py",
)
_ISNET_MODEL_NAME = "isnet-general-use"
_ISNET_SESSION_MODULE = "rembg.sessions.dis_general_use"
_ISNET_SESSION_CLASS = "DisSession"
_ISNET_EXPECTED_SHA256 = "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"
_ISNET_EXPECTED_MD5 = "fc16ebd8b0c10d971d3513d564d01e29"
_ISNET_PROVIDER = "CPUExecutionProvider"


def _resolve_basnet_snapshot() -> tuple:
    """Return the exact locally cached revision and snapshot used for loading."""
    hub_cache = os.environ.get("HF_HUB_CACHE")
    if hub_cache:
        hub = Path(hub_cache).expanduser()
    else:
        hf_home = Path(os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser()
        hub = hf_home / "hub"
    repo = hub / "models--creative-graphic-design--BASNet"
    ref = repo / "refs" / "main"
    if not ref.is_file():
        raise RuntimeError(f"cached BASNet ref is missing; downloads are disabled: {ref}")
    revision = ref.read_text(encoding="utf-8").strip()
    if not revision:
        raise RuntimeError(f"cached BASNet ref is empty: {ref}")
    snapshot = repo / "snapshots" / revision
    missing = [
        str(snapshot / filename)
        for filename in _BASNET_REQUIRED_FILES
        if not (snapshot / filename).is_file()
    ]
    if missing:
        raise RuntimeError(f"cached BASNet artifacts are missing: {missing}")
    return revision, snapshot


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unavailable"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_verified_isnet_artifact() -> tuple:
    """Return exact verified ISNet bytes before importing rembg or ONNX Runtime."""
    u2net_home = Path(os.environ.get("U2NET_HOME", "~/.u2net")).expanduser()
    path = u2net_home / "isnet-general-use.onnx"
    if not path.is_file():
        raise RuntimeError(
            f"cached ISNet artifact is missing; downloads are forbidden: {path}"
        )
    payload = path.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    actual_md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    if actual_sha256 != _ISNET_EXPECTED_SHA256 or actual_md5 != _ISNET_EXPECTED_MD5:
        raise RuntimeError(
            "cached ISNet artifact hash mismatch; downloads are forbidden: "
            f"sha256={actual_sha256}, md5={actual_md5}"
        )
    return (
        {
            "path": str(path.resolve()),
            "sha256": actual_sha256,
            "md5": actual_md5,
            "size_bytes": len(payload),
        },
        payload,
    )


def _verify_isnet_artifact() -> dict:
    artifact, _ = _read_verified_isnet_artifact()
    return artifact


def _verify_basnet_executed_code(model, snapshot: Path) -> dict:
    """Bind the Python classes actually executed to authoritative snapshot bytes."""
    model_config = getattr(model, "config", None)
    classes = {
        "configuration_basnet.py": None if model_config is None else model_config.__class__,
        "modeling_basnet.py": model.__class__,
    }
    executed = {}
    for filename, class_object in classes.items():
        if class_object is None:
            raise RuntimeError(f"loaded BASNet has no class for {filename}")
        source = inspect.getsourcefile(class_object)
        if not source:
            raise RuntimeError(f"cannot locate executed BASNet source for {filename}")
        source_path = Path(source).resolve()
        authoritative_path = (snapshot / filename).resolve()
        source_sha256 = _sha256_file(source_path)
        authoritative_sha256 = _sha256_file(authoritative_path)
        if source_sha256 != authoritative_sha256:
            raise RuntimeError(
                f"executed BASNet code differs from snapshot {filename}: "
                f"{source_sha256} != {authoritative_sha256}"
            )
        executed[filename] = {
            "executed_path": str(source_path),
            "executed_sha256": source_sha256,
            "authoritative_path": str(authoritative_path),
            "authoritative_sha256": authoritative_sha256,
        }
    return executed


def _validated_map(
    value: np.ndarray,
    stage: str,
    expected_shape: Optional[tuple] = None,
) -> np.ndarray:
    """Return a strict 2D float32 saliency map or fail closed."""
    try:
        result = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{stage} saliency output is not numeric: {exc}") from exc
    if result.ndim != 2:
        raise RuntimeError(f"{stage} saliency output must be 2D, got {result.shape}")
    if expected_shape is not None and result.shape != expected_shape:
        raise RuntimeError(
            f"{stage} saliency output must have shape {expected_shape}, got {result.shape}"
        )
    if result.size == 0 or not np.isfinite(result).all():
        raise RuntimeError(f"{stage} saliency output must be non-empty and finite")
    minimum = float(result.min())
    maximum = float(result.max())
    if minimum < 0.0 or maximum > 1.0:
        raise RuntimeError(
            f"{stage} saliency output must be within [0, 1], got "
            f"[{minimum}, {maximum}]"
        )
    return np.ascontiguousarray(result, dtype=np.float32)


def _load_basnet():
    """Lazy-load BASNet on first use. Thread-safe."""
    global _BASNET_MODEL
    if _BASNET_MODEL is not None:
        return _BASNET_MODEL
    with _BASNET_LOCK:
        if _BASNET_MODEL is not None:
            return _BASNET_MODEL
        revision, snapshot = _resolve_basnet_snapshot()
        try:
            import torch  # noqa: F401
            from transformers import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                f"BASNet requires torch + transformers; missing: {exc}"
            ) from exc
        import torch as _torch

        load_kwargs = {
            "revision": revision,
            "local_files_only": True,
            "trust_remote_code": True,
            "force_download": False,
        }
        try:
            model = AutoModel.from_pretrained(
                str(snapshot.resolve()),
                dtype=_torch.float32,
                **load_kwargs,
            )
        except TypeError:
            # Older transformers (<4.46) still uses torch_dtype.
            model = AutoModel.from_pretrained(
                str(snapshot.resolve()),
                torch_dtype=_torch.float32,
                **load_kwargs,
            )
        model.eval()
        model_config = getattr(model, "config", None)
        executed_code = _verify_basnet_executed_code(model, snapshot)
        global _BASNET_RUNTIME_IDENTITY
        _BASNET_RUNTIME_IDENTITY = {
            "model_id": _BASNET_HF_ID,
            "requested_revision": revision,
            "resolved_snapshot": str(snapshot.resolve()),
            "from_pretrained_path": str(snapshot.resolve()),
            "local_files_only": True,
            "trust_remote_code": True,
            "force_download": False,
            "model_class_module": model.__class__.__module__,
            "model_class_name": model.__class__.__name__,
            "config_class_module": (
                None if model_config is None else model_config.__class__.__module__
            ),
            "config_class_name": (
                None if model_config is None else model_config.__class__.__name__
            ),
            "torch_version": _package_version("torch"),
            "transformers_version": _package_version("transformers"),
            "executed_code": executed_code,
        }
        _BASNET_MODEL = model
        return _BASNET_MODEL


def _load_isnet_session():
    """Load exact local ISNet bytes directly, bypassing rembg's downloader."""
    global _ISNET_SESSION
    if _ISNET_SESSION is not None:
        return _ISNET_SESSION
    with _ISNET_LOCK:
        if _ISNET_SESSION is not None:
            return _ISNET_SESSION
        artifact, model_bytes = _read_verified_isnet_artifact()
        try:
            import onnxruntime as ort
            from rembg.sessions.dis_general_use import DisSession
        except ImportError as exc:
            raise RuntimeError(
                f"ISNet stage requires rembg + onnxruntime; missing: {exc}"
            ) from exc
        session_class = DisSession
        if (
            session_class.__module__ != _ISNET_SESSION_MODULE
            or session_class.__name__ != _ISNET_SESSION_CLASS
            or session_class.name() != _ISNET_MODEL_NAME
        ):
            raise RuntimeError(
                "installed rembg does not expose the exact isnet-general-use DisSession"
            )
        available_providers = list(ort.get_available_providers())
        if _ISNET_PROVIDER not in available_providers:
            raise RuntimeError(
                f"required frozen provider {_ISNET_PROVIDER} is unavailable: "
                f"{available_providers}"
            )
        session_options = ort.SessionOptions()
        if "OMP_NUM_THREADS" in os.environ:
            thread_count = int(os.environ["OMP_NUM_THREADS"])
            session_options.inter_op_num_threads = thread_count
            session_options.intra_op_num_threads = thread_count
        # Construct the exact rembg session class without BaseSession.__init__:
        # that initializer always calls download_models()/pooch.retrieve().
        session = object.__new__(session_class)
        session.model_name = _ISNET_MODEL_NAME
        session.providers = [_ISNET_PROVIDER]
        session.inner_session = ort.InferenceSession(
            model_bytes,
            providers=[_ISNET_PROVIDER],
            sess_options=session_options,
        )
        session_class = session.__class__
        try:
            reported_name = session_class.name()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "rembg session cannot report its model identity; refusing fallback"
            ) from exc
        identity = {
            "requested_model_name": _ISNET_MODEL_NAME,
            "session_model_name": getattr(session, "model_name", None),
            "session_reported_name": reported_name,
            "session_class_module": session_class.__module__,
            "session_class_name": session_class.__name__,
            "rembg_version": _package_version("rembg"),
            "onnxruntime_version": _package_version("onnxruntime"),
            "requested_providers": list(getattr(session, "providers", []) or []),
            "active_providers": [],
            "available_providers": available_providers,
            "verified_artifact": artifact,
            "session_construction": "direct verified bytes; downloader bypassed",
        }
        inner_session = getattr(session, "inner_session", None)
        if inner_session is not None and hasattr(inner_session, "get_providers"):
            identity["active_providers"] = list(inner_session.get_providers())
        expected = (
            identity["session_model_name"] == _ISNET_MODEL_NAME
            and identity["session_reported_name"] == _ISNET_MODEL_NAME
            and identity["session_class_module"] == _ISNET_SESSION_MODULE
            and identity["session_class_name"] == _ISNET_SESSION_CLASS
            and identity["requested_providers"] == [_ISNET_PROVIDER]
            and identity["active_providers"] == [_ISNET_PROVIDER]
        )
        if not expected:
            raise RuntimeError(
                "rembg did not create the exact isnet-general-use session; "
                f"refusing possible U2Net fallback: {identity}"
            )
        global _ISNET_RUNTIME_IDENTITY
        _ISNET_RUNTIME_IDENTITY = identity
        _ISNET_SESSION = session
        return _ISNET_SESSION


def detector_runtime_identity(require_loaded: bool = True) -> dict:
    """Return the identities observed from the actual loaded detector objects."""
    if require_loaded and (_BASNET_RUNTIME_IDENTITY is None or _ISNET_RUNTIME_IDENTITY is None):
        raise RuntimeError("BASNet and ISNet runtime identities are not both available")
    return {
        "basnet": copy.deepcopy(_BASNET_RUNTIME_IDENTITY),
        "isnet": copy.deepcopy(_ISNET_RUNTIME_IDENTITY),
    }


def _unwrap_basnet_primary(output, torch_module):
    """Select BASNet's primary refined ``dout`` map from its eight outputs."""
    value = output
    if hasattr(value, "activated"):
        value = value.activated
    elif isinstance(value, dict) and "activated" in value:
        value = value["activated"]
    elif isinstance(value, (list, tuple)):
        value = value[0]

    if hasattr(value, "dout"):
        value = value.dout
    elif isinstance(value, dict) and "dout" in value:
        value = value["dout"]
    elif isinstance(value, (list, tuple)):
        value = value[0]

    if not isinstance(value, torch_module.Tensor):
        raise RuntimeError(
            f"BASNet primary dout is not a tensor after unwrap: {type(output)}"
        )
    return value


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
    # The frozen remote-code model returns eight maps: refined dout, d1..d6,
    # and the bridge output db. ``dout`` is the primary saliency map.
    sal = _unwrap_basnet_primary(out, torch)
    if sal.dim() == 4 and tuple(sal.shape[:2]) == (1, 1):
        sal = sal[0, 0]
    elif sal.dim() == 3 and sal.shape[0] == 1:
        sal = sal[0]
    elif sal.dim() != 2:
        raise RuntimeError(f"BASNet output has unsupported tensor shape: {tuple(sal.shape)}")
    sal_np = sal.detach().cpu().float().numpy()
    return _validated_map(sal_np, "BASNet")


def _isnet_saliency(bg_rgb: np.ndarray) -> np.ndarray:
    """Run rembg ISNet on bg_rgb (H, W, 3) uint8. Returns (H, W) float32 in [0, 1]."""
    session = _load_isnet_session()
    pil = Image.fromarray(bg_rgb).convert("RGB")
    masks = session.predict(pil)
    if not masks:
        raise RuntimeError("rembg isnet-general-use returned no masks")
    mask_pil = masks[0]
    arr = np.array(mask_pil).astype(np.float32) / 255.0
    return _validated_map(arr, "ISNet")


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
    bg_rgb = np.asarray(bg_rgb)
    if bg_rgb.ndim != 3 or bg_rgb.shape[2] != 3 or not bg_rgb.size:
        raise ValueError(f"bg_rgb must have shape (H, W, 3), got {bg_rgb.shape}")
    if not np.isfinite(bg_rgb).all():
        raise ValueError("bg_rgb contains NaN or infinity")
    if bg_rgb.dtype != np.uint8:
        if float(bg_rgb.min()) < 0.0 or float(bg_rgb.max()) > 255.0:
            raise ValueError("bg_rgb values must be within [0, 255]")
        bg_rgb = bg_rgb.astype(np.uint8)
    h, w = bg_rgb.shape[:2]
    if out_hw is None:
        out_h, out_w = h, w
    else:
        out_h, out_w = int(out_hw[0]), int(out_hw[1])
    if out_h <= 0 or out_w <= 0:
        raise ValueError(f"output shape must be positive, got {(out_h, out_w)}")

    import cv2

    basnet_raw = _validated_map(_basnet_saliency(bg_rgb), "BASNet")
    basnet_resized = cv2.resize(basnet_raw, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    basnet_resized = _validated_map(
        basnet_resized, "resized BASNet", (out_h, out_w)
    )

    isnet_raw = _validated_map(_isnet_saliency(bg_rgb), "ISNet")
    isnet_resized = cv2.resize(isnet_raw, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    isnet_resized = _validated_map(
        isnet_resized, "resized ISNet", (out_h, out_w)
    )

    # Fuse via per-pixel MAX, matching PKU's fusion operation. Detector
    # identity still differs: this evaluator substitutes ISNet for PFPN.
    # Therefore direct comparisons require re-evaluating every method with
    # this exact implementation; published cross-paper values are literature
    # references only.
    fused = np.maximum(basnet_resized, isnet_resized)
    return _validated_map(fused, "fused BASNet+ISNet", (out_h, out_w))


__all__ = ["basnet_isnet_saliency", "detector_runtime_identity"]
