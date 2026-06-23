from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.geometry.types import GeometryResult
from app.navigation.object_goal_projection import ProjectionConfig, project_objects_to_bev
from app.schemas import BoundingBox2D, Position, SceneObject


class ObjectGoalProjectionTest(unittest.TestCase):
    def test_projects_bbox_to_goal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            point_map = np.zeros((20, 20, 3), dtype=np.float32)
            point_map[..., 0] = 2.0
            point_map[..., 1] = 0.25
            point_map[..., 2] = 0.1
            point_path = tmp / "point_map.npy"
            esdf_path = tmp / "esdf.npy"
            metadata_path = tmp / "bev_metadata.json"
            np.save(point_path, point_map)
            np.save(esdf_path, np.ones((10, 10), dtype=np.float32))
            metadata_path.write_text(
                json.dumps(
                    {
                        "resolution": 0.5,
                        "x_min": 0.0,
                        "x_max": 5.0,
                        "y_min": -2.5,
                        "y_max": 2.5,
                        "width": 10,
                        "height": 10,
                    }
                ),
                encoding="utf-8",
            )
            geometry = GeometryResult(
                point_map_path=str(point_path),
                esdf_path=str(esdf_path),
                bev_metadata_path=str(metadata_path),
                available=True,
                backend="heuristic_fallback",
            )

            [projected] = project_objects_to_bev(
                [_object()],
                geometry,
                str(tmp),
                ProjectionConfig(),
            )

            self.assertAlmostEqual(projected.bev_x, 2.0, places=2)
            self.assertAlmostEqual(projected.bev_y, 0.25, places=2)
            self.assertTrue(projected.reachable)
            self.assertIsNotNone(projected.goal_bev_x)


def _object() -> SceneObject:
    return SceneObject(
        id="obj_001",
        name="phone",
        name_zh="手机",
        category="electronics",
        attributes=[],
        visible=True,
        position=Position(horizontal="center", vertical="front", relative_to_robot="front"),
        bbox_2d=BoundingBox2D(x1=0.25, y1=0.25, x2=0.75, y2=0.75),
        confidence=0.8,
    )


if __name__ == "__main__":
    unittest.main()
