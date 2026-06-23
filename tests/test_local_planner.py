from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.geometry.types import GeometryResult
from app.navigation.local_planner import PlannerConfig, plan_to_goal


class LocalPlannerTest(unittest.TestCase):
    def test_astar_reaches_goal_around_obstacle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            esdf = np.ones((20, 20), dtype=np.float32)
            esdf[4:16, 10] = -0.5
            esdf[10, 10] = 1.0
            esdf_path = tmp / "esdf.npy"
            metadata_path = tmp / "bev_metadata.json"
            np.save(esdf_path, esdf)
            metadata_path.write_text(
                json.dumps(
                    {
                        "resolution": 0.25,
                        "x_min": 0.0,
                        "x_max": 5.0,
                        "y_min": -2.5,
                        "y_max": 2.5,
                        "width": 20,
                        "height": 20,
                    }
                ),
                encoding="utf-8",
            )
            geometry = GeometryResult(
                esdf_path=str(esdf_path),
                bev_metadata_path=str(metadata_path),
                available=True,
                backend="test",
            )

            plan = plan_to_goal(
                geometry,
                (3.5, 0.0),
                PlannerConfig(min_clearance_m=0.1, max_steps=5000),
            )

            self.assertTrue(plan.available)
            self.assertEqual(plan.status, "success")
            self.assertGreater(len(plan.path_xy), 2)
            self.assertTrue(plan.collision_free)


if __name__ == "__main__":
    unittest.main()
