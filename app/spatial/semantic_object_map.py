"""SemanticObjectMap: observed object facts with optional spatial localization.

It stores only facts from the perception/localizer stack.  PSG predictions are
never inserted here (they live in :class:`SemanticPrior`).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.perception.depth_object_localizer import ObjectSpatialObservation
from app.spatial.models import SPATIAL_QUALITY_CAMERA_LOCAL


@dataclass
class SemanticObjectEntry:
    object_id: str
    label: str
    depth_m: float | None = None
    camera_xyz: tuple[float, float, float] | None = None
    map_xyz: tuple[float, float, float] | None = None
    bearing_deg: float | None = None
    spatial_quality: str = SPATIAL_QUALITY_CAMERA_LOCAL
    confidence: float = 0.0
    observation_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    seen_from_places: list[str] = field(default_factory=list)
    negative_evidence: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)
    source_observation_ids: list[str] = field(default_factory=list)
    merge_history: list[dict[str, Any]] = field(default_factory=list)
    association_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "label": self.label,
            "depth_m": self.depth_m,
            "camera_xyz": list(self.camera_xyz) if self.camera_xyz else None,
            "map_xyz": list(self.map_xyz) if self.map_xyz else None,
            "bearing_deg": self.bearing_deg,
            "spatial_quality": self.spatial_quality,
            "confidence": self.confidence,
            "observation_count": self.observation_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "seen_from_places": self.seen_from_places,
            "negative_evidence": self.negative_evidence,
            "provenance": self.provenance,
            "source_observation_ids": self.source_observation_ids,
            "merge_history": self.merge_history,
            "association_score": self.association_score,
        }


class SemanticObjectMap:
    def __init__(self, *, merge_distance_m: float = 0.4, label_similarity: bool = True) -> None:
        self.merge_distance_m = float(merge_distance_m)
        self.label_similarity = bool(label_similarity)
        self.objects: dict[str, SemanticObjectEntry] = {}
        self._next_id = 1

    def update(
        self,
        spatial_objects: list[ObjectSpatialObservation],
        *,
        place_id: str | None = None,
        now: float | None = None,
    ) -> list[str]:
        """Update observed objects; returns newly created object ids."""
        now = now if now is not None else time.time()
        new_ids: list[str] = []
        for obs in spatial_objects:
            if place_id:
                obs.provenance = {**obs.provenance, "place_id": place_id}
            existing = self._find_existing(obs)
            if existing is None:
                object_id = f"obj_{self._next_id:03d}"
                self._next_id += 1
                self.objects[object_id] = SemanticObjectEntry(
                    object_id=object_id,
                    label=obs.label,
                    depth_m=obs.depth_m,
                    camera_xyz=obs.camera_xyz,
                    map_xyz=obs.map_xyz,
                    bearing_deg=obs.bearing_deg,
                    spatial_quality=obs.spatial_quality,
                    confidence=obs.confidence,
                    observation_count=1,
                    first_seen=now,
                    last_seen=now,
                    seen_from_places=[place_id] if place_id else [],
                    provenance=dict(obs.provenance),
                    source_observation_ids=_source_ids(obs),
                )
                new_ids.append(object_id)
            else:
                self._merge(existing, obs, place_id=place_id, now=now)
        return new_ids

    def _find_existing(self, obs: ObjectSpatialObservation) -> SemanticObjectEntry | None:
        best: SemanticObjectEntry | None = None
        best_dist = self.merge_distance_m
        for entry in self.objects.values():
            if self.label_similarity and entry.label != obs.label:
                continue
            # World coordinates are the only cross-view spatial identity
            # signal.  Camera-local coordinates are comparable only when the
            # producer explicitly says both observations share a frame.
            if obs.map_xyz is not None and entry.map_xyz is not None:
                dist = math.dist(obs.map_xyz, entry.map_xyz)
            elif (
                obs.camera_xyz is not None
                and entry.camera_xyz is not None
                and _same_camera_frame(entry, obs)
            ):
                dist = math.dist(obs.camera_xyz, entry.camera_xyz)
            elif (
                obs.bearing_deg is not None
                and entry.bearing_deg is not None
                and obs.depth_m is not None
                and entry.depth_m is not None
                and str(obs.provenance.get("place_id")) == str(entry.provenance.get("place_id"))
            ):
                dist = math.hypot(
                    abs(float(obs.bearing_deg) - float(entry.bearing_deg)) / 90.0,
                    abs(float(obs.depth_m) - float(entry.depth_m)),
                )
            else:
                # A repeated label without spatial evidence is a new
                # hypothesis; merging it would collapse multiple chairs,
                # doors or boxes into one false entity.
                dist = float("inf")
            if dist <= best_dist:
                best = entry
                best_dist = dist
        return best

    def _merge(
        self,
        entry: SemanticObjectEntry,
        obs: ObjectSpatialObservation,
        *,
        place_id: str | None,
        now: float,
    ) -> None:
        entry.observation_count += 1
        entry.last_seen = now
        entry.association_score = max(entry.association_score, _association_score(entry, obs))
        source_id = _source_id(obs)
        if source_id and source_id not in entry.source_observation_ids:
            entry.source_observation_ids.append(source_id)
        entry.merge_history.append(
            {"timestamp": now, "source_observation_id": source_id, "association_score": entry.association_score}
        )
        if obs.depth_m is not None:
            entry.depth_m = obs.depth_m
        if obs.camera_xyz is not None:
            entry.camera_xyz = obs.camera_xyz
        if obs.map_xyz is not None:
            entry.map_xyz = obs.map_xyz
        if obs.bearing_deg is not None:
            entry.bearing_deg = obs.bearing_deg
        # Never downgrade a spatial quality.
        if _quality_rank(obs.spatial_quality) > _quality_rank(entry.spatial_quality):
            entry.spatial_quality = obs.spatial_quality
        entry.confidence = max(entry.confidence, obs.confidence)
        if place_id and place_id not in entry.seen_from_places:
            entry.seen_from_places.append(place_id)

    def mark_negative(self, label: str, *, place_id: str | None = None) -> None:
        for entry in self.objects.values():
            if entry.label == label:
                entry.negative_evidence += 1

    def observed_scene_graph(self) -> dict[str, Any]:
        return {
            "nodes": [entry.to_dict() for entry in self.objects.values()],
            "edges": [],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "objects": [entry.to_dict() for entry in self.objects.values()],
            "merge_distance_m": self.merge_distance_m,
        }


def _quality_rank(value: str) -> int:
    order = {
        "RGB_ONLY": 0,
        "CAMERA_LOCAL": 1,
        "RELATIVE_RGBD": 2,
        "METRIC_RGBD": 3,
    }
    return order.get(value, 0)


def _source_id(obs: ObjectSpatialObservation) -> str | None:
    value = obs.provenance.get("observation_id") or obs.provenance.get("frame_id")
    return str(value) if value else None


def _source_ids(obs: ObjectSpatialObservation) -> list[str]:
    value = _source_id(obs)
    return [value] if value else []


def _same_camera_frame(entry: SemanticObjectEntry, obs: ObjectSpatialObservation) -> bool:
    left = entry.provenance.get("frame_id")
    right = obs.provenance.get("frame_id")
    return bool(left and right and left == right)


def _association_score(entry: SemanticObjectEntry, obs: ObjectSpatialObservation) -> float:
    if entry.map_xyz is not None and obs.map_xyz is not None:
        distance = math.dist(entry.map_xyz, obs.map_xyz)
        return max(0.0, 1.0 - distance / max(0.001, entry.merge_distance_m if hasattr(entry, "merge_distance_m") else 0.4))
    if entry.label == obs.label:
        return 0.5
    return 0.0
