"""Streamlit UI for the robot scene understanding demo."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st
from openai import OpenAIError
from pydantic import ValidationError

from app.config import DEFAULT_OUTPUT_DIR, SettingsError, get_settings
from app.detectors.grounded_sam_subprocess import (
    DetectorRuntimeError,
    GroundedSAMSubprocessDetector,
)
from app.knowledge.retrieval import retrieve_relevant_knowledge
from app.llm_clients.siliconflow_client import SiliconFlowVisionClient
from app.reasoning.task_parser import parse_robot_task
from app.schemas import (
    KnowledgeAwareSceneResult,
    PredictiveSceneGraph,
    RobotTask,
    SceneAnalysisResult,
)
from app.services.knowledge_aware_analyzer import KnowledgeAwareAnalyzer
from app.services.accuracy_pipeline import enhance_image_result
from app.services.knowledge_output_writer import write_knowledge_aware_outputs
from app.services.detector_scene_builder import build_scene_from_detections
from app.services.output_writer import prepare_analysis_result, write_analysis_outputs
from app.services.psg_builder import (
    build_predictive_scene_graph,
    export_predictive_scene_graph_graphml,
)
from app.services.route_planner import format_route_plan
from app.services.scene_analyzer import SceneAnalyzer
from app.services.target_matcher import format_target_decision
from app.video.pipeline import run_video_search
from app.video.target_profile import TargetProfileResolver
from app.video.video_reader import VideoReadError
from run_demo import _run_prior_free_runtime


MOCK_PATH = Path("examples/mock_scene_result.json")
UPLOAD_DIR = Path(DEFAULT_OUTPUT_DIR) / "uploads"
VIDEO_UPLOAD_DIR = Path(DEFAULT_OUTPUT_DIR) / "video_uploads"

TASK_PRESETS = [
    {
        "label": "find_object | 找可见目标 | 桌子上的手机",
        "text": "桌子上的手机",
        "caption": "用于验证目标已经在画面里时的识别、匹配和靠近计划。",
    },
    {
        "label": "find_object | 找不可见目标 | 找到手机",
        "text": "找到手机",
        "caption": "用于验证目标不可见时，系统是否会根据桌面、键盘等线索生成候选位置。",
    },
    {
        "label": "count_objects | 计数任务 | 数椅子",
        "text": "数数这个房间里有几个椅子",
        "caption": "用于验证当前视野计数、遮挡区域提示和下一视角规划。",
    },
    {
        "label": "inspect_area + check_door_state | 巡查门状态 | 巡查开门",
        "text": "巡查这层楼看看有几个房间的门是打开的",
        "caption": "用于验证楼层巡查、门状态检查和按门序规划。",
    },
    {
        "label": "find_room | 找房间 | 找 503",
        "text": "找到 503 房间",
        "caption": "用于验证门牌/房间号解析、运行时推理和下一视角规划。",
    },
    {
        "label": "navigate_to_location | 导航到地点 | 走廊尽头",
        "text": "去走廊尽头的房间",
        "caption": "用于验证地点导航任务和走廊拓扑线索。",
    },
]


def main() -> None:
    st.set_page_config(page_title="机器狗场景理解工作台", layout="wide")
    _apply_page_style()

    st.title("机器狗场景理解工作台")

    settings = _render_sidebar()
    _render_workspace(settings)


def _render_sidebar() -> dict:
    with st.sidebar:
        st.header("任务配置")

        mode = st.radio(
            "运行模式",
            ["模拟数据", "真实 API", "GroundingDINO+SAM2", "视频目标搜索"],
            horizontal=False,
        )

        preset = st.selectbox(
            "任务模板",
            TASK_PRESETS,
            index=0,
            format_func=lambda item: item["label"],
        )
        if st.session_state.get("selected_task_preset") != preset["label"]:
            st.session_state["selected_task_preset"] = preset["label"]
            st.session_state["target_text"] = preset["text"]
        st.caption(preset["caption"])
        target_text = st.text_area(
            "目标描述",
            value=st.session_state.get("target_text", preset["text"]),
            height=84,
        ).strip()
        st.session_state["target_text"] = target_text

        uploaded_file = None
        uploaded_video = None
        sample_fps = 3.0
        max_frames = 300
        video_detector = "llm"
        enable_video_memory = True
        if mode == "视频目标搜索":
            uploaded_video = st.file_uploader(
                "上传机器狗第一视角视频",
                type=["mp4", "avi", "mov", "mkv"],
            )
            video_detector = st.selectbox(
                "视频检测器",
                ["llm", "grounded_sam", "mock"],
                format_func=lambda value: {
                    "mock": "Mock（流程验收）",
                    "llm": "视觉大模型 API",
                    "grounded_sam": "GroundingDINO+SAM2",
                }[value],
            )
            st.caption(
                "自然语言目标会先自动解析为开放词表目标画像；"
                "LLM 适合复杂属性/关系，GroundingDINO 适合精确框选。"
            )
            sample_fps = st.slider("关键帧采样 FPS", 0.2, 5.0, 3.0, 0.2)
            max_frames = st.slider("最大分析帧数", 10, 500, 300, 10)
            enable_video_memory = st.toggle(
                "视频场景长期记忆",
                value=True,
                help="即使目标未出现，也记录环境、负证据并生成视频 PSG。",
            )
        else:
            uploaded_file = st.file_uploader("场景图片", type=["jpg", "jpeg", "png"])

        st.header("增强选项")
        enable_knowledge = st.toggle("知识增强流程", value=True)
        show_psg = st.toggle("预测性场景图", value=True)
        enable_llm_reasoning = st.toggle("启用大模型情境推理", value=True)
        enable_reasoning_memory = st.toggle("使用长期经验记忆辅助推理", value=True)
        show_psg_sources = st.toggle("显示 PSG 节点来源", value=True)
        only_executable = st.toggle("只显示机械狗可执行建议", value=False)
        hide_unreachable = st.toggle("隐藏不可验证假设", value=False)
        high_precision = st.toggle("高精度复查", value=False)
        with st.expander("无人工先验设置", expanded=True):
            enable_llm_prior = st.toggle("大模型运行时常识先验", value=True)
            enable_observation_memory = st.toggle("观察记忆", value=True)
            enable_evidence_gating = st.toggle("视觉证据门控", value=True)
            disable_handwritten_priors = st.toggle("禁用人工静态先验", value=True)
            show_prior_audit = st.toggle("显示先验使用审计", value=True)
            st.caption("大模型常识只用于生成搜索假设，目标确认必须依赖 bbox/crop/mask/frame 等视觉证据。")
        with st.expander("高级识别设置", expanded=False):
            enable_target_profile = st.toggle("启用目标画像解析", value=True)
            enable_high_recall = st.toggle("启用高召回检测", value=True)
            enable_crop_verify = st.toggle("启用候选 crop 复核", value=True)
            enable_track_voting = st.toggle("启用视频 track-level 投票", value=True)
            box_threshold = st.slider(
                "GroundingDINO box threshold", 0.01, 0.50, 0.10, 0.01
            )
            text_threshold = st.slider(
                "GroundingDINO text threshold", 0.01, 0.50, 0.08, 0.01
            )
            max_candidates = st.slider("最大候选数量", 1, 100, 40, 1)

        runtime_settings = get_settings()
        with st.expander("运动视界设置", expanded=True):
            motion_profile = st.selectbox(
                "运动策略档位",
                [
                    "strict_safe",
                    "platform_assisted_indoor",
                    "platform_assisted_open_area",
                    "platform_assisted_auto",
                ],
                index=[
                    "strict_safe",
                    "platform_assisted_indoor",
                    "platform_assisted_open_area",
                    "platform_assisted_auto",
                ].index(runtime_settings.motion_horizon_profile)
                if runtime_settings.motion_horizon_profile
                in {
                    "strict_safe",
                    "platform_assisted_indoor",
                    "platform_assisted_open_area",
                    "platform_assisted_auto",
                }
                else 3,
                format_func=lambda value: {
                    "strict_safe": "严格安全 0.5m",
                    "platform_assisted_indoor": "平台避障室内",
                    "platform_assisted_open_area": "平台避障开放区域",
                    "platform_assisted_auto": "平台避障自动",
                }[value],
            )
            platform_obstacle_avoidance = st.toggle(
                "假设机械狗已有基础避障",
                value=runtime_settings.platform_obstacle_avoidance_assumed,
            )
            enable_dynamic_motion_horizon = st.toggle(
                "启用自适应移动视界",
                value=runtime_settings.enable_dynamic_motion_horizon,
            )
            max_open_step = st.slider(
                "开放区域最大移动距离",
                1.0,
                6.0,
                float(runtime_settings.motion_platform_open_max_step_m),
                0.1,
            )
            max_indoor_step = st.slider(
                "室内最大移动距离",
                0.5,
                3.0,
                float(runtime_settings.motion_platform_indoor_max_step_m),
                0.1,
            )

        analyze_clicked = st.button(
            "开始分析",
            type="primary",
            use_container_width=True,
        )

        st.divider()
        st.caption(f"输出目录：{DEFAULT_OUTPUT_DIR}")

    return {
        "mode": mode,
        "target_text": target_text,
        "uploaded_file": uploaded_file,
        "uploaded_video": uploaded_video,
        "video_detector": video_detector,
        "sample_fps": sample_fps,
        "max_frames": max_frames,
        "enable_video_memory": enable_video_memory,
        "high_precision": high_precision,
        "enable_target_profile": enable_target_profile,
        "enable_high_recall": enable_high_recall,
        "enable_crop_verify": enable_crop_verify,
        "enable_track_voting": enable_track_voting,
        "box_threshold": box_threshold,
        "text_threshold": text_threshold,
        "max_candidates": max_candidates,
        "enable_knowledge": enable_knowledge,
        "show_psg": show_psg,
        "enable_llm_reasoning": enable_llm_reasoning,
        "enable_reasoning_memory": enable_reasoning_memory,
        "show_psg_sources": show_psg_sources,
        "only_executable": only_executable,
        "hide_unreachable": hide_unreachable,
        "enable_llm_prior": enable_llm_prior,
        "enable_observation_memory": enable_observation_memory,
        "enable_evidence_gating": enable_evidence_gating,
        "disable_handwritten_priors": disable_handwritten_priors,
        "show_prior_audit": show_prior_audit,
        "motion_profile": motion_profile,
        "platform_obstacle_avoidance": platform_obstacle_avoidance,
        "enable_dynamic_motion_horizon": enable_dynamic_motion_horizon,
        "max_open_step": max_open_step,
        "max_indoor_step": max_indoor_step,
        "analyze_clicked": analyze_clicked,
    }


def _render_workspace(settings: dict) -> None:
    preview_col, status_col = st.columns([1.1, 1.4], gap="large")
    with preview_col:
        _render_input_preview(
            settings["uploaded_file"],
            settings["mode"],
            settings["uploaded_video"],
        )
    with status_col:
        _render_runtime_status(settings)

    if settings["analyze_clicked"]:
        _run_and_render(settings)
    elif settings["mode"] == "视频目标搜索":
        _render_existing_video_outputs()
    else:
        _render_existing_outputs(
            target_text=settings["target_text"],
            show_psg=settings["show_psg"],
            enable_knowledge=settings["enable_knowledge"],
            ui_settings=settings,
        )


def _runtime_settings_from_ui(ui_settings: dict):
    base = get_settings()
    updates = {
        "motion_horizon_profile": ui_settings.get(
            "motion_profile", getattr(base, "motion_horizon_profile", "platform_assisted_auto")
        ),
        "platform_obstacle_avoidance_assumed": ui_settings.get(
            "platform_obstacle_avoidance",
            getattr(base, "platform_obstacle_avoidance_assumed", True),
        ),
        "enable_dynamic_motion_horizon": ui_settings.get(
            "enable_dynamic_motion_horizon",
            getattr(base, "enable_dynamic_motion_horizon", True),
        ),
        "motion_platform_open_max_step_m": ui_settings.get(
            "max_open_step",
            getattr(base, "motion_platform_open_max_step_m", 5.0),
        ),
        "motion_platform_indoor_max_step_m": ui_settings.get(
            "max_indoor_step",
            getattr(base, "motion_platform_indoor_max_step_m", 2.0),
        ),
        "llm_commonsense_prior_enabled": ui_settings.get(
            "enable_llm_prior",
            getattr(base, "llm_commonsense_prior_enabled", True),
        ),
        "observation_memory_enabled": ui_settings.get(
            "enable_observation_memory",
            getattr(base, "observation_memory_enabled", True),
        ),
        "evidence_gating_enabled": ui_settings.get(
            "enable_evidence_gating",
            getattr(base, "evidence_gating_enabled", True),
        ),
        "handwritten_object_priors_enabled": (
            False
            if ui_settings.get("disable_handwritten_priors", True)
            else getattr(base, "handwritten_object_priors_enabled", False)
        ),
        "handwritten_location_priors_enabled": (
            False
            if ui_settings.get("disable_handwritten_priors", True)
            else getattr(base, "handwritten_location_priors_enabled", False)
        ),
        "handwritten_room_priors_enabled": (
            False
            if ui_settings.get("disable_handwritten_priors", True)
            else getattr(base, "handwritten_room_priors_enabled", False)
        ),
        "allow_handcrafted_search_rules": (
            False
            if ui_settings.get("disable_handwritten_priors", True)
            else getattr(base, "allow_handcrafted_search_rules", False)
        ),
        "static_knowledge_base_enabled": False,
        "static_object_prompts_enabled": getattr(
            base, "static_object_prompts_enabled", False
        ),
        "prior_usage_audit_enabled": ui_settings.get(
            "show_prior_audit",
            getattr(base, "prior_usage_audit_enabled", True),
        ),
        "llm_prior_output_language": getattr(base, "llm_prior_output_language", "zh"),
        "llm_prior_max_hypotheses": getattr(base, "llm_prior_max_hypotheses", 8),
        "llm_prior_max_detector_prompts": getattr(
            base, "llm_prior_max_detector_prompts", 12
        ),
        "target_confirmation_require_visual_evidence": getattr(
            base, "target_confirmation_require_visual_evidence", True
        ),
        "target_confirmation_require_bbox": getattr(
            base, "target_confirmation_require_bbox", True
        ),
        "target_confirmation_require_crop_verify": getattr(
            base, "target_confirmation_require_crop_verify", True
        ),
        "target_confirmation_require_mask": getattr(
            base, "target_confirmation_require_mask", False
        ),
        "target_confirmation_min_score": getattr(
            base, "target_confirmation_min_score", 0.72
        ),
        "observation_memory_store_path": getattr(
            base,
            "observation_memory_store_path",
            "data/memory/observational_memory.jsonl",
        ),
        "observation_memory_retrieval_top_k": getattr(
            base, "observation_memory_retrieval_top_k", 10
        ),
        "observation_memory_write_visual_only": getattr(
            base, "observation_memory_write_visual_only", True
        ),
        "observation_memory_require_provenance": getattr(
            base, "observation_memory_require_provenance", True
        ),
        "prior_usage_report_path": getattr(
            base, "prior_usage_report_path", "outputs/prior_usage_report.json"
        ),
    }
    return _replace_settings_compat(base, updates)


def _replace_settings_compat(base, updates: dict):
    fields = getattr(base, "__dataclass_fields__", {})
    known_updates = {key: value for key, value in updates.items() if key in fields}
    settings = replace(base, **known_updates)
    for key, value in updates.items():
        if not hasattr(settings, key):
            object.__setattr__(settings, key, value)
    return settings


def _render_input_preview(uploaded_file, mode: str, uploaded_video=None) -> None:
    st.subheader("输入预览")
    if mode == "视频目标搜索":
        if uploaded_video is not None:
            st.video(uploaded_video)
        else:
            st.info("上传第一视角视频后即可构建视频语义记忆。")
        return
    if uploaded_file is not None:
        st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)
        return
    if mode == "模拟数据":
        st.info("当前使用内置 mock 场景，可直接点击开始分析。")
    else:
        st.warning("真实 API 或本地检测模式需要上传图片。")


def _render_runtime_status(settings: dict) -> None:
    parsed_task = None
    try:
        parsed_task = parse_robot_task(settings["target_text"])
    except ValueError:
        pass

    st.subheader("运行状态")
    metric_cols = st.columns(4)
    metric_cols[0].metric("模式", settings["mode"])
    metric_cols[1].metric("知识增强", "开" if settings["enable_knowledge"] else "关")
    metric_cols[2].metric("PSG", "开" if settings["show_psg"] else "关")
    metric_cols[3].metric("任务类型", parsed_task.task_type if parsed_task else "-")

    if parsed_task is not None:
        with st.expander("当前任务解析", expanded=True):
            st.json(parsed_task.model_dump(mode="json"))


def _run_and_render(settings: dict) -> None:
    try:
        if not settings["target_text"]:
            raise ValueError("目标描述不能为空。")
        if settings["mode"] == "视频目标搜索":
            _run_video_and_render(settings)
            return

        with st.spinner("正在分析场景..."):
            runtime_settings = _runtime_settings_from_ui(settings)
            result = (
                _run_mock()
                if settings["mode"] == "模拟数据"
                else _run_api(
                    settings["uploaded_file"],
                    settings["target_text"],
                    settings["high_precision"],
                    use_grounded_sam=(settings["mode"] == "GroundingDINO+SAM2"),
                    ui_settings=settings,
                )
            )
            result = prepare_analysis_result(result)
            image_path = st.session_state.get("last_image_path")
            result, prior_free_paths = _run_prior_free_runtime(
                result,
                settings["target_text"],
                runtime_settings,
                Path(DEFAULT_OUTPUT_DIR),
                image_path=image_path,
            )
            paths = write_analysis_outputs(
                result,
                DEFAULT_OUTPUT_DIR,
                image_path=image_path,
                settings=runtime_settings,
            )
            paths.update(prior_free_paths)
            paths.update(
                {
                    key: Path(value)
                    for key, value in st.session_state.get(
                        "last_accuracy_paths", {}
                    ).items()
                }
            )

            knowledge_result = None
            if settings["enable_knowledge"]:
                visual_retry_callback = _build_ui_visual_retry_callback(settings)
                knowledge_result = KnowledgeAwareAnalyzer(
                    update_kb=(settings["mode"] != "模拟数据"),
                    enable_llm_reasoning=settings["enable_llm_reasoning"],
                    enable_reasoning_memory=settings["enable_reasoning_memory"],
                    quadruped_mode=True,
                    allow_remote_reasoning=(settings["mode"] != "模拟数据"),
                    hide_unexecutable_actions=settings["only_executable"],
                    visual_retry_callback=visual_retry_callback,
                    settings=runtime_settings,
                ).enrich_base_scene(result, settings["target_text"])
                knowledge_paths = write_knowledge_aware_outputs(
                    knowledge_result,
                    DEFAULT_OUTPUT_DIR,
                    image_path=image_path,
                )
                st.session_state["last_knowledge_paths"] = {
                    key: str(path) for key, path in knowledge_paths.items()
                }

        _render_result(
            result,
            paths,
            target_text=settings["target_text"],
            show_psg=settings["show_psg"] and knowledge_result is None,
        )
        if knowledge_result is not None:
            _render_knowledge_result(knowledge_result, settings)
    except OpenAIError as exc:
        st.error(f"API 请求失败：{exc}")
    except DetectorRuntimeError as exc:
        st.error(f"本地检测器运行失败：{exc}")
    except (SettingsError, FileNotFoundError, VideoReadError, RuntimeError, ValueError, ValidationError) as exc:
        st.error(f"错误：{exc}")


def _run_video_and_render(settings: dict) -> None:
    uploaded_video = settings["uploaded_video"]
    if uploaded_video is None:
        raise ValueError("视频目标搜索模式需要上传视频。")

    VIDEO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(uploaded_video.name).name
    video_path = (VIDEO_UPLOAD_DIR / safe_name).resolve()
    video_path.write_bytes(uploaded_video.getbuffer())

    progress = st.progress(0.0, text="正在读取视频...")

    def update_progress(done: int, total: int, message: str) -> None:
        progress.progress(done / max(1, total), text=message)

    result, paths = run_video_search(
        video_path=video_path,
        target=settings["target_text"],
        detector=settings["video_detector"],
        sample_fps=settings["sample_fps"],
        max_frames=settings["max_frames"],
        enable_knowledge=settings["enable_knowledge"],
        enable_video_memory=settings["enable_video_memory"],
        output_dir=DEFAULT_OUTPUT_DIR,
        annotate=True,
        progress_callback=update_progress,
        enable_tracking=settings["enable_track_voting"],
        enable_crop_verify=settings["enable_crop_verify"],
        track_iou_threshold=0.35,
        enable_llm_prior=settings["enable_llm_prior"],
        enable_observation_memory=settings["enable_observation_memory"],
        enable_evidence_gating=settings["enable_evidence_gating"],
        disable_handwritten_priors=settings["disable_handwritten_priors"],
        disable_static_kb=True,
        prior_audit=settings["show_prior_audit"],
    )
    progress.progress(1.0, text="视频目标搜索完成")
    _render_video_result(result, paths)


def _render_video_result(result: dict, paths: dict[str, Path]) -> None:
    st.divider()
    st.subheader("视频目标搜索结果")
    meta = result["video_meta"]
    metric_cols = st.columns(5)
    metric_cols[0].metric("目标", "已找到" if result["target_found"] else "未发现")
    metric_cols[1].metric("视频时长", f"{meta['duration_sec']:.1f}s")
    metric_cols[2].metric("关键帧", meta["sampled_keyframes"])
    metric_cols[3].metric("失败帧", result.get("processing", {}).get("failed_frames", 0))
    metric_cols[4].metric(
        "长期记忆",
        result.get("environment_memories_written", 0),
    )

    best = result.get("best_evidence")
    conclusion_col, evidence_col = st.columns([1.0, 1.2], gap="large")
    with conclusion_col:
        st.markdown("#### 结论与建议")
        profile = result.get("target_profile") or {}
        if profile:
            with st.expander("自然语言目标画像", expanded=False):
                st.json(profile)
        if best:
            st.success(f"目标最清楚地出现在 {best['timestamp_sec']:.2f}s。")
            st.write(best["description"])
        else:
            if result.get("memory_updates_generated", 0) > 0:
                st.warning("未直接发现目标，但已生成环境记忆和 PSG 假设。")
            else:
                st.warning("采样帧中未直接发现目标。")
            st.code(result.get("reason", ""), language=None)
        if result.get("final_summary"):
            st.write(result["final_summary"])
        st.write(result["navigation_interpretation"]["suggestion"])
        st.caption(result["navigation_interpretation"]["reason"])
    with evidence_col:
        st.markdown("#### 最佳证据帧")
        if best and best.get("annotated_frame_path"):
            _render_image_path(Path(best["annotated_frame_path"]))
        else:
            st.info("没有可展示的直接目标证据帧。")

    (
        tab_memory,
        tab_negative,
        tab_psg,
        tab_timeline,
        tab_tracks,
        tab_crops,
        tab_regions,
        tab_context,
        tab_llm_prior,
        tab_dynamic_prompts,
        tab_evidence_gate,
        tab_observation_memory,
        tab_prior_audit,
        tab_json,
        tab_files,
    ) = st.tabs(
        [
            "环境记忆",
            "负目标证据",
            "PSG 假设",
            "时间线",
            "Track 结果",
            "候选 crop",
            "候选区域",
            "附近参照物",
            "LLM 自生成常识",
            "动态检测词表",
            "视觉证据门控",
            "观察记忆更新",
            "先验使用审计",
            "JSON",
            "输出文件",
        ]
    )
    with tab_memory:
        memories = result.get("observed_places", [])
        if memories:
            rows = [
                {
                    "时间": (
                        f"{item['time_range'][0]:.2f}-{item['time_range'][1]:.2f}s"
                        if len(item.get("time_range", [])) == 2
                        else "-"
                    ),
                    "环境类型": item.get("room_type"),
                    "场景摘要": item.get("summary"),
                    "稳定参照物": "、".join(item.get("stable_landmarks", [])),
                    "目标出现": "是" if item.get("target_found") else "否",
                    "重要性": item.get("importance"),
                }
                for item in memories
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            stats = result.get("memory_statistics", {})
            st.caption(
                f"本次生成 {stats.get('generated_count', 0)} 条，"
                f"实际写入 {stats.get('written_count', 0)} 条；"
                f"记忆库：{result.get('memory_store_path', '-')}"
            )
        else:
            st.info("当前结果中没有环境记忆。")
    with tab_negative:
        negative = result.get("negative_evidence", [])
        if negative:
            st.dataframe(
                pd.DataFrame(negative),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("没有负目标证据。")
    with tab_psg:
        hypotheses = result.get("psg_hypotheses", [])
        if hypotheses:
            rows = [
                {
                    "假设": item.get("summary"),
                    "类型": item.get("type"),
                    "关联区域": item.get("related_place"),
                    "置信度": item.get("confidence"),
                    "证据": "；".join(item.get("supporting_evidence", [])),
                }
                for item in hypotheses
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("没有 PSG 假设。")
    with tab_timeline:
        timeline = result.get("timeline", [])
        if timeline:
            st.dataframe(pd.DataFrame(timeline), use_container_width=True, hide_index=True)
        else:
            st.info("时间线上没有目标或候选线索。")
    with tab_tracks:
        tracks_path = paths.get("video_object_tracks")
        if tracks_path and Path(tracks_path).is_file():
            tracks = json.loads(Path(tracks_path).read_text(encoding="utf-8"))
            rows = [
                {
                    "track_id": item.get("track_id"),
                    "label": item.get("label"),
                    "decision": item.get("decision"),
                    "final_score": item.get("final_score"),
                    "first_seen_sec": item.get("first_seen_sec"),
                    "last_seen_sec": item.get("last_seen_sec"),
                    "frame_count": item.get("frame_count"),
                    "best_frame": item.get("best_frame"),
                    "evidence": "；".join(item.get("evidence", [])),
                }
                for item in tracks
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("没有 track 级结果。")
    with tab_crops:
        crop_dir = Path(DEFAULT_OUTPUT_DIR) / "video_crops"
        crop_paths = sorted(crop_dir.glob("*.jpg")) if crop_dir.is_dir() else []
        if crop_paths:
            st.image([str(path) for path in crop_paths[:20]], width=180)
        else:
            st.info("没有候选 crop。")
    with tab_regions:
        regions = result.get("candidate_regions", [])
        if regions:
            st.dataframe(pd.DataFrame(regions), use_container_width=True, hide_index=True)
        else:
            st.info("没有足够强的候选区域。")
    with tab_context:
        if best and best.get("nearby_objects"):
            st.dataframe(
                pd.DataFrame(best["nearby_objects"]),
                use_container_width=True,
                hide_index=True,
            )
        elif result.get("candidate_regions"):
            rows = [
                {
                    "时间": region["timestamp_sec"],
                    "优先级": region["priority"],
                    "候选参照物": "、".join(region.get("nearby_objects", [])),
                    "原因": region["reason"],
                }
                for region in result["candidate_regions"]
            ]
            st.info("目标尚未直接确认，以下展示候选区域的视觉参照物。")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("没有直接目标的附近参照物。")
    with tab_llm_prior:
        _render_json_file(paths.get("video_llm_generated_priors"))
    with tab_dynamic_prompts:
        _render_json_file(paths.get("video_dynamic_detector_prompts"))
    with tab_evidence_gate:
        _render_json_file(paths.get("video_evidence_gating_report"))
    with tab_observation_memory:
        _render_json_file(paths.get("video_observation_memory_updates"))
    with tab_prior_audit:
        _render_json_file(paths.get("video_prior_usage_report"))
    with tab_json:
        st.json(result)
        if "video_target_search" in paths:
            _render_file_download(paths["video_target_search"], "下载搜索结果 JSON")
        if "video_reasoning_report" in paths:
            _render_file_download(paths["video_reasoning_report"], "下载 Markdown 报告")
        if "video_hypotheses" in paths:
            _render_file_download(paths["video_hypotheses"], "下载 PSG 假设 JSON")
        if "video_predictive_scene_graph" in paths:
            _render_file_download(
                paths["video_predictive_scene_graph"],
                "下载视频 PSG GraphML",
            )
    with tab_files:
        _render_output_files(paths, group="video")


def _render_existing_video_outputs() -> None:
    result_path = Path(DEFAULT_OUTPUT_DIR) / "video_target_search.json"
    if not result_path.is_file():
        st.info("还没有历史视频输出。上传视频后点击开始分析。")
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        st.warning("历史视频输出无法读取，请重新运行分析。")
        return
    path_names = {
        "video_target_profile": "video_target_profile.json",
        "video_target_search": "video_target_search.json",
        "video_target_timeline": "video_target_timeline.json",
        "video_object_tracks": "video_object_tracks.json",
        "video_track_summary": "video_track_summary.json",
        "video_crop_verify_results": "video_crop_verify_results.json",
        "video_tracking_debug_report": "video_tracking_debug_report.md",
        "video_candidate_regions": "video_candidate_regions.json",
        "video_memory_graph_json": "video_memory_graph.json",
        "video_memory_graph_graphml": "video_memory_graph.graphml",
        "video_memory_updates": "video_memory_updates.json",
        "video_spatial_memory_snapshot": "video_spatial_memory_snapshot.json",
        "video_predictive_scene_graph": "video_predictive_scene_graph.graphml",
        "video_predictive_scene_graph_json": "video_predictive_scene_graph.json",
        "video_hypotheses": "video_hypotheses.json",
        "video_navigation_trace": "video_navigation_trace.json",
        "video_reasoning_report": "video_reasoning_report.md",
        "video_llm_generated_priors": "video_llm_generated_priors.json",
        "video_dynamic_detector_prompts": "video_dynamic_detector_prompts.json",
        "video_evidence_gating_report": "video_evidence_gating_report.json",
        "video_observation_memory_updates": "video_observation_memory_updates.json",
        "video_prior_usage_report": "video_prior_usage_report.json",
    }
    paths = {
        key: Path(DEFAULT_OUTPUT_DIR) / filename
        for key, filename in path_names.items()
        if (Path(DEFAULT_OUTPUT_DIR) / filename).is_file()
    }
    _render_video_result(result, paths)


def _run_mock() -> SceneAnalysisResult:
    data = json.loads(MOCK_PATH.read_text(encoding="utf-8"))
    return SceneAnalysisResult.model_validate(data)


def _run_api(
    uploaded_file,
    target_text: str,
    high_precision: bool,
    use_grounded_sam: bool,
    ui_settings: dict | None = None,
) -> SceneAnalysisResult:
    if uploaded_file is None:
        raise ValueError("真实 API 或本地检测模式需要上传图片。")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    image_path = (UPLOAD_DIR / uploaded_file.name).resolve()
    image_path.write_bytes(uploaded_file.getbuffer())
    st.session_state["last_image_path"] = str(image_path)

    ui_settings = ui_settings or {}
    settings = _runtime_settings_from_ui(ui_settings)
    settings = replace(
        settings,
        enable_target_profile=ui_settings.get(
            "enable_target_profile", settings.enable_target_profile
        ),
        enable_gdino_high_recall=ui_settings.get(
            "enable_high_recall", settings.enable_gdino_high_recall
        ),
        grounding_dino_box_threshold=ui_settings.get(
            "box_threshold", settings.grounding_dino_box_threshold
        ),
        grounding_dino_text_threshold=ui_settings.get(
            "text_threshold", settings.grounding_dino_text_threshold
        ),
        crop_verify_max_candidates=ui_settings.get(
            "max_candidates", settings.crop_verify_max_candidates
        ),
    )
    profile = TargetProfileResolver(settings=settings).resolve(
        target_text.strip(),
        use_llm=settings.enable_target_profile,
    )
    if use_grounded_sam:
        analyzer = SceneAnalyzer(
            object_detector=GroundedSAMSubprocessDetector(
                settings,
                target_profile=profile,
            ),
            output_dir=settings.output_dir,
        )
    else:
        analyzer = SceneAnalyzer(
            llm_client=SiliconFlowVisionClient(settings=settings),
            output_dir=settings.output_dir,
            enable_low_object_retry=(
                high_precision or settings.enable_low_object_retry
            ),
            min_objects_for_complex_scene=settings.min_objects_for_complex_scene,
        )
    result = analyzer.analyze(
        str(image_path),
        target_text.strip(),
        extra_instructions=(
            profile.prompt_context() if not use_grounded_sam else None
        ),
    )
    result, accuracy_paths = enhance_image_result(
        result,
        image_path,
        profile,
        settings,
        settings.output_dir,
        enable_crop_verify=ui_settings.get(
            "enable_crop_verify", settings.enable_crop_verify
        ),
    )
    st.session_state["last_accuracy_paths"] = {
        key: str(path) for key, path in accuracy_paths.items()
    }
    return result


def _build_ui_visual_retry_callback(settings: dict):
    if settings["mode"] == "模拟数据":
        return None
    image_path_value = st.session_state.get("last_image_path")
    if not image_path_value:
        return None
    image_path = str(image_path_value)
    target_text = settings["target_text"]
    runtime = get_settings()
    runtime = replace(
        runtime,
        enable_target_profile=settings.get(
            "enable_target_profile", runtime.enable_target_profile
        ),
        grounding_dino_box_threshold=settings.get(
            "box_threshold", runtime.grounding_dino_box_threshold
        ),
        grounding_dino_text_threshold=settings.get(
            "text_threshold", runtime.grounding_dino_text_threshold
        ),
    )
    profile = TargetProfileResolver(settings=runtime).resolve(
        target_text,
        use_llm=runtime.enable_target_profile,
    )
    if settings["mode"] == "GroundingDINO+SAM2":
        detector = GroundedSAMSubprocessDetector(
            runtime,
            target_profile=profile,
        )

        def grounded_retry(terms: list[str]) -> SceneAnalysisResult:
            detections = detector.detect_with_dynamic_terms(
                image_path,
                target_text,
                terms,
            )
            return build_scene_from_detections(detections, target_text)

        return grounded_retry

    client = SiliconFlowVisionClient(settings=runtime)

    def llm_retry(terms: list[str]) -> SceneAnalysisResult:
        payload = client.analyze_scene(
            image_path,
            target_text,
            extra_instructions=(
                "执行二次视觉复核。只把图像中明确可见的目标设为 is_present=true；"
                "上下文物体和关联线索不能代替目标本体。动态开放词表："
                + "、".join(terms)
            ),
        )
        return SceneAnalysisResult.model_validate(payload)

    return llm_retry


def _render_result(
    result: SceneAnalysisResult,
    paths: dict[str, Path],
    target_text: str | None = None,
    show_psg: bool = False,
) -> None:
    st.divider()
    st.subheader("场景结果")

    metric_cols = st.columns(4)
    metric_cols[0].metric("物体", len(result.objects))
    metric_cols[1].metric("关系", len(result.relations))
    metric_cols[2].metric(
        "目标",
        "已找到" if result.target_decision.is_present else "未确认",
    )
    metric_cols[3].metric("置信度", f"{result.target_decision.confidence:.2f}")

    summary_col, route_col = st.columns(2, gap="large")
    with summary_col:
        st.markdown("#### 场景摘要")
        st.write(result.scene_summary_zh)
        st.markdown("#### 目标判断")
        st.text(format_target_decision(result))
    with route_col:
        st.markdown("#### 路线规划")
        st.text(format_route_plan(result))

    parsed_task = parse_robot_task(target_text or result.target_decision.target_text)
    tab_names = ["物体", "关系", "拓扑", "标注", "任务"]
    if show_psg:
        tab_names.append("PSG")
    tab_names.extend(
        [
            "ROS2",
            "LLM 自生成常识",
            "动态检测词表",
            "视觉证据门控",
            "观察记忆更新",
            "先验使用审计",
            "JSON",
        ]
    )
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_dataframe(paths.get("object_table"))
        if result.candidate_objects:
            st.markdown("#### 候选目标列表")
            st.dataframe(
                pd.DataFrame(result.candidate_objects),
                use_container_width=True,
                hide_index=True,
            )
            crop_paths = [
                item.get("crop_path")
                for item in result.candidate_objects
                if item.get("crop_path")
                and item.get("decision") in {"confirmed", "candidate"}
            ]
            if crop_paths:
                st.markdown("#### 候选 crop 预览")
                st.image(crop_paths[:20], width=180)
    with tabs[1]:
        _render_dataframe(paths.get("relation_table"))
    with tabs[2]:
        _render_image_path(paths.get("topology_png"))
    with tabs[3]:
        annotated = paths.get("annotated_image")
        if annotated is not None:
            _render_image_path(annotated)
        else:
            st.info("当前结果没有可用原图路径，未生成标注图。")
    with tabs[4]:
        st.json(parsed_task.model_dump(mode="json"))

    next_tab_index = 5
    if show_psg:
        with tabs[next_tab_index]:
            psg, graphml_path = _build_and_export_psg(result, parsed_task)
            st.caption(f"节点数：{len(psg.nodes)} | 边数：{len(psg.edges)}")
            st.json(psg.model_dump(mode="json"))
            _render_file_download(graphml_path)
        next_tab_index += 1

    with tabs[next_tab_index]:
        _render_json_file(paths.get("ros2_motion_plan"))

    with tabs[next_tab_index + 1]:
        _render_json_file(paths.get("llm_generated_priors"))
    with tabs[next_tab_index + 2]:
        _render_json_file(paths.get("dynamic_detector_prompts"))
    with tabs[next_tab_index + 3]:
        _render_json_file(paths.get("evidence_gating_report"))
    with tabs[next_tab_index + 4]:
        _render_json_file(paths.get("observation_memory_updates"))
    with tabs[next_tab_index + 5]:
        _render_json_file(paths.get("prior_usage_report"))
    with tabs[next_tab_index + 6]:
        st.json(result.model_dump(mode="json"))

    _render_output_files(paths, group="base")


def _render_existing_outputs(
    target_text: str | None = None,
    show_psg: bool = False,
    enable_knowledge: bool = False,
    ui_settings: dict | None = None,
) -> None:
    scene_path = Path(DEFAULT_OUTPUT_DIR) / "scene_result.json"
    if not scene_path.is_file():
        st.info("还没有历史输出。可以直接运行模拟数据或上传图片分析。")
        return

    try:
        result = SceneAnalysisResult.model_validate(
            json.loads(scene_path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, ValidationError):
        st.warning("历史输出无法读取，请重新运行分析。")
        return

    result = prepare_analysis_result(result)
    image_path = st.session_state.get("last_image_path")
    runtime_settings = _runtime_settings_from_ui(ui_settings or {})
    paths = write_analysis_outputs(
        result,
        DEFAULT_OUTPUT_DIR,
        image_path=image_path,
        settings=runtime_settings,
    )
    for key, filename in {
        "llm_generated_priors": "llm_generated_priors.json",
        "dynamic_detector_prompts": "dynamic_detector_prompts.json",
        "evidence_gating_report": "evidence_gating_report.json",
        "observation_memory_updates": "observation_memory_updates.json",
        "prior_usage_report": "prior_usage_report.json",
    }.items():
        candidate = Path(DEFAULT_OUTPUT_DIR) / filename
        if candidate.is_file():
            paths[key] = candidate
    if not all(path.is_file() for path in paths.values()):
        return

    _render_result(
        result,
        paths,
        target_text=target_text,
        show_psg=show_psg and not enable_knowledge,
    )
    if enable_knowledge:
        ui_settings = ui_settings or {}
        knowledge_result = KnowledgeAwareAnalyzer(
            update_kb=False,
            enable_llm_reasoning=ui_settings.get("enable_llm_reasoning", True),
            enable_reasoning_memory=ui_settings.get("enable_reasoning_memory", True),
            quadruped_mode=True,
            allow_remote_reasoning=False,
            settings=runtime_settings,
        ).enrich_base_scene(
            result,
            target_text or result.target_decision.target_text,
        )
        knowledge_paths = write_knowledge_aware_outputs(
            knowledge_result,
            DEFAULT_OUTPUT_DIR,
            image_path=image_path,
        )
        st.session_state["last_knowledge_paths"] = {
            key: str(path) for key, path in knowledge_paths.items()
        }
        _render_knowledge_result(knowledge_result, ui_settings)


def _build_and_export_psg(
    result: SceneAnalysisResult,
    task: RobotTask,
) -> tuple[PredictiveSceneGraph, Path]:
    runtime = get_settings()
    knowledge = (
        retrieve_relevant_knowledge(
            target_text=task.raw_text,
            kb_dir=Path("data/scene_kb"),
        )
        if runtime.static_knowledge_base_enabled
        else []
    )
    psg = build_predictive_scene_graph(result, knowledge, task)
    graphml_path = export_predictive_scene_graph_graphml(
        psg,
        Path(DEFAULT_OUTPUT_DIR) / "predictive_scene_graph.graphml",
    )
    return psg, graphml_path


def _render_knowledge_result(
    result: KnowledgeAwareSceneResult,
    ui_settings: dict | None = None,
) -> None:
    ui_settings = ui_settings or {}
    st.divider()
    st.subheader("知识增强工作台")
    st.write(result.final_answer_zh)

    metric_cols = st.columns(4)
    metric_cols[0].metric("知识项", len(result.retrieved_knowledge))
    metric_cols[1].metric("PSG 节点", len(result.predictive_scene_graph.nodes))
    metric_cols[2].metric("假设", len(result.hypotheses))
    metric_cols[3].metric("计划步骤", len(result.task_plan.steps))

    (
        tab_observed,
        tab_reasoning,
        tab_motion,
        tab_psg,
        tab_plan,
        tab_unreachable,
        tab_memory,
    ) = st.tabs(
        [
            "视觉观察结果",
            "大模型情境推理",
            "运动视界决策",
            "PSG 来源可视化",
            "机械狗下一视角计划",
            "不可验证/需人工区域",
            "长期经验写入记录",
        ]
    )
    with tab_observed:
        _render_observed_nodes(result)
    with tab_reasoning:
        _render_llm_reasoning(result, ui_settings)
    with tab_motion:
        _render_motion_horizon_decision(result)
    with tab_psg:
        graph = result.reasoned_predictive_scene_graph
        reasoned_image = st.session_state.get(
            "last_knowledge_paths", {}
        ).get("reasoned_annotated_scene")
        if reasoned_image:
            st.markdown("#### 来源与状态标注图")
            _render_image_path(Path(reasoned_image))
        if graph is None:
            st.info("未启用 LLM 情境推理，当前仅有旧版 PSG。")
            st.json(result.predictive_scene_graph.model_dump(mode="json"))
        elif ui_settings.get("show_psg_sources", True):
            st.graphviz_chart(
                _reasoned_graph_dot(graph),
                use_container_width=True,
            )
            st.dataframe(
                pd.DataFrame(
                    [node.model_dump(mode="json") for node in graph.nodes]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.json(
                {
                    "nodes": [
                        {
                            "node_id": node.node_id,
                            "label_zh": node.label_zh,
                            "node_type": node.node_type,
                            "observation_status": node.observation_status,
                        }
                        for node in graph.nodes
                    ],
                    "edges": [edge.model_dump(mode="json") for edge in graph.edges],
                }
            )
    with tab_plan:
        _render_quadruped_plan(result, ui_settings)
    with tab_unreachable:
        _render_unreachable_nodes(result)
    with tab_memory:
        if result.retrieved_experiences:
            st.markdown("#### 检索到的历史经验")
            st.dataframe(
                pd.DataFrame(result.retrieved_experiences),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("本次没有检索到相似经验。")
        if result.experience_writes:
            st.markdown("#### 本次写入的经验")
            st.dataframe(
                pd.DataFrame(result.experience_writes),
                use_container_width=True,
                hide_index=True,
            )
        elif not result.retrieved_experiences:
            st.caption("Mock 或关闭知识写入时不会新增经验。")

    paths = st.session_state.get("last_knowledge_paths", {})
    if paths:
        _render_output_files(
            {key: Path(path) for key, path in paths.items()},
            group="knowledge",
        )


def _render_dataframe(path: Path | None) -> None:
    if path is None or not path.is_file():
        st.info("文件尚未生成。")
        return
    st.dataframe(pd.read_csv(path), use_container_width=True, hide_index=True)


def _render_image_path(path: Path | None) -> None:
    if path is None or not path.is_file():
        st.info("图片尚未生成。")
        return
    st.image(str(path), use_container_width=True)


def _render_json_file(path: Path | None) -> None:
    if path is None or not path.is_file():
        st.info("文件尚未生成。")
        return
    try:
        st.json(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        st.warning("JSON 文件无法读取。")
        return
    _render_file_download(path)


def _render_knowledge_table(result: KnowledgeAwareSceneResult) -> None:
    rows = [
        {
            "id": item.id,
            "type": item.knowledge_type,
            "confidence": item.confidence,
            "content": item.content_zh,
        }
        for item in result.retrieved_knowledge
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("本次没有检索到相关知识。")


def _render_hypothesis_table(result: KnowledgeAwareSceneResult) -> None:
    rows = [
        {
            "id": item.hypothesis_id,
            "location": item.possible_location,
            "probability": item.probability,
            "status": item.status,
            "verification": item.verification_action,
        }
        for item in result.hypotheses
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("本次没有生成假设。")


def _render_observed_nodes(result: KnowledgeAwareSceneResult) -> None:
    facts = result.observed_facts
    if facts is None:
        st.info("当前结果没有 ObservedSceneFacts。")
        return
    rows = [
        {
            "节点名称": item.label_zh,
            "来源": item.source.value,
            "bbox": item.bbox,
            "置信度": item.confidence,
            "是否目标": item.object_id
            in result.base_scene.target_decision.matched_object_ids,
            "证据": f"视觉区域 {item.image_region}",
        }
        for item in facts.visible_anchors
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if not facts.target_observed:
        st.warning("目标当前未被视觉确认；以下推断不能改变 found 状态。")


def _render_llm_reasoning(
    result: KnowledgeAwareSceneResult,
    ui_settings: dict,
) -> None:
    reasoning = result.llm_reasoning
    if reasoning is None:
        st.info("大模型情境推理未启用。")
        return
    if not reasoning.reasoning_available:
        st.warning(
            "LLM 情境推理不可用，当前仅显示视觉锚点降级策略。"
        )
    report = result.visual_grounding_report
    if report:
        if report.get("upgraded_target"):
            st.success("二次视觉检测已将候选升级为 OBSERVED。")
        elif report.get("attempted"):
            st.info("已执行 LLM 动态检测词复核，但仍未视觉确认目标。")
        elif report.get("prompts"):
            st.caption("动态检测词已生成，但当前模式没有可用的二次视觉检测器。")
    rows = []
    for item in reasoning.hypotheses:
        if (
            ui_settings.get("hide_unreachable", False)
            and item.status.value == "unreachable"
        ):
            continue
        if (
            ui_settings.get("only_executable", False)
            and item.actionability.value
            in {"needs_human", "unsafe_or_impossible"}
        ):
            continue
        rows.append(
            {
                "候选区域": item.candidate_region_zh,
                "状态": item.status.value,
                "推理来源": "llm_situated_reasoning",
                "支持锚点": "、".join(item.supporting_visible_anchor_names),
                "推理依据": item.human_like_rationale_zh,
                "机械狗可验证性": item.actionability.value,
                "下一视角动作": " → ".join(
                    action.value for action in item.quadruped_view_strategy
                ),
                "不能标记为已找到": item.should_not_mark_found,
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("当前筛选条件下没有可展示的推理假设。")


def _render_quadruped_plan(
    result: KnowledgeAwareSceneResult,
    ui_settings: dict,
) -> None:
    plan = result.quadruped_search_plan
    if plan is None:
        st.info("没有生成机械狗下一视角计划。")
        return
    rows = [
        {
            "步骤": item.step_id,
            "动作原语": item.primitive.value,
            "画面区域": item.target_image_region,
            "原因": item.reason_zh,
            "运动视界": item.motion_horizon_m,
            "运动策略": item.motion_policy,
            "运动后重观测": item.requires_stop_observation,
            "信息增益": item.expected_information_gain,
            "安全级别": item.safety_level,
        }
        for item in plan.steps
        if not (
            ui_settings.get("only_executable", False)
            and item.safety_level == "human_required"
        )
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_motion_horizon_decision(result: KnowledgeAwareSceneResult) -> None:
    decision = result.motion_horizon_decision
    if decision is None:
        st.info("当前结果没有动态运动视界决策。")
        return
    cols = st.columns(4)
    cols[0].metric("运动策略", decision.motion_policy)
    cols[1].metric("最终移动", f"{decision.recommended_distance_m:.2f}m")
    cols[2].metric("规则上限", f"{decision.max_allowed_distance_m:.2f}m")
    cols[3].metric(
        "平台避障",
        "是" if decision.platform_obstacle_avoidance_assumed else "否",
    )
    rows = [
        {
            "字段": "profile",
            "值": str(decision.profile),
        },
        {
            "字段": "scene_type",
            "值": str(decision.scene_type),
        },
        {
            "字段": "task_phase",
            "值": str(decision.task_phase),
        },
        {
            "字段": "llm_recommended_horizon_m",
            "值": str(decision.llm_recommended_horizon_m),
        },
        {
            "字段": "original_requested_distance_m",
            "值": str(decision.original_requested_distance_m),
        },
        {
            "字段": "requires_stop_after_motion",
            "值": str(decision.requires_stop_after_motion),
        },
        {
            "字段": "observe_while_moving",
            "值": str(decision.observe_while_moving),
        },
        {
            "字段": "source",
            "值": str(decision.source),
        },
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.write(decision.decision_reason_zh)


def _render_unreachable_nodes(result: KnowledgeAwareSceneResult) -> None:
    reasoning = result.llm_reasoning
    if reasoning is None:
        st.info("没有不可验证假设。")
        return
    rows = [
        {
            "候选区域": item.candidate_region_zh,
            "为什么可能相关": item.human_like_rationale_zh,
            "为什么不能验证": item.uncertainty_zh,
            "建议处理": item.suggested_verification_question_zh,
        }
        for item in reasoning.hypotheses
        if item.status.value == "unreachable"
        or item.actionability.value in {"needs_human", "unsafe_or_impossible"}
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("本次没有需要人工介入或不可验证的区域。")


def _reasoned_graph_dot(graph) -> str:
    lines = [
        "digraph ReasonedPSG {",
        'rankdir="LR";',
        'graph [bgcolor="transparent"];',
        'node [shape="box", style="rounded,filled", fontname="sans-serif"];',
    ]
    for node in graph.nodes:
        style = "dashed,rounded,filled" if node.display_style == "dashed" else "rounded,filled"
        if node.display_style == "dotted":
            style = "dotted,rounded,filled"
        label = (
            f"{node.label_zh}\\n"
            f"{node.observation_status.value} | {node.source.value}"
        ).replace('"', '\\"')
        lines.append(
            f'"{node.node_id}" [label="{label}", style="{style}", '
            f'color="{node.border_color}", fillcolor="{node.fill_color}"];'
        )
    for edge in graph.edges:
        label = edge.relation_type.replace('"', '\\"')
        lines.append(
            f'"{edge.source_id}" -> "{edge.target_id}" '
            f'[label="{label}", style="{edge.line_style}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def _render_task_plan_table(result: KnowledgeAwareSceneResult) -> None:
    rows = [
        {
            "step": item.step_id,
            "action": item.action_type,
            "target": item.target,
            "description": item.description_zh,
            "confidence": item.confidence,
        }
        for item in result.task_plan.steps
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("任务规划没有步骤。")


def _render_output_files(paths: dict[str, Path], group: str = "outputs") -> None:
    st.markdown("#### 输出文件")
    for key, path in paths.items():
        cols = st.columns([3, 1])
        cols[0].code(str(path), language=None)
        with cols[1]:
            _render_file_download(
                path,
                label=f"下载 {key}",
                widget_key=f"download_{group}_{key}_{path}",
            )


def _render_file_download(
    path: Path,
    label: str = "下载文件",
    widget_key: str | None = None,
) -> None:
    if not path.is_file():
        st.caption("未生成")
        return
    st.download_button(
        label,
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/octet-stream",
        use_container_width=True,
        key=widget_key or f"download_{label}_{path}",
    )


def _apply_page_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
            max-width: 1360px;
        }
        div[data-testid="stMetric"] {
            border: 1px solid #e6e8eb;
            border-radius: 8px;
            padding: 0.7rem 0.8rem;
            background: #ffffff;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.15rem;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.25rem;
        }
        .stTabs [data-baseweb="tab"] {
            padding: 0.65rem 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
