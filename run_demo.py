"""Command-line entry point for the robot scene understanding demo."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openai import OpenAIError
from pydantic import ValidationError

from app.config import DEFAULT_OUTPUT_DIR, SettingsError, get_settings
from app.detectors.grounded_sam_subprocess import (
    DetectorRuntimeError,
    GroundedSAMSubprocessDetector,
)
from app.detectors.florence2_detector import Florence2Detector
from app.llm_clients.siliconflow_client import SiliconFlowVisionClient
from app.reasoning.task_parser import parse_robot_task
from app.schemas import SceneAnalysisResult
from app.services.knowledge_aware_analyzer import KnowledgeAwareAnalyzer
from app.services.geometry_navigation_pipeline import enrich_with_geometry_and_navigation
from app.services.knowledge_output_writer import write_knowledge_aware_outputs
from app.services.output_writer import prepare_analysis_result, write_analysis_outputs
from app.services.route_planner import format_route_plan
from app.services.scene_analyzer import SceneAnalyzer
from app.services.target_matcher import format_target_decision


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="机器狗场景理解 Demo：输入单张图片和目标描述，输出场景理解结果。"
    )
    parser.add_argument("--image", help="场景图片路径")
    parser.add_argument("--target", help="目标描述文本，例如：桌子上的手机")
    parser.add_argument(
        "--detector",
        choices=["llm", "grounded_sam", "florence2"],
        default=None,
        help="物体检测后端。llm 使用视觉大模型；grounded_sam 使用 Grounding DINO + SAM2；florence2 使用 Florence-2。",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="使用 examples/mock_scene_result.json，不调用真实 API",
    )
    parser.add_argument(
        "--enable-knowledge",
        action="store_true",
        help="启用知识库、预测性场景图、场景推理和任务规划增强输出。",
    )
    return parser.parse_args(argv)


def run(
    image_path: str,
    target_text: str,
    detector_backend: str | None = None,
    enable_knowledge: bool = False,
) -> list[Path]:
    image = Path(image_path)
    if not image.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    settings = get_settings()
    output_dir = Path(settings.output_dir)
    backend = detector_backend or settings.detection_backend

    if backend == "grounded_sam":
        analyzer = SceneAnalyzer(
            object_detector=GroundedSAMSubprocessDetector(settings),
            output_dir=output_dir,
        )
    elif backend == "florence2":
        analyzer = SceneAnalyzer(
            object_detector=Florence2Detector(settings),
            output_dir=output_dir,
        )
    elif backend == "llm":
        analyzer = SceneAnalyzer(
            llm_client=SiliconFlowVisionClient(settings=settings),
            output_dir=output_dir,
            enable_low_object_retry=settings.enable_low_object_retry,
            min_objects_for_complex_scene=settings.min_objects_for_complex_scene,
        )
    else:
        raise ValueError(f"Unsupported detector backend: {backend}")
    if enable_knowledge:
        base_scene = analyzer.analyze(str(image), target_text)
        base_scene = enrich_with_geometry_and_navigation(
            base_scene,
            image,
            output_dir,
            settings,
        )
        knowledge_result = KnowledgeAwareAnalyzer(
            output_dir=output_dir,
            update_kb=True,
        ).enrich_base_scene(base_scene, target_text)
        output_paths = export_and_print_result(
            knowledge_result.base_scene,
            output_dir,
            image_path=image,
        )
        knowledge_paths = write_knowledge_aware_outputs(knowledge_result, output_dir)
        _print_knowledge_outputs(knowledge_result, knowledge_paths)
        return output_paths + list(knowledge_paths.values())

    result = analyzer.analyze(str(image), target_text)
    result = enrich_with_geometry_and_navigation(
        result,
        image,
        output_dir,
        settings,
    )
    return export_and_print_result(result, output_dir, image_path=image)


def run_mock(
    mock_path: str | Path = "examples/mock_scene_result.json",
    enable_knowledge: bool = False,
) -> list[Path]:
    path = Path(mock_path)
    if not path.is_file():
        raise FileNotFoundError(f"Mock result file not found: {path}")

    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    result = SceneAnalysisResult.model_validate(data)
    output_paths = export_and_print_result(result, Path(DEFAULT_OUTPUT_DIR))
    if not enable_knowledge:
        return output_paths

    knowledge_result = KnowledgeAwareAnalyzer(update_kb=False).enrich_base_scene(
        result,
        result.target_decision.target_text,
    )
    knowledge_paths = write_knowledge_aware_outputs(
        knowledge_result,
        Path(DEFAULT_OUTPUT_DIR),
    )
    _print_knowledge_outputs(knowledge_result, knowledge_paths)
    return output_paths + list(knowledge_paths.values())


def export_and_print_result(
    result: SceneAnalysisResult,
    output_dir: Path,
    image_path: str | Path | None = None,
) -> list[Path]:
    result = prepare_analysis_result(result)
    output_paths = write_analysis_outputs(result, output_dir, image_path=image_path)

    print(f"场景摘要：{result.scene_summary_zh}")
    print()
    print(format_target_decision(result))
    print()
    parsed_task = parse_robot_task(result.target_decision.target_text)
    print("任务解析：")
    print(parsed_task.model_dump_json(indent=2))
    print()
    print(format_route_plan(result))
    print()
    print("已生成：")

    ordered_paths = [
        output_paths["scene_result"],
        output_paths["object_table"],
        output_paths["relation_table"],
        output_paths["topology_png"],
        output_paths["topology_graphml"],
        output_paths["ros2_motion_plan"],
    ]
    if "annotated_image" in output_paths:
        ordered_paths.append(output_paths["annotated_image"])
    for optional_key in [
        "bev_occupancy",
        "free_space_mask",
        "esdf",
        "geometry_debug",
        "local_plan",
    ]:
        if optional_key in output_paths:
            ordered_paths.append(output_paths[optional_key])
    for path in ordered_paths:
        print(path)

    return ordered_paths


def _print_knowledge_outputs(
    result,
    output_paths: dict[str, Path],
) -> None:
    print()
    print("知识增强推理：")
    print(result.reasoning_summary_zh)
    print()
    print("任务规划：")
    print(result.task_plan.summary_zh)
    print()
    print("知识增强输出：")
    for path in output_paths.values():
        print(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.mock:
            run_mock(enable_knowledge=args.enable_knowledge)
        else:
            if not args.image or not args.target:
                raise ValueError("非 mock 模式必须同时提供 --image 和 --target。")
            run(args.image, args.target, args.detector, enable_knowledge=args.enable_knowledge)
    except (
        SettingsError,
        FileNotFoundError,
        ImportError,
        DetectorRuntimeError,
        OpenAIError,
        ValueError,
        ValidationError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
