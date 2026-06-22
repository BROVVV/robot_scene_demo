"""Streamlit UI for the robot scene understanding demo."""

from __future__ import annotations

import json
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
from app.services.knowledge_output_writer import write_knowledge_aware_outputs
from app.services.output_writer import prepare_analysis_result, write_analysis_outputs
from app.services.psg_builder import (
    build_predictive_scene_graph,
    export_predictive_scene_graph_graphml,
)
from app.services.route_planner import format_route_plan
from app.services.scene_analyzer import SceneAnalyzer
from app.services.target_matcher import format_target_decision


MOCK_PATH = Path("examples/mock_scene_result.json")
UPLOAD_DIR = Path(DEFAULT_OUTPUT_DIR) / "uploads"

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
        "caption": "用于验证门牌/房间号解析和楼层知识库检索。",
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
            ["模拟数据", "真实 API", "GroundingDINO+SAM2"],
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

        uploaded_file = st.file_uploader("场景图片", type=["jpg", "jpeg", "png"])

        st.header("增强选项")
        enable_knowledge = st.toggle("知识增强流程", value=True)
        show_psg = st.toggle("预测性场景图", value=True)
        high_precision = st.toggle("高精度复查", value=False)

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
        "high_precision": high_precision,
        "enable_knowledge": enable_knowledge,
        "show_psg": show_psg,
        "analyze_clicked": analyze_clicked,
    }


def _render_workspace(settings: dict) -> None:
    preview_col, status_col = st.columns([1.1, 1.4], gap="large")
    with preview_col:
        _render_input_preview(settings["uploaded_file"], settings["mode"])
    with status_col:
        _render_runtime_status(settings)

    if settings["analyze_clicked"]:
        _run_and_render(settings)
    else:
        _render_existing_outputs(
            target_text=settings["target_text"],
            show_psg=settings["show_psg"],
            enable_knowledge=settings["enable_knowledge"],
        )


def _render_input_preview(uploaded_file, mode: str) -> None:
    st.subheader("输入预览")
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

        with st.spinner("正在分析场景..."):
            result = (
                _run_mock()
                if settings["mode"] == "模拟数据"
                else _run_api(
                    settings["uploaded_file"],
                    settings["target_text"],
                    settings["high_precision"],
                    use_grounded_sam=(settings["mode"] == "GroundingDINO+SAM2"),
                )
            )
            result = prepare_analysis_result(result)
            image_path = st.session_state.get("last_image_path")
            paths = write_analysis_outputs(result, DEFAULT_OUTPUT_DIR, image_path=image_path)

            knowledge_result = None
            if settings["enable_knowledge"]:
                knowledge_result = KnowledgeAwareAnalyzer(
                    update_kb=(settings["mode"] != "模拟数据")
                ).enrich_base_scene(result, settings["target_text"])
                knowledge_paths = write_knowledge_aware_outputs(
                    knowledge_result,
                    DEFAULT_OUTPUT_DIR,
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
            _render_knowledge_result(knowledge_result)
    except OpenAIError as exc:
        st.error(f"API 请求失败：{exc}")
    except DetectorRuntimeError as exc:
        st.error(f"本地检测器运行失败：{exc}")
    except (SettingsError, FileNotFoundError, ValueError, ValidationError) as exc:
        st.error(f"错误：{exc}")


def _run_mock() -> SceneAnalysisResult:
    data = json.loads(MOCK_PATH.read_text(encoding="utf-8"))
    return SceneAnalysisResult.model_validate(data)


def _run_api(
    uploaded_file,
    target_text: str,
    high_precision: bool,
    use_grounded_sam: bool,
) -> SceneAnalysisResult:
    if uploaded_file is None:
        raise ValueError("真实 API 或本地检测模式需要上传图片。")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    image_path = (UPLOAD_DIR / uploaded_file.name).resolve()
    image_path.write_bytes(uploaded_file.getbuffer())
    st.session_state["last_image_path"] = str(image_path)

    settings = get_settings()
    if use_grounded_sam:
        analyzer = SceneAnalyzer(
            object_detector=GroundedSAMSubprocessDetector(settings),
            output_dir=settings.output_dir,
        )
    else:
        analyzer = SceneAnalyzer(
            llm_client=SiliconFlowVisionClient(settings=settings),
            output_dir=settings.output_dir,
            enable_low_object_retry=high_precision,
            min_objects_for_complex_scene=settings.min_objects_for_complex_scene,
        )
    return analyzer.analyze(str(image_path), target_text.strip())


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
    tab_names.extend(["ROS2", "JSON"])
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_dataframe(paths.get("object_table"))
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
        st.json(result.model_dump(mode="json"))

    _render_output_files(paths)


def _render_existing_outputs(
    target_text: str | None = None,
    show_psg: bool = False,
    enable_knowledge: bool = False,
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
    paths = write_analysis_outputs(result, DEFAULT_OUTPUT_DIR, image_path=image_path)
    if not all(path.is_file() for path in paths.values()):
        return

    _render_result(
        result,
        paths,
        target_text=target_text,
        show_psg=show_psg and not enable_knowledge,
    )
    if enable_knowledge:
        knowledge_result = KnowledgeAwareAnalyzer(update_kb=False).enrich_base_scene(
            result,
            target_text or result.target_decision.target_text,
        )
        knowledge_paths = write_knowledge_aware_outputs(
            knowledge_result,
            DEFAULT_OUTPUT_DIR,
        )
        st.session_state["last_knowledge_paths"] = {
            key: str(path) for key, path in knowledge_paths.items()
        }
        _render_knowledge_result(knowledge_result)


def _build_and_export_psg(
    result: SceneAnalysisResult,
    task: RobotTask,
) -> tuple[PredictiveSceneGraph, Path]:
    knowledge = retrieve_relevant_knowledge(
        target_text=task.raw_text,
        kb_dir=Path("data/scene_kb"),
    )
    psg = build_predictive_scene_graph(result, knowledge, task)
    graphml_path = export_predictive_scene_graph_graphml(
        psg,
        Path(DEFAULT_OUTPUT_DIR) / "predictive_scene_graph.graphml",
    )
    return psg, graphml_path


def _render_knowledge_result(result: KnowledgeAwareSceneResult) -> None:
    st.divider()
    st.subheader("知识增强工作台")
    st.write(result.final_answer_zh)

    metric_cols = st.columns(4)
    metric_cols[0].metric("知识项", len(result.retrieved_knowledge))
    metric_cols[1].metric("PSG 节点", len(result.predictive_scene_graph.nodes))
    metric_cols[2].metric("假设", len(result.hypotheses))
    metric_cols[3].metric("计划步骤", len(result.task_plan.steps))

    tab_task, tab_knowledge, tab_psg, tab_hypotheses, tab_plan, tab_updates = st.tabs(
        ["任务", "知识", "PSG", "假设", "任务规划", "知识更新"]
    )
    with tab_task:
        st.json(result.parsed_task.model_dump(mode="json"))
    with tab_knowledge:
        _render_knowledge_table(result)
    with tab_psg:
        st.json(result.predictive_scene_graph.model_dump(mode="json"))
    with tab_hypotheses:
        _render_hypothesis_table(result)
    with tab_plan:
        st.write(result.task_plan.summary_zh)
        _render_task_plan_table(result)
    with tab_updates:
        st.json([item.model_dump(mode="json") for item in result.knowledge_updates])

    paths = st.session_state.get("last_knowledge_paths", {})
    if paths:
        _render_output_files({key: Path(path) for key, path in paths.items()})


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


def _render_output_files(paths: dict[str, Path]) -> None:
    st.markdown("#### 输出文件")
    for key, path in paths.items():
        cols = st.columns([3, 1])
        cols[0].code(str(path), language=None)
        with cols[1]:
            _render_file_download(path, label=f"下载 {key}")


def _render_file_download(path: Path, label: str = "下载文件") -> None:
    if not path.is_file():
        st.caption("未生成")
        return
    st.download_button(
        label,
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/octet-stream",
        use_container_width=True,
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
