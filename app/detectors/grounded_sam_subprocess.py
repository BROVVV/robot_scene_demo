"""Grounding DINO + SAM2 detector via an external Python environment."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.config import Settings
from app.detectors.base import BaseObjectDetector, DetectedObject
from app.detectors.vocabulary import (
    build_detection_prompts,
    category_for_label,
    color_for_label,
    label_zh,
)


class DetectorRuntimeError(RuntimeError):
    """Raised when the external detector cannot run or returns invalid output."""


class GroundedSAMSubprocessDetector(BaseObjectDetector):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def detect(self, image_path: str, target_text: str) -> list[DetectedObject]:
        image = Path(image_path).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "detections.json"
            prompts = build_detection_prompts(target_text)
            command = [
                self.settings.grounded_sam_python,
                str(Path(__file__).with_name("grounded_sam_worker.py")),
                "--image",
                str(image),
                "--output",
                str(output_path),
                "--root",
                self.settings.grounded_sam_root,
                "--text-prompt",
                " || ".join(prompts),
                "--grounding-config",
                self.settings.grounding_dino_config,
                "--grounding-checkpoint",
                self.settings.grounding_dino_checkpoint,
                "--box-threshold",
                str(self.settings.grounding_dino_box_threshold),
                "--text-threshold",
                str(self.settings.grounding_dino_text_threshold),
                "--sam2-config",
                self.settings.sam2_config,
                "--sam2-checkpoint",
                self.settings.sam2_checkpoint,
                "--max-objects",
                str(self.settings.max_detected_objects),
                "--device",
                self.settings.detection_device,
            ]
            if not self.settings.enable_sam2:
                command.append("--disable-sam2")

            env = os.environ.copy()
            if self.settings.grounded_sam_pythonpath:
                existing_pythonpath = env.get("PYTHONPATH")
                env["PYTHONPATH"] = (
                    self.settings.grounded_sam_pythonpath
                    if not existing_pythonpath
                    else f"{self.settings.grounded_sam_pythonpath}:{existing_pythonpath}"
                )

            completed = subprocess.run(
                command,
                cwd=self.settings.grounded_sam_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.detector_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise DetectorRuntimeError(
                    "Grounding DINO/SAM2 detector failed.\n"
                    f"Command: {' '.join(command)}\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.is_file():
                raise DetectorRuntimeError(
                    "Grounding DINO/SAM2 detector finished but did not create output JSON."
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        return [_to_detected_object(item) for item in payload.get("objects", [])]


def _to_detected_object(item: dict) -> DetectedObject:
    label = str(item.get("label") or "object").lower().strip()
    bbox = item.get("bbox_2d") or [0.0, 0.0, 1.0, 1.0]
    attributes = list(item.get("attributes") or [])
    attributes.append("detected_by_grounding_dino")
    if item.get("mask_area_ratio") is not None:
        attributes.append("segmented_by_sam2")
        attributes.append(f"mask_area_ratio={float(item['mask_area_ratio']):.4f}")

    return DetectedObject(
        label=label,
        label_zh=label_zh(label),
        category=category_for_label(label),
        color=color_for_label(label),
        bbox_2d=(
            _clamp(float(bbox[0])),
            _clamp(float(bbox[1])),
            _clamp(float(bbox[2])),
            _clamp(float(bbox[3])),
        ),
        score=_clamp(float(item.get("score", 0.5))),
        attributes=attributes,
        mask_area_ratio=(
            None
            if item.get("mask_area_ratio") is None
            else _clamp(float(item["mask_area_ratio"]))
        ),
        source="grounding_dino_sam2",
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
