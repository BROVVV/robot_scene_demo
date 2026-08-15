"""Event-driven live semantic observer backed by the existing scene graph stack."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
import time
from typing import Any, Callable

from app.video.object_tracker import VideoObjectTracker
from app.video.observed_scene_graph_builder import ObservedSceneGraphBuilder
from app.video.schemas import FrameObject, FrameObservation, FrameRelation, SceneGraph


@dataclass
class SemanticObservation:
    frame_id: str
    timestamp_sec: float
    robot_pose: dict[str, Any] | None
    objects: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    source: str
    stale: bool
    heading_sector: int | None = None
    scene_graph: SceneGraph | None = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scene_graph"] = self.scene_graph.to_dict() if self.scene_graph else None
        return payload


class LiveSemanticObserver:
    """Refresh semantic context only after stable observation events.

    ``analyze`` is injected so unit tests remain offline and the real runner may
    use the established full-scene vision worker. Its payload must use the
    worker's ``scene_objects`` / ``scene_relations`` fields.
    """

    def __init__(self, analyze: Callable[[Any, Any], dict[str, Any]] | None = None,
                 *, ttl_seconds: float = 10.0, heading_sector_deg: float = 30.0,
                 translation_refresh_m: float = 0.05,
                 now: Callable[[], float] = time.time) -> None:
        self.analyze = analyze
        self.ttl_seconds = float(ttl_seconds)
        self.heading_sector_deg = max(1.0, float(heading_sector_deg))
        self.translation_refresh_m = max(0.0, float(translation_refresh_m))
        self._now = now
        self._cached: SemanticObservation | None = None
        self.last_semantic_observation_timestamp: float | None = None
        self.last_heading_sector: int | None = None
        self.last_scene_signature: str | None = None
        self.last_target_profile_hash: str | None = None

    def observe(self, *, target_profile: Any, frame_or_bundle: Any,
                robot_pose: dict[str, Any] | None,
                force: bool = False) -> SemanticObservation:
        now = float(self._now())
        yaw = float((robot_pose or {}).get("yaw_deg", 0.0))
        sector = int(round(yaw / self.heading_sector_deg))
        profile_hash = hashlib.sha1(
            repr(target_profile.to_dict() if hasattr(target_profile, "to_dict") else target_profile).encode("utf-8")
        ).hexdigest()
        translation_delta = _translation_delta(
            robot_pose,
            self._cached.robot_pose if self._cached is not None else None,
        )
        fresh = (
            self._cached is not None
            and self.last_semantic_observation_timestamp is not None
            and now - self.last_semantic_observation_timestamp < self.ttl_seconds
            and self.last_heading_sector == sector
            and self.last_target_profile_hash == profile_hash
            and translation_delta <= self.translation_refresh_m
        )
        if fresh and not force:
            cached = self._cached
            return SemanticObservation(
                **{**cached.__dict__, "cache_hit": True, "stale": False}
            )
        if self.analyze is None:
            payload = frame_or_bundle if isinstance(frame_or_bundle, dict) else {}
        else:
            payload = self.analyze(frame_or_bundle, target_profile)
        observation = _from_payload(payload, robot_pose=robot_pose, sector=sector, now=now)
        self._cached = observation
        self.last_semantic_observation_timestamp = now
        self.last_heading_sector = sector
        self.last_target_profile_hash = profile_hash
        self.last_scene_signature = _signature(observation)
        return observation


def semantic_payload_from_quick_target_absence(
    payload: dict[str, Any] | None,
    *,
    image_path: str,
    frame_id: str,
) -> dict[str, Any] | None:
    """Reuse a definitive quick negative without inventing scene objects.

    The quick worker may provide a useful scene summary while intentionally
    returning only target-matched objects.  When it explicitly says the target
    is absent, an empty/zero-match semantic observation is sufficient for
    next-view reasoning and avoids a duplicate full-scene API call.  Ambiguous
    or positive decisions must still use the full semantic observer.
    """

    value = payload if isinstance(payload, dict) else {}
    decision = value.get("target_decision") or {}
    summary = str(value.get("scene_summary_zh") or "").strip()
    if decision.get("is_present") is not False or not summary:
        return None
    return {
        "scene_objects": [],
        "scene_relations": [],
        "scene_summary_zh": summary,
        "image_path": image_path,
        "frame_id": frame_id,
        "source": "siliconflow_quick_explicit_target_absence",
        "semantic_reuse_reason": "quick_target_decision_explicitly_absent",
        "target_decision": decision,
    }


def _from_payload(payload: dict[str, Any], *, robot_pose: dict[str, Any] | None,
                  sector: int, now: float) -> SemanticObservation:
    raw_objects = list(payload.get("scene_objects") or payload.get("objects") or [])
    raw_relations = list(payload.get("scene_relations") or payload.get("relations") or [])
    frame_id_text = str(payload.get("frame_id", "semantic_live"))
    frame_id = _numeric_frame_id(frame_id_text)
    objects: list[FrameObject] = []
    for index, item in enumerate(raw_objects, start=1):
        bbox = item.get("bbox_2d") or item.get("bbox")
        if isinstance(bbox, dict):
            bbox = [bbox.get(key, 0.0) for key in ("x1", "y1", "x2", "y2")]
        if not isinstance(bbox, list) or len(bbox) != 4:
            bbox = None
        position = item.get("position") or {}
        horizontal = str(position.get("horizontal") or _bbox_horizontal(bbox))
        label = str(item.get("name") or item.get("label") or item.get("name_zh") or "object")
        label_zh = str(item.get("name_zh") or item.get("label_zh") or label)
        objects.append(FrameObject(
            frame_object_id=str(item.get("id") or f"semantic_obj_{index:03d}"),
            label=label, label_zh=label_zh,
            category=str(item.get("category") or "object"), bbox=bbox,
            mask_area_ratio=item.get("mask_area_ratio"),
            confidence=float(item.get("confidence", item.get("score", 0.5))),
            position_2d=horizontal,
            attributes=list(item.get("attributes") or []) + ([str(item["color"])] if item.get("color") else []),
            navigation_role=str(item.get("navigation_role") or "landmark"),
            is_landmark=True,
        ))
    relations: list[FrameRelation] = []
    for item in raw_relations:
        relations.append(FrameRelation(
            subject_label=str(item.get("subject_label") or item.get("source_id") or ""),
            object_label=str(item.get("object_label") or item.get("target_id") or ""),
            relation=str(item.get("relation") or item.get("relation_type") or "near"),
            confidence=float(item.get("confidence", 0.5)),
            subject_id=str(item.get("subject_id") or item.get("source_id") or ""),
            object_id=str(item.get("object_id") or item.get("target_id") or ""),
            description_zh=str(item.get("description_zh") or ""),
        ))
    frame = FrameObservation(
        frame_id=frame_id, timestamp_sec=now,
        frame_path=str(payload.get("image_path") or "live_semantic_frame"),
        scene_type=payload.get("scene_type"),
        summary_zh=str(payload.get("scene_summary_zh") or ""),
        objects=objects, relations=relations,
    )
    tracks = VideoObjectTracker().build_tracks([frame])
    graph = ObservedSceneGraphBuilder().build([frame], tracks)
    for node in graph.nodes:
        node.attributes["observed_from_pose"] = robot_pose
        node.attributes["heading_sector"] = sector
        node.attributes["observed_heading_deg"] = float((robot_pose or {}).get("yaw_deg", 0.0))
        node.attributes["position_status"] = "observation_pose_only"
    return SemanticObservation(
        frame_id=frame_id_text, timestamp_sec=now, robot_pose=robot_pose,
        objects=[item.to_dict() for item in objects],
        relations=[item.to_dict() for item in relations],
        source=str(payload.get("source") or "existing_scene_analysis"),
        stale=False, heading_sector=sector, scene_graph=graph,
    )


def _numeric_frame_id(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:7], 16)


def _bbox_horizontal(bbox: list[float] | None) -> str:
    if not bbox:
        return "unknown"
    center = (float(bbox[0]) + float(bbox[2])) / 2.0
    return "left" if center < 0.4 else "right" if center > 0.6 else "center"


def _signature(observation: SemanticObservation) -> str:
    text = "|".join(sorted(str(item.get("label")) for item in observation.objects))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _translation_delta(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
) -> float:
    if first is None and second is None:
        return 0.0
    if first is None or second is None:
        return math.inf
    try:
        return math.hypot(
            float(first.get("x", 0.0)) - float(second.get("x", 0.0)),
            float(first.get("y", 0.0)) - float(second.get("y", 0.0)),
        )
    except (TypeError, ValueError):
        return math.inf
