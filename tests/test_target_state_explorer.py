from __future__ import annotations

from app.live_robot.autonomous_explorer import AutonomousExplorer, SemanticMatch, VerificationOutcome
from app.navigation.backend_factory import MockBackend
from app.navigation.exploration_config import ExplorationBudget, load_exploration_policy
from app.navigation.exploration_graph import ExplorationGraph
from app.navigation.models import LiveObservation


def test_possible_target_is_reobserved_before_any_motion() -> None:
    policy = load_exploration_policy()
    policy.budget = ExplorationBudget(max_planning_cycles=1, max_motion_steps=1)
    observations = [
        LiveObservation(
            bundle_id=f"possible_{index}", timestamp=float(index),
            target_match={"target_state": "POSSIBLE", "target_present": False},
        )
        for index in range(3)
    ]
    cursor = [0]

    def observe() -> LiveObservation:
        index = min(cursor[0], len(observations) - 1)
        cursor[0] += 1
        return observations[index]

    backend = MockBackend()
    explorer = AutonomousExplorer(
        target="目标",
        observer=observe,
        matcher=lambda _observation: SemanticMatch(has_candidate=False),
        verifier=lambda _observation, _match: VerificationOutcome(
            confirmed=False, attempts=1, reason_zh="not confirmed"
        ),
        backend=backend,
        policy=policy,
        graph=ExplorationGraph(session_id="possible_test"),
    )
    result = explorer.run()
    events = [item["event"] for item in explorer.events]
    assert result.motion_steps >= 0
    first_action = events.index("action_start") if "action_start" in events else len(events)
    possible_events = [index for index, event in enumerate(events) if event == "target_possible_reobserve"]
    assert len(possible_events) == 2
    assert all(index < first_action for index in possible_events)
