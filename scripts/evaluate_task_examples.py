"""Evaluate example task fixtures against the knowledge-aware pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.knowledge.retrieval import retrieve_relevant_knowledge
from app.planning.task_planner import plan_task
from app.reasoning.scene_reasoner import reason_about_scene
from app.reasoning.task_parser import parse_robot_task
from app.schemas import KnowledgeAwareSceneResult, SceneAnalysisResult
from app.services.psg_builder import build_predictive_scene_graph


DEFAULT_TASK_DIR = ROOT / "examples" / "tasks"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 examples/tasks 下的任务样例。")
    parser.add_argument(
        "--tasks-dir",
        default=str(DEFAULT_TASK_DIR),
        help="任务样例目录，默认 examples/tasks",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="可选 JSON 报告输出路径。不提供时只打印到 stdout。",
    )
    return parser.parse_args(argv)


def evaluate_task_examples(tasks_dir: str | Path = DEFAULT_TASK_DIR) -> list[dict[str, Any]]:
    reports = []
    for path in sorted(Path(tasks_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        reports.append(_evaluate_one(path, payload))
    return reports


def _evaluate_one(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    task_text = str(payload["task_text"])
    expected_task_type = str(payload["expected_task_type"])
    parsed_task = parse_robot_task(task_text)
    scene = _load_scene_fixture(payload.get("scene_fixture"))
    knowledge = retrieve_relevant_knowledge(
        target_text=task_text,
        room_type=_room_type_hint(scene),
        location_hint=parsed_task.target_location or parsed_task.scope,
        kb_dir=ROOT / "data" / "scene_kb",
    )

    report: dict[str, Any] = {
        "example": path.name,
        "task_text": task_text,
        "expected_task_type": expected_task_type,
        "parsed_task_type": parsed_task.task_type,
        "task_type_ok": parsed_task.task_type == expected_task_type,
        "knowledge_items": len(knowledge),
        "has_scene_fixture": scene is not None,
        "checks": [],
    }
    report["checks"].append(_check("task_type", report["task_type_ok"]))
    report["checks"].append(_check("knowledge_retrieval", len(knowledge) > 0))

    if scene is not None:
        psg = build_predictive_scene_graph(scene, knowledge, parsed_task)
        reasoning = reason_about_scene(scene, parsed_task, knowledge, psg)
        task_plan = plan_task(scene, parsed_task, reasoning.hypotheses, psg)
        report.update(
            {
                "psg_nodes": len(psg.nodes),
                "psg_edges": len(psg.edges),
                "hypotheses": len(reasoning.hypotheses),
                "task_plan_steps": len(task_plan.steps),
            }
        )
        report["checks"].extend(
            [
                _check("psg_nodes", len(psg.nodes) > 0),
                _check("reasoning_summary", bool(reasoning.reasoning_summary_zh)),
                _check("task_plan_steps", len(task_plan.steps) > 0),
            ]
        )

    report["passed"] = all(check["passed"] for check in report["checks"])
    return report


def _load_scene_fixture(fixture: object) -> SceneAnalysisResult | None:
    if not isinstance(fixture, str):
        return None
    path = ROOT / fixture
    if not path.is_file():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    if "base_scene" in payload:
        return KnowledgeAwareSceneResult.model_validate(payload).base_scene
    if "scene_summary_zh" in payload:
        return SceneAnalysisResult.model_validate(payload)
    return None


def _room_type_hint(scene: SceneAnalysisResult | None) -> str | None:
    if scene is None:
        return None
    names = {obj.name.lower() for obj in scene.objects}
    if names & {"desk", "table", "monitor", "keyboard", "chair"}:
        return "office"
    return None


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": passed}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reports = evaluate_task_examples(args.tasks_dir)
    output = {"passed": all(item["passed"] for item in reports), "examples": reports}
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
