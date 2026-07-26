"""Versioned, explicit configuration contract for AgentLayout A3 runs.

This module contains provenance-bearing configuration only.  It deliberately
does not change the legacy pipeline yet; later A3 phases must consume this
contract instead of relying on ambient defaults.
"""
from __future__ import annotations

from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


A3_CONFIG_SCHEMA_VERSION = "a3.run-config.v1"


class ModelCallConfig(BaseModel):
    """Resolved settings for one model-backed A3 stage."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1)
    reasoning_effort: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = Field(default=None, ge=1)
    image_detail: Optional[Literal["low", "high", "auto", "original"]] = None
    structured_output: bool = True


class ImageNormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_long_edge_px: int = Field(default=512, ge=1)
    text_padding_px: int = Field(default=8, ge=0)
    alpha_threshold: int = Field(default=1, ge=0, le=255)
    resize_filter: Literal["lanczos"] = "lanczos"


class A3RunConfig(BaseModel):
    """Frozen experimental configuration included verbatim in every manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a3.run-config.v1"] = A3_CONFIG_SCHEMA_VERSION
    architecture: Literal["A3-MLLM"] = "A3-MLLM"
    foreground_protocol: Literal["P-Full"] = "P-Full"
    renderer: Literal["R3"] = "R3"
    loop: Literal["L0", "L1-Gated"]
    internal_judge: str = Field(..., min_length=1)
    evaluation_judge: Optional[str] = None
    dataset_split: str = Field(..., min_length=1)
    seed: int = 42
    models: Dict[str, ModelCallConfig]
    image_normalization: ImageNormalizationConfig = Field(
        default_factory=ImageNormalizationConfig
    )
    schema_versions: Dict[str, str] = Field(default_factory=dict)
    price_table_version: Optional[str] = None

    @model_validator(mode="after")
    def _require_system_stages(self) -> "A3RunConfig":
        required = {
            "analyst",
            "asset_planner",
            "composition_director",
            "coordinate_mapper",
            "judge_select",
        }
        if self.loop == "L1-Gated":
            required.add("judge_critic")
        missing = sorted(required - set(self.models))
        if missing:
            raise ValueError(f"models is missing required A3 stages: {missing}")
        return self
