"""R3 text-bitmap normalization and runtime asset contract for AgentLayout A3."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.run_manifest import sha256_bytes, sha256_file, write_json_once
from metagpt.ext.agentlayout.tools.pfull_preprocessor import PFullAssetManifest


R3_SCHEMA_VERSION = "a3.r3-asset-manifest.v1"
R3_NORMALIZATION_VERSION = "r3.alpha-tight-long-edge.v1"
R3_MANIFEST_FILENAME = "r3_asset_manifest.json"
R3_ASSET_DIRNAME = "assets"
R3_TEXT_SUFFIX = "_r3_text.png"


class R3NormalizationError(ValueError):
    """A text asset cannot be normalized without violating the R3 contract."""


class R3NormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["r3.alpha-tight-long-edge.v1"] = R3_NORMALIZATION_VERSION
    long_edge_px: int = Field(default=512, ge=3)
    padding_px: int = Field(default=8, ge=0)
    alpha_threshold: int = Field(default=1, ge=0, le=254)
    resize_filter: Literal["lanczos"] = "lanczos"

    @model_validator(mode="after")
    def _content_area_exists(self) -> "R3NormalizationConfig":
        if self.long_edge_px <= 2 * self.padding_px:
            raise ValueError("long_edge_px must exceed twice padding_px")
        return self


class R3Asset(BaseModel):
    """Leakage-safe runtime asset; original text dimensions are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    role: Literal["background", "placeable"]
    media_type: Literal["raster", "text_bitmap"]
    content: Optional[str] = None
    asset_ref: str
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    bitmap_width: int = Field(..., ge=1)
    bitmap_height: int = Field(..., ge=1)
    bitmap_aspect_ratio: float = Field(..., gt=0)
    source_bitmap_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class R3AssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.r3-asset-manifest.v1"] = R3_SCHEMA_VERSION
    sample_id: str = Field(..., min_length=1)
    canvas_width: int = Field(..., ge=1)
    canvas_height: int = Field(..., ge=1)
    background_asset_id: Optional[str] = None
    normalization: R3NormalizationConfig
    source_pfull_manifest_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    assets: List[R3Asset]

    @model_validator(mode="after")
    def _coverage(self) -> "R3AssetManifest":
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("R3 asset IDs must be unique")
        backgrounds = [asset.asset_id for asset in self.assets if asset.role == "background"]
        actual = backgrounds[0] if len(backgrounds) == 1 else None
        if len(backgrounds) > 1 or actual != self.background_asset_id:
            raise ValueError("R3 background contract disagrees with asset roles")
        return self

    def foreground_assets(self) -> List[R3Asset]:
        return [asset for asset in self.assets if asset.role == "placeable"]


def alpha_tight_bbox(image: Image.Image, alpha_threshold: int) -> Tuple[int, int, int, int]:
    alpha = image.convert("RGBA").getchannel("A")
    mask = alpha.point(lambda value: 255 if value > alpha_threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        raise R3NormalizationError("text bitmap has no alpha pixels above threshold")
    return bbox


def normalize_text_bitmap(
    source: Path,
    destination: Path,
    config: R3NormalizationConfig,
) -> Tuple[int, int, str]:
    """Tight-crop, resize and pad one RGBA bitmap; publish it exactly once."""
    try:
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
            rgba.load()
    except (OSError, IOError) as error:
        raise R3NormalizationError(f"unreadable text bitmap: {source}") from error

    tight = rgba.crop(alpha_tight_bbox(rgba, config.alpha_threshold))
    content_long_edge = config.long_edge_px - 2 * config.padding_px
    scale = content_long_edge / max(tight.size)
    resized_size = (
        max(1, int(round(tight.width * scale))),
        max(1, int(round(tight.height * scale))),
    )
    resized = tight.resize(resized_size, Image.Resampling.LANCZOS)
    normalized = Image.new(
        "RGBA",
        (resized.width + 2 * config.padding_px, resized.height + 2 * config.padding_px),
        (0, 0, 0, 0),
    )
    normalized.alpha_composite(resized, (config.padding_px, config.padding_px))
    if max(normalized.size) != config.long_edge_px:
        raise AssertionError("R3 normalization failed to produce the frozen long edge")

    buffer = BytesIO()
    normalized.save(buffer, format="PNG", compress_level=9, optimize=False)
    payload = buffer.getvalue()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(payload)
    return normalized.width, normalized.height, sha256_bytes(payload)


def prepare_r3_sample(
    pfull_manifest_path: Path,
    output_dir: Path,
    config: R3NormalizationConfig,
) -> R3AssetManifest:
    """Create an immutable R3 runtime manifest from one P-Full manifest."""
    pfull = PFullAssetManifest.model_validate_json(pfull_manifest_path.read_bytes())
    output_dir.mkdir(parents=True, exist_ok=False)
    asset_dir = output_dir / R3_ASSET_DIRNAME
    asset_dir.mkdir()
    assets: List[R3Asset] = []

    for asset in pfull.assets:
        if not asset.asset_ref or not asset.sha256:
            raise R3NormalizationError(
                f"asset {asset.asset_id} has no bitmap; R3 forbids text-only fallback"
            )
        source = Path(asset.asset_ref)
        if not source.is_file():
            raise R3NormalizationError(f"asset {asset.asset_id} is missing: {source}")
        if asset.media_type == "text":
            if not asset.content:
                raise R3NormalizationError(f"text asset {asset.asset_id} has no content")
            destination = asset_dir / f"{asset.asset_id}{R3_TEXT_SUFFIX}"
            width, height, digest = normalize_text_bitmap(source, destination, config)
            assets.append(
                R3Asset(
                    asset_id=asset.asset_id,
                    role=asset.role,
                    media_type="text_bitmap",
                    content=asset.content,
                    asset_ref=str(destination.resolve()),
                    sha256=digest,
                    bitmap_width=width,
                    bitmap_height=height,
                    bitmap_aspect_ratio=width / height,
                    source_bitmap_sha256=asset.sha256,
                )
            )
        else:
            if not asset.native_width or not asset.native_height:
                raise R3NormalizationError(f"raster asset {asset.asset_id} has no dimensions")
            assets.append(
                R3Asset(
                    asset_id=asset.asset_id,
                    role=asset.role,
                    media_type="raster",
                    content=asset.content,
                    asset_ref=asset.asset_ref,
                    sha256=asset.sha256,
                    bitmap_width=asset.native_width,
                    bitmap_height=asset.native_height,
                    bitmap_aspect_ratio=asset.native_width / asset.native_height,
                )
            )

    manifest = R3AssetManifest(
        sample_id=pfull.sample_id,
        canvas_width=pfull.canvas_width,
        canvas_height=pfull.canvas_height,
        background_asset_id=pfull.background_asset_id,
        normalization=config,
        source_pfull_manifest_sha256=sha256_file(pfull_manifest_path),
        assets=assets,
    )
    write_json_once(output_dir / R3_MANIFEST_FILENAME, manifest.model_dump(mode="json"))
    return manifest


def is_r3_text_bitmap(asset_ref: Optional[str]) -> bool:
    return bool(asset_ref and asset_ref.endswith(R3_TEXT_SUFFIX))


def r3_prompt_descriptor(asset_ref: str) -> str:
    """Expose only aspect ratio, never normalized or source pixel dimensions."""
    with Image.open(asset_ref) as image:
        ratio = image.width / image.height
    return (
        " [R3 normalized text bitmap: preserve aspect ratio "
        f"{ratio:.6f}; choose final bbox scale and position from the design context; "
        "font and colour are baked into the bitmap]"
    )


def contain_size(source_size: Tuple[int, int], target_size: Tuple[int, int]) -> Tuple[int, int]:
    """Largest aspect-preserving integer size contained by a target bbox."""
    source_width, source_height = source_size
    target_width, target_height = target_size
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise ValueError("source and target dimensions must be positive")
    scale = min(target_width / source_width, target_height / source_height)
    return (
        max(1, min(target_width, int(round(source_width * scale)))),
        max(1, min(target_height, int(round(source_height * scale)))),
    )
