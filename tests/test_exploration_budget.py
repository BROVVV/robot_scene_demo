"""Unit tests for the exploration search budget (plan section 14)."""

from __future__ import annotations

import unittest

from app.live_robot.autonomous_explorer import AutonomousExplorer
from app.live_robot.mock_observation_scene import (
    MockObservationScene,
    MockSceneStep,
    scenario_no_target,
)
from app.navigation.backend_factory import MockBackend
from app.navigation.exploration_config import (
    ExplorationBudget,
    ExplorationPolicy,
    load_exploration_policy,
)
from app.navigation.exploration_graph import ExplorationGraph


class TestExplorationBudget(unittest.TestCase):
    def test_budget_remaining(self) -> None:
        budget = ExplorationBudget(max_search_seconds=10.0,
                                   max_planning_cycles=3,
                                   max_motion_steps=2)
        self.assertTrue(budget.remaining(elapsed_sec=1.0, planning_cycles=0,
                                          motion_steps=0))
        self.assertFalse(budget.remaining(elapsed_sec=10.0, planning_cycles=0,
                                          motion_steps=0))
        self.assertFalse(budget.remaining(elapsed_sec=1.0, planning_cycles=3,
                                          motion_steps=0))
        self.assertFalse(budget.remaining(elapsed_sec=1.0, planning_cycles=0,
                                          motion_steps=2))

    def test_motion_step_limit_terminates(self) -> None:
        scene = scenario_no_target(empty_scenes=2)
        policy = load_exploration_policy()
        policy.budget = ExplorationBudget(max_motion_steps=3,
                                          max_planning_cycles=100)
        explorer = AutonomousExplorer(
            target="t", observer=scene.observer(), matcher=scene.matcher(),
            verifier=scene.verifier(), backend=MockBackend(),
            graph=ExplorationGraph(session_id="t"), policy=policy,
        )
        result = explorer.run()
        self.assertEqual(result.result, "MAX_STEPS_REACHED")
        self.assertEqual(result.motion_steps, 3)

    def test_planning_cycle_limit_terminates(self) -> None:
        scene = MockObservationScene(scenes=[
            MockSceneStep(objects=["desk"]),
        ])
        policy = load_exploration_policy()
        policy.budget = ExplorationBudget(max_motion_steps=0,
                                          max_planning_cycles=4)
        explorer = AutonomousExplorer(
            target="t", observer=scene.observer(), matcher=scene.matcher(),
            verifier=scene.verifier(), backend=MockBackend(),
            graph=ExplorationGraph(session_id="t"), policy=policy,
        )
        result = explorer.run()
        self.assertEqual(result.result, "MAX_PLANNING_CYCLES_REACHED")
        self.assertEqual(result.planning_cycles, 4)

    def test_time_limit_terminates(self) -> None:
        scene = scenario_no_target(empty_scenes=2)
        policy = ExplorationPolicy(
            budget=ExplorationBudget(max_search_seconds=0.0,
                                     max_planning_cycles=1000,
                                     max_motion_steps=1000),
        )
        explorer = AutonomousExplorer(
            target="t", observer=scene.observer(), matcher=scene.matcher(),
            verifier=scene.verifier(), backend=MockBackend(),
            graph=ExplorationGraph(session_id="t"), policy=policy,
        )
        result = explorer.run()
        self.assertEqual(result.result, "TIMEOUT")

    def test_no_information_cycles_exhaust(self) -> None:
        scene = scenario_no_target(empty_scenes=1)
        policy = load_exploration_policy()
        policy.budget = ExplorationBudget(
            max_motion_steps=0, max_planning_cycles=1000,
            max_consecutive_no_information_cycles=3,
        )
        explorer = AutonomousExplorer(
            target="t", observer=scene.observer(), matcher=scene.matcher(),
            verifier=scene.verifier(), backend=MockBackend(),
            graph=ExplorationGraph(session_id="t"), policy=policy,
        )
        result = explorer.run()
        self.assertEqual(result.result, "SEARCH_EXHAUSTED")

    def test_default_policy_from_yaml(self) -> None:
        policy = load_exploration_policy()
        self.assertEqual(policy.budget.max_search_seconds, 600.0)
        self.assertEqual(policy.scoring.semantic_relevance, 0.35)
        self.assertEqual(policy.candidates.heading_sectors, 12)


if __name__ == "__main__":
    unittest.main()
