"""Leakage-resistant P-Full input extraction for AgentLayout A3.

The cached Crello ``meta.json`` contains designer geometry and legacy labels
derived from that geometry.  This module treats those fields as tainted: it
never reads them and never composites layers.  Every non-background element
remains a separately addressable asset with a stable id.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Dict, List, Literal, Optional

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.run_manifest import sha256_file, write_json_once


PFULL_SCHEMA_VERSION = "a3.pfull-asset-manifest.v1"
PFULL_POLICY_VERSION = "pfull.crello.pixel-only-background.v1"
ASSET_MANIFEST_FILENAME = "asset_manifest.json"
ASSET_DIRNAME = "assets"
BACKGROUND_MIN_OPAQUE_FRACTION = 0.98

# These keys are intentionally named here so tests and reviewers can verify
# that no output contract accidentally grows a designer-geometry field.
FORBIDDEN_GT_KEYS = frozenset(
    {
        "left",
        "top",
        "width",
        "height",
        "bbox",
        "x",
        "y",
        "font_size",
        "area_ratio",
        "underlay_regions",
    }
)


class PFullInputError(ValueError):
    """The source sample cannot be converted without dropping an element."""


class PFullAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    source_index: int = Field(..., ge=0)
    role: Literal["background", "placeable"]
    media_type: Literal["raster", "text"]
    semantic_hint: Literal["base_background", "image", "text", "text_bitmap"]
    content: Optional[str] = None
    asset_ref: Optional[str] = None
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime_type: Optional[str] = None
    native_width: Optional[int] = Field(default=None, ge=1)
    native_height: Optional[int] = Field(default=None, ge=1)
    classification_reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _payload_is_complete(self) -> "PFullAsset":
        if self.media_type == "raster" and not self.asset_ref:
            raise ValueError("raster assets require asset_ref")
        if self.media_type == "text" and not (self.content or self.asset_ref):
            raise ValueError("text assets require content or asset_ref")
        return self


class PFullAssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.pfull-asset-manifest.v1"] = PFULL_SCHEMA_VERSION
    policy_version: Literal["pfull.crello.pixel-only-background.v1"] = (
        PFULL_POLICY_VERSION
    )
    sample_id: str = Field(..., min_length=1)
    title: str = ""
    canvas_width: int = Field(..., ge=1)
    canvas_height: int = Field(..., ge=1)
    background_asset_id: Optional[str] = None
    assets: List[PFullAsset]
    source_meta_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _coverage_and_background(self) -> "PFullAssetManifest":
        ids = [asset.asset_id for asset in self.assets]
        indices = [asset.source_index for asset in self.assets]
        if len(ids) != len(set(ids)) or len(indices) != len(set(indices)):
            raise ValueError("asset ids and source indices must be unique")
        backgrounds = [asset for asset in self.assets if asset.role == "background"]
        if len(backgrounds) > 1:
            raise ValueError("P-Full permits at most one base background")
        actual = backgrounds[0].asset_id if backgrounds else None
        if self.background_asset_id != actual:
            raise ValueError("background_asset_id disagrees with asset roles")
        return self

    def foreground_assets(self) -> List[PFullAsset]:
        return [asset for asset in self.assets if asset.role == "placeable"]


class PFullPreparedInput(BaseModel):
    """Stable-ID boundary consumed by the future A3 Analyst/pipeline."""

    model_config = ConfigDict(extra="forbid")

    user_brief: str
    canvas_width: int
    canvas_height: int
    background_asset_ref: Optional[str] = None
    foreground_assets: List[PFullAsset]


def _opaque_fraction(image: Image.Image) -> float:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    histogram = alpha.histogram()
    opaque = sum(histogram[250:])
    return opaque / max(1, rgba.width * rgba.height)


def _is_pixel_only_background(image: Image.Image, canvas_width: int, canvas_height: int) -> bool:
    """Conservative background rule using asset pixels, never GT placement."""
    return (
        image.size == (canvas_width, canvas_height)
        and _opaque_fraction(image) >= BACKGROUND_MIN_OPAQUE_FRACTION
    )


def _copy_once(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def _asset_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def _load_raster_descriptor(descriptor: Dict, index: int) -> tuple[Path, Image.Image]:
    ref = descriptor.get("asset_ref")
    if not ref:
        raise PFullInputError(f"raster element {index} has no asset_ref")
    path = Path(ref)
    if not path.is_file():
        raise PFullInputError(f"raster element {index} is missing: {path}")
    try:
        image = Image.open(path).convert("RGBA")
        image.load()
    except (OSError, IOError) as error:
        raise PFullInputError(f"raster element {index} is unreadable: {path}") from error
    return path, image


def prepare_pfull_sample(sample_dir: Path, output_dir: Path) -> PFullAssetManifest:
    """Snapshot one cached sample into a new, non-overwritable P-Full directory."""
    meta_path = sample_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    canvas_width = int(meta["canvas_width"])
    canvas_height = int(meta["canvas_height"])
    descriptors = meta.get("elements")
    if not isinstance(descriptors, list) or not descriptors:
        raise PFullInputError("meta.json must contain at least one element")

    # Inspect all raster pixels before choosing the single background.  Legacy
    # kind/classifier labels are deliberately ignored because full_canvas and
    # area_ratio were derived from designer bbox geometry.
    raster_inputs: Dict[int, tuple[Path, Image.Image]] = {}
    background_candidates: List[int] = []
    for position, descriptor in enumerate(descriptors):
        source_index = int(descriptor.get("idx", position))
        is_text = descriptor.get("type_code") == 1 or descriptor.get("kind") == "text"
        if is_text and not descriptor.get("asset_ref"):
            continue
        if descriptor.get("asset_ref"):
            path, image = _load_raster_descriptor(descriptor, source_index)
            raster_inputs[source_index] = (path, image)
            if not is_text and _is_pixel_only_background(image, canvas_width, canvas_height):
                background_candidates.append(source_index)
        elif not is_text:
            raise PFullInputError(
                f"non-text element {source_index} has neither a raster asset nor text payload"
            )

    background_index = min(background_candidates) if background_candidates else None

    output_dir.mkdir(parents=True, exist_ok=False)
    asset_dir = output_dir / ASSET_DIRNAME
    asset_dir.mkdir()
    assets: List[PFullAsset] = []
    try:
        for position, descriptor in enumerate(descriptors):
            source_index = int(descriptor.get("idx", position))
            asset_id = f"asset_{source_index:04d}"
            is_text = descriptor.get("type_code") == 1 or descriptor.get("kind") == "text"
            content = (descriptor.get("content") or "").strip() or None
            raster = raster_inputs.get(source_index)
            stored_ref: Optional[str] = None
            digest: Optional[str] = None
            mime: Optional[str] = None
            native_width: Optional[int] = None
            native_height: Optional[int] = None
            if raster is not None:
                source_path, image = raster
                destination = asset_dir / f"{asset_id}{_asset_extension(source_path)}"
                _copy_once(source_path, destination)
                stored_ref = str(destination.resolve())
                digest = sha256_file(destination)
                mime = mimetypes.guess_type(destination.name)[0] or "image/png"
                native_width, native_height = image.size

            is_background = not is_text and source_index == background_index
            if is_text:
                semantic_hint = "text_bitmap" if raster is not None else "text"
                reason = "Crello text type; content retained; bitmap remains separate when available"
                media_type = "text"
            elif is_background:
                semantic_hint = "base_background"
                reason = (
                    "pixel-only rule: native raster equals canvas dimensions and is >=98% opaque; "
                    "lowest source index wins"
                )
                media_type = "raster"
            else:
                semantic_hint = "image"
                reason = "kept placeable; legacy geometry-derived kind/classifier ignored"
                media_type = "raster"

            assets.append(
                PFullAsset(
                    asset_id=asset_id,
                    source_index=source_index,
                    role="background" if is_background else "placeable",
                    media_type=media_type,
                    semantic_hint=semantic_hint,
                    content=content,
                    asset_ref=stored_ref,
                    sha256=digest,
                    mime_type=mime,
                    native_width=native_width,
                    native_height=native_height,
                    classification_reason=reason,
                )
            )

        assets.sort(key=lambda asset: asset.source_index)
        manifest = PFullAssetManifest(
            sample_id=str(meta["id"]),
            title=str(meta.get("title", "")),
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            background_asset_id=(
                f"asset_{background_index:04d}" if background_index is not None else None
            ),
            assets=assets,
            source_meta_sha256=sha256_file(meta_path),
        )
        write_json_once(
            output_dir / ASSET_MANIFEST_FILENAME, manifest.model_dump(mode="json")
        )
        return manifest
    except Exception:
        # Preserve partial output and consume the directory name, matching the
        # A3 run-store forensic/non-overwrite policy.
        raise


def build_prepared_input(manifest: PFullAssetManifest) -> PFullPreparedInput:
    background = next(
        (asset.asset_ref for asset in manifest.assets if asset.role == "background"),
        None,
    )
    brief = (
        f"Create a {manifest.canvas_width}x{manifest.canvas_height} foreground layout "
        f"for the theme '{manifest.title}'. Use every provided placeable foreground "
        "asset exactly once. The theme is context, not visible copy."
    )
    return PFullPreparedInput(
        user_brief=brief,
        canvas_width=manifest.canvas_width,
        canvas_height=manifest.canvas_height,
        background_asset_ref=background,
        foreground_assets=manifest.foreground_assets(),
    )
