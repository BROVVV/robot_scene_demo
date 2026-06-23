from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.config import get_settings
from run_demo import run


class RunDemoFlorence2SmokeTest(unittest.TestCase):
    def test_run_demo_florence2_with_mock_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            image = tmp / "scene.jpg"
            output = tmp / "outputs"
            Image.new("RGB", (96, 72), color=(100, 120, 130)).save(image)
            old_env = {
                key: os.environ.get(key)
                for key in ["FLORENCE2_ALLOW_MOCK", "OUTPUT_DIR", "ENABLE_GEOMETRY"]
            }
            os.environ["FLORENCE2_ALLOW_MOCK"] = "true"
            os.environ["OUTPUT_DIR"] = str(output)
            os.environ["ENABLE_GEOMETRY"] = "true"
            get_settings.cache_clear()
            try:
                paths = run(
                    str(image),
                    "找到手机",
                    detector_backend="florence2",
                    enable_knowledge=False,
                )
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                get_settings.cache_clear()

            self.assertTrue(paths)
            scene = json.loads((output / "scene_result.json").read_text(encoding="utf-8"))
            self.assertEqual(scene["objects"][0]["source"], "florence2")
            self.assertIsNotNone(scene["geometry"])
            self.assertTrue((output / "ros2_motion_plan.json").is_file())


if __name__ == "__main__":
    unittest.main()
