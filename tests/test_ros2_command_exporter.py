from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.schemas import (
    RoutePlan,
    SceneAnalysisResult,
)
from app.services.ros2_command_exporter import (
    build_ros2_motion_plan,
    export_ros2_motion_plan,
)


ROOT = Path(__file__).resolve().parents[1]


class Ros2CommandExporterTest(unittest.TestCase):
    def test_builds_cmd_vel_commands_from_route_steps(self) -> None:
        result = _mock_scene()

        plan = build_ros2_motion_plan(
            result,
            generated_at="2026-06-17T00:00:00+00:00",
        )

        self.assertTrue(plan.dry_run)
        self.assertEqual(plan.topic, "/cmd_vel")
        self.assertEqual(plan.message_type, "geometry_msgs/msg/Twist")
        self.assertEqual(plan.frame_id, "base_link")
        self.assertEqual(len(plan.commands), 4)

        move = plan.commands[0]
        self.assertEqual(move.source_action, "move_forward")
        self.assertEqual(move.route_step_id, 1)
        self.assertEqual(move.twist.linear.x, 0.25)
        self.assertEqual(move.twist.angular.z, 0.0)
        self.assertEqual(move.duration_sec, 4.0)

        turn = plan.commands[1]
        self.assertEqual(turn.source_action, "turn_right")
        self.assertEqual(turn.twist.linear.x, 0.0)
        self.assertEqual(turn.twist.angular.z, -0.5)
        self.assertAlmostEqual(turn.duration_sec, 0.698, places=3)

        stop = plan.commands[-1]
        self.assertEqual(stop.source_action, "stop")
        self.assertEqual(stop.twist.linear.x, 0.0)
        self.assertEqual(stop.twist.angular.z, 0.0)
        self.assertEqual(stop.duration_sec, 1.0)

    def test_empty_route_exports_safe_stop_command(self) -> None:
        result = _mock_scene().model_copy(
            update={
                "route_plan": RoutePlan(
                    route_type="explore_likely_location",
                    summary_zh="无安全路线。",
                    steps=[],
                    safety_notes_zh=[],
                )
            }
        )

        plan = build_ros2_motion_plan(
            result,
            generated_at="2026-06-17T00:00:00+00:00",
        )

        self.assertEqual(len(plan.commands), 1)
        self.assertEqual(plan.commands[0].source_action, "stop")
        self.assertIsNone(plan.commands[0].route_step_id)
        self.assertEqual(plan.commands[0].twist.linear.x, 0.0)
        self.assertIn("dry_run", " ".join(plan.safety_notes_zh))

    def test_export_writes_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "ros2_motion_plan.json"

            returned_path = export_ros2_motion_plan(_mock_scene(), output_path)

            self.assertEqual(returned_path, output_path)
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["topic"], "/cmd_vel")
            self.assertEqual(data["commands"][0]["message_type"], "geometry_msgs/msg/Twist")


def _mock_scene() -> SceneAnalysisResult:
    data = json.loads((ROOT / "examples" / "mock_scene_result.json").read_text(encoding="utf-8"))
    return SceneAnalysisResult.model_validate(data)


if __name__ == "__main__":
    unittest.main()
