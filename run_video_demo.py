"""Command-line entry point for prerecorded first-person video target search."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openai import OpenAIError
from pydantic import ValidationError

from app.config import DEFAULT_OUTPUT_DIR, SettingsError
from app.detectors.grounded_sam_subprocess import DetectorRuntimeError
from app.video.pipeline import run_video_search
from app.video.video_reader import VideoReadError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="输入机器狗第一视角视频，构建语义视频记忆并搜索目标物。"
    )
    parser.add_argument("--video", required=True, help="输入视频路径")
    parser.add_argument("--target", required=True, help="要寻找的目标物")
    parser.add_argument(
        "--detector",
        choices=["mock", "llm", "grounded_sam"],
        default="llm",
        help="逐关键帧使用的检测后端",
    )
    parser.add_argument("--sample-fps", type=float, default=None, help="每秒采样帧数，默认读取配置")
    parser.add_argument("--max-frames", type=int, default=None, help="最大分析帧数，默认读取配置")
    parser.add_argument("--enable-knowledge", action="store_true", help="启用上下文候选区域规则")
    parser.add_argument(
        "--enable-video-memory",
        action="store_true",
        help="启用逐帧场景记忆、负证据、长期记忆和视频 PSG",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--no-annotate", action="store_true", help="不生成标注关键帧")
    tracking_group = parser.add_mutually_exclusive_group()
    tracking_group.add_argument(
        "--enable-tracking", dest="enable_tracking", action="store_true"
    )
    tracking_group.add_argument(
        "--disable-tracking", dest="enable_tracking", action="store_false"
    )
    crop_group = parser.add_mutually_exclusive_group()
    crop_group.add_argument(
        "--enable-crop-verify", dest="enable_crop_verify", action="store_true"
    )
    crop_group.add_argument(
        "--disable-crop-verify", dest="enable_crop_verify", action="store_false"
    )
    parser.add_argument("--verify-every-n-frames", type=int)
    parser.add_argument("--track-iou-threshold", type=float)
    parser.add_argument("--target-confirm-min-frames", type=int)
    parser.add_argument("--target-confirm-score", type=float)
    llm_prior_group = parser.add_mutually_exclusive_group()
    llm_prior_group.add_argument(
        "--enable-llm-prior", dest="enable_llm_prior", action="store_true"
    )
    llm_prior_group.add_argument(
        "--disable-llm-prior", dest="enable_llm_prior", action="store_false"
    )
    memory_group = parser.add_mutually_exclusive_group()
    memory_group.add_argument(
        "--enable-observation-memory",
        dest="enable_observation_memory",
        action="store_true",
    )
    memory_group.add_argument(
        "--disable-observation-memory",
        dest="enable_observation_memory",
        action="store_false",
    )
    gate_group = parser.add_mutually_exclusive_group()
    gate_group.add_argument(
        "--enable-evidence-gating",
        dest="enable_evidence_gating",
        action="store_true",
    )
    gate_group.add_argument(
        "--disable-evidence-gating",
        dest="enable_evidence_gating",
        action="store_false",
    )
    parser.add_argument("--disable-handwritten-priors", action="store_true")
    parser.add_argument("--disable-static-kb", action="store_true")
    parser.add_argument("--prior-audit", action="store_true")
    parser.set_defaults(
        enable_tracking=None,
        enable_crop_verify=None,
        enable_llm_prior=None,
        enable_observation_memory=None,
        enable_evidence_gating=None,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.enable_knowledge:
            print(
                "--enable-knowledge is deprecated. Use --enable-llm-prior "
                "--enable-observation-memory --enable-evidence-gating instead.",
                file=sys.stderr,
            )
        result, paths = run_video_search(
            video_path=args.video,
            target=args.target,
            detector=args.detector,
            sample_fps=args.sample_fps,
            max_frames=args.max_frames,
            enable_knowledge=args.enable_knowledge,
            enable_video_memory=(True if args.enable_video_memory else None),
            output_dir=args.output_dir,
            annotate=not args.no_annotate,
            enable_tracking=args.enable_tracking,
            enable_crop_verify=args.enable_crop_verify,
            verify_every_n_frames=args.verify_every_n_frames,
            track_iou_threshold=args.track_iou_threshold,
            target_confirm_min_frames=args.target_confirm_min_frames,
            target_confirm_score=args.target_confirm_score,
            enable_llm_prior=args.enable_llm_prior,
            enable_observation_memory=args.enable_observation_memory,
            enable_evidence_gating=args.enable_evidence_gating,
            disable_handwritten_priors=args.disable_handwritten_priors,
            disable_static_kb=args.disable_static_kb,
            prior_audit=args.prior_audit,
        )
    except (
        SettingsError,
        FileNotFoundError,
        ImportError,
        VideoReadError,
        DetectorRuntimeError,
        OpenAIError,
        ValidationError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if result["target_found"]:
        best = result["best_evidence"]
        print(f"已找到目标：{args.target}")
        print(f"最佳证据：{best['timestamp_sec']:.2f}s，置信度 {best['confidence']:.3f}")
    else:
        print(f"未直接找到目标：{args.target}")
        print(f"原因：{result['reason']}")
        if result.get("environment_memories_written", 0) > 0:
            print(
                "已生成并写入环境记忆："
                f"{result['environment_memories_written']} 条；"
                f"负目标证据：{result.get('negative_evidence_count', 0)} 条；"
                f"PSG 假设：{len(result.get('psg_hypotheses', []))} 条。"
            )
    print(result["navigation_interpretation"]["suggestion"])
    print("已生成：")
    for path in paths.values():
        print(Path(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
