from __future__ import annotations

import unittest

from app.navigation.local_planner import LocalPlanResult
from app.navigation.path_to_cmd_vel import motion_plan_from_path


class CmdVelFromPathTest(unittest.TestCase):
    def test_converts_path_to_ros2_motion_plan(self) -> None:
        local_plan = LocalPlanResult(
            available=True,
            status="success",
            path_xy=[(0.0, 0.0), (0.5, 0.0), (1.0, 0.2)],
            goal_xy=(1.0, 0.2),
            used_goal_xy=(1.0, 0.2),
            progress_score=1.0,
            collision_free=True,
            min_clearance_m=0.4,
            planner_backend="astar",
        )

        plan = motion_plan_from_path(
            local_plan,
            linear_speed_mps=0.25,
            angular_speed_radps=0.5,
            command_rate_hz=10.0,
            waypoint_tolerance_m=0.01,
            generated_at="2026-06-22T00:00:00+00:00",
        )

        self.assertIsNotNone(plan)
        self.assertTrue(plan.dry_run)
        self.assertEqual(plan.topic, "/cmd_vel")
        self.assertEqual(plan.commands[-1].source_action, "stop")


if __name__ == "__main__":
    unittest.main()
