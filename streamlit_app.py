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
from app.llm_clients.siliconflow_client import SiliconFlowVisionClient
from app.schemas import SceneAnalysisResult
from app.services.output_writer import prepare_analysis_result, write_analysis_outputs
from app.services.route_planner import format_route_plan
from app.services.scene_analyzer import SceneAnalyzer
from app.services.target_matcher import format_target_decision


MOCK_PATH = Path("examples/mock_scene_result.json")
UPLOAD_DIR = Path(DEFAULT_OUTPUT_DIR) / "uploads"


def main() -> None:
    st.set_page_config(page_title="机器狗场景理解 Demo", layout="wide")
    st.title("机器狗场景理解 Demo")

    with st.sidebar:
        mode = st.radio(
            "模式",
            ["模拟数据", "真实 API", "GroundingDINO+SAM2"],
        )
        target_text = st.text_input("目标描述", value="桌子上的手机")
        uploaded_file = st.file_uploader("场景图片", type=["jpg", "jpeg", "png"])
        high_precision = st.checkbox("高精度复查", value=False)
        analyze_clicked = st.button("开始分析", type="primary", use_container_width=True)

    if uploaded_file is not None:
        st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)

    if analyze_clicked:
        try:
            result = (
                _run_mock()
                if mode == "模拟数据"
                else _run_api(
                    uploaded_file,
                    target_text,
                    high_precision,
                    use_grounded_sam=(mode == "GroundingDINO+SAM2"),
                )
            )
            result = prepare_analysis_result(result)
            image_path = st.session_state.get("last_image_path")
            paths = write_analysis_outputs(result, DEFAULT_OUTPUT_DIR, image_path=image_path)
            _render_result(result, paths)
        except OpenAIError as exc:
            st.error(f"API 请求失败：{exc}")
            st.info(
                "机器狗部署建议保持高精度复查关闭；如仍超时，可降低 IMAGE_MAX_SIDE 或改用更快的视觉模型。"
            )
        except DetectorRuntimeError as exc:
            st.error(f"本地检测器运行失败：{exc}")
            st.info("请确认 Grounding DINO/SAM2 的 Python 环境、torch 依赖和权重路径已配置正确。")
        except (SettingsError, FileNotFoundError, ValueError, ValidationError) as exc:
            st.error(f"错误：{exc}")
    else:
        _render_existing_outputs()


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
        raise ValueError("真实 API 模式需要上传图片。")
    if target_text.strip() == "":
        raise ValueError("目标描述不能为空。")

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


def _render_result(result: SceneAnalysisResult, paths: dict[str, Path]) -> None:
    st.subheader("场景摘要")
    st.write(result.scene_summary_zh)
    st.caption(f"识别物体数：{len(result.objects)} | 关系数：{len(result.relations)}")

    target_col, route_col = st.columns(2)
    with target_col:
        st.subheader("目标判断")
        st.text(format_target_decision(result))
    with route_col:
        st.subheader("路线规划")
        st.text(format_route_plan(result))

    tab_objects, tab_relations, tab_topology, tab_annotated, tab_json = st.tabs(
        ["物体表", "关系表", "拓扑图", "标注图", "JSON"]
    )

    with tab_objects:
        st.dataframe(pd.read_csv(paths["object_table"]), use_container_width=True)
    with tab_relations:
        st.dataframe(pd.read_csv(paths["relation_table"]), use_container_width=True)
    with tab_topology:
        st.image(str(paths["topology_png"]), use_container_width=True)
    with tab_annotated:
        if "annotated_image" in paths and paths["annotated_image"].is_file():
            st.image(str(paths["annotated_image"]), use_container_width=True)
        else:
            st.info("当前结果没有可用原图路径，无法生成标注图。")
    with tab_json:
        st.json(result.model_dump(mode="json"))

    st.subheader("输出文件")
    for path in paths.values():
        st.code(str(path), language=None)


def _render_existing_outputs() -> None:
    scene_path = Path(DEFAULT_OUTPUT_DIR) / "scene_result.json"
    if not scene_path.is_file():
        return

    try:
        result = SceneAnalysisResult.model_validate(
            json.loads(scene_path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, ValidationError):
        return
    result = prepare_analysis_result(result)

    image_path = st.session_state.get("last_image_path")
    paths = write_analysis_outputs(result, DEFAULT_OUTPUT_DIR, image_path=image_path)
    if all(path.is_file() for path in paths.values()):
        _render_result(result, paths)


if __name__ == "__main__":
    main()
