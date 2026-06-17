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


class EnvironmentRoom(StrictBaseModel):
    room_id: str
    name: str | None = None
    room_type: str | None = None
    floor_id: str | None = None
    location_hint: str | None = None
    confidence: Confidence = Field(ge=0.0, le=1.0)


class EnvironmentDoor(StrictBaseModel):
    door_id: str
    label: str | None = None
    connected_room_id: str | None = None
    floor_id: str | None = None
    location_hint: str | None = None
    state: Literal["open", "closed", "unknown"] = "unknown"
    confidence: Confidence = Field(ge=0.0, le=1.0)


class RoomTypePrior(StrictBaseModel):
    room_type: str
    common_objects: list[str] = Field(default_factory=list)
    likely_layout: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(ge=0.0, le=1.0)


class ObjectLocationPrior(StrictBaseModel):
    object_name: str
    likely_locations: list[str] = Field(default_factory=list)
    unlikely_locations: list[str] = Field(default_factory=list)
    related_objects: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(ge=0.0, le=1.0)


class EnvironmentKnowledge(StrictBaseModel):
    building_id: str | None = None
    floor_id: str | None = None
    known_rooms: list[EnvironmentRoom] = Field(default_factory=list)
    known_doors: list[EnvironmentDoor] = Field(default_factory=list)
    corridor_layout: str | None = None
    room_type_priors: list[RoomTypePrior] = Field(default_factory=list)
    object_location_priors: list[ObjectLocationPrior] = Field(default_factory=list)
    last_updated_at: str | None = None
    confidence: Confidence = Field(ge=0.0, le=1.0)


class ObservationRecord(StrictBaseModel):
    observation_id: str
    timestamp: str
    image_id: str | None = None
    frame_id: str | None = None
    location_hint: str | None = None
    visible_objects: list[SceneObject] = Field(default_factory=list)
    visible_relations: list[SceneRelation] = Field(default_factory=list)
    detected_doors: list[EnvironmentDoor] = Field(default_factory=list)
    room_state: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    task_context: str | None = None
    confidence: Confidence = Field(ge=0.0, le=1.0)


class KnowledgeItem(StrictBaseModel):
    id: str
    knowledge_type: Literal[
        "environment_layout",
        "room_type_prior",
        "object_location_prior",
        "observation",
        "task_memory",
    ]
    content_zh: str
    source: str
    confidence: Confidence = Field(ge=0.0, le=1.0)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class RobotTask(StrictBaseModel):
    task_id: str
    raw_text: str
    task_type: Literal[
        "find_object",
        "count_objects",
        "inspect_area",
        "check_door_state",
        "find_room",
        "navigate_to_location",
        "verify_condition",
        "summarize_scene",
        "compare_states",
    ]
    target_object: str | None = None
    target_location: str | None = None
    target_room: str | None = None
    scope: str | None = None
    constraints: list[str] = Field(default_factory=list)
    parsed_slots: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    confidence: Confidence = Field(ge=0.0, le=1.0)


class SceneHypothesis(StrictBaseModel):
    hypothesis_id: str
    target: str
    possible_location: str
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    probability: Confidence = Field(ge=0.0, le=1.0)
    verification_action: str
    status: Literal["proposed", "verified", "rejected", "unknown"] = "proposed"


class PredictiveSceneGraphNode(StrictBaseModel):
    id: str
    label: str
    node_type: Literal[
        "observed_object",
        "inferred_object",
        "place_node",
        "container_node",
        "task_target_node",
    ]
    object_id: str | None = None
    visible: bool
    confidence: Confidence = Field(ge=0.0, le=1.0)
    reason_zh: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class PredictiveSceneGraphEdge(StrictBaseModel):
    source_id: str
    target_id: str
    edge_type: Literal[
        "visible_relation",
        "spatial_prior",
        "functional_prior",
        "containment_prior",
        "navigation_relation",
        "verification_relation",
    ]
    label: str | None = None
    confidence: Confidence = Field(ge=0.0, le=1.0)
    reason_zh: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class PredictiveSceneGraph(StrictBaseModel):
    nodes: list[PredictiveSceneGraphNode] = Field(default_factory=list)
    edges: list[PredictiveSceneGraphEdge] = Field(default_factory=list)
    observed_node_ids: list[str] = Field(default_factory=list)
    inferred_node_ids: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    recommended_verification_targets: list[str] = Field(default_factory=list)


class TaskPlanStep(StrictBaseModel):
    step_id: int = Field(ge=1)
    action_type: Literal[
        "observe",
        "move",
        "turn",
        "inspect",
        "count",
        "verify",
        "summarize",
        "stop",
    ]
    target: str | None = None
    description_zh: str
    expected_result: str | None = None
    depends_on: list[int] = Field(default_factory=list)
    confidence: Confidence = Field(ge=0.0, le=1.0)


class CountState(StrictBaseModel):
    counted_object_ids: list[str] = Field(default_factory=list)
    possible_duplicates: list[list[str]] = Field(default_factory=list)
    uncertain_regions: list[str] = Field(default_factory=list)
    recommended_next_viewpoints: list[str] = Field(default_factory=list)


class TaskPlan(StrictBaseModel):
    plan_type: Literal[
        "find_object",
        "count_objects",
        "inspect_area",
        "check_door_state",
        "navigate_to_location",
        "general",
    ]
    summary_zh: str
    steps: list[TaskPlanStep] = Field(default_factory=list)
    fallback_steps: list[TaskPlanStep] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    count_state: CountState | None = None


class KnowledgeUpdate(StrictBaseModel):
    update_id: str
    update_type: Literal["new", "confirmed", "conflict", "expired", "ignored"]
    knowledge_type: Literal[
        "environment_layout",
        "room_type_prior",
        "object_location_prior",
        "temporary_state",
        "task_memory",
    ]
    content_zh: str
    source_observation_id: str | None = None
    stable: bool
    confidence: Confidence = Field(ge=0.0, le=1.0)


class CandidateFact(StrictBaseModel):
    fact_id: str
    fact_type: Literal[
        "environment_layout",
        "room_type_prior",
        "object_location_prior",
        "temporary_state",
        "task_memory",
    ]
    content_zh: str
    source_object_ids: list[str] = Field(default_factory=list)
    stable: bool
    confidence: Confidence = Field(ge=0.0, le=1.0)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class KnowledgeAwareSceneResult(StrictBaseModel):
    base_scene: SceneAnalysisResult
    parsed_task: RobotTask
    retrieved_knowledge: list[KnowledgeItem] = Field(default_factory=list)
    predictive_scene_graph: PredictiveSceneGraph
    hypotheses: list[SceneHypothesis] = Field(default_factory=list)
    reasoning_summary_zh: str
    task_plan: TaskPlan
    knowledge_updates: list[KnowledgeUpdate] = Field(default_factory=list)
    final_answer_zh: str
