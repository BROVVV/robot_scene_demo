"""Florence-2 detector adapter for the scene analyzer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.detectors.base import BaseObjectDetector, DetectedObject
from app.detectors.grounded_sam_subprocess import DetectorRuntimeError
from app.detectors.vocabulary import category_for_label, color_for_label, label_zh


class Florence2Detector(BaseObjectDetector):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def detect(self, image_path: str, target_text: str) -> list[DetectedObject]:
        image = Path(image_path).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "florence2_result.json"
            python_bin = self.settings.florence2_python or sys.executable
            command = [
                python_bin,
                str(Path(__file__).with_name("florence2_worker.py")),
                "--image",
                str(image),
                "--output",
                str(output_path),
                "--model-id",
                self.settings.florence2_model_id,
                "--device",
                self.settings.florence2_device,
                "--max-objects",
                str(self.settings.florence2_max_objects),
                "--confidence-threshold",
                str(self.settings.florence2_confidence_threshold),
                "--task-prompt",
                self.settings.florence2_task_prompt,
            ]
            if self.settings.florence2_allow_mock:
                command.append("--allow-mock")

            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.detector_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise DetectorRuntimeError(
                    "Florence-2 detector failed.\n"
                    f"Command: {' '.join(command)}\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.is_file():
                raise DetectorRuntimeError(
                    "Florence-2 detector finished but did not create output JSON."
                )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        with Image.open(image) as source:
            width, height = source.size
        return [
            _to_detected_object(item, width, height, raw)
            for item in payload.get("objects", [])
        ]


def _to_detected_object(
    item: dict,
    width: int,
    height: int,
    raw: dict,
) -> DetectedObject:
    label = str(item.get("label") or "object").lower().strip()
    bbox = item.get("bbox_xyxy") or [0.0, 0.0, width, height]
    x1, y1, x2, y2 = _clamp_box(bbox, width, height)
    raw_attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    attributes = ["detected_by_florence2"]
    if raw_attributes.get("mock"):
        attributes.append("mock_florence2_result")
    if raw.get("device"):
        attributes.append(f"florence2_device={raw['device']}")
    if raw.get("device_name"):
        attributes.append(f"florence2_device_name={raw['device_name']}")
    if raw.get("torch_dtype"):
        attributes.append(f"florence2_torch_dtype={raw['torch_dtype']}")
    raw_attributes = {
        **raw_attributes,
        "florence2_device": raw.get("device"),
        "florence2_device_name": raw.get("device_name"),
        "florence2_torch_dtype": raw.get("torch_dtype"),
    }
    mask_area_ratio = item.get("mask_area_ratio")

    return DetectedObject(
        label=label,
        label_zh=label_zh(label),
        category=category_for_label(label),
        color=color_for_label(label),
        bbox_2d=(
            _safe_norm(x1, width),
            _safe_norm(y1, height),
            _safe_norm(x2, width),
            _safe_norm(y2, height),
        ),
        score=_clamp(float(item.get("confidence", 0.5))),
        attributes=attributes,
        mask_area_ratio=None if mask_area_ratio is None else _clamp(float(mask_area_ratio)),
        source=str(item.get("source") or "florence2"),
        caption=item.get("caption"),
        raw_attributes=raw_attributes,
    )


def _clamp_box(box: list, width: int, height: int) -> tuple[float, float, float, float]:
    values = [float(value) for value in list(box)[:4]]
    x1 = max(0.0, min(float(width), values[0]))
    y1 = max(0.0, min(float(height), values[1]))
    x2 = max(0.0, min(float(width), values[2]))
    y2 = max(0.0, min(float(height), values[3]))
    return x1, y1, x2, y2


def _safe_norm(value: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(value / denominator)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
