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
from app.llm_clients.siliconflow_client import SiliconFlowVisionClient
from app.schemas import SceneAnalysisResult
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
        choices=["llm", "grounded_sam"],
        default=None,
        help="物体检测后端。llm 使用视觉大模型；grounded_sam 使用 Grounding DINO + SAM2。",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="使用 examples/mock_scene_result.json，不调用真实 API",
    )
    return parser.parse_args(argv)


def run(image_path: str, target_text: str, detector_backend: str | None = None) -> list[Path]:
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
    elif backend == "llm":
        analyzer = SceneAnalyzer(
            llm_client=SiliconFlowVisionClient(settings=settings),
            output_dir=output_dir,
            enable_low_object_retry=settings.enable_low_object_retry,
            min_objects_for_complex_scene=settings.min_objects_for_complex_scene,
        )
    else:
        raise ValueError(f"Unsupported detector backend: {backend}")
    result = analyzer.analyze(str(image), target_text)

    return export_and_print_result(result, output_dir, image_path=image)


def run_mock(mock_path: str | Path = "examples/mock_scene_result.json") -> list[Path]:
    path = Path(mock_path)
    if not path.is_file():
        raise FileNotFoundError(f"Mock result file not found: {path}")

    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    result = SceneAnalysisResult.model_validate(data)
    return export_and_print_result(result, Path(DEFAULT_OUTPUT_DIR))


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
    print(format_route_plan(result))
    print()
    print("已生成：")

    ordered_paths = [
        output_paths["scene_result"],
        output_paths["object_table"],
        output_paths["relation_table"],
        output_paths["topology_png"],
        output_paths["topology_graphml"],
    ]
    if "annotated_image" in output_paths:
        ordered_paths.append(output_paths["annotated_image"])
    for path in ordered_paths:
        print(path)

    return ordered_paths


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.mock:
            run_mock()
        else:
            if not args.image or not args.target:
                raise ValueError("非 mock 模式必须同时提供 --image 和 --target。")
            run(args.image, args.target, args.detector)
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
