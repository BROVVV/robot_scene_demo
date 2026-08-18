"""Scripted observation scenes for offline E2E (mock backend + mock vision).

The AutonomousExplorer's observer/verifier are injected callables; this module
provides deterministic fakes so the full loop can run without a robot or LLM:
target appears after N nodes, target never appears, anchor appears then
target, operator stop, etc.  Also used by ``scripts/go2w/run_semantic_exploration.py
--backend mock`` for offline dry-runs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.navigation.models import LiveObservation
from app.live_robot.autonomous_explorer import SemanticMatch, VerificationOutcome


@dataclass
class MockSceneStep:
    objects: list[str]
    relations: list[dict[str, Any]] | None = None
    target_present: bool = False
    bundle_id: str | None = None
    anchor_labels: list[str] = field(default_factory=list)
    target_score: float = 0.0


class MockObservationScene:
    """Deterministic observer + verifier pair for offline E2E scenarios."""

    def __init__(
        self,
        *,
        scenes: list[MockSceneStep],
        confirm_after_seen: int = 1,
        yaw_deg: float = 0.0,
        scene_graph: bool = True,
    ) -> None:
        self.scenes = list(scenes)
        self.index = 0
        self.yaw_deg = float(yaw_deg)
        self.confirm_after_seen = max(1, int(confirm_after_seen))
        self.scene_graph = bool(scene_graph)
        self.target_seen_count = 0
        self.observations_made = 0
        self.last_observation: LiveObservation | None = None

    # ---- observer ---------------------------------------------------------

    def observer(self) -> Callable[[], LiveObservation]:
        def observe() -> LiveObservation:
            step = self.scenes[min(self.index, len(self.scenes) - 1)]
            self.index += 1
            self.observations_made += 1
            bundle_id = step.bundle_id or f"mock_{self.observations_made:03d}"
            objects = [
                {
                    "label": label,
                    "label_zh": label,
                    "name": label,
                    "position_2d": "center",
                    "confidence": 0.9,
                    "bbox_2d": [0.4, 0.3, 0.6, 0.7],
                }
                for label in step.objects
            ]
            observation = LiveObservation(
                bundle_id=bundle_id,
                timestamp=time.time(),
                image_ref=f"mock://{bundle_id}",
                detections=[
                    {
                        "label": label,
                        "score": 0.9,
                        "bbox_2d": [0.4, 0.3, 0.6, 0.7],
                    }
                    for label in step.objects
                ],
                scene_graph=(
                    {
                        "nodes": [
                            {"node_id": f"mock_{label}", "label": label,
                             "label_zh": label, "attributes": {}}
                            for label in step.objects
                        ],
                        "edges": [],
                    }
                    if self.scene_graph else None
                ),
                scene_objects=objects,
                scene_relations=list(step.relations or []),
                target_match={
                    "target_present": bool(step.target_present),
                    "score": step.target_score,
                },
                pose={
                    "x": 0.0, "y": 0.0,
                    "yaw_deg": self.yaw_deg,
                },
                sensor_health={"camera": True, "lidar": True},
                provenance={"source": "mock_scene", "step": self.index - 1},
            )
            self.last_observation = observation
            return observation

        return observe

    # ---- matcher ----------------------------------------------------------

    def matcher(self) -> Callable[[LiveObservation], SemanticMatch]:
        def match(observation: LiveObservation) -> SemanticMatch:
            step = self.scenes[min(max(0, self.index - 1), len(self.scenes) - 1)]
            return SemanticMatch(
                has_candidate=bool(observation.target_present),
                target_match=observation.target_match,
                target_profile=None,
                anchor_labels=list(step.anchor_labels),
                target_score=float((observation.target_match or {}).get("score", 0.0)),
                target_match_level=(
                    "candidate" if observation.target_present else "none"
                ),
                provenance={"source": "mock_matcher"},
            )

        return match

    # ---- verifier ---------------------------------------------------------

    def verifier(self) -> Callable[[LiveObservation, SemanticMatch], VerificationOutcome]:
        def verify(observation: LiveObservation,
                   match: SemanticMatch) -> VerificationOutcome:
            if observation.target_present:
                self.target_seen_count += 1
            confirmed = (
                observation.target_present
                and self.target_seen_count >= self.confirm_after_seen
            )
            return VerificationOutcome(
                confirmed=confirmed,
                attempts=1,
                reason_zh=(
                    "mock verify confirmed target"
                    if confirmed else "mock verify: target not confirmed"
                ),
                details={"seen_count": self.target_seen_count},
            )

        return verify


def scenario_target_appears_after(n: int, *, target: str = "blue trash bin",
                                  anchor: str = "water dispenser") -> MockObservationScene:
    """Target appears after ``n`` observations (anchor on observation 1)."""
    scenes: list[MockSceneStep] = []
    for index in range(n):
        if index == 0:
            scenes.append(
                MockSceneStep(
                    objects=[anchor], anchor_labels=[anchor],
                    bundle_id=f"obs_{index + 1:03d}",
                )
            )
        else:
            scenes.append(
                MockSceneStep(
                    objects=["desk", "chair"], bundle_id=f"obs_{index + 1:03d}",
                )
            )
    scenes.append(
        MockSceneStep(
            objects=[anchor, target],
            relations=[
                {"subject_label": target, "object_label": anchor,
                 "relation": "near", "confidence": 0.9},
            ],
            target_present=True,
            anchor_labels=[anchor],
            target_score=0.95,
            bundle_id="obs_final",
        )
    )
    return MockObservationScene(scenes=scenes, confirm_after_seen=1)


def scenario_no_target(*, empty_scenes: int = 6) -> MockObservationScene:
    scenes = [
        MockSceneStep(objects=["desk"], bundle_id=f"obs_{index + 1:03d}")
        for index in range(empty_scenes)
    ]
    return MockObservationScene(scenes=scenes)


def scenario_anchor_then_target() -> MockObservationScene:
    return scenario_target_appears_after(3)
