from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.geometry import GeometryConfig, build_scene_geometry


class GeometryFallbackTest(unittest.TestCase):
    def test_generates_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            image = tmp / "scene.jpg"
            Image.new("RGB", (96, 72), color=(80, 100, 120)).save(image)

            result = build_scene_geometry(str(image), str(tmp), GeometryConfig())

            self.assertTrue(result.available)
            self.assertEqual(result.backend, "heuristic_fallback")
            self.assertFalse(result.metric_reliable)
            for path in [
                result.point_map_path,
                result.depth_path,
                result.bev_occupancy_path,
                result.free_space_mask_path,
                result.esdf_path,
                result.esdf_visualization_path,
                result.bev_metadata_path,
                result.debug_overlay_path,
            ]:
                self.assertTrue(Path(path).is_file())
            metadata = json.loads(Path(result.bev_metadata_path).read_text(encoding="utf-8"))
            self.assertFalse(metadata["metric_reliable"])
            self.assertEqual(metadata["geometry_backend"], "heuristic_fallback")


if __name__ == "__main__":
    unittest.main()
