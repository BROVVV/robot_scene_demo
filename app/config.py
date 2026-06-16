"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_SILICONFLOW_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
DEFAULT_OUTPUT_DIR = "outputs"
DEFAULT_SILICONFLOW_TIMEOUT_SECONDS = 25.0
DEFAULT_SILICONFLOW_MAX_TOKENS = 2048
DEFAULT_IMAGE_MAX_SIDE = 640
DEFAULT_IMAGE_DETAIL = "low"
DEFAULT_ENABLE_LOW_OBJECT_RETRY = False
DEFAULT_MIN_OBJECTS_FOR_COMPLEX_SCENE = 10
DEFAULT_DETECTION_BACKEND = "llm"
DEFAULT_GROUNDED_SAM_ROOT = "/home/user/python3.10.0/Grounded-SAM-2"
DEFAULT_GROUNDED_SAM_PYTHON = "/home/user/python3.10/bin/python"
DEFAULT_GROUNDED_SAM_PYTHONPATH = "/home/user/python3.10/lib/python3.10/site-packages"
DEFAULT_GROUNDING_DINO_CONFIG = "grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py"
DEFAULT_GROUNDING_DINO_CHECKPOINT = "gdino_checkpoints/groundingdino_swint_ogc.pth"
DEFAULT_GROUNDING_DINO_BOX_THRESHOLD = 0.25
DEFAULT_GROUNDING_DINO_TEXT_THRESHOLD = 0.20
DEFAULT_ENABLE_SAM2 = True
DEFAULT_SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_t.yaml"
DEFAULT_SAM2_CHECKPOINT = "checkpoints/sam2.1_hiera_tiny.pt"
DEFAULT_MAX_DETECTED_OBJECTS = 30
DEFAULT_DETECTION_DEVICE = "auto"
DEFAULT_DETECTOR_TIMEOUT_SECONDS = 60.0


class SettingsError(RuntimeError):
    """Raised when required runtime configuration is missing."""


@dataclass(frozen=True)
class Settings:
    siliconflow_api_key: str
    siliconflow_base_url: str = DEFAULT_SILICONFLOW_BASE_URL
    siliconflow_model: str = DEFAULT_SILICONFLOW_MODEL
    output_dir: str = DEFAULT_OUTPUT_DIR
    siliconflow_timeout_seconds: float = DEFAULT_SILICONFLOW_TIMEOUT_SECONDS
    siliconflow_max_tokens: int = DEFAULT_SILICONFLOW_MAX_TOKENS
    image_max_side: int = DEFAULT_IMAGE_MAX_SIDE
    image_detail: str = DEFAULT_IMAGE_DETAIL
    enable_low_object_retry: bool = DEFAULT_ENABLE_LOW_OBJECT_RETRY
    min_objects_for_complex_scene: int = DEFAULT_MIN_OBJECTS_FOR_COMPLEX_SCENE
    detection_backend: str = DEFAULT_DETECTION_BACKEND
    grounded_sam_root: str = DEFAULT_GROUNDED_SAM_ROOT
    grounded_sam_python: str = DEFAULT_GROUNDED_SAM_PYTHON
    grounded_sam_pythonpath: str = DEFAULT_GROUNDED_SAM_PYTHONPATH
    grounding_dino_config: str = DEFAULT_GROUNDING_DINO_CONFIG
    grounding_dino_checkpoint: str = DEFAULT_GROUNDING_DINO_CHECKPOINT
    grounding_dino_box_threshold: float = DEFAULT_GROUNDING_DINO_BOX_THRESHOLD
    grounding_dino_text_threshold: float = DEFAULT_GROUNDING_DINO_TEXT_THRESHOLD
    enable_sam2: bool = DEFAULT_ENABLE_SAM2
    sam2_config: str = DEFAULT_SAM2_CONFIG
    sam2_checkpoint: str = DEFAULT_SAM2_CHECKPOINT
    max_detected_objects: int = DEFAULT_MAX_DETECTED_OBJECTS
    detection_device: str = DEFAULT_DETECTION_DEVICE
    detector_timeout_seconds: float = DEFAULT_DETECTOR_TIMEOUT_SECONDS


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_float(name: str, default: float) -> float:
    value = _env_value(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number, got: {value}") from exc


def _env_int(name: str, default: int) -> int:
    value = _env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got: {value}") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = _env_value(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false, got: {value}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from `.env` and process environment variables."""

    load_dotenv(_project_root() / ".env")

    return Settings(
        siliconflow_api_key=_env_value("SILICONFLOW_API_KEY", ""),
        siliconflow_base_url=_env_value(
            "SILICONFLOW_BASE_URL", DEFAULT_SILICONFLOW_BASE_URL
        ),
        siliconflow_model=_env_value("SILICONFLOW_MODEL", DEFAULT_SILICONFLOW_MODEL),
        output_dir=_env_value("OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
        siliconflow_timeout_seconds=_env_float(
            "SILICONFLOW_TIMEOUT_SECONDS", DEFAULT_SILICONFLOW_TIMEOUT_SECONDS
        ),
        siliconflow_max_tokens=_env_int(
            "SILICONFLOW_MAX_TOKENS", DEFAULT_SILICONFLOW_MAX_TOKENS
        ),
        image_max_side=_env_int("IMAGE_MAX_SIDE", DEFAULT_IMAGE_MAX_SIDE),
        image_detail=_env_value("IMAGE_DETAIL", DEFAULT_IMAGE_DETAIL),
        enable_low_object_retry=_env_bool(
            "ENABLE_LOW_OBJECT_RETRY", DEFAULT_ENABLE_LOW_OBJECT_RETRY
        ),
        min_objects_for_complex_scene=_env_int(
            "MIN_OBJECTS_FOR_COMPLEX_SCENE", DEFAULT_MIN_OBJECTS_FOR_COMPLEX_SCENE
        ),
        detection_backend=_env_value("DETECTION_BACKEND", DEFAULT_DETECTION_BACKEND),
        grounded_sam_root=_env_value("GROUNDED_SAM_ROOT", DEFAULT_GROUNDED_SAM_ROOT),
        grounded_sam_python=_env_value(
            "GROUNDED_SAM_PYTHON", DEFAULT_GROUNDED_SAM_PYTHON
        ),
        grounded_sam_pythonpath=_env_value(
            "GROUNDED_SAM_PYTHONPATH", DEFAULT_GROUNDED_SAM_PYTHONPATH
        ),
        grounding_dino_config=_env_value(
            "GROUNDING_DINO_CONFIG", DEFAULT_GROUNDING_DINO_CONFIG
        ),
        grounding_dino_checkpoint=_env_value(
            "GROUNDING_DINO_CHECKPOINT", DEFAULT_GROUNDING_DINO_CHECKPOINT
        ),
        grounding_dino_box_threshold=_env_float(
            "GROUNDING_DINO_BOX_THRESHOLD", DEFAULT_GROUNDING_DINO_BOX_THRESHOLD
        ),
        grounding_dino_text_threshold=_env_float(
            "GROUNDING_DINO_TEXT_THRESHOLD", DEFAULT_GROUNDING_DINO_TEXT_THRESHOLD
        ),
        enable_sam2=_env_bool("ENABLE_SAM2", DEFAULT_ENABLE_SAM2),
        sam2_config=_env_value("SAM2_CONFIG", DEFAULT_SAM2_CONFIG),
        sam2_checkpoint=_env_value("SAM2_CHECKPOINT", DEFAULT_SAM2_CHECKPOINT),
        max_detected_objects=_env_int("MAX_DETECTED_OBJECTS", DEFAULT_MAX_DETECTED_OBJECTS),
        detection_device=_env_value("DETECTION_DEVICE", DEFAULT_DETECTION_DEVICE),
        detector_timeout_seconds=_env_float(
            "DETECTOR_TIMEOUT_SECONDS", DEFAULT_DETECTOR_TIMEOUT_SECONDS
        ),
    )
