"""Unified live semantic navigation graph.

Observation bundles are evidence records only.  The renderable and searchable
graph contains stable Places, observed Objects and expandable Frontiers.
"""

from __future__ import annotations

import math
import time
from typing import Any

from app.perception.depth_object_localizer import ObjectSpatialObservation
from app.spatial.models import SpatialPose
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_object_map import SemanticObjectMap


class SemanticNavigationGraph:
    def __init__(
        self,
        *,
        place_graph: PlaceGraph | None = None,
        object_map: SemanticObjectMap | None = None,
        heading_sectors: int = 12,
    ) -> None:
        self.place_graph = place_graph or PlaceGraph()
        self.object_map = object_map or SemanticObjectMap()
        self.heading_sectors = max(1, int(heading_sectors))
        self.frontiers: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.revision = 0
        self._frontier_sequence = 0

    @property
    def current_place_id(self) -> str | None:
        return getattr(self.place_graph, "_current_place_id", None)

    def update_observation(
        self,
        *,
        observation_id: str,
        heading_sector: int | None,
        scene_objects: list[dict[str, Any]],
        scene_relations: list[dict[str, Any]],
        pose: dict[str, Any] | None = None,
        spatial_objects: list[ObjectSpatialObservation] | None = None,
        timestamp: float | None = None,
        observed_displacement_m: float | None = None,
        target_candidate: bool = False,
    ) -> dict[str, Any]:
        observation_time = timestamp if timestamp is not None else time.time()
        spatial_pose = _pose_from_dict(pose)
        labels = [_label(item) for item in scene_objects if _label(item)]
        place_id, created = self.place_graph.register_observation(
            observation_id=observation_id,
            heading_sector=heading_sector,
            objects=labels,
            rgbd_frame_id=None,
            pose=spatial_pose,
            observed_displacement_m=observed_displacement_m,
            timestamp=observation_time,
            target_candidate=target_candidate,
        )
        objects = list(
            spatial_objects
            or self._object_observations(scene_objects, pose, observation_id)
        )
        new_object_ids = self.object_map.update(objects, place_id=place_id, now=observation_time)
        observation_object_ids = [
            object_id
            for object_id, entry in self.object_map.objects.items()
            if observation_id in entry.source_observation_ids
        ]
        if not observation_object_ids:
            observation_object_ids = [
                object_id
                for object_id, entry in self.object_map.objects.items()
                if entry.last_seen == observation_time
            ]
        self._refresh_edges(place_id, scene_relations)
        self._refresh_frontiers(place_id, heading_sector, pose)
        self.observations.append(
            {
                "observation_id": observation_id,
                "timestamp": observation_time,
                "place_id": place_id,
                "object_ids": observation_object_ids,
            }
        )
        self.revision += 1
        return {
            "place_id": place_id,
            "created_place": created,
            "new_object_ids": new_object_ids,
            "map": self.to_dict(),
        }

    def mark_target_confirmed(
        self,
        *,
        object_id: str | None = None,
        observation_id: str | None = None,
    ) -> None:
        place = self.place_graph.current_place()
        if place is not None:
            place.target_confirmed = True
            place.target_candidate = True
        if object_id and object_id in self.object_map.objects:
            self.object_map.objects[object_id].provenance["target_confirmed"] = True
            self.object_map.objects[object_id].provenance["confirmed_observation_id"] = observation_id
        self.revision += 1

    def to_dict(self) -> dict[str, Any]:
        places = [place.to_dict() for place in self.place_graph.places.values()]
        objects = [obj.to_dict() for obj in self.object_map.objects.values()]
        frontiers = list(self.frontiers.values())
        nodes: list[dict[str, Any]] = []
        for place in places:
            nodes.append({
                "node_id": place["place_id"],
                "node_type": "PLACE",
                "label": place["place_id"],
                "pose": place.get("pose"),
                "pose_quality": place.get("pose_quality", "unavailable"),
                "current": place["place_id"] == self.current_place_id,
                **place,
            })
        for obj in objects:
            nodes.append({
                "node_id": obj["object_id"],
                "node_type": "OBJECT",
                "label": obj.get("label", "object"),
                "pose": _object_pose(obj),
                **obj,
            })
        for frontier in frontiers:
            nodes.append({
                "node_id": frontier["frontier_id"],
                "node_type": "FRONTIER",
                "label": frontier["frontier_id"],
                "pose": {
                    "x": frontier.get("position", [0.0, 0.0])[0],
                    "y": frontier.get("position", [0.0, 0.0])[1],
                    "yaw": math.radians(frontier.get("bearing_deg") or 0.0),
                },
                **frontier,
            })
        return {
            "schema_version": "semantic_navigation_graph_v1",
            "revision": self.revision,
            "current_place_id": self.current_place_id,
            "nodes": nodes,
            "edges": list(self.edges),
            "places": places,
            "objects": objects,
            "frontiers": frontiers,
            "observations": list(self.observations),
        }

    def _object_observations(
        self,
        scene_objects: list[dict[str, Any]],
        pose: dict[str, Any] | None,
        observation_id: str,
    ) -> list[ObjectSpatialObservation]:
        result: list[ObjectSpatialObservation] = []
        for item in scene_objects:
            label = _label(item)
            camera = _tuple3(item.get("camera_xyz"))
            map_xyz = _tuple3(item.get("map_xyz"))
            if map_xyz is None and camera is not None and pose is not None:
                map_xyz = _camera_to_map(camera, pose)
            result.append(
                ObjectSpatialObservation(
                    object_id=item.get("id") or item.get("object_id"),
                    label=label or "object",
                    bbox=item.get("bbox_2d") or item.get("bbox"),
                    depth_m=item.get("depth_m") or item.get("estimated_distance_m"),
                    camera_xyz=camera,
                    map_xyz=map_xyz,
                    bearing_deg=item.get("bearing_deg"),
                    spatial_quality=(
                        item.get("spatial_quality")
                        or ("RELATIVE_RGBD" if map_xyz is not None else "RGB_ONLY")
                    ),
                    confidence=float(item.get("confidence", item.get("score", 0.5))),
                    provenance={
                        "source": "live_vlm_rgbd",
                        "observation_id": observation_id,
                    },
                )
            )
        return result

    def _refresh_edges(self, place_id: str, relations: list[dict[str, Any]]) -> None:
        for edge in self.place_graph.edges:
            item = {
                "edge_id": edge.edge_id,
                "from": edge.from_place,
                "to": edge.to_place,
                "relation": "CONNECTED_TO",
                "provenance": "geometry_derived",
                "traversable": edge.navigation_result not in {"failed", "unreachable"},
                "distance": edge.observed_displacement_m or 1.0,
            }
            self._upsert_edge(item)
        for object_id, obj in self.object_map.objects.items():
            if place_id in obj.seen_from_places:
                self._upsert_edge({
                    "edge_id": f"{place_id}__observed_from__{object_id}",
                    "from": place_id,
                    "to": object_id,
                    "relation": "OBSERVED_FROM",
                    "provenance": "visual_observed",
                    "traversable": False,
                })
        for index, relation in enumerate(relations):
            source = relation.get("source_id") or relation.get("subject_id") or relation.get("subject_label")
            target = relation.get("target_id") or relation.get("object_id") or relation.get("object_label")
            if not source or not target:
                continue
            source_id = self._object_id_for(str(source))
            target_id = self._object_id_for(str(target))
            if not source_id or not target_id:
                continue
            relation_name = str(relation.get("relation_type") or relation.get("relation") or "RELATED_TO").upper()
            self._upsert_edge({
                "edge_id": f"{source_id}__{relation_name}__{target_id}",
                "from": source_id,
                "to": target_id,
                "relation": relation_name,
                "provenance": "visual_observed",
                "confidence": float(relation.get("confidence", 0.0) or 0.0),
                "traversable": False,
            })

    def _refresh_frontiers(
        self,
        place_id: str,
        heading_sector: int | None,
        pose: dict[str, Any] | None,
    ) -> None:
        place = self.place_graph.places[place_id]
        covered = set(place.heading_coverage)
        for sector in range(self.heading_sectors):
            if str(sector) in covered:
                continue
            frontier_id = f"F{sector + 1:02d}"
            if frontier_id not in self.frontiers:
                self.frontiers[frontier_id] = {
                    "frontier_id": frontier_id,
                    "position": _frontier_position(pose, sector, self.heading_sectors),
                    "source_place": place_id,
                    "bearing_deg": sector * 360.0 / self.heading_sectors,
                    "information_gain": round(1.0 - len(covered) / self.heading_sectors, 3),
                    "semantic_relevance": 0.0,
                    "traversability": "unknown",
                    "state": "UNVISITED",
                    "provenance": "topological_heading_gap",
                }
                self._upsert_edge({
                    "edge_id": f"{place_id}__frontier__{frontier_id}",
                    "from": place_id,
                    "to": frontier_id,
                    "relation": "FRONTIER_TO",
                    "provenance": "geometry_derived",
                    "traversable": True,
                })

    def _object_id_for(self, value: str) -> str | None:
        if value in self.object_map.objects:
            return value
        matches = [object_id for object_id, obj in self.object_map.objects.items() if obj.label == value]
        return matches[0] if len(matches) == 1 else None

    def _upsert_edge(self, edge: dict[str, Any]) -> None:
        edge_id = edge["edge_id"]
        for index, existing in enumerate(self.edges):
            if existing.get("edge_id") == edge_id:
                self.edges[index] = {**existing, **edge}
                return
        self.edges.append(edge)


def _label(item: dict[str, Any]) -> str:
    return str(item.get("label_zh") or item.get("label") or item.get("name") or "").strip()


def _tuple3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _pose_from_dict(value: dict[str, Any] | None) -> SpatialPose | None:
    if not isinstance(value, dict):
        return None
    try:
        yaw_value = value.get("yaw_rad")
        if yaw_value is None:
            yaw_value = value.get("yaw")
        if yaw_value is None and value.get("yaw_deg") is not None:
            yaw_value = math.radians(float(value["yaw_deg"]))
        return SpatialPose(
            x=float(value.get("x", 0.0)),
            y=float(value.get("y", 0.0)),
            yaw=float(yaw_value or 0.0),
            frame_id=str(value.get("frame_id", "odom")),
            quality=str(value.get("quality", "relative")),
            source=str(value.get("source", "live_odom")),
        )
    except (TypeError, ValueError):
        return None


def _camera_to_map(camera: tuple[float, float, float], pose: dict[str, Any]) -> tuple[float, float, float]:
    lateral, vertical, forward = camera
    yaw_value = pose.get("yaw_rad")
    if yaw_value is None:
        yaw_value = pose.get("yaw")
    if yaw_value is None and pose.get("yaw_deg") is not None:
        yaw_value = math.radians(float(pose["yaw_deg"]))
    yaw = float(yaw_value or 0.0)
    x = float(pose.get("x", 0.0)) + forward * math.cos(yaw) - lateral * math.sin(yaw)
    y = float(pose.get("y", 0.0)) + forward * math.sin(yaw) + lateral * math.cos(yaw)
    return (round(x, 4), round(y, 4), round(vertical, 4))


def _object_pose(obj: dict[str, Any]) -> dict[str, Any] | None:
    xyz = obj.get("map_xyz")
    if not isinstance(xyz, (list, tuple)) or len(xyz) < 2:
        return None
    return {"x": xyz[0], "y": xyz[1], "z": xyz[2] if len(xyz) > 2 else 0.0}


def _frontier_position(
    pose: dict[str, Any] | None, sector: int, total: int
) -> list[float]:
    px = float((pose or {}).get("x", 0.0))
    py = float((pose or {}).get("y", 0.0))
    yaw_value = (pose or {}).get("yaw_rad")
    if yaw_value is None:
        yaw_value = (pose or {}).get("yaw")
    if yaw_value is None and (pose or {}).get("yaw_deg") is not None:
        yaw_value = math.radians(float((pose or {})["yaw_deg"]))
    yaw = float(yaw_value or 0.0)
    heading = yaw + sector * math.tau / total
    return [round(px + math.cos(heading) * 0.8, 3), round(py + math.sin(heading) * 0.8, 3)]
