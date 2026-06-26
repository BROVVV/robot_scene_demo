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
DEFAULT_IMAGE_MAX_SIDE = 1280
DEFAULT_IMAGE_DETAIL = "high"
DEFAULT_ENABLE_LOW_OBJECT_RETRY = True
DEFAULT_MIN_OBJECTS_FOR_COMPLEX_SCENE = 6
DEFAULT_ENABLE_IMAGE_PREPROCESS = True
DEFAULT_IMAGE_PREPROCESS_SHARPEN = False
DEFAULT_IMAGE_PREPROCESS_DENOISE = False
DEFAULT_ENABLE_TILED_DETECTION = False
DEFAULT_TILE_SIZE = 960
DEFAULT_TILE_OVERLAP = 160
DEFAULT_ENABLE_TARGET_PROFILE = True
DEFAULT_TARGET_PROFILE_LANGUAGE = "zh_en"
DEFAULT_TARGET_PROFILE_MAX_TERMS = 40
DEFAULT_ENABLE_TARGET_SYNONYM_EXPANSION = True
DEFAULT_ENABLE_CONTEXT_OBJECT_EXPANSION = True
DEFAULT_DETECTION_BACKEND = "llm"
DEFAULT_GROUNDED_SAM_ROOT = "/home/user/python3.10.0/Grounded-SAM-2"
DEFAULT_GROUNDED_SAM_PYTHON = "/home/user/python3.10/bin/python"
DEFAULT_GROUNDED_SAM_PYTHONPATH = "/home/user/python3.10/lib/python3.10/site-packages"
DEFAULT_GROUNDING_DINO_CONFIG = "grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py"
DEFAULT_GROUNDING_DINO_CHECKPOINT = "gdino_checkpoints/groundingdino_swint_ogc.pth"
DEFAULT_GROUNDING_DINO_BOX_THRESHOLD = 0.12
DEFAULT_GROUNDING_DINO_TEXT_THRESHOLD = 0.10
DEFAULT_GROUNDING_DINO_HIGH_RECALL_BOX_THRESHOLD = 0.10
DEFAULT_GROUNDING_DINO_HIGH_RECALL_TEXT_THRESHOLD = 0.08
DEFAULT_ENABLE_GDINO_HIGH_RECALL = True
DEFAULT_ENABLE_SAM2 = True
DEFAULT_SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_t.yaml"
DEFAULT_SAM2_CHECKPOINT = "checkpoints/sam2.1_hiera_tiny.pt"
DEFAULT_MAX_DETECTED_OBJECTS = 100
DEFAULT_DETECTION_DEVICE = "auto"
DEFAULT_DETECTOR_TIMEOUT_SECONDS = 60.0
DEFAULT_ENABLE_SAM2_MASK_FEATURES = True
DEFAULT_ENABLE_CROP_VERIFY = True
DEFAULT_CROP_VERIFY_BACKEND = "llm"
DEFAULT_CROP_VERIFY_MAX_CANDIDATES = 40
DEFAULT_CROP_VERIFY_EXPAND_RATIO = 1.35
DEFAULT_CROP_VERIFY_MIN_SCORE = 0.55
DEFAULT_CROP_VERIFY_TARGET_SCORE = 0.70
DEFAULT_CROP_VERIFY_TIMEOUT_SECONDS = 60.0
DEFAULT_CROP_VERIFY_SAVE_CROPS = True
DEFAULT_CROP_VERIFY_OUTPUT_DIR = "outputs/crops"
DEFAULT_ENABLE_SCORE_FUSION = True
DEFAULT_FUSION_WEIGHT_DETECTOR = 0.30
DEFAULT_FUSION_WEIGHT_VLM = 0.45
DEFAULT_FUSION_WEIGHT_ATTRIBUTE = 0.15
DEFAULT_FUSION_WEIGHT_CONTEXT = 0.10
DEFAULT_FINAL_TARGET_SCORE_THRESHOLD = 0.65
DEFAULT_FINAL_CANDIDATE_SCORE_THRESHOLD = 0.45
DEFAULT_VIDEO_SAMPLE_FPS = 3.0
DEFAULT_VIDEO_MAX_FRAMES = 300
DEFAULT_VIDEO_ENABLE_TRACKING = True
DEFAULT_VIDEO_TRACK_IOU_THRESHOLD = 0.35
DEFAULT_VIDEO_TRACK_MAX_MISSING_FRAMES = 8
DEFAULT_VIDEO_TRACK_MIN_HITS = 2
DEFAULT_VIDEO_TARGET_CONFIRM_MIN_FRAMES = 3
DEFAULT_VIDEO_TARGET_CONFIRM_SCORE = 0.65
DEFAULT_VIDEO_ENABLE_TRACK_LEVEL_VOTING = True
DEFAULT_VIDEO_VERIFY_EVERY_N_FRAMES = 2
DEFAULT_VIDEO_SAVE_CANDIDATE_CROPS = True
DEFAULT_EVAL_IOU_THRESHOLD = 0.5
DEFAULT_EVAL_OUTPUT_DIR = "outputs/eval"
DEFAULT_VIDEO_ENABLE_SCENE_MEMORY = True
DEFAULT_VIDEO_ALWAYS_WRITE_MEMORY = True
DEFAULT_VIDEO_ENABLE_VIDEO_PSG = True
DEFAULT_VIDEO_ENABLE_NEGATIVE_EVIDENCE = True
DEFAULT_VIDEO_ENABLE_REASONING_REPORT = True
DEFAULT_VIDEO_MEMORY_STORE_PATH = "data/memory/video_spatial_memory.jsonl"
DEFAULT_VIDEO_MIN_MEMORY_IMPORTANCE = "low"
DEFAULT_VIDEO_LLM_FRAME_INTERVAL_SEC = 2.0
DEFAULT_VIDEO_PSG_SUMMARY_INTERVAL_SEC = 5.0
DEFAULT_VIDEO_MAX_MEMORY_ENTRIES_PER_VIDEO = 200
DEFAULT_VIDEO_MEMORY_DEDUP_SIMILARITY = 0.86
DEFAULT_VIDEO_ENABLE_MEMORY_RETRIEVAL = True
DEFAULT_VIDEO_MEMORY_RETRIEVAL_TOP_K = 10
DEFAULT_VIDEO_SCENE_REASONER_BACKEND = "llm"
DEFAULT_VIDEO_FORCE_JSON_OUTPUT = True
DEFAULT_ENABLE_LLM_SITUATED_REASONING = True
DEFAULT_ENABLE_LLM_REASONING_MEMORY = True
DEFAULT_LLM_REASONING_MAX_HYPOTHESES = 5
DEFAULT_LLM_REASONING_TIMEOUT_SECONDS = 40.0
DEFAULT_LLM_REASONING_TEMPERATURE = 0.2
DEFAULT_LLM_REASONING_REQUIRE_ACTIONABILITY_GATE = True
DEFAULT_LLM_REASONING_REQUIRE_VISUAL_GATE = True
DEFAULT_QUADRUPED_MAX_FORWARD_STEP_M = 0.5
DEFAULT_QUADRUPED_CAN_MANIPULATE = False
DEFAULT_QUADRUPED_CAN_OPEN_CONTAINER = False
DEFAULT_QUADRUPED_CAN_LOOK_DOWN = False
DEFAULT_LLM_EXPERIENCE_MEMORY_PATH = "data/memory/llm_spatial_experience.jsonl"
DEFAULT_ENABLE_LLM_DYNAMIC_VISUAL_RETRY = True
DEFAULT_LLM_DYNAMIC_VISUAL_RETRY_MAX_TERMS = 12
DEFAULT_PLATFORM_OBSTACLE_AVOIDANCE_ASSUMED = True
DEFAULT_ENABLE_DYNAMIC_MOTION_HORIZON = True
DEFAULT_MOTION_HORIZON_PROFILE = "platform_assisted_auto"
DEFAULT_MOTION_STRICT_SAFE_MAX_STEP_M = 0.5
DEFAULT_MOTION_PLATFORM_INDOOR_DEFAULT_STEP_M = 1.2
DEFAULT_MOTION_PLATFORM_INDOOR_MAX_STEP_M = 2.0
DEFAULT_MOTION_PLATFORM_OPEN_DEFAULT_STEP_M = 3.0
DEFAULT_MOTION_PLATFORM_OPEN_MAX_STEP_M = 5.0
DEFAULT_MOTION_ABSOLUTE_MAX_STEP_M = 6.0
DEFAULT_MOTION_TARGET_CONFIRM_MAX_STEP_M = 0.8
DEFAULT_MOTION_PLATFORM_FALLBACK_STEP_M = 1.5
DEFAULT_MOTION_DEFAULT_STOP_AND_REOBSERVE = True
DEFAULT_MOTION_ENABLE_OBSERVE_WHILE_MOVING = False
DEFAULT_MOTION_SOFT_OBSERVE_INTERVAL_SEC = 1.0
DEFAULT_MOTION_SHORTEN_ON_TARGET_CANDIDATE = True
DEFAULT_MOTION_ALLOW_LLM_RECOMMENDED_HORIZON = True
DEFAULT_MOTION_LLM_HORIZON_WEIGHT = 0.6
DEFAULT_STATIC_KNOWLEDGE_BASE_ENABLED = False
DEFAULT_HANDWRITTEN_OBJECT_PRIORS_ENABLED = False
DEFAULT_HANDWRITTEN_LOCATION_PRIORS_ENABLED = False
DEFAULT_HANDWRITTEN_ROOM_PRIORS_ENABLED = False
DEFAULT_STATIC_OBJECT_PROMPTS_ENABLED = False
DEFAULT_ALLOW_HANDCRAFTED_SEARCH_RULES = False
DEFAULT_LLM_COMMONSENSE_PRIOR_ENABLED = True
DEFAULT_LLM_PRIOR_GENERATION_MODE = "runtime"
DEFAULT_LLM_PRIOR_REQUIRE_REASON = True
DEFAULT_LLM_PRIOR_ALLOW_SEARCH_HINTS = True
DEFAULT_LLM_PRIOR_ALLOW_DETECTOR_PROMPTS = True
DEFAULT_LLM_PRIOR_CAN_CONFIRM_TARGET = False
DEFAULT_LLM_PRIOR_MAX_HYPOTHESES = 8
DEFAULT_LLM_PRIOR_MAX_DETECTOR_PROMPTS = 12
DEFAULT_LLM_PRIOR_OUTPUT_LANGUAGE = "zh"
DEFAULT_EVIDENCE_GATING_ENABLED = True
DEFAULT_TARGET_CONFIRMATION_REQUIRE_VISUAL_EVIDENCE = True
DEFAULT_TARGET_CONFIRMATION_REQUIRE_BBOX = True
DEFAULT_TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY = True
DEFAULT_TARGET_CONFIRMATION_REQUIRE_MASK = False
DEFAULT_TARGET_CONFIRMATION_MIN_SCORE = 0.72
DEFAULT_TARGET_INFERRED_IS_NOT_FOUND = True
DEFAULT_OBSERVATION_MEMORY_ENABLED = True
DEFAULT_OBSERVATION_MEMORY_STORE_PATH = "data/memory/observational_memory.jsonl"
DEFAULT_OBSERVATION_MEMORY_WRITE_VISUAL_ONLY = True
DEFAULT_OBSERVATION_MEMORY_ALLOW_LLM_SUMMARY = True
DEFAULT_OBSERVATION_MEMORY_LLM_SUMMARY_AS_HYPOTHESIS = True
DEFAULT_OBSERVATION_MEMORY_RETRIEVAL_TOP_K = 10
DEFAULT_OBSERVATION_MEMORY_REQUIRE_PROVENANCE = True
DEFAULT_PRIOR_USAGE_AUDIT_ENABLED = True
DEFAULT_PRIOR_USAGE_REPORT_PATH = "outputs/prior_usage_report.json"


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
    enable_image_preprocess: bool = DEFAULT_ENABLE_IMAGE_PREPROCESS
    image_preprocess_sharpen: bool = DEFAULT_IMAGE_PREPROCESS_SHARPEN
    image_preprocess_denoise: bool = DEFAULT_IMAGE_PREPROCESS_DENOISE
    enable_tiled_detection: bool = DEFAULT_ENABLE_TILED_DETECTION
    tile_size: int = DEFAULT_TILE_SIZE
    tile_overlap: int = DEFAULT_TILE_OVERLAP
    enable_target_profile: bool = DEFAULT_ENABLE_TARGET_PROFILE
    target_profile_language: str = DEFAULT_TARGET_PROFILE_LANGUAGE
    target_profile_max_terms: int = DEFAULT_TARGET_PROFILE_MAX_TERMS
    enable_target_synonym_expansion: bool = DEFAULT_ENABLE_TARGET_SYNONYM_EXPANSION
    enable_context_object_expansion: bool = DEFAULT_ENABLE_CONTEXT_OBJECT_EXPANSION
    detection_backend: str = DEFAULT_DETECTION_BACKEND
    grounded_sam_root: str = DEFAULT_GROUNDED_SAM_ROOT
    grounded_sam_python: str = DEFAULT_GROUNDED_SAM_PYTHON
    grounded_sam_pythonpath: str = DEFAULT_GROUNDED_SAM_PYTHONPATH
    grounding_dino_config: str = DEFAULT_GROUNDING_DINO_CONFIG
    grounding_dino_checkpoint: str = DEFAULT_GROUNDING_DINO_CHECKPOINT
    grounding_dino_box_threshold: float = DEFAULT_GROUNDING_DINO_BOX_THRESHOLD
    grounding_dino_text_threshold: float = DEFAULT_GROUNDING_DINO_TEXT_THRESHOLD
    grounding_dino_high_recall_box_threshold: float = DEFAULT_GROUNDING_DINO_HIGH_RECALL_BOX_THRESHOLD
    grounding_dino_high_recall_text_threshold: float = DEFAULT_GROUNDING_DINO_HIGH_RECALL_TEXT_THRESHOLD
    enable_gdino_high_recall: bool = DEFAULT_ENABLE_GDINO_HIGH_RECALL
    enable_sam2: bool = DEFAULT_ENABLE_SAM2
    enable_sam2_mask_features: bool = DEFAULT_ENABLE_SAM2_MASK_FEATURES
    sam2_config: str = DEFAULT_SAM2_CONFIG
    sam2_checkpoint: str = DEFAULT_SAM2_CHECKPOINT
    max_detected_objects: int = DEFAULT_MAX_DETECTED_OBJECTS
    detection_device: str = DEFAULT_DETECTION_DEVICE
    detector_timeout_seconds: float = DEFAULT_DETECTOR_TIMEOUT_SECONDS
    enable_crop_verify: bool = DEFAULT_ENABLE_CROP_VERIFY
    crop_verify_backend: str = DEFAULT_CROP_VERIFY_BACKEND
    crop_verify_max_candidates: int = DEFAULT_CROP_VERIFY_MAX_CANDIDATES
    crop_verify_expand_ratio: float = DEFAULT_CROP_VERIFY_EXPAND_RATIO
    crop_verify_min_score: float = DEFAULT_CROP_VERIFY_MIN_SCORE
    crop_verify_target_score: float = DEFAULT_CROP_VERIFY_TARGET_SCORE
    crop_verify_timeout_seconds: float = DEFAULT_CROP_VERIFY_TIMEOUT_SECONDS
    crop_verify_save_crops: bool = DEFAULT_CROP_VERIFY_SAVE_CROPS
    crop_verify_output_dir: str = DEFAULT_CROP_VERIFY_OUTPUT_DIR
    enable_score_fusion: bool = DEFAULT_ENABLE_SCORE_FUSION
    fusion_weight_detector: float = DEFAULT_FUSION_WEIGHT_DETECTOR
    fusion_weight_vlm: float = DEFAULT_FUSION_WEIGHT_VLM
    fusion_weight_attribute: float = DEFAULT_FUSION_WEIGHT_ATTRIBUTE
    fusion_weight_context: float = DEFAULT_FUSION_WEIGHT_CONTEXT
    final_target_score_threshold: float = DEFAULT_FINAL_TARGET_SCORE_THRESHOLD
    final_candidate_score_threshold: float = DEFAULT_FINAL_CANDIDATE_SCORE_THRESHOLD
    video_sample_fps: float = DEFAULT_VIDEO_SAMPLE_FPS
    video_max_frames: int = DEFAULT_VIDEO_MAX_FRAMES
    video_enable_tracking: bool = DEFAULT_VIDEO_ENABLE_TRACKING
    video_track_iou_threshold: float = DEFAULT_VIDEO_TRACK_IOU_THRESHOLD
    video_track_max_missing_frames: int = DEFAULT_VIDEO_TRACK_MAX_MISSING_FRAMES
    video_track_min_hits: int = DEFAULT_VIDEO_TRACK_MIN_HITS
    video_target_confirm_min_frames: int = DEFAULT_VIDEO_TARGET_CONFIRM_MIN_FRAMES
    video_target_confirm_score: float = DEFAULT_VIDEO_TARGET_CONFIRM_SCORE
    video_enable_track_level_voting: bool = DEFAULT_VIDEO_ENABLE_TRACK_LEVEL_VOTING
    video_verify_every_n_frames: int = DEFAULT_VIDEO_VERIFY_EVERY_N_FRAMES
    video_save_candidate_crops: bool = DEFAULT_VIDEO_SAVE_CANDIDATE_CROPS
    eval_iou_threshold: float = DEFAULT_EVAL_IOU_THRESHOLD
    eval_output_dir: str = DEFAULT_EVAL_OUTPUT_DIR
    video_enable_scene_memory: bool = DEFAULT_VIDEO_ENABLE_SCENE_MEMORY
    video_always_write_memory: bool = DEFAULT_VIDEO_ALWAYS_WRITE_MEMORY
    video_enable_video_psg: bool = DEFAULT_VIDEO_ENABLE_VIDEO_PSG
    video_enable_negative_evidence: bool = DEFAULT_VIDEO_ENABLE_NEGATIVE_EVIDENCE
    video_enable_reasoning_report: bool = DEFAULT_VIDEO_ENABLE_REASONING_REPORT
    video_memory_store_path: str = DEFAULT_VIDEO_MEMORY_STORE_PATH
    video_min_memory_importance: str = DEFAULT_VIDEO_MIN_MEMORY_IMPORTANCE
    video_llm_frame_interval_sec: float = DEFAULT_VIDEO_LLM_FRAME_INTERVAL_SEC
    video_psg_summary_interval_sec: float = DEFAULT_VIDEO_PSG_SUMMARY_INTERVAL_SEC
    video_max_memory_entries_per_video: int = DEFAULT_VIDEO_MAX_MEMORY_ENTRIES_PER_VIDEO
    video_memory_dedup_similarity: float = DEFAULT_VIDEO_MEMORY_DEDUP_SIMILARITY
    video_enable_memory_retrieval: bool = DEFAULT_VIDEO_ENABLE_MEMORY_RETRIEVAL
    video_memory_retrieval_top_k: int = DEFAULT_VIDEO_MEMORY_RETRIEVAL_TOP_K
    video_scene_reasoner_backend: str = DEFAULT_VIDEO_SCENE_REASONER_BACKEND
    video_force_json_output: bool = DEFAULT_VIDEO_FORCE_JSON_OUTPUT
    enable_llm_situated_reasoning: bool = DEFAULT_ENABLE_LLM_SITUATED_REASONING
    enable_llm_reasoning_memory: bool = DEFAULT_ENABLE_LLM_REASONING_MEMORY
    llm_reasoning_max_hypotheses: int = DEFAULT_LLM_REASONING_MAX_HYPOTHESES
    llm_reasoning_timeout_seconds: float = DEFAULT_LLM_REASONING_TIMEOUT_SECONDS
    llm_reasoning_temperature: float = DEFAULT_LLM_REASONING_TEMPERATURE
    llm_reasoning_require_actionability_gate: bool = (
        DEFAULT_LLM_REASONING_REQUIRE_ACTIONABILITY_GATE
    )
    llm_reasoning_require_visual_gate: bool = DEFAULT_LLM_REASONING_REQUIRE_VISUAL_GATE
    quadruped_max_forward_step_m: float = DEFAULT_QUADRUPED_MAX_FORWARD_STEP_M
    quadruped_can_manipulate: bool = DEFAULT_QUADRUPED_CAN_MANIPULATE
    quadruped_can_open_container: bool = DEFAULT_QUADRUPED_CAN_OPEN_CONTAINER
    quadruped_can_look_down: bool = DEFAULT_QUADRUPED_CAN_LOOK_DOWN
    llm_experience_memory_path: str = DEFAULT_LLM_EXPERIENCE_MEMORY_PATH
    enable_llm_dynamic_visual_retry: bool = DEFAULT_ENABLE_LLM_DYNAMIC_VISUAL_RETRY
    llm_dynamic_visual_retry_max_terms: int = (
        DEFAULT_LLM_DYNAMIC_VISUAL_RETRY_MAX_TERMS
    )
    platform_obstacle_avoidance_assumed: bool = (
        DEFAULT_PLATFORM_OBSTACLE_AVOIDANCE_ASSUMED
    )
    enable_dynamic_motion_horizon: bool = DEFAULT_ENABLE_DYNAMIC_MOTION_HORIZON
    motion_horizon_profile: str = DEFAULT_MOTION_HORIZON_PROFILE
    motion_strict_safe_max_step_m: float = DEFAULT_MOTION_STRICT_SAFE_MAX_STEP_M
    motion_platform_indoor_default_step_m: float = (
        DEFAULT_MOTION_PLATFORM_INDOOR_DEFAULT_STEP_M
    )
    motion_platform_indoor_max_step_m: float = DEFAULT_MOTION_PLATFORM_INDOOR_MAX_STEP_M
    motion_platform_open_default_step_m: float = DEFAULT_MOTION_PLATFORM_OPEN_DEFAULT_STEP_M
    motion_platform_open_max_step_m: float = DEFAULT_MOTION_PLATFORM_OPEN_MAX_STEP_M
    motion_absolute_max_step_m: float = DEFAULT_MOTION_ABSOLUTE_MAX_STEP_M
    motion_target_confirm_max_step_m: float = DEFAULT_MOTION_TARGET_CONFIRM_MAX_STEP_M
    motion_platform_fallback_step_m: float = DEFAULT_MOTION_PLATFORM_FALLBACK_STEP_M
    motion_default_stop_and_reobserve: bool = DEFAULT_MOTION_DEFAULT_STOP_AND_REOBSERVE
    motion_enable_observe_while_moving: bool = (
        DEFAULT_MOTION_ENABLE_OBSERVE_WHILE_MOVING
    )
    motion_soft_observe_interval_sec: float = DEFAULT_MOTION_SOFT_OBSERVE_INTERVAL_SEC
    motion_shorten_on_target_candidate: bool = DEFAULT_MOTION_SHORTEN_ON_TARGET_CANDIDATE
    motion_allow_llm_recommended_horizon: bool = (
        DEFAULT_MOTION_ALLOW_LLM_RECOMMENDED_HORIZON
    )
    motion_llm_horizon_weight: float = DEFAULT_MOTION_LLM_HORIZON_WEIGHT
    static_knowledge_base_enabled: bool = DEFAULT_STATIC_KNOWLEDGE_BASE_ENABLED
    handwritten_object_priors_enabled: bool = DEFAULT_HANDWRITTEN_OBJECT_PRIORS_ENABLED
    handwritten_location_priors_enabled: bool = DEFAULT_HANDWRITTEN_LOCATION_PRIORS_ENABLED
    handwritten_room_priors_enabled: bool = DEFAULT_HANDWRITTEN_ROOM_PRIORS_ENABLED
    static_object_prompts_enabled: bool = DEFAULT_STATIC_OBJECT_PROMPTS_ENABLED
    allow_handcrafted_search_rules: bool = DEFAULT_ALLOW_HANDCRAFTED_SEARCH_RULES
    llm_commonsense_prior_enabled: bool = DEFAULT_LLM_COMMONSENSE_PRIOR_ENABLED
    llm_prior_generation_mode: str = DEFAULT_LLM_PRIOR_GENERATION_MODE
    llm_prior_require_reason: bool = DEFAULT_LLM_PRIOR_REQUIRE_REASON
    llm_prior_allow_search_hints: bool = DEFAULT_LLM_PRIOR_ALLOW_SEARCH_HINTS
    llm_prior_allow_detector_prompts: bool = DEFAULT_LLM_PRIOR_ALLOW_DETECTOR_PROMPTS
    llm_prior_can_confirm_target: bool = DEFAULT_LLM_PRIOR_CAN_CONFIRM_TARGET
    llm_prior_max_hypotheses: int = DEFAULT_LLM_PRIOR_MAX_HYPOTHESES
    llm_prior_max_detector_prompts: int = DEFAULT_LLM_PRIOR_MAX_DETECTOR_PROMPTS
    llm_prior_output_language: str = DEFAULT_LLM_PRIOR_OUTPUT_LANGUAGE
    evidence_gating_enabled: bool = DEFAULT_EVIDENCE_GATING_ENABLED
    target_confirmation_require_visual_evidence: bool = (
        DEFAULT_TARGET_CONFIRMATION_REQUIRE_VISUAL_EVIDENCE
    )
    target_confirmation_require_bbox: bool = DEFAULT_TARGET_CONFIRMATION_REQUIRE_BBOX
    target_confirmation_require_crop_verify: bool = (
        DEFAULT_TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY
    )
    target_confirmation_require_mask: bool = DEFAULT_TARGET_CONFIRMATION_REQUIRE_MASK
    target_confirmation_min_score: float = DEFAULT_TARGET_CONFIRMATION_MIN_SCORE
    target_inferred_is_not_found: bool = DEFAULT_TARGET_INFERRED_IS_NOT_FOUND
    observation_memory_enabled: bool = DEFAULT_OBSERVATION_MEMORY_ENABLED
    observation_memory_store_path: str = DEFAULT_OBSERVATION_MEMORY_STORE_PATH
    observation_memory_write_visual_only: bool = DEFAULT_OBSERVATION_MEMORY_WRITE_VISUAL_ONLY
    observation_memory_allow_llm_summary: bool = DEFAULT_OBSERVATION_MEMORY_ALLOW_LLM_SUMMARY
    observation_memory_llm_summary_as_hypothesis: bool = (
        DEFAULT_OBSERVATION_MEMORY_LLM_SUMMARY_AS_HYPOTHESIS
    )
    observation_memory_retrieval_top_k: int = DEFAULT_OBSERVATION_MEMORY_RETRIEVAL_TOP_K
    observation_memory_require_provenance: bool = (
        DEFAULT_OBSERVATION_MEMORY_REQUIRE_PROVENANCE
    )
    prior_usage_audit_enabled: bool = DEFAULT_PRIOR_USAGE_AUDIT_ENABLED
    prior_usage_report_path: str = DEFAULT_PRIOR_USAGE_REPORT_PATH


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
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = _env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env_value(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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
        enable_image_preprocess=_env_bool(
            "ENABLE_IMAGE_PREPROCESS", DEFAULT_ENABLE_IMAGE_PREPROCESS
        ),
        image_preprocess_sharpen=_env_bool(
            "IMAGE_PREPROCESS_SHARPEN", DEFAULT_IMAGE_PREPROCESS_SHARPEN
        ),
        image_preprocess_denoise=_env_bool(
            "IMAGE_PREPROCESS_DENOISE", DEFAULT_IMAGE_PREPROCESS_DENOISE
        ),
        enable_tiled_detection=_env_bool(
            "ENABLE_TILED_DETECTION", DEFAULT_ENABLE_TILED_DETECTION
        ),
        tile_size=_env_int("TILE_SIZE", DEFAULT_TILE_SIZE),
        tile_overlap=_env_int("TILE_OVERLAP", DEFAULT_TILE_OVERLAP),
        enable_target_profile=_env_bool(
            "ENABLE_TARGET_PROFILE", DEFAULT_ENABLE_TARGET_PROFILE
        ),
        target_profile_language=_env_value(
            "TARGET_PROFILE_LANGUAGE", DEFAULT_TARGET_PROFILE_LANGUAGE
        ),
        target_profile_max_terms=_env_int(
            "TARGET_PROFILE_MAX_TERMS", DEFAULT_TARGET_PROFILE_MAX_TERMS
        ),
        enable_target_synonym_expansion=_env_bool(
            "ENABLE_TARGET_SYNONYM_EXPANSION",
            DEFAULT_ENABLE_TARGET_SYNONYM_EXPANSION,
        ),
        enable_context_object_expansion=_env_bool(
            "ENABLE_CONTEXT_OBJECT_EXPANSION",
            DEFAULT_ENABLE_CONTEXT_OBJECT_EXPANSION,
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
        grounding_dino_high_recall_box_threshold=_env_float(
            "GROUNDING_DINO_HIGH_RECALL_BOX_THRESHOLD",
            DEFAULT_GROUNDING_DINO_HIGH_RECALL_BOX_THRESHOLD,
        ),
        grounding_dino_high_recall_text_threshold=_env_float(
            "GROUNDING_DINO_HIGH_RECALL_TEXT_THRESHOLD",
            DEFAULT_GROUNDING_DINO_HIGH_RECALL_TEXT_THRESHOLD,
        ),
        enable_gdino_high_recall=_env_bool(
            "ENABLE_GDINO_HIGH_RECALL", DEFAULT_ENABLE_GDINO_HIGH_RECALL
        ),
        enable_sam2=_env_bool("ENABLE_SAM2", DEFAULT_ENABLE_SAM2),
        enable_sam2_mask_features=_env_bool(
            "ENABLE_SAM2_MASK_FEATURES", DEFAULT_ENABLE_SAM2_MASK_FEATURES
        ),
        sam2_config=_env_value("SAM2_CONFIG", DEFAULT_SAM2_CONFIG),
        sam2_checkpoint=_env_value("SAM2_CHECKPOINT", DEFAULT_SAM2_CHECKPOINT),
        max_detected_objects=_env_int("MAX_DETECTED_OBJECTS", DEFAULT_MAX_DETECTED_OBJECTS),
        detection_device=_env_value("DETECTION_DEVICE", DEFAULT_DETECTION_DEVICE),
        detector_timeout_seconds=_env_float(
            "DETECTOR_TIMEOUT_SECONDS", DEFAULT_DETECTOR_TIMEOUT_SECONDS
        ),
        enable_crop_verify=_env_bool(
            "ENABLE_CROP_VERIFY", DEFAULT_ENABLE_CROP_VERIFY
        ),
        crop_verify_backend=_env_value(
            "CROP_VERIFY_BACKEND", DEFAULT_CROP_VERIFY_BACKEND
        ),
        crop_verify_max_candidates=_env_int(
            "CROP_VERIFY_MAX_CANDIDATES", DEFAULT_CROP_VERIFY_MAX_CANDIDATES
        ),
        crop_verify_expand_ratio=_env_float(
            "CROP_VERIFY_EXPAND_RATIO", DEFAULT_CROP_VERIFY_EXPAND_RATIO
        ),
        crop_verify_min_score=_env_float(
            "CROP_VERIFY_MIN_SCORE", DEFAULT_CROP_VERIFY_MIN_SCORE
        ),
        crop_verify_target_score=_env_float(
            "CROP_VERIFY_TARGET_SCORE", DEFAULT_CROP_VERIFY_TARGET_SCORE
        ),
        crop_verify_timeout_seconds=_env_float(
            "CROP_VERIFY_TIMEOUT_SECONDS", DEFAULT_CROP_VERIFY_TIMEOUT_SECONDS
        ),
        crop_verify_save_crops=_env_bool(
            "CROP_VERIFY_SAVE_CROPS", DEFAULT_CROP_VERIFY_SAVE_CROPS
        ),
        crop_verify_output_dir=_env_value(
            "CROP_VERIFY_OUTPUT_DIR", DEFAULT_CROP_VERIFY_OUTPUT_DIR
        ),
        enable_score_fusion=_env_bool(
            "ENABLE_SCORE_FUSION", DEFAULT_ENABLE_SCORE_FUSION
        ),
        fusion_weight_detector=_env_float(
            "FUSION_WEIGHT_DETECTOR", DEFAULT_FUSION_WEIGHT_DETECTOR
        ),
        fusion_weight_vlm=_env_float(
            "FUSION_WEIGHT_VLM", DEFAULT_FUSION_WEIGHT_VLM
        ),
        fusion_weight_attribute=_env_float(
            "FUSION_WEIGHT_ATTRIBUTE", DEFAULT_FUSION_WEIGHT_ATTRIBUTE
        ),
        fusion_weight_context=_env_float(
            "FUSION_WEIGHT_CONTEXT", DEFAULT_FUSION_WEIGHT_CONTEXT
        ),
        final_target_score_threshold=_env_float(
            "FINAL_TARGET_SCORE_THRESHOLD", DEFAULT_FINAL_TARGET_SCORE_THRESHOLD
        ),
        final_candidate_score_threshold=_env_float(
            "FINAL_CANDIDATE_SCORE_THRESHOLD",
            DEFAULT_FINAL_CANDIDATE_SCORE_THRESHOLD,
        ),
        video_sample_fps=_env_float("VIDEO_SAMPLE_FPS", DEFAULT_VIDEO_SAMPLE_FPS),
        video_max_frames=_env_int("VIDEO_MAX_FRAMES", DEFAULT_VIDEO_MAX_FRAMES),
        video_enable_tracking=_env_bool(
            "VIDEO_ENABLE_TRACKING", DEFAULT_VIDEO_ENABLE_TRACKING
        ),
        video_track_iou_threshold=_env_float(
            "VIDEO_TRACK_IOU_THRESHOLD", DEFAULT_VIDEO_TRACK_IOU_THRESHOLD
        ),
        video_track_max_missing_frames=_env_int(
            "VIDEO_TRACK_MAX_MISSING_FRAMES",
            DEFAULT_VIDEO_TRACK_MAX_MISSING_FRAMES,
        ),
        video_track_min_hits=_env_int(
            "VIDEO_TRACK_MIN_HITS", DEFAULT_VIDEO_TRACK_MIN_HITS
        ),
        video_target_confirm_min_frames=_env_int(
            "VIDEO_TARGET_CONFIRM_MIN_FRAMES",
            DEFAULT_VIDEO_TARGET_CONFIRM_MIN_FRAMES,
        ),
        video_target_confirm_score=_env_float(
            "VIDEO_TARGET_CONFIRM_SCORE", DEFAULT_VIDEO_TARGET_CONFIRM_SCORE
        ),
        video_enable_track_level_voting=_env_bool(
            "VIDEO_ENABLE_TRACK_LEVEL_VOTING",
            DEFAULT_VIDEO_ENABLE_TRACK_LEVEL_VOTING,
        ),
        video_verify_every_n_frames=_env_int(
            "VIDEO_VERIFY_EVERY_N_FRAMES", DEFAULT_VIDEO_VERIFY_EVERY_N_FRAMES
        ),
        video_save_candidate_crops=_env_bool(
            "VIDEO_SAVE_CANDIDATE_CROPS", DEFAULT_VIDEO_SAVE_CANDIDATE_CROPS
        ),
        eval_iou_threshold=_env_float(
            "EVAL_IOU_THRESHOLD", DEFAULT_EVAL_IOU_THRESHOLD
        ),
        eval_output_dir=_env_value("EVAL_OUTPUT_DIR", DEFAULT_EVAL_OUTPUT_DIR),
        video_enable_scene_memory=_env_bool(
            "VIDEO_ENABLE_SCENE_MEMORY", DEFAULT_VIDEO_ENABLE_SCENE_MEMORY
        ),
        video_always_write_memory=_env_bool(
            "VIDEO_ALWAYS_WRITE_MEMORY", DEFAULT_VIDEO_ALWAYS_WRITE_MEMORY
        ),
        video_enable_video_psg=_env_bool(
            "VIDEO_ENABLE_VIDEO_PSG", DEFAULT_VIDEO_ENABLE_VIDEO_PSG
        ),
        video_enable_negative_evidence=_env_bool(
            "VIDEO_ENABLE_NEGATIVE_EVIDENCE", DEFAULT_VIDEO_ENABLE_NEGATIVE_EVIDENCE
        ),
        video_enable_reasoning_report=_env_bool(
            "VIDEO_ENABLE_REASONING_REPORT", DEFAULT_VIDEO_ENABLE_REASONING_REPORT
        ),
        video_memory_store_path=_env_value(
            "VIDEO_MEMORY_STORE_PATH", DEFAULT_VIDEO_MEMORY_STORE_PATH
        ),
        video_min_memory_importance=_env_value(
            "VIDEO_MIN_MEMORY_IMPORTANCE", DEFAULT_VIDEO_MIN_MEMORY_IMPORTANCE
        ),
        video_llm_frame_interval_sec=_env_float(
            "VIDEO_LLM_FRAME_INTERVAL_SEC", DEFAULT_VIDEO_LLM_FRAME_INTERVAL_SEC
        ),
        video_psg_summary_interval_sec=_env_float(
            "VIDEO_PSG_SUMMARY_INTERVAL_SEC", DEFAULT_VIDEO_PSG_SUMMARY_INTERVAL_SEC
        ),
        video_max_memory_entries_per_video=_env_int(
            "VIDEO_MAX_MEMORY_ENTRIES_PER_VIDEO",
            DEFAULT_VIDEO_MAX_MEMORY_ENTRIES_PER_VIDEO,
        ),
        video_memory_dedup_similarity=_env_float(
            "VIDEO_MEMORY_DEDUP_SIMILARITY", DEFAULT_VIDEO_MEMORY_DEDUP_SIMILARITY
        ),
        video_enable_memory_retrieval=_env_bool(
            "VIDEO_ENABLE_MEMORY_RETRIEVAL", DEFAULT_VIDEO_ENABLE_MEMORY_RETRIEVAL
        ),
        video_memory_retrieval_top_k=_env_int(
            "VIDEO_MEMORY_RETRIEVAL_TOP_K", DEFAULT_VIDEO_MEMORY_RETRIEVAL_TOP_K
        ),
        video_scene_reasoner_backend=_env_value(
            "VIDEO_SCENE_REASONER_BACKEND", DEFAULT_VIDEO_SCENE_REASONER_BACKEND
        ),
        video_force_json_output=_env_bool(
            "VIDEO_FORCE_JSON_OUTPUT", DEFAULT_VIDEO_FORCE_JSON_OUTPUT
        ),
        enable_llm_situated_reasoning=_env_bool(
            "ENABLE_LLM_SITUATED_REASONING",
            DEFAULT_ENABLE_LLM_SITUATED_REASONING,
        ),
        enable_llm_reasoning_memory=_env_bool(
            "ENABLE_LLM_REASONING_MEMORY", DEFAULT_ENABLE_LLM_REASONING_MEMORY
        ),
        llm_reasoning_max_hypotheses=_env_int(
            "LLM_REASONING_MAX_HYPOTHESES",
            DEFAULT_LLM_REASONING_MAX_HYPOTHESES,
        ),
        llm_reasoning_timeout_seconds=_env_float(
            "LLM_REASONING_TIMEOUT_SECONDS",
            DEFAULT_LLM_REASONING_TIMEOUT_SECONDS,
        ),
        llm_reasoning_temperature=_env_float(
            "LLM_REASONING_TEMPERATURE", DEFAULT_LLM_REASONING_TEMPERATURE
        ),
        llm_reasoning_require_actionability_gate=_env_bool(
            "LLM_REASONING_REQUIRE_ACTIONABILITY_GATE",
            DEFAULT_LLM_REASONING_REQUIRE_ACTIONABILITY_GATE,
        ),
        llm_reasoning_require_visual_gate=_env_bool(
            "LLM_REASONING_REQUIRE_VISUAL_GATE",
            DEFAULT_LLM_REASONING_REQUIRE_VISUAL_GATE,
        ),
        quadruped_max_forward_step_m=_env_float(
            "QUADRUPED_MAX_FORWARD_STEP_M", DEFAULT_QUADRUPED_MAX_FORWARD_STEP_M
        ),
        quadruped_can_manipulate=_env_bool(
            "QUADRUPED_CAN_MANIPULATE", DEFAULT_QUADRUPED_CAN_MANIPULATE
        ),
        quadruped_can_open_container=_env_bool(
            "QUADRUPED_CAN_OPEN_CONTAINER", DEFAULT_QUADRUPED_CAN_OPEN_CONTAINER
        ),
        quadruped_can_look_down=_env_bool(
            "QUADRUPED_CAN_LOOK_DOWN", DEFAULT_QUADRUPED_CAN_LOOK_DOWN
        ),
        llm_experience_memory_path=_env_value(
            "LLM_EXPERIENCE_MEMORY_PATH", DEFAULT_LLM_EXPERIENCE_MEMORY_PATH
        ),
        enable_llm_dynamic_visual_retry=_env_bool(
            "ENABLE_LLM_DYNAMIC_VISUAL_RETRY",
            DEFAULT_ENABLE_LLM_DYNAMIC_VISUAL_RETRY,
        ),
        llm_dynamic_visual_retry_max_terms=_env_int(
            "LLM_DYNAMIC_VISUAL_RETRY_MAX_TERMS",
            DEFAULT_LLM_DYNAMIC_VISUAL_RETRY_MAX_TERMS,
        ),
        platform_obstacle_avoidance_assumed=_env_bool(
            "PLATFORM_OBSTACLE_AVOIDANCE_ASSUMED",
            DEFAULT_PLATFORM_OBSTACLE_AVOIDANCE_ASSUMED,
        ),
        enable_dynamic_motion_horizon=_env_bool(
            "ENABLE_DYNAMIC_MOTION_HORIZON",
            DEFAULT_ENABLE_DYNAMIC_MOTION_HORIZON,
        ),
        motion_horizon_profile=_env_value(
            "MOTION_HORIZON_PROFILE",
            DEFAULT_MOTION_HORIZON_PROFILE,
        ),
        motion_strict_safe_max_step_m=_env_float(
            "MOTION_STRICT_SAFE_MAX_STEP_M",
            DEFAULT_MOTION_STRICT_SAFE_MAX_STEP_M,
        ),
        motion_platform_indoor_default_step_m=_env_float(
            "MOTION_PLATFORM_INDOOR_DEFAULT_STEP_M",
            DEFAULT_MOTION_PLATFORM_INDOOR_DEFAULT_STEP_M,
        ),
        motion_platform_indoor_max_step_m=_env_float(
            "MOTION_PLATFORM_INDOOR_MAX_STEP_M",
            DEFAULT_MOTION_PLATFORM_INDOOR_MAX_STEP_M,
        ),
        motion_platform_open_default_step_m=_env_float(
            "MOTION_PLATFORM_OPEN_DEFAULT_STEP_M",
            DEFAULT_MOTION_PLATFORM_OPEN_DEFAULT_STEP_M,
        ),
        motion_platform_open_max_step_m=_env_float(
            "MOTION_PLATFORM_OPEN_MAX_STEP_M",
            DEFAULT_MOTION_PLATFORM_OPEN_MAX_STEP_M,
        ),
        motion_absolute_max_step_m=_env_float(
            "MOTION_ABSOLUTE_MAX_STEP_M",
            DEFAULT_MOTION_ABSOLUTE_MAX_STEP_M,
        ),
        motion_target_confirm_max_step_m=_env_float(
            "MOTION_TARGET_CONFIRM_MAX_STEP_M",
            DEFAULT_MOTION_TARGET_CONFIRM_MAX_STEP_M,
        ),
        motion_platform_fallback_step_m=_env_float(
            "MOTION_PLATFORM_FALLBACK_STEP_M",
            DEFAULT_MOTION_PLATFORM_FALLBACK_STEP_M,
        ),
        motion_default_stop_and_reobserve=_env_bool(
            "MOTION_DEFAULT_STOP_AND_REOBSERVE",
            DEFAULT_MOTION_DEFAULT_STOP_AND_REOBSERVE,
        ),
        motion_enable_observe_while_moving=_env_bool(
            "MOTION_ENABLE_OBSERVE_WHILE_MOVING",
            DEFAULT_MOTION_ENABLE_OBSERVE_WHILE_MOVING,
        ),
        motion_soft_observe_interval_sec=_env_float(
            "MOTION_SOFT_OBSERVE_INTERVAL_SEC",
            DEFAULT_MOTION_SOFT_OBSERVE_INTERVAL_SEC,
        ),
        motion_shorten_on_target_candidate=_env_bool(
            "MOTION_SHORTEN_ON_TARGET_CANDIDATE",
            DEFAULT_MOTION_SHORTEN_ON_TARGET_CANDIDATE,
        ),
        motion_allow_llm_recommended_horizon=_env_bool(
            "MOTION_ALLOW_LLM_RECOMMENDED_HORIZON",
            DEFAULT_MOTION_ALLOW_LLM_RECOMMENDED_HORIZON,
        ),
        motion_llm_horizon_weight=_env_float(
            "MOTION_LLM_HORIZON_WEIGHT",
            DEFAULT_MOTION_LLM_HORIZON_WEIGHT,
        ),
        static_knowledge_base_enabled=_env_bool(
            "STATIC_KNOWLEDGE_BASE_ENABLED",
            DEFAULT_STATIC_KNOWLEDGE_BASE_ENABLED,
        ),
        handwritten_object_priors_enabled=_env_bool(
            "HANDWRITTEN_OBJECT_PRIORS_ENABLED",
            DEFAULT_HANDWRITTEN_OBJECT_PRIORS_ENABLED,
        ),
        handwritten_location_priors_enabled=_env_bool(
            "HANDWRITTEN_LOCATION_PRIORS_ENABLED",
            DEFAULT_HANDWRITTEN_LOCATION_PRIORS_ENABLED,
        ),
        handwritten_room_priors_enabled=_env_bool(
            "HANDWRITTEN_ROOM_PRIORS_ENABLED",
            DEFAULT_HANDWRITTEN_ROOM_PRIORS_ENABLED,
        ),
        static_object_prompts_enabled=_env_bool(
            "STATIC_OBJECT_PROMPTS_ENABLED",
            DEFAULT_STATIC_OBJECT_PROMPTS_ENABLED,
        ),
        allow_handcrafted_search_rules=_env_bool(
            "ALLOW_HANDCRAFTED_SEARCH_RULES",
            DEFAULT_ALLOW_HANDCRAFTED_SEARCH_RULES,
        ),
        llm_commonsense_prior_enabled=_env_bool(
            "LLM_COMMONSENSE_PRIOR_ENABLED",
            DEFAULT_LLM_COMMONSENSE_PRIOR_ENABLED,
        ),
        llm_prior_generation_mode=_env_value(
            "LLM_PRIOR_GENERATION_MODE",
            DEFAULT_LLM_PRIOR_GENERATION_MODE,
        ),
        llm_prior_require_reason=_env_bool(
            "LLM_PRIOR_REQUIRE_REASON",
            DEFAULT_LLM_PRIOR_REQUIRE_REASON,
        ),
        llm_prior_allow_search_hints=_env_bool(
            "LLM_PRIOR_ALLOW_SEARCH_HINTS",
            DEFAULT_LLM_PRIOR_ALLOW_SEARCH_HINTS,
        ),
        llm_prior_allow_detector_prompts=_env_bool(
            "LLM_PRIOR_ALLOW_DETECTOR_PROMPTS",
            DEFAULT_LLM_PRIOR_ALLOW_DETECTOR_PROMPTS,
        ),
        llm_prior_can_confirm_target=_env_bool(
            "LLM_PRIOR_CAN_CONFIRM_TARGET",
            DEFAULT_LLM_PRIOR_CAN_CONFIRM_TARGET,
        ),
        llm_prior_max_hypotheses=_env_int(
            "LLM_PRIOR_MAX_HYPOTHESES",
            DEFAULT_LLM_PRIOR_MAX_HYPOTHESES,
        ),
        llm_prior_max_detector_prompts=_env_int(
            "LLM_PRIOR_MAX_DETECTOR_PROMPTS",
            DEFAULT_LLM_PRIOR_MAX_DETECTOR_PROMPTS,
        ),
        llm_prior_output_language=_env_value(
            "LLM_PRIOR_OUTPUT_LANGUAGE",
            DEFAULT_LLM_PRIOR_OUTPUT_LANGUAGE,
        ),
        evidence_gating_enabled=_env_bool(
            "EVIDENCE_GATING_ENABLED",
            DEFAULT_EVIDENCE_GATING_ENABLED,
        ),
        target_confirmation_require_visual_evidence=_env_bool(
            "TARGET_CONFIRMATION_REQUIRE_VISUAL_EVIDENCE",
            DEFAULT_TARGET_CONFIRMATION_REQUIRE_VISUAL_EVIDENCE,
        ),
        target_confirmation_require_bbox=_env_bool(
            "TARGET_CONFIRMATION_REQUIRE_BBOX",
            DEFAULT_TARGET_CONFIRMATION_REQUIRE_BBOX,
        ),
        target_confirmation_require_crop_verify=_env_bool(
            "TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY",
            DEFAULT_TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY,
        ),
        target_confirmation_require_mask=_env_bool(
            "TARGET_CONFIRMATION_REQUIRE_MASK",
            DEFAULT_TARGET_CONFIRMATION_REQUIRE_MASK,
        ),
        target_confirmation_min_score=_env_float(
            "TARGET_CONFIRMATION_MIN_SCORE",
            DEFAULT_TARGET_CONFIRMATION_MIN_SCORE,
        ),
        target_inferred_is_not_found=_env_bool(
            "TARGET_INFERRED_IS_NOT_FOUND",
            DEFAULT_TARGET_INFERRED_IS_NOT_FOUND,
        ),
        observation_memory_enabled=_env_bool(
            "OBSERVATION_MEMORY_ENABLED",
            DEFAULT_OBSERVATION_MEMORY_ENABLED,
        ),
        observation_memory_store_path=_env_value(
            "OBSERVATION_MEMORY_STORE_PATH",
            DEFAULT_OBSERVATION_MEMORY_STORE_PATH,
        ),
        observation_memory_write_visual_only=_env_bool(
            "OBSERVATION_MEMORY_WRITE_VISUAL_ONLY",
            DEFAULT_OBSERVATION_MEMORY_WRITE_VISUAL_ONLY,
        ),
        observation_memory_allow_llm_summary=_env_bool(
            "OBSERVATION_MEMORY_ALLOW_LLM_SUMMARY",
            DEFAULT_OBSERVATION_MEMORY_ALLOW_LLM_SUMMARY,
        ),
        observation_memory_llm_summary_as_hypothesis=_env_bool(
            "OBSERVATION_MEMORY_LLM_SUMMARY_AS_HYPOTHESIS",
            DEFAULT_OBSERVATION_MEMORY_LLM_SUMMARY_AS_HYPOTHESIS,
        ),
        observation_memory_retrieval_top_k=_env_int(
            "OBSERVATION_MEMORY_RETRIEVAL_TOP_K",
            DEFAULT_OBSERVATION_MEMORY_RETRIEVAL_TOP_K,
        ),
        observation_memory_require_provenance=_env_bool(
            "OBSERVATION_MEMORY_REQUIRE_PROVENANCE",
            DEFAULT_OBSERVATION_MEMORY_REQUIRE_PROVENANCE,
        ),
        prior_usage_audit_enabled=_env_bool(
            "PRIOR_USAGE_AUDIT_ENABLED",
            DEFAULT_PRIOR_USAGE_AUDIT_ENABLED,
        ),
        prior_usage_report_path=_env_value(
            "PRIOR_USAGE_REPORT_PATH",
            DEFAULT_PRIOR_USAGE_REPORT_PATH,
        ),
    )
