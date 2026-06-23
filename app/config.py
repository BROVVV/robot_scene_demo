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
DEFAULT_FLORENCE2_MODEL_ID = "microsoft/Florence-2-base"
DEFAULT_FLORENCE2_PYTHON = ""
DEFAULT_FLORENCE2_DEVICE = "auto"
DEFAULT_FLORENCE2_MAX_OBJECTS = 40
DEFAULT_FLORENCE2_CONFIDENCE_THRESHOLD = 0.15
DEFAULT_FLORENCE2_TASK_PROMPT = "<OD>"
DEFAULT_FLORENCE2_ALLOW_MOCK = False
DEFAULT_ENABLE_SAM2_BOX_REFINEMENT = False
DEFAULT_ENABLE_GEOMETRY = True
DEFAULT_GEOMETRY_BACKEND = "auto"
DEFAULT_MOGE_ROOT = ""
DEFAULT_MOGE_PYTHON = ""
DEFAULT_MOGE_MODEL_ID = ""
DEFAULT_DEPTH_FALLBACK_BACKEND = "heuristic"
DEFAULT_CAMERA_HEIGHT_M = 0.45
DEFAULT_CAMERA_PITCH_DEG = 0.0
DEFAULT_BEV_X_MIN_M = 0.0
DEFAULT_BEV_X_MAX_M = 5.0
DEFAULT_BEV_Y_MIN_M = -2.5
DEFAULT_BEV_Y_MAX_M = 2.5
DEFAULT_BEV_RESOLUTION_M = 0.05
DEFAULT_OBSTACLE_MIN_HEIGHT_M = 0.08
DEFAULT_OBSTACLE_MAX_HEIGHT_M = 1.8
DEFAULT_UNKNOWN_AS_OCCUPIED = True
DEFAULT_ROBOT_RADIUS_M = 0.25
DEFAULT_SAFETY_MARGIN_M = 0.10
DEFAULT_GEOMETRY_TIMEOUT_SECONDS = 180.0
DEFAULT_ENABLE_LOCAL_PLANNER = True
DEFAULT_LOCAL_PLANNER_BACKEND = "astar"
DEFAULT_LOCAL_PLANNER_MAX_STEPS = 2000
DEFAULT_LOCAL_PLANNER_GOAL_TOLERANCE_M = 0.20
DEFAULT_LOCAL_PLANNER_MIN_CLEARANCE_M = 0.20
DEFAULT_LOCAL_PLANNER_ALLOW_PARTIAL = True
DEFAULT_CMD_VEL_LINEAR_SPEED = 0.25
DEFAULT_CMD_VEL_ANGULAR_SPEED = 0.6
DEFAULT_CMD_VEL_COMMAND_RATE_HZ = 10.0
DEFAULT_CMD_VEL_WAYPOINT_TOLERANCE_M = 0.15


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
    florence2_model_id: str = DEFAULT_FLORENCE2_MODEL_ID
    florence2_python: str = DEFAULT_FLORENCE2_PYTHON
    florence2_device: str = DEFAULT_FLORENCE2_DEVICE
    florence2_max_objects: int = DEFAULT_FLORENCE2_MAX_OBJECTS
    florence2_confidence_threshold: float = DEFAULT_FLORENCE2_CONFIDENCE_THRESHOLD
    florence2_task_prompt: str = DEFAULT_FLORENCE2_TASK_PROMPT
    florence2_allow_mock: bool = DEFAULT_FLORENCE2_ALLOW_MOCK
    enable_sam2_box_refinement: bool = DEFAULT_ENABLE_SAM2_BOX_REFINEMENT
    enable_geometry: bool = DEFAULT_ENABLE_GEOMETRY
    geometry_backend: str = DEFAULT_GEOMETRY_BACKEND
    moge_root: str = DEFAULT_MOGE_ROOT
    moge_python: str = DEFAULT_MOGE_PYTHON
    moge_model_id: str = DEFAULT_MOGE_MODEL_ID
    depth_fallback_backend: str = DEFAULT_DEPTH_FALLBACK_BACKEND
    camera_fx: float | None = None
    camera_fy: float | None = None
    camera_cx: float | None = None
    camera_cy: float | None = None
    camera_height_m: float = DEFAULT_CAMERA_HEIGHT_M
    camera_pitch_deg: float = DEFAULT_CAMERA_PITCH_DEG
    bev_x_min_m: float = DEFAULT_BEV_X_MIN_M
    bev_x_max_m: float = DEFAULT_BEV_X_MAX_M
    bev_y_min_m: float = DEFAULT_BEV_Y_MIN_M
    bev_y_max_m: float = DEFAULT_BEV_Y_MAX_M
    bev_resolution_m: float = DEFAULT_BEV_RESOLUTION_M
    obstacle_min_height_m: float = DEFAULT_OBSTACLE_MIN_HEIGHT_M
    obstacle_max_height_m: float = DEFAULT_OBSTACLE_MAX_HEIGHT_M
    unknown_as_occupied: bool = DEFAULT_UNKNOWN_AS_OCCUPIED
    robot_radius_m: float = DEFAULT_ROBOT_RADIUS_M
    safety_margin_m: float = DEFAULT_SAFETY_MARGIN_M
    geometry_timeout_seconds: float = DEFAULT_GEOMETRY_TIMEOUT_SECONDS
    enable_local_planner: bool = DEFAULT_ENABLE_LOCAL_PLANNER
    local_planner_backend: str = DEFAULT_LOCAL_PLANNER_BACKEND
    local_planner_max_steps: int = DEFAULT_LOCAL_PLANNER_MAX_STEPS
    local_planner_goal_tolerance_m: float = DEFAULT_LOCAL_PLANNER_GOAL_TOLERANCE_M
    local_planner_min_clearance_m: float = DEFAULT_LOCAL_PLANNER_MIN_CLEARANCE_M
    local_planner_allow_partial: bool = DEFAULT_LOCAL_PLANNER_ALLOW_PARTIAL
    cmd_vel_linear_speed: float = DEFAULT_CMD_VEL_LINEAR_SPEED
    cmd_vel_angular_speed: float = DEFAULT_CMD_VEL_ANGULAR_SPEED
    cmd_vel_command_rate_hz: float = DEFAULT_CMD_VEL_COMMAND_RATE_HZ
    cmd_vel_waypoint_tolerance_m: float = DEFAULT_CMD_VEL_WAYPOINT_TOLERANCE_M


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


def _env_optional_float(name: str) -> float | None:
    value = _env_value(name)
    if value is None:
        return None
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
        florence2_model_id=_env_value("FLORENCE2_MODEL_ID", DEFAULT_FLORENCE2_MODEL_ID),
        florence2_python=_env_value("FLORENCE2_PYTHON", DEFAULT_FLORENCE2_PYTHON),
        florence2_device=_env_value("FLORENCE2_DEVICE", DEFAULT_FLORENCE2_DEVICE),
        florence2_max_objects=_env_int(
            "FLORENCE2_MAX_OBJECTS", DEFAULT_FLORENCE2_MAX_OBJECTS
        ),
        florence2_confidence_threshold=_env_float(
            "FLORENCE2_CONFIDENCE_THRESHOLD",
            DEFAULT_FLORENCE2_CONFIDENCE_THRESHOLD,
        ),
        florence2_task_prompt=_env_value(
            "FLORENCE2_TASK_PROMPT", DEFAULT_FLORENCE2_TASK_PROMPT
        ),
        florence2_allow_mock=_env_bool(
            "FLORENCE2_ALLOW_MOCK", DEFAULT_FLORENCE2_ALLOW_MOCK
        ),
        enable_sam2_box_refinement=_env_bool(
            "ENABLE_SAM2_BOX_REFINEMENT", DEFAULT_ENABLE_SAM2_BOX_REFINEMENT
        ),
        enable_geometry=_env_bool("ENABLE_GEOMETRY", DEFAULT_ENABLE_GEOMETRY),
        geometry_backend=_env_value("GEOMETRY_BACKEND", DEFAULT_GEOMETRY_BACKEND),
        moge_root=_env_value("MOGE_ROOT", DEFAULT_MOGE_ROOT),
        moge_python=_env_value("MOGE_PYTHON", DEFAULT_MOGE_PYTHON),
        moge_model_id=_env_value("MOGE_MODEL_ID", DEFAULT_MOGE_MODEL_ID),
        depth_fallback_backend=_env_value(
            "DEPTH_FALLBACK_BACKEND", DEFAULT_DEPTH_FALLBACK_BACKEND
        ),
        camera_fx=_env_optional_float("CAMERA_FX"),
        camera_fy=_env_optional_float("CAMERA_FY"),
        camera_cx=_env_optional_float("CAMERA_CX"),
        camera_cy=_env_optional_float("CAMERA_CY"),
        camera_height_m=_env_float("CAMERA_HEIGHT_M", DEFAULT_CAMERA_HEIGHT_M),
        camera_pitch_deg=_env_float("CAMERA_PITCH_DEG", DEFAULT_CAMERA_PITCH_DEG),
        bev_x_min_m=_env_float("BEV_X_MIN_M", DEFAULT_BEV_X_MIN_M),
        bev_x_max_m=_env_float("BEV_X_MAX_M", DEFAULT_BEV_X_MAX_M),
        bev_y_min_m=_env_float("BEV_Y_MIN_M", DEFAULT_BEV_Y_MIN_M),
        bev_y_max_m=_env_float("BEV_Y_MAX_M", DEFAULT_BEV_Y_MAX_M),
        bev_resolution_m=_env_float("BEV_RESOLUTION_M", DEFAULT_BEV_RESOLUTION_M),
        obstacle_min_height_m=_env_float(
            "OBSTACLE_MIN_HEIGHT_M", DEFAULT_OBSTACLE_MIN_HEIGHT_M
        ),
        obstacle_max_height_m=_env_float(
            "OBSTACLE_MAX_HEIGHT_M", DEFAULT_OBSTACLE_MAX_HEIGHT_M
        ),
        unknown_as_occupied=_env_bool(
            "UNKNOWN_AS_OCCUPIED", DEFAULT_UNKNOWN_AS_OCCUPIED
        ),
        robot_radius_m=_env_float("ROBOT_RADIUS_M", DEFAULT_ROBOT_RADIUS_M),
        safety_margin_m=_env_float("SAFETY_MARGIN_M", DEFAULT_SAFETY_MARGIN_M),
        geometry_timeout_seconds=_env_float(
            "GEOMETRY_TIMEOUT_SECONDS", DEFAULT_GEOMETRY_TIMEOUT_SECONDS
        ),
        enable_local_planner=_env_bool(
            "ENABLE_LOCAL_PLANNER", DEFAULT_ENABLE_LOCAL_PLANNER
        ),
        local_planner_backend=_env_value(
            "LOCAL_PLANNER_BACKEND", DEFAULT_LOCAL_PLANNER_BACKEND
        ),
        local_planner_max_steps=_env_int(
            "LOCAL_PLANNER_MAX_STEPS", DEFAULT_LOCAL_PLANNER_MAX_STEPS
        ),
        local_planner_goal_tolerance_m=_env_float(
            "LOCAL_PLANNER_GOAL_TOLERANCE_M",
            DEFAULT_LOCAL_PLANNER_GOAL_TOLERANCE_M,
        ),
        local_planner_min_clearance_m=_env_float(
            "LOCAL_PLANNER_MIN_CLEARANCE_M",
            DEFAULT_LOCAL_PLANNER_MIN_CLEARANCE_M,
        ),
        local_planner_allow_partial=_env_bool(
            "LOCAL_PLANNER_ALLOW_PARTIAL", DEFAULT_LOCAL_PLANNER_ALLOW_PARTIAL
        ),
        cmd_vel_linear_speed=_env_float(
            "CMD_VEL_LINEAR_SPEED", DEFAULT_CMD_VEL_LINEAR_SPEED
        ),
        cmd_vel_angular_speed=_env_float(
            "CMD_VEL_ANGULAR_SPEED", DEFAULT_CMD_VEL_ANGULAR_SPEED
        ),
        cmd_vel_command_rate_hz=_env_float(
            "CMD_VEL_COMMAND_RATE_HZ", DEFAULT_CMD_VEL_COMMAND_RATE_HZ
        ),
        cmd_vel_waypoint_tolerance_m=_env_float(
            "CMD_VEL_WAYPOINT_TOLERANCE_M",
            DEFAULT_CMD_VEL_WAYPOINT_TOLERANCE_M,
        ),
    )
