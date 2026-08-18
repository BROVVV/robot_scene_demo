"""Unit tests for the Go2-W experimental backend (injected hardware)."""

from __future__ import annotations

import unittest

from app.navigation.go2w_experimental_backend import (
    Go2WBackendConfig,
    Go2WExperimentalBackend,
)
from app.navigation.models import (
    GOAL_INSPECT_ANCHOR,
    GOAL_NAVIGATE_POSE,
    GOAL_RELATIVE_MOVE,
    GOAL_ROTATE_VIEW,
    GOAL_STOP,
    ExplorationGoal,
)
from app.navigation.robot_backend import NavigationStatus, PoseQuality


class _FakeMotion:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.odom = [0.0, 0.0, 0.0]
        self.fail_steps: set[str] = set()

    def execute(self, step: str) -> tuple[bool, str, dict]:
        self.steps.append(step)
        if step in self.fail_steps:
            return False, f"motion rejected: {step}", {"step": step}
        if step == "f":
            self.odom[0] += 0.18
        else:
            degrees = float(step[1:])
            if step.startswith("r"):
                degrees = -degrees
            self.odom[2] += degrees * 3.141592653589793 / 180.0
        return True, "ok", {"step": step}

    def odometry(self) -> tuple[float, float, float]:
        return tuple(self.odom)


def _backend(motion: _FakeMotion, **config) -> Go2WExperimentalBackend:
    return Go2WExperimentalBackend(
        execute_step=motion.execute,
        odometry=motion.odometry,
        config=Go2WBackendConfig(**config),
    )


class TestGo2WExperimentalBackend(unittest.TestCase):
    def test_capabilities_relative(self) -> None:
        backend = _backend(_FakeMotion())
        caps = backend.capabilities()
        self.assertFalse(caps.supports_global_pose)
        self.assertFalse(caps.supports_metric_navigation)
        self.assertTrue(caps.supports_relative_rotation)
        self.assertTrue(caps.supports_relative_translation)

    def test_pose_quality_relative(self) -> None:
        backend = _backend(_FakeMotion())
        pose = backend.get_pose()
        self.assertEqual(pose.quality, PoseQuality.RELATIVE)
        self.assertEqual(pose.frame_id, "odom")

    def test_rotate_view_clamps_to_max_turn(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion, max_turn_deg_per_action=30.0)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_ROTATE_VIEW,
                               relative_dyaw=90.0)
        result = backend.execute_goal(goal).result
        self.assertTrue(result.succeeded)
        self.assertEqual(motion.steps, ["l30"])
        self.assertIn("yaw_delta_deg", result.observed_motion)

    def test_rotate_right_uses_r_step(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_ROTATE_VIEW,
                               relative_dyaw=-20.0)
        backend.execute_goal(goal)
        self.assertEqual(motion.steps, ["r20"])

    def test_forward_clamps_to_max(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion, max_forward_step_m=0.30)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_RELATIVE_MOVE,
                               relative_dx=5.0)
        result = backend.execute_goal(goal).result
        self.assertTrue(result.succeeded)
        self.assertEqual(motion.steps, ["f"])
        self.assertLessEqual(result.requested_motion["distance_m"], 0.30)

    def test_lateral_rejected_by_default(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_RELATIVE_MOVE,
                               relative_dx=0.0, relative_dy=0.2)
        result = backend.execute_goal(goal).result
        self.assertEqual(result.status, NavigationStatus.REJECTED)
        self.assertEqual(motion.steps, [])

    def test_metric_goal_rejected(self) -> None:
        backend = _backend(_FakeMotion())
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_NAVIGATE_POSE,
                               position=(1.0, 1.0))
        result = backend.execute_goal(goal).result
        self.assertEqual(result.status, NavigationStatus.REJECTED)

    def test_motion_failure_maps_to_failed(self) -> None:
        motion = _FakeMotion()
        motion.fail_steps.add("l10")
        backend = _backend(motion)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_ROTATE_VIEW,
                               relative_dyaw=10.0)
        result = backend.execute_goal(goal).result
        self.assertEqual(result.status, NavigationStatus.FAILED)

    def test_stop_calls_injected_stop(self) -> None:
        stopped = []
        motion = _FakeMotion()
        backend = Go2WExperimentalBackend(
            execute_step=motion.execute, odometry=motion.odometry,
            stop=lambda: stopped.append(True),
        )
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_STOP)
        result = backend.execute_goal(goal).result
        self.assertTrue(result.succeeded)
        self.assertEqual(stopped, [True])

    def test_health_reports_metric_pose_degraded(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion)
        health = backend.health()
        self.assertTrue(health.ready)
        self.assertIn("metric_pose_unavailable", health.degraded)

    def test_health_fails_closed_on_motion_unavailable(self) -> None:
        motion = _FakeMotion()
        backend = Go2WExperimentalBackend(
            execute_step=motion.execute, odometry=motion.odometry,
            health_probe=lambda: {"motion_action_available": False},
        )
        health = backend.health()
        self.assertFalse(health.ready)
        self.assertIn("motion_action_unavailable", health.degraded)

    def test_opportunistic_correction_learning(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion, correction_min_samples=2,
                           correction_min_confidence=0.6)
        for _ in range(4):
            goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_ROTATE_VIEW,
                                   relative_dyaw=30.0)
            backend.execute_goal(goal)
        correction = backend.correction()
        self.assertEqual(correction.samples, 4)
        # Fake executes exactly 30 deg for a 30 deg request -> scale ~1.0
        self.assertAlmostEqual(correction.rotation_scale, 1.0, places=3)

    def test_inspect_anchor_uses_heading(self) -> None:
        motion = _FakeMotion()
        backend = _backend(motion)
        goal = ExplorationGoal(goal_id="g1", goal_type=GOAL_INSPECT_ANCHOR,
                               relative_dyaw=25.0, semantic_anchor="water dispenser")
        result = backend.execute_goal(goal).result
        self.assertTrue(result.succeeded)
        self.assertEqual(motion.steps, ["l25"])


if __name__ == "__main__":
    unittest.main()
