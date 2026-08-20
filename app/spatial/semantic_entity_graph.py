"""SemanticEntityGraph: persistent world-model graph for WebUI / planning.

The graph combines:

* PLACE nodes from :class:`PlaceGraph`
* OBJECT nodes from :class:`SemanticObjectMap`
* persistent OBJECT -> OBJECT relations (remapped from per-frame scene
  relations through :class:`SemanticObjectMap` associations)
* MOVED_TO edges from PlaceGraph movement edges
* OBSERVED_FROM edges from Place -> persistent object observations

Unlike per-frame ``observed_scene_graph()``, this is the stable entity graph:
object ids are persistent entity ids (``obj_001``), not labels.  The WebUI
"semantic topology" view is a projection (``object_topology_snapshot``) whose
node layout is display-only and never feeds navigation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.spatial.models import SpatialPose
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_object_map import SemanticObjectMap

GRAPH_SCHEMA_VERSION = "semantic_entity_graph_v1"
OBJECT_TOPOLOGY_SCHEMA_VERSION = "semantic_object_topology_v1"

# Single relation vocabulary, kept identical to the offline builder
# (``app/video/observed_scene_graph_builder.py``).  Do not create a fourth
# bespoke relation enum.
OBJECT_RELATIONS = {
    "near",
    "left_of",
    "right_of",
    "in_front_of",
    "behind",
    "on",
    "under",
    "above",
    "below",
    "in",
    "inside",
    "contains",
    "attached_to",
    "blocks",
    "adjacent_to",
}

# Normalise common free-text variants to the canonical vocabulary.  Unknown
# relations are ignored (never coerced to "near") so we do not fabricate facts.
RELATION_ALIASES = {
    "left": "left_of",
    "right": "right_of",
    "front": "in_front_of",
    "in_front": "in_front_of",
    "front_of": "in_front_of",
    "close": "near",
    "close_to": "near",
    "next_to": "adjacent_to",
    "beside": "adjacent_to",
    "within": "inside",
}

# Semantically symmetric relations: ``a near b`` and ``b near a`` merge into a
# single canonical edge.
SYMMETRIC_RELATIONS = {
    "near",
    "adjacent_to",
    "attached_to",
}

# Relations that reflect the current viewpoint and may change when the robot
# moves.  They are persisted but flagged VIEW_RELATIVE so the UI never treats
# them as eternal world facts.
VIEW_RELATIVE_RELATIONS = {
    "left_of",
    "right_of",
    "in_front_of",
    "behind",
    "above",
    "below",
    "under",
}

# Relations that are stable structural facts worth persisting/confirming.
STRUCTURAL_RELATIONS = {
    "near",
    "adjacent_to",
    "inside",
    "in",
    "contains",
    "on",
    "attached_to",
    "blocks",
}

RELATION_TENTATIVE = "TENTATIVE"
RELATION_CONFIRMED = "CONFIRMED"
RELATION_STALE = "STALE"


def normalize_relation(value: str | None) -> str | None:
    """Normalise a relation string to the canonical vocabulary.

    Unknown relations return ``None`` so callers can ignore them instead of
    fabricating a ``near`` edge.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in OBJECT_RELATIONS:
        return text
    return RELATION_ALIASES.get(text)


def relation_scope_of(relation: str) -> str:
    if relation in STRUCTURAL_RELATIONS:
        return "STRUCTURAL"
    return "VIEW_RELATIVE"


def relation_is_symmetric(relation: str) -> bool:
    return relation in SYMMETRIC_RELATIONS


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, v)) if v == v else float(default)  # NaN guard


@dataclass
class PersistentObjectRelation:
    edge_id: str
    source_object_id: str
    target_object_id: str
    relation: str

    confidence: float = 0.5
    observation_count: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    status: str = RELATION_TENTATIVE
    relation_scope: str = "STRUCTURAL"
    directed: bool = False
    source_observation_ids: list[str] = field(default_factory=list)
    descriptions_zh: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "from": self.source_object_id,
            "to": self.target_object_id,
            "relation": self.relation,
            "relation_scope": self.relation_scope,
            "directed": bool(self.directed),
            "confidence": round(self.confidence, 4),
            "observation_count": self.observation_count,
            "status": self.status,
            "first_seen": round(self.first_seen, 4),
            "last_seen": round(self.last_seen, 4),
            "description_zh": (self.descriptions_zh[-1] if self.descriptions_zh else ""),
            "descriptions_zh": list(self.descriptions_zh),
            "source_observation_ids": list(self.source_observation_ids),
            "provenance": dict(self.provenance),
        }


class SemanticEntityGraph:
    def __init__(
        self,
        *,
        place_graph: PlaceGraph | None = None,
        object_map: SemanticObjectMap | None = None,
        frame_id: str = "map",
        relation_min_confidence: float = 0.45,
        relation_confirm_min_observations: int = 2,
        relation_stale_after_seconds: float = 180.0,
        max_relation_descriptions: int = 5,
        include_tentative_objects: bool = True,
        include_stale_objects: bool = True,
        include_view_relative_relations: bool = True,
    ) -> None:
        self.place_graph = place_graph or PlaceGraph()
        self.object_map = object_map or SemanticObjectMap()
        self.frame_id = frame_id
        self.revision = 0
        self.route_plan: dict[str, Any] | None = None
        self.association_debug: list[dict[str, Any]] = []

        self.relation_min_confidence = _clamp01(relation_min_confidence, 0.45)
        self.relation_confirm_min_observations = max(1, int(relation_confirm_min_observations))
        self.relation_stale_after_seconds = max(1.0, float(relation_stale_after_seconds))
        self.max_relation_descriptions = max(1, int(max_relation_descriptions))
        self.include_tentative_objects = bool(include_tentative_objects)
        self.include_stale_objects = bool(include_stale_objects)
        self.include_view_relative_relations = bool(include_view_relative_relations)

        # persistent OBJECT -> OBJECT relation store keyed by
        # (source_object_id, relation, target_object_id)
        self.object_relations: dict[tuple[str, str, str], PersistentObjectRelation] = {}

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
        relations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Refresh the graph after one observation + entity-map update.

        ``relations`` are the per-frame scene relations (already normalised by
        the semantic observer).  Endpoints are resolved only through
        ``update_result.associations`` (frame_object_id -> persistent id);
        label-based identity is never used here.
        """
        now = timestamp if timestamp is not None else time.time()
        place = self.place_graph.places.get(place_id or "")
        if place is not None:
            # Attach persistent object ids to the Place strictly via the
            # entity association result (never by label).
            persistent_ids = self._association_persistent_ids(update_result)
            if persistent_ids:
                self.place_graph.attach_objects(place.place_id, persistent_ids)
        if update_result is not None:
            self.association_debug.extend(
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in getattr(update_result, "associations", [])
            )
            self.association_debug.extend(update_result.rejected_pairs)
        if relations:
            self._sync_object_relations(
                relations=relations,
                update_result=update_result,
                observation_id=observation_id,
                timestamp=now,
            )
        self._mark_stale_relations(now=now)
        self.revision += 1
        return self.snapshot()

    def set_route_plan(self, route_plan: dict[str, Any] | None) -> None:
        self.route_plan = route_plan
        self.revision += 1

    # ------------------------------------------------------------------ #
    # association helpers                                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _association_persistent_ids(update_result: Any) -> list[str]:
        if update_result is None:
            return []
        return [
            str(assoc.persistent_object_id)
            for assoc in getattr(update_result, "associations", [])
            if getattr(assoc, "persistent_object_id", None)
        ]

    @staticmethod
    def _association_mapping(update_result: Any) -> dict[str, str]:
        """frame_object_id -> persistent_object_id from associations."""
        mapping: dict[str, str] = {}
        if update_result is None:
            return mapping
        for assoc in getattr(update_result, "associations", []):
            source = str(getattr(assoc, "source_object_id", "") or "").strip()
            persistent = str(getattr(assoc, "persistent_object_id", "") or "").strip()
            if source and persistent:
                mapping[source] = persistent
        return mapping

    # ------------------------------------------------------------------ #
    # object relations                                                   #
    # ------------------------------------------------------------------ #
    def _sync_object_relations(
        self,
        *,
        relations: list[dict[str, Any]],
        update_result: Any,
        observation_id: str,
        timestamp: float,
    ) -> None:
        source_to_persistent = self._association_mapping(update_result)
        for raw in relations:
            if not isinstance(raw, dict):
                continue
            source_frame_id = str(raw.get("subject_id") or "").strip()
            target_frame_id = str(raw.get("object_id") or "").strip()
            relation_text = raw.get("relation") or ""
            source_id = source_to_persistent.get(source_frame_id)
            target_id = source_to_persistent.get(target_frame_id)

            if not source_id or not target_id:
                self._relation_debug(
                    observation_id=observation_id,
                    subject_id=source_frame_id,
                    object_id=target_frame_id,
                    relation=relation_text,
                    result="rejected",
                    reason=(
                        "source_endpoint_unresolved"
                        if not source_id
                        else "target_endpoint_unresolved"
                    ),
                )
                continue
            if source_id == target_id:
                self._relation_debug(
                    observation_id=observation_id,
                    subject_id=source_frame_id,
                    object_id=target_frame_id,
                    relation=relation_text,
                    result="rejected",
                    reason="self_relation_rejected",
                )
                continue

            relation = normalize_relation(relation_text)
            if relation is None:
                self._relation_debug(
                    observation_id=observation_id,
                    subject_id=source_frame_id,
                    object_id=target_frame_id,
                    relation=relation_text,
                    result="rejected",
                    reason="relation_not_allowed",
                )
                continue

            confidence = _clamp01(raw.get("confidence"), 0.5)
            if confidence < self.relation_min_confidence:
                self._relation_debug(
                    observation_id=observation_id,
                    subject_id=source_frame_id,
                    object_id=target_frame_id,
                    relation=relation,
                    result="rejected",
                    reason="confidence_below_min",
                    confidence=confidence,
                )
                continue

            if source_id > target_id and relation_is_symmetric(relation):
                source_id, target_id = target_id, source_id

            self._upsert_object_relation(
                source_object_id=source_id,
                target_object_id=target_id,
                relation=relation,
                confidence=confidence,
                observation_id=observation_id,
                timestamp=timestamp,
                description_zh=raw.get("description_zh"),
            )
            self._relation_debug(
                observation_id=observation_id,
                subject_id=source_frame_id,
                object_id=target_frame_id,
                relation=relation,
                result="accepted",
                persistent_source_id=source_id,
                persistent_target_id=target_id,
                confidence=confidence,
            )

    def _upsert_object_relation(
        self,
        *,
        source_object_id: str,
        target_object_id: str,
        relation: str,
        confidence: float,
        observation_id: str,
        timestamp: float,
        description_zh: Any,
    ) -> None:
        key = (source_object_id, relation, target_object_id)
        edge_id = f"{source_object_id}__{relation}__{target_object_id}"
        scope = relation_scope_of(relation)
        desc = str(description_zh or "").strip()
        existing = self.object_relations.get(key)
        if existing is None:
            self.object_relations[key] = PersistentObjectRelation(
                edge_id=edge_id,
                source_object_id=source_object_id,
                target_object_id=target_object_id,
                relation=relation,
                confidence=confidence,
                observation_count=1,
                first_seen=timestamp,
                last_seen=timestamp,
                status=(
                    RELATION_CONFIRMED
                    if self.relation_confirm_min_observations <= 1
                    else RELATION_TENTATIVE
                ),
                relation_scope=scope,
                directed=not relation_is_symmetric(relation),
                source_observation_ids=[observation_id] if observation_id else [],
                descriptions_zh=[desc] if desc else [],
                provenance={"source": "framed_object_relation", "last_observation_id": observation_id},
            )
            return
        # Evidence merge: same edge keeps the same id, only counters fuse.
        prior_count = existing.observation_count
        existing.confidence = (
            existing.confidence * prior_count + confidence
        ) / (prior_count + 1)
        existing.observation_count += 1
        existing.last_seen = timestamp
        existing.provenance["last_observation_id"] = observation_id
        if observation_id and observation_id not in existing.source_observation_ids:
            existing.source_observation_ids.append(observation_id)
        if desc and desc not in existing.descriptions_zh:
            existing.descriptions_zh.append(desc)
            existing.descriptions_zh = existing.descriptions_zh[-self.max_relation_descriptions:]
        existing.status = (
            RELATION_CONFIRMED
            if existing.observation_count >= self.relation_confirm_min_observations
            else RELATION_TENTATIVE
        )

    def _mark_stale_relations(self, *, now: float) -> None:
        for rel in self.object_relations.values():
            if rel.last_seen and (now - rel.last_seen) > self.relation_stale_after_seconds:
                rel.status = RELATION_STALE

    def _relation_debug(self, **fields: Any) -> None:
        entry: dict[str, Any] = {"type": "relation_association"}
        entry.update(fields)
        self.association_debug.append(entry)

    # ------------------------------------------------------------------ #
    # projections                                                        #
    # ------------------------------------------------------------------ #
    def object_topology_snapshot(self) -> dict[str, Any]:
        """Projection of persistent OBJECT nodes + OBJECT relations.

        This projection deliberately contains no PLACE / ROBOT / FRONTIER nodes
        and no metric coordinates: the WebUI places nodes purely with a display
        layout.
        """
        nodes: list[dict[str, Any]] = []
        for obj in self.object_map.objects.values():
            if obj.status == "STALE" and not self.include_stale_objects:
                continue
            if obj.status == "TENTATIVE" and not self.include_tentative_objects:
                continue
            provenance = obj.provenance or {}
            nodes.append(
                {
                    "node_id": obj.object_id,
                    "node_type": "OBJECT",
                    "label": obj.label,
                    "status": obj.status,
                    "confidence": round(obj.confidence, 4),
                    "observation_count": obj.observation_count,
                    "spatial_quality": obj.spatial_quality,
                    "is_target_candidate": bool(
                        provenance.get("target_candidate") or provenance.get("target_confirmed")
                    ),
                    "is_target_confirmed": bool(provenance.get("target_confirmed")),
                }
            )

        edges: list[dict[str, Any]] = []
        for rel in self.object_relations.values():
            if rel.status == "STALE" and not self.include_stale_objects:
                continue
            if rel.relation_scope == "VIEW_RELATIVE" and not self.include_view_relative_relations:
                continue
            edges.append(rel.to_dict())

        node_ids = {node["node_id"] for node in nodes}
        edges = [edge for edge in edges if edge["from"] in node_ids and edge["to"] in node_ids]
        confirmed_nodes = sum(1 for node in nodes if node["status"] == "CONFIRMED")
        confirmed_edges = sum(1 for edge in edges if edge["status"] == "CONFIRMED")
        return {
            "schema_version": OBJECT_TOPOLOGY_SCHEMA_VERSION,
            "revision": self.revision,
            "generated_at": time.time(),
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "confirmed_nodes": confirmed_nodes,
                "confirmed_edges": confirmed_edges,
                "connected_components": _connected_components(node_ids, edges),
            },
        }

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

        # OBSERVED_FROM edges: derived from the association-based Place attaches
        # (``place.observed_object_ids``), never from label matching.
        for place_id, place in self.place_graph.places.items():
            for object_id in place.observed_object_ids:
                entry = self.object_map.objects.get(object_id)
                if entry is None:
                    continue
                edge_id = f"{place_id}__observed_from__{object_id}"
                count = 0
                for sid in getattr(entry, "source_observation_ids", []):
                    ob = self.place_graph.observations.get(sid)
                    if ob is not None and (ob.provenance or {}).get("place_id") == place_id:
                        count += 1
                edges.append(
                    {
                        "edge_id": edge_id,
                        "from": place_id,
                        "to": object_id,
                        "relation": "OBSERVED_FROM",
                        "observation_count": max(1, count),
                        "last_seen": entry.last_seen,
                        "provenance": "visual_observed_association",
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
            "object_topology": self.object_topology_snapshot(),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()

    def _entry_touches(
        self,
        entry: Any,
        observation_ids: str | list[str],
        labels: list[str],
        now: float,
    ) -> bool:
        """Legacy label/observation matching, kept only for non-identity uses.

        Persistent identity and Place->object attachment must go through
        ``update_result.associations``, not this helper.
        """
        ids = (
            list(observation_ids)
            if isinstance(observation_ids, list)
            else [observation_ids]
        )
        if any(observation_id in entry.source_observation_ids for observation_id in ids):
            return True
        return entry.label in (labels or [])

    def summary_stats(self) -> dict[str, Any]:
        return {
            "unique_places": len(self.place_graph.places),
            "places_revisited": sum(
                1 for place in self.place_graph.places.values() if place.revisited
            ),
            **self.object_map.summary_stats(),
            "persistent_object_relations": len(self.object_relations),
            "graph_revision": self.revision,
        }


def _connected_components(node_ids: set[str], edges: list[dict[str, Any]]) -> int:
    parent = {node_id: node_id for node_id in node_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for edge in edges:
        a, b = edge.get("from"), edge.get("to")
        if a in parent and b in parent:
            union(a, b)
    return len({find(node_id) for node_id in node_ids})
