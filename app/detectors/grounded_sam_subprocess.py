"""Grounding DINO + SAM2 detector via an external Python environment."""

from __future__ import annotations

import json
import os
import re
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
from app.video.target_profile import TargetProfile


class DetectorRuntimeError(RuntimeError):
    """Raised when the external detector cannot run or returns invalid output."""


class GroundedSAMSubprocessDetector(BaseObjectDetector):
    def __init__(
        self,
        settings: Settings,
        target_profile: TargetProfile | None = None,
    ) -> None:
        self.settings = settings
        self.target_profile = target_profile

    def detect(self, image_path: str, target_text: str) -> list[DetectedObject]:
        return self.detect_with_dynamic_terms(image_path, target_text, [])

    def detect_with_dynamic_terms(
        self,
        image_path: str,
        target_text: str,
        dynamic_terms: list[str],
    ) -> list[DetectedObject]:
        image = Path(image_path).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "detections.json"
            prompts = build_detection_prompts(
                target_text,
                dynamic_terms=(
                    [
                        *(
                            self.target_profile.detector_terms()
                            if self.target_profile is not None
                            else []
                        ),
                        *dynamic_terms,
                    ]
                ),
                context_terms=(
                    self.target_profile.context_labels_en
                    if self.target_profile is not None
                    and self.settings.static_object_prompts_enabled
                    else None
                ),
                include_base_terms=(
                    self.settings.static_object_prompts_enabled
                    and self.target_profile is None
                    and not dynamic_terms
                ),
            )
            if not prompts:
                prompts = build_detection_prompts(
                    target_text,
                    include_base_terms=self.settings.static_object_prompts_enabled,
                )
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

            try:
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
            except subprocess.TimeoutExpired as exc:
                raise DetectorRuntimeError(
                    "Grounding DINO/SAM2 detector timed out after "
                    f"{self.settings.detector_timeout_seconds:.1f}s. "
                    "建议减少开放词表、降低输入分辨率或暂时关闭 SAM2。"
                ) from exc
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
            if (
                not payload.get("objects")
                and self.settings.enable_gdino_high_recall
                and (
                    self.settings.grounding_dino_box_threshold
                    > self.settings.grounding_dino_high_recall_box_threshold
                    or self.settings.grounding_dino_text_threshold
                    > self.settings.grounding_dino_high_recall_text_threshold
                )
            ):
                retry_command = list(command)
                retry_command[retry_command.index("--box-threshold") + 1] = str(
                    self.settings.grounding_dino_high_recall_box_threshold
                )
                retry_command[retry_command.index("--text-threshold") + 1] = str(
                    self.settings.grounding_dino_high_recall_text_threshold
                )
                try:
                    retried = subprocess.run(
                        retry_command,
                        cwd=self.settings.grounded_sam_root,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=self.settings.detector_timeout_seconds,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    retried = None
                if (
                    retried is not None
                    and retried.returncode == 0
                    and output_path.is_file()
                ):
                    payload = json.loads(output_path.read_text(encoding="utf-8"))

        return [
            _to_detected_object(item, self.target_profile)
            for item in payload.get("objects", [])
        ]


def _to_detected_object(
    item: dict,
    target_profile: TargetProfile | None = None,
) -> DetectedObject:
    label = str(item.get("label") or "object").lower().strip()
    bbox = item.get("bbox_2d") or [0.0, 0.0, 1.0, 1.0]
    attributes = list(item.get("attributes") or [])
    attributes.append("detected_by_grounding_dino")
    if item.get("mask_area_ratio") is not None:
        attributes.append("segmented_by_sam2")
        attributes.append(f"mask_area_ratio={float(item['mask_area_ratio']):.4f}")

    is_direct_target = (
        target_profile is not None
        and _matches_any_term(label, target_profile.direct_terms())
    )
    return DetectedObject(
        label=label,
        label_zh=(
            target_profile.canonical_name_zh
            if is_direct_target and target_profile is not None
            else label_zh(label)
        ),
        category=(
            target_profile.target_type
            if is_direct_target and target_profile is not None
            else category_for_label(label)
        ),
        color=color_for_label(label),
        bbox_2d=(
            _clamp(float(bbox[0])),
            _clamp(float(bbox[1])),
            _clamp(float(bbox[2])),
            _clamp(float(bbox[3])),
        ),
        score=_clamp(float(item.get("score", 0.5))),
        text_score=(
            None
            if item.get("text_score") is None
            else _clamp(float(item["text_score"]))
        ),
        attributes=attributes,
        mask_area_ratio=(
            None
            if item.get("mask_area_ratio") is None
            else _clamp(float(item["mask_area_ratio"]))
        ),
        source="grounding_dino_sam2",
        source_prompt_term=str(item.get("source_prompt_term") or label),
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _matches_any_term(label: str, terms: list[str]) -> bool:
    normalized_label = " ".join(
        re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", label.lower()).split()
    )
    for term in terms:
        normalized_term = " ".join(
            re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", term.lower()).split()
        )
        if normalized_term and normalized_term in normalized_label:
            return True
    return False
