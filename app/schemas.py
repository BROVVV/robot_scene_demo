"""Pydantic models for scene analysis results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Confidence = float


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(StrictBaseModel):
    horizontal: Literal["left", "center", "right"]
    vertical: Literal["front", "middle", "back"]
    relative_to_robot: str
    estimated_distance_m: float | None = None


class BoundingBox2D(StrictBaseModel):
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)


class SceneObject(StrictBaseModel):
    id: str
    name: str
    name_zh: str
    category: str
    color: str | None = None
    attributes: list[str] = Field(default_factory=list)
    visible: bool
    position: Position
    bbox_2d: BoundingBox2D
    confidence: Confidence = Field(ge=0.0, le=1.0)


class SceneRelation(StrictBaseModel):
    source_id: str
    target_id: str
    relation_type: Literal[
        "left_of",
        "right_of",
        "in_front_of",
        "behind",
        "on",
        "under",
        "above",
        "below",
        "in",
        "near",
        "far",
        "contains",
        "occluding",
    ]
    description_zh: str
    estimated_distance_m: float | None = None
    confidence: Confidence = Field(ge=0.0, le=1.0)


class TopologyNode(StrictBaseModel):
    id: str
    label: str
    object_id: str | None = None


class TopologyEdge(StrictBaseModel):
    source_id: str
    target_id: str
    relation_type: str
    label: str | None = None
    relation_id: str | None = None


class TopologyGraph(StrictBaseModel):
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)


class TargetDecision(StrictBaseModel):
    target_text: str
    is_present: bool
    matched_object_ids: list[str] = Field(default_factory=list)
    match_reason_zh: str
    confidence: Confidence = Field(ge=0.0, le=1.0)


class RouteStep(StrictBaseModel):
    step_id: int = Field(ge=1)
    action: Literal[
        "move_forward",
        "move_backward",
        "turn_left",
        "turn_right",
        "stop",
    ]
    distance_m: float | None = None
    turn_angle_deg: float | None = None
    description_zh: str


class RoutePlan(StrictBaseModel):
    route_type: Literal["approach_visible_target", "explore_likely_location"]
    summary_zh: str
    steps: list[RouteStep] = Field(default_factory=list)
    safety_notes_zh: list[str] = Field(default_factory=list)


class SceneAnalysisResult(StrictBaseModel):
    scene_summary_zh: str
    objects: list[SceneObject] = Field(default_factory=list)
    relations: list[SceneRelation] = Field(default_factory=list)
    topology: TopologyGraph
    target_decision: TargetDecision
    route_plan: RoutePlan
