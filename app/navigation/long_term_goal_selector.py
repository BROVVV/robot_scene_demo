"""LongTermGoalSelector: choose the next spatial exploration goal.

This is the module that separates *where to explore* (Frontier / Anchor Region /
Target Viewpoint) from *how to execute it* (LocalGoalExecutor).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.spatial.models import (
    INTENT_APPROACH_TARGET,
    INTENT_EXPLORE_FRONTIER,
    INTENT_INSPECT_ANCHOR_REGION,
    INTENT_VERIFY_TARGET,
    ExplorationIntent,
    FrontierCandidate,
    SemanticPrior,
)
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_object_map import SemanticObjectMap

MATCH_ZERO = "ZERO"
MATCH_PARTIAL = "PARTIAL"
MATCH_STRONG = "STRONG"
MATCH_VERIFY = "VERIFY"


@dataclass
class ScoredIntent:
    intent: ExplorationIntent
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "score": round(self.score, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "reasons": self.reasons,
        }


class LongTermGoalSelector:
    def __init__(
        self,
        *,
        psg_zero_weight: float = 1.0,
        psg_partial_weight: float = 0.7,
        psg_strong_weight: float = 0.2,
        psg_verify_weight: float = 0.0,
        travel_cost_weight: float = 0.25,
        visited_penalty: float = 0.2,
    ) -> None:
        self.psg_weights = {
            MATCH_ZERO: psg_zero_weight,
            MATCH_PARTIAL: psg_partial_weight,
            MATCH_STRONG: psg_strong_weight,
            MATCH_VERIFY: psg_verify_weight,
        }
        self.travel_cost_weight = float(travel_cost_weight)
        self.visited_penalty = float(visited_penalty)

    def select(
        self,
        *,
        match_state: str,
        frontiers: list[FrontierCandidate],
        place_graph: PlaceGraph | None = None,
        semantic_map: SemanticObjectMap | None = None,
        psg_prior: SemanticPrior | None = None,
        frontier_memory: dict[str, dict[str, Any]] | None = None,
    ) -> ScoredIntent | None:
        match_state = match_state.upper()
        if match_state in {MATCH_STRONG, MATCH_VERIFY}:
            return self._select_verify(semantic_map=semantic_map, match_state=match_state)
        if match_state == MATCH_PARTIAL and psg_prior is not None and psg_prior.region_hypotheses:
            return self._select_anchor_region(psg_prior)
        return self._select_frontier(
            frontiers=frontiers,
            match_state=match_state,
            place_graph=place_graph,
            psg_prior=psg_prior,
            frontier_memory=frontier_memory or {},
        )

    def _select_frontier(
        self,
        *,
        frontiers: list[FrontierCandidate],
        match_state: str,
        place_graph: PlaceGraph | None,
        psg_prior: SemanticPrior | None,
        frontier_memory: dict[str, dict[str, Any]],
    ) -> ScoredIntent | None:
        if not frontiers:
            return None
        psg_weight = self.psg_weights.get(match_state, 0.5)
        psg_scores = (psg_prior.frontier_scores if psg_prior else {}) or {}
        scored: list[ScoredIntent] = []
        for frontier in frontiers:
            spatial = max(0.0, min(1.0, frontier.spatial_information_gain))
            psg = max(0.0, min(1.0, float(psg_scores.get(frontier.frontier_id, 0.0)))) * psg_weight
            distance = frontier.distance_m if frontier.distance_m is not None else 0.5
            travel = min(1.0, distance / 5.0) * self.travel_cost_weight
            memory = frontier_memory.get(frontier.frontier_id, {})
            visited = min(1.0, int(memory.get("visit_count", 0)) / 3.0) * self.visited_penalty
            score = spatial + psg - travel - visited
            reasons = []
            if spatial > 0:
                reasons.append(f"spatial gain {spatial:.2f}")
            if psg > 0:
                reasons.append(f"PSG prior {psg:.2f}")
            if travel > 0:
                reasons.append(f"travel {travel:.2f}")
            intent = ExplorationIntent(
                intent_id=f"intent_{len(scored) + 1:03d}",
                intent_type=INTENT_EXPLORE_FRONTIER,
                target_frontier_id=frontier.frontier_id,
                preferred_position=frontier.position,
                preferred_bearing_deg=frontier.bearing_deg,
                semantic_reason="; ".join(reasons) or "frontier exploration",
                semantic_score=0.0,
                psg_score=psg,
                spatial_gain=spatial,
                travel_cost=travel,
                provenance={
                    "frontier": frontier.to_dict(),
                    "match_state": match_state,
                },
            )
            scored.append(ScoredIntent(intent, score, {
                "spatial_gain": spatial,
                "psg_prior": psg,
                "travel_cost": travel,
                "visited_penalty": visited,
            }, reasons))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[0]

    def _select_anchor_region(self, psg_prior: SemanticPrior) -> ScoredIntent:
        # Pick the highest-confidence PSG region that is still predicted.
        regions = [r for r in psg_prior.region_hypotheses if r.state != "REJECTED"]
        if not regions:
            return None
        region = max(regions, key=lambda r: r.confidence)
        intent = ExplorationIntent(
            intent_id="intent_anchor_region",
            intent_type=INTENT_INSPECT_ANCHOR_REGION,
            target_region=region.to_dict(),
            target_object_id=region.anchor_object_id,
            preferred_position=region.center,
            preferred_bearing_deg=(
                (region.bearing_range_deg[0] + region.bearing_range_deg[1]) / 2.0
                if region.bearing_range_deg else None
            ),
            semantic_reason=f"anchor region {region.region_id} confidence {region.confidence:.2f}",
            semantic_score=region.confidence,
            psg_score=region.confidence,
            spatial_gain=0.4,
            travel_cost=0.0,
            provenance={"source": "psg_semantic_region", "region_id": region.region_id},
        )
        return ScoredIntent(intent, score=0.8, components={"semantic": 0.8, "psg": 0.8},
                            reasons=["PARTIAL match", "anchor spatially located", "PSG region"])

    def _select_verify(self, *, semantic_map: SemanticObjectMap | None, match_state: str) -> ScoredIntent:
        intent_type = INTENT_VERIFY_TARGET if match_state == MATCH_VERIFY else INTENT_APPROACH_TARGET
        bearing = None
        object_id = None
        if semantic_map is not None:
            best = max(
                semantic_map.objects.values(),
                key=lambda item: item.confidence,
                default=None,
            )
            if best is not None:
                bearing = best.bearing_deg
                object_id = best.object_id
        intent = ExplorationIntent(
            intent_id="intent_verify_target",
            intent_type=intent_type,
            target_object_id=object_id,
            preferred_bearing_deg=bearing,
            semantic_reason="STRONG/VERIFY target candidate requires real visual verification",
            semantic_score=1.0,
            psg_score=0.0,
            spatial_gain=0.0,
            travel_cost=0.0,
            provenance={"source": "unigoal_v2", "match_state": match_state},
        )
        return ScoredIntent(intent, score=1.0, components={"semantic": 1.0},
                            reasons=[f"{match_state} match -> {intent_type}"])
