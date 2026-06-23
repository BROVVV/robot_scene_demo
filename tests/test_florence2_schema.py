from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.detectors.vocabulary import label_zh


ROOT = Path(__file__).resolve().parents[1]


class Florence2SchemaTest(unittest.TestCase):
    def test_worker_mock_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            image_path = tmp / "scene.jpg"
            output_path = tmp / "florence2.json"
            Image.new("RGB", (80, 60), color=(120, 120, 120)).save(image_path)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "app" / "detectors" / "florence2_worker.py"),
                    "--image",
                    str(image_path),
                    "--output",
                    str(output_path),
                    "--allow-mock",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["backend"], "florence2")
            self.assertGreaterEqual(len(data["objects"]), 1)
            obj = data["objects"][0]
            self.assertEqual(obj["source"], "florence2")
            self.assertIn("bbox_xyxy", obj)
            self.assertIn("confidence", obj)

    def test_florence_labels_have_chinese_display_names(self) -> None:
        self.assertEqual(label_zh("cabinetry"), "柜子")
        self.assertEqual(label_zh("furniture"), "家具")
        self.assertEqual(label_zh("house"), "室内空间")
        self.assertEqual(label_zh("unmapped florence token"), "物体")
        for value in ["cabinetry", "furniture", "house", "unmapped florence token"]:
            self.assertFalse(_has_ascii_letter(label_zh(value)))


def _has_ascii_letter(value: str) -> bool:
    return any(("a" <= char.lower() <= "z") for char in value)


if __name__ == "__main__":
    unittest.main()
