"""SemanticEntityGraph: persistent world-model graph for WebUI / planning.

The graph combines:

* PLACE nodes from :class:`PlaceGraph`
* OBJECT nodes from :class:`SemanticObjectMap`
* MOVED_TO edges from PlaceGraph movement edges
* OBSERVED_FROM edges from Place -> persistent object observations

Unlike per-frame ``observed_scene_graph()``, this is the stable entity graph:
object ids are persistent entity ids (``obj_001``), not labels.
"""

from __future__ import annotations

import time
from typing import Any

from app.spatial.models import (
    SPATIAL_QUALITY_METRIC_RGBD,
    SpatialPose,
)
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_object_map import SemanticObjectMap

GRAPH_SCHEMA_VERSION = "semantic_entity_graph_v1"


class SemanticEntityGraph:
    def __init__(
        self,
        *,
        place_graph: PlaceGraph | None = None,
        object_map: SemanticObjectMap | None = None,
        frame_id: str = "map",
    ) -> None:
        self.place_graph = place_graph or PlaceGraph()
        self.object_map = object_map or SemanticObjectMap()
        self.frame_id = frame_id
        self.revision = 0
        self.route_plan: dict[str, Any] | None = None
        self.association_debug: list[dict[str, Any]] = []

    @property
    def current_place_id(self) -> str | None:
        return self.place_graph.current_place().place_id if self.place_graph.current_place() else None

    def sync_from_observation(
        self,
        *,
        observation_id: str,
        heading_sector: int | None,
        labels: list[str],
        spatial_objects: list[Any],
        pose: SpatialPose | None = None,
        timestamp: float | None = None,
        place_id: str | None = None,
        update_result: Any | None = None,
    ) -> dict[str, Any]:
        """Refresh the graph after one observation + entity-map update."""
        now = timestamp if timestamp is not None else time.time()
        place = self.place_graph.places.get(place_id or "")
        if place is not None and spatial_objects:
            # Attach persistent object ids to the Place (not labels).
            persistent_ids = [
                entry.object_id
                for entry in self.object_map.objects.values()
                if self._entry_touches(entry, observation_id, labels, now)
            ]
            self.place_graph.attach_objects(place.place_id, persistent_ids)
        if update_result is not None:
            self.association_debug.extend(
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in getattr(update_result, "associations", [])
            )
            self.association_debug.extend(update_result.rejected_pairs)
        self.revision += 1
        return self.snapshot()

    def set_route_plan(self, route_plan: dict[str, Any] | None) -> None:
        self.route_plan = route_plan
        self.revision += 1

    def snapshot(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for place in self.place_graph.places.values():
            node = place.to_dict()
            node.update(
                {
                    "node_id": place.place_id,
                    "node_type": "PLACE",
                    "label": place.place_id,
                    "current": place.place_id == self.current_place_id,
                }
            )
            nodes.append(node)

        for obj in self.object_map.objects.values():
            node = obj.to_dict()
            node.update(
                {
                    "node_id": obj.object_id,
                    "node_type": "OBJECT",
                    "label": obj.label,
                }
            )
            nodes.append(node)

        for edge in self.place_graph.edges:
            edges.append(
                {
                    "edge_id": edge.edge_id,
                    "from": edge.from_place,
                    "to": edge.to_place,
                    "relation": "MOVED_TO",
                    "observations": [],
                    "provenance": edge.provenance,
                }
            )

        for place_id, place in self.place_graph.places.items():
            for object_id in place.observed_object_ids:
                if object_id not in self.object_map.objects:
                    continue
                edge_id = f"{place_id}__observed_from__{object_id}"
                edges.append(
                    {
                        "edge_id": edge_id,
                        "from": place_id,
                        "to": object_id,
                        "relation": "OBSERVED_FROM",
                        "observation_count": len(
                            [
                                ob
                                for ob in self.place_graph.observations.values()
                                if ob.provenance.get("place_id") == place_id
                                and object_id
                                in [
                                    obj.object_id
                                    for obj in self.object_map.objects.values()
                                    if self._entry_touches(
                                        obj,
                                        ob.observation_id,
                                        ob.objects,
                                        ob.timestamp,
                                    )
                                ]
                            ]
                        )
                        or 1,
                        "last_seen": self.object_map.objects[object_id].last_seen,
                        "provenance": "visual_observed",
                    }
                )

        # Deduplicate edges preserving stable ids.
        unique: dict[str, dict[str, Any]] = {}
        for edge in edges:
            unique[edge["edge_id"]] = edge

        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "revision": self.revision,
            "frame_id": self.frame_id,
            "nodes": nodes,
            "edges": list(unique.values()),
            "current_place_id": self.current_place_id,
            "route_plan": self.route_plan,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()

    def _entry_touches(
        self,
        entry: Any,
        observation_id: str,
        labels: list[str],
        now: float,
    ) -> bool:
        if observation_id in entry.source_observation_ids:
            return True
        return entry.label in (labels or [])

    def summary_stats(self) -> dict[str, Any]:
        return {
            "unique_places": len(self.place_graph.places),
            "places_revisited": sum(
                1 for place in self.place_graph.places.values() if place.revisited
            ),
            **self.object_map.summary_stats(),
            "graph_revision": self.revision,
        }