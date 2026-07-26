"""Deterministic vision packet and output contract for the A3 MLLM Analyst."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator

from metagpt.ext.agentlayout.schema import (
    Canvas,
    DesignSpec,
    Element,
    SemanticType,
    VisualType,
)
from metagpt.ext.agentlayout.tools.text_bitmap_normalizer import R3AssetManifest


A3_ANALYST_OUTPUT_SCHEMA_VERSION = "a3.analyst-output.v1"
A3_ANALYST_VISION_VERSION = "a3.analyst-vision-packet.v1"
BACKGROUND_OVERVIEW_FILENAME = "background_overview.png"
CONTACT_SHEET_PREFIX = "asset_contact_sheet_"
CONTACT_CELL_WIDTH = 240
CONTACT_CELL_HEIGHT = 220
CONTACT_COLUMNS = 4
CONTACT_MAX_ASSETS_PER_SHEET = 20
THUMBNAIL_BOX = (208, 158)
BACKGROUND_MAX_EDGE = 768


class A3AssetUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(..., pattern=r"^asset_[0-9]{4}$")
    semantic_type: SemanticType
    description: str = Field(..., min_length=1)
    semantic_role: str = Field(..., min_length=1)
    key_message: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _foreground_only(self) -> "A3AssetUnderstanding":
        # A3-08 smoke finding: full-canvas textures demoted to placeable by
        # the P-Full pixel rule tempted the Analyst into 'background_image',
        # which the P-Full contract forbids. Rejecting at the schema level
        # gives the retry loop an actionable per-asset error.
        if self.semantic_type == SemanticType.BACKGROUND_IMAGE:
            raise ValueError(
                f"{self.asset_id}: P-Full foreground assets cannot use "
                "semantic_type='background_image'; use the closest placeable "
                "type such as 'decorative_image'"
            )
        return self


class A3AnalystOutput(BaseModel):
    """Semantic-only Analyst output; geometry and file paths are forbidden."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.analyst-output.v1"] = A3_ANALYST_OUTPUT_SCHEMA_VERSION
    background_summary: str = Field(..., min_length=1)
    design_intent: str = Field(..., min_length=1)
    style_keywords: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    assets: List[A3AssetUnderstanding]

    @model_validator(mode="after")
    def _unique_ids(self) -> "A3AnalystOutput":
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("Analyst output contains duplicate asset IDs")
        return self


class AnalystVisionPacket(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    version: Literal["a3.analyst-vision-packet.v1"] = A3_ANALYST_VISION_VERSION
    prompt: str
    prompt_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    image_labels: List[str]
    images: List[Image.Image]


def _font(size: int = 18) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _checkerboard(size: Tuple[int, int], step: int = 12) -> Image.Image:
    image = Image.new("RGBA", size, (245, 245, 245, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            if (x // step + y // step) % 2:
                draw.rectangle(
                    (x, y, min(size[0], x + step), min(size[1], y + step)),
                    fill=(220, 220, 220, 255),
                )
    return image


def _contain(image: Image.Image, box: Tuple[int, int]) -> Image.Image:
    copy = image.convert("RGBA")
    copy.thumbnail(box, Image.Resampling.LANCZOS)
    return copy


def build_background_overview(manifest: R3AssetManifest) -> Image.Image:
    background = next((asset for asset in manifest.assets if asset.role == "background"), None)
    if background is not None:
        with Image.open(background.asset_ref) as opened:
            image = opened.convert("RGB")
            image.thumbnail((BACKGROUND_MAX_EDGE, BACKGROUND_MAX_EDGE), Image.Resampling.LANCZOS)
            return image.copy()

    scale = min(BACKGROUND_MAX_EDGE / manifest.canvas_width, BACKGROUND_MAX_EDGE / manifest.canvas_height)
    size = (
        max(1, int(round(manifest.canvas_width * scale))),
        max(1, int(round(manifest.canvas_height * scale))),
    )
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    label = "NO BASE BACKGROUND — blank canvas"
    bbox = draw.textbbox((0, 0), label, font=_font(20))
    draw.text(
        ((size[0] - (bbox[2] - bbox[0])) / 2, (size[1] - (bbox[3] - bbox[1])) / 2),
        label,
        fill=(80, 80, 80),
        font=_font(20),
    )
    return image


def build_contact_sheets(manifest: R3AssetManifest) -> List[Image.Image]:
    """Render every foreground in uniform cells; native scale cannot leak."""
    assets = manifest.foreground_assets()
    sheets: List[Image.Image] = []
    for start in range(0, len(assets), CONTACT_MAX_ASSETS_PER_SHEET):
        page = assets[start : start + CONTACT_MAX_ASSETS_PER_SHEET]
        rows = max(1, math.ceil(len(page) / CONTACT_COLUMNS))
        sheet = Image.new(
            "RGB",
            (CONTACT_COLUMNS * CONTACT_CELL_WIDTH, rows * CONTACT_CELL_HEIGHT),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for offset, asset in enumerate(page):
            col = offset % CONTACT_COLUMNS
            row = offset // CONTACT_COLUMNS
            left = col * CONTACT_CELL_WIDTH
            top = row * CONTACT_CELL_HEIGHT
            draw.rectangle(
                (left, top, left + CONTACT_CELL_WIDTH - 1, top + CONTACT_CELL_HEIGHT - 1),
                outline=(150, 150, 150),
                width=1,
            )
            thumb_bg = _checkerboard(THUMBNAIL_BOX)
            with Image.open(asset.asset_ref) as opened:
                thumb = _contain(opened, THUMBNAIL_BOX)
            thumb_bg.alpha_composite(
                thumb,
                ((THUMBNAIL_BOX[0] - thumb.width) // 2, (THUMBNAIL_BOX[1] - thumb.height) // 2),
            )
            sheet.paste(thumb_bg.convert("RGB"), (left + 16, top + 8))
            draw.text((left + 12, top + 171), asset.asset_id, fill="black", font=_font(18))
            media = "TEXT BITMAP" if asset.media_type == "text_bitmap" else "IMAGE"
            draw.text((left + 12, top + 194), media, fill=(70, 70, 70), font=_font(14))
        sheets.append(sheet)
    return sheets


def _prompt_assets(manifest: R3AssetManifest) -> List[Dict]:
    return [
        {
            "asset_id": asset.asset_id,
            "media_type": asset.media_type,
            "content": asset.content,
            "bitmap_aspect_ratio": round(asset.bitmap_aspect_ratio, 6),
        }
        for asset in manifest.foreground_assets()
    ]


def build_analyst_prompt(manifest: R3AssetManifest, user_brief: str) -> str:
    schema = A3AnalystOutput.model_json_schema()
    return f"""Role: You are the semantic design Analyst in AgentLayout A3.

You MUST inspect BOTH the first attached background overview and every following
foreground contact-sheet page. The contact-sheet labels are authoritative stable
asset IDs. Uniform cells deliberately remove original placement and scale.

# User brief
{user_brief}

# Canvas
{manifest.canvas_width}x{manifest.canvas_height}

# Foreground assets (same IDs/order as contact sheets)
{json.dumps(_prompt_assets(manifest), ensure_ascii=False, indent=2)}

# Responsibilities
- Describe the background's visual content, saliency/quiet regions and palette.
- Assign every foreground asset a semantic type and semantic role.
- semantic_type must NEVER be "background_image": every listed asset is
  placeable foreground by contract, even full-canvas textures or panels.
  Use "decorative_image" for texture/panel-like assets.
- Use text `content` for meaning and inspect its bitmap for visual style.
- State semantic constraints only. Do NOT output coordinates, bbox, x/y, width,
  height, font size, original scale, z-index or file paths.
- Include every listed asset ID exactly once; never invent or rename IDs.
- The theme/brief is context, not permission to invent new foreground assets.

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_text_only_analyst_prompt(manifest: R3AssetManifest, user_brief: str) -> str:
    """Gate A ablation arm: identical output contract, zero visual access.

    The prompt states explicitly that no images are attached, so semantic
    judgements come from text content and media type alone. Everything else
    (schema, ID coverage, the background_image ban) matches the vision arm.
    """
    schema = A3AnalystOutput.model_json_schema()
    return f"""Role: You are the semantic design Analyst in AgentLayout A3.

You have NO visual access in this configuration: no background image and no
foreground thumbnails are attached. Reason from the brief, each asset's text
content and its media type alone.

# User brief
{user_brief}

# Canvas
{manifest.canvas_width}x{manifest.canvas_height}

# Foreground assets
{json.dumps(_prompt_assets(manifest), ensure_ascii=False, indent=2)}

# Responsibilities
- Assign every foreground asset a semantic type and semantic role.
- semantic_type must NEVER be "background_image": every listed asset is
  placeable foreground by contract. Use "decorative_image" when unsure
  about a non-text asset.
- background_summary must describe only what the brief implies; do not
  invent visual details you cannot see.
- State semantic constraints only. Do NOT output coordinates, bbox, x/y,
  width, height, font size, original scale, z-index or file paths.
- Include every listed asset ID exactly once; never invent or rename IDs.

# Output JSON Schema
{json.dumps(schema, ensure_ascii=False, indent=2)}

Output one JSON object only, without markdown fences."""


def build_vision_packet(manifest: R3AssetManifest, user_brief: str) -> AnalystVisionPacket:
    prompt = build_analyst_prompt(manifest, user_brief)
    background = build_background_overview(manifest)
    sheets = build_contact_sheets(manifest)
    images = [background, *sheets]
    labels = [BACKGROUND_OVERVIEW_FILENAME] + [
        f"{CONTACT_SHEET_PREFIX}{index:02d}.png" for index in range(1, len(sheets) + 1)
    ]
    return AnalystVisionPacket(
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        image_labels=labels,
        images=images,
    )


def validate_asset_coverage(output: A3AnalystOutput, manifest: R3AssetManifest) -> None:
    expected = {asset.asset_id for asset in manifest.foreground_assets()}
    actual = {asset.asset_id for asset in output.assets}
    if expected != actual:
        raise ValueError(
            f"Analyst asset coverage mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    invalid_backgrounds = [
        asset.asset_id
        for asset in output.assets
        if asset.semantic_type == SemanticType.BACKGROUND_IMAGE
    ]
    if invalid_backgrounds:
        raise ValueError(
            "P-Full foreground assets cannot be reclassified as background: "
            f"{invalid_backgrounds}"
        )


def parse_analyst_output(response: str) -> A3AnalystOutput:
    """Parse a JSON object with defensive removal of fences/surrounding prose."""
    text = (response or "").strip()
    if "```" in text:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    return A3AnalystOutput.model_validate(json.loads(text))


def analyst_output_to_design_spec(
    output: A3AnalystOutput, manifest: R3AssetManifest
) -> DesignSpec:
    validate_asset_coverage(output, manifest)
    by_id = {asset.asset_id: asset for asset in output.assets}
    elements: List[Element] = []
    for runtime_asset in manifest.foreground_assets():
        understood = by_id[runtime_asset.asset_id]
        elements.append(
            Element(
                id=runtime_asset.asset_id,
                semantic_type=understood.semantic_type,
                visual_type=VisualType.IMAGE,
                content=runtime_asset.content,
                asset_ref=runtime_asset.asset_ref,
            )
        )
    background = next((asset for asset in manifest.assets if asset.role == "background"), None)
    if background is not None:
        elements.insert(
            0,
            Element(
                id=background.asset_id,
                semantic_type=SemanticType.BACKGROUND_IMAGE,
                visual_type=VisualType.IMAGE,
                asset_ref=background.asset_ref,
            ),
        )
    return DesignSpec(
        canvas=Canvas(
            width=manifest.canvas_width,
            height=manifest.canvas_height,
            background_asset_ref=background.asset_ref if background else None,
            background_color="#FFFFFF" if background is None else None,
        ),
        elements=elements,
        style_keywords=output.style_keywords,
        language=output.language,
    )


def image_to_base64(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=9)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def save_vision_packet(packet: AnalystVisionPacket, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    for label, image in zip(packet.image_labels, packet.images):
        image.save(output_dir / label, format="PNG", compress_level=9)
    request = {
        "version": packet.version,
        "prompt": packet.prompt,
        "prompt_sha256": packet.prompt_sha256,
        "image_labels": packet.image_labels,
    }
    from metagpt.ext.agentlayout.run_manifest import write_json_once

    write_json_once(output_dir / "analyst_request.json", request)
