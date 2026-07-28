"""Frontier-style exploration planning on a video navigation topology."""

from __future__ import annotations

from typing import Any

from .models import NavigationWaypoint, Pose2D


def generate_exploration_candidates(
    navigation_map: dict[str, Any],
    target_search_result: dict[str, Any] | None = None,
    max_candidates: int = 8,
) -> list[NavigationWaypoint]:
    nodes = list(navigation_map.get("nodes", []))
    candidates: list[NavigationWaypoint] = []
    for index, node in enumerate(nodes[1:] or nodes):
        pose = Pose2D.from_dict(node.get("pose") or {})
        information_gain = _information_gain(index, len(nodes))
        target_relevance = _target_relevance(node, target_search_result or {})
        path_cost = index / max(len(nodes), 1)
        score = information_gain + target_relevance + 0.2 - path_cost * 0.25
        if score <= 0:
            continue
        candidates.append(
            NavigationWaypoint(
                waypoint_id=f"frontier_{index + 1:02d}",
                pose=pose,
                source_frame_id=node.get("frame_id"),
                semantic_label=f"探索点 {index + 1}",
                waypoint_type="frontier",
                confidence=round(min(score, 1.0), 4),
                provenance={
                    "source": "video_frontier_exploration",
                    "information_gain": round(information_gain, 4),
                    "target_relevance": round(target_relevance, 4),
                    "path_cost": round(path_cost, 4),
                },
            )
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[:max_candidates]


def _information_gain(index: int, total: int) -> float:
    if total <= 1:
        return 0.5
    return 0.35 + 0.5 * (index + 1) / total


def _target_relevance(node: dict[str, Any], result: dict[str, Any]) -> float:
    text = " ".join(str(item) for item in node.get("objects", []))
    profile = result.get("target_profile") or {}
    terms = profile.get("context_terms") or profile.get("detector_terms") or []
    if any(str(term).lower() in text.lower() for term in terms):
        return 0.25
    return 0.0
