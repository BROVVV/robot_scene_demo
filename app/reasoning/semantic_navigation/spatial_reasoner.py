"""SemanticNavigation V2 spatial reasoner: choose a long-term spatial goal from semantic
match state + frontiers + PSG + PlaceGraph.

This is the V2 replacement for the V1 next-view policy.  It still returns an
``ExplorationIntent`` (or ``None``), never a raw motion primitive.
"""

from __future__ import annotations

from typing import Any

from app.navigation.long_term_goal_selector import (
    MATCH_PARTIAL,
    MATCH_STRONG,
    MATCH_VERIFY,
    MATCH_ZERO,
    LongTermGoalSelector,
    ScoredIntent,
)
from app.reasoning.semantic_navigation.models import GraphMatchResult, GraphMatchState
from app.spatial.models import ExplorationIntent, FrontierCandidate, SemanticPrior
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_object_map import SemanticObjectMap


class SpatialSearchReasoner:
    def __init__(self, selector: LongTermGoalSelector | None = None) -> None:
        self.selector = selector or LongTermGoalSelector()

    def propose(
        self,
        *,
        graph_match: GraphMatchResult | None,
        frontiers: list[FrontierCandidate],
        place_graph: PlaceGraph | None = None,
        semantic_map: SemanticObjectMap | None = None,
        psg_prior: SemanticPrior | None = None,
        frontier_memory: dict[str, dict[str, Any]] | None = None,
    ) -> ScoredIntent | None:
        match_state = self._match_state(graph_match)
        return self.selector.select(
            match_state=match_state,
            frontiers=frontiers,
            place_graph=place_graph,
            semantic_map=semantic_map,
            psg_prior=psg_prior,
            frontier_memory=frontier_memory or {},
        )

    @staticmethod
    def _match_state(graph_match: GraphMatchResult | None) -> str:
        if graph_match is None:
            return MATCH_ZERO
        if graph_match.state == GraphMatchState.STRONG:
            return MATCH_STRONG
        if graph_match.state == GraphMatchState.PARTIAL:
            return MATCH_PARTIAL
        return MATCH_ZERO
