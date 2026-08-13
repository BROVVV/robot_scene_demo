import tempfile
import unittest
from pathlib import Path

from app.live_robot.frame_bundle_reader import FrameBundle
from app.live_robot.live_search_pipeline import (
    run_live_bundle_search,
    sensor_snapshot_from_health,
)


class LiveSearchPipelineTests(unittest.TestCase):
    def test_camera_intrinsics_never_imply_rgb_lidar_extrinsics(self):
        snapshot = sensor_snapshot_from_health(
            {
                "camera": True,
                "camera_info_calibrated": True,
                "rgb_lidar_extrinsics": False,
                "lidar": True,
                "lio": False,
                "tf": True,
            }
        )
        self.assertTrue(snapshot.camera_fresh)
        self.assertFalse(snapshot.extrinsics_ready)

    def test_unhealthy_lidar_writes_complete_blocked_session_without_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "image.jpg"
            image.write_bytes(b"not-decoded-because-gate-closes-first")
            bundle = FrameBundle(
                directory=root,
                image_path=image,
                payload={
                    "session_id": "test_session",
                    "frame_id": 1,
                    "image_receive_time_ns": 1,
                    "camera_info": {"width": 1920, "height": 1080},
                    "sensor_health": {
                        "camera": True,
                        "camera_info_calibrated": False,
                        "rgb_lidar_extrinsics": False,
                        "rgb_lidar_fusion": False,
                        "lidar": False,
                        "lio": False,
                        "tf": False,
                    },
                },
            )
            output = root / "session"
            result = run_live_bundle_search(
                [bundle], target="手机", detector="grounded_sam", output_dir=output
            )
            self.assertEqual(result["status"], "blocked_wait_for_sensors")
            required = {
                "target_profile.json",
                "target_search.json",
                "target_timeline.json",
                "target_candidates.json",
                "object_tracks.json",
                "track_summary.json",
                "crop_verify_results.json",
                "evidence_gating_report.json",
                "frame_observations.json",
                "scene_graph.json",
                "scene_graph.graphml",
                "navigation_topology.json",
                "navigation_topology.graphml",
                "search_trace.json",
                "sensor_health.json",
                "safety_events.jsonl",
                "report.md",
                "final_report.md",
                "task.json",
                "parsed_task.json",
                "capability_gate_result.json",
                "grounding_prompt_plan.json",
                "memory_provenance.json",
                "motion_commands.jsonl",
                "nav2_requests.jsonl",
                "sensor_health.jsonl",
            }
            self.assertTrue(required.issubset({item.name for item in output.iterdir()}))
            self.assertEqual((output / "motion_commands.jsonl").read_text(), "")
            self.assertEqual((output / "nav2_requests.jsonl").read_text(), "")


if __name__ == "__main__":
    unittest.main()
