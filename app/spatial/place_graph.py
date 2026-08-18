"""PlaceGraph: spatial observation places, not per-bundle nodes.

A Place is created only when the robot physically relocates enough (metric pose
distance or observed displacement).  In-place rotations only update
``heading_coverage`` on the current Place.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.spatial.models import MovementEdge, PlaceNode, SpatialPose


@dataclass
class PlaceObservation:
    observation_id: str
    heading_sector: int | None
    timestamp: float
    objects: list[str] = field(default_factory=list)
    rgbd_frame_id: str | None = None
    pose: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "heading_sector": self.heading_sector,
            "timestamp": self.timestamp,
            "objects": self.objects,
            "rgbd_frame_id": self.rgbd_frame_id,
            "pose": self.pose,
            "provenance": self.provenance,
        }


class PlaceGraph:
    def __init__(
        self,
        *,
        merge_distance_m: float = 0.25,
        relocation_min_displacement_m: float = 0.10,
    ) -> None:
        self.merge_distance_m = float(merge_distance_m)
        self.relocation_min_displacement_m = float(relocation_min_displacement_m)
        self.places: dict[str, PlaceNode] = {}
        self.observations: dict[str, PlaceObservation] = {}
        self.edges: list[MovementEdge] = []
        self._current_place_id: str | None = None
        self._sequence = 0

    def register_observation(
        self,
        *,
        observation_id: str,
        heading_sector: int | None,
        objects: list[str],
        rgbd_frame_id: str | None = None,
        pose: SpatialPose | None = None,
        observed_displacement_m: float | None = None,
        timestamp: float | None = None,
        target_candidate: bool = False,
    ) -> tuple[str, bool]:
        """Register one observation; returns (place_id, created_new_place)."""
        now = timestamp if timestamp is not None else time.time()
        if self._current_place_id is None:
            place_id = self._new_place(pose=pose)
            self._current_place_id = place_id
            created = True
        else:
            place_id, created = self._resolve_place(
                pose=pose,
                observed_displacement_m=observed_displacement_m,
            )
        place = self.places[place_id]
        place.observation_ids.append(observation_id)
        place.visit_count += 1
        if heading_sector is not None:
            place.heading_coverage[str(heading_sector)] = (
                place.heading_coverage.get(str(heading_sector), 0) + 1
            )
        for label in objects:
            if label not in place.observed_object_ids:
                place.observed_object_ids.append(label)
        if target_candidate:
            place.target_candidate = True
        self.observations[observation_id] = PlaceObservation(
            observation_id=observation_id,
            heading_sector=heading_sector,
            timestamp=now,
            objects=list(objects),
            rgbd_frame_id=rgbd_frame_id,
            pose=pose.to_dict() if pose else None,
            provenance={"place_id": place_id},
        )
        self._current_place_id = place_id
        return place_id, created

    def _resolve_place(
        self,
        *,
        pose: SpatialPose | None,
        observed_displacement_m: float | None,
    ) -> tuple[str, bool]:
        current = self.places[self._current_place_id]
        if pose is not None:
            current_pose = current.pose
            if current_pose is not None:
                distance = math.hypot(pose.x - current_pose.x, pose.y - current_pose.y)
                if distance >= self.merge_distance_m:
                    new_id = self._new_place(pose=pose, from_place=self._current_place_id)
                    return new_id, True
                return self._current_place_id, False
            # current place has no pose: adopt pose but do not create yet
            current.pose = pose
            current.pose_quality = pose.quality
            return self._current_place_id, False
        if observed_displacement_m is not None and observed_displacement_m >= self.relocation_min_displacement_m:
            new_id = self._new_place(pose=None, from_place=self._current_place_id)
            return new_id, True
        return self._current_place_id, False

    def _new_place(self, *, pose: SpatialPose | None, from_place: str | None = None) -> str:
        self._sequence += 1
        place_id = f"P{self._sequence}"
        self.places[place_id] = PlaceNode(
            place_id=place_id,
            pose=pose,
            pose_quality=pose.quality if pose else "unavailable",
            provenance={"created_at": time.time(), "from_place": from_place},
        )
        if from_place is not None and from_place in self.places:
            self.edges.append(
                MovementEdge(
                    edge_id=f"E{len(self.edges) + 1}",
                    from_place=from_place,
                    to_place=place_id,
                    provenance={"source": "place_graph_relocation"},
                )
            )
        return place_id

    def mark_negative(self, place_id: str | None = None) -> None:
        place_id = place_id or self._current_place_id
        if place_id in self.places:
            self.places[place_id].negative_evidence += 1

    def current_place(self) -> PlaceNode | None:
        if self._current_place_id is None:
            return None
        return self.places.get(self._current_place_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "places": [place.to_dict() for place in self.places.values()],
            "observations": [obs.to_dict() for obs in self.observations.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "current_place_id": self._current_place_id,
            "merge_distance_m": self.merge_distance_m,
            "relocation_min_displacement_m": self.relocation_min_displacement_m,
        }
