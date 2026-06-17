"""Persist knowledge-aware scene analysis outputs."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas import KnowledgeAwareSceneResult
from app.services.psg_builder import export_predictive_scene_graph_graphml


def write_knowledge_aware_outputs(
    result: KnowledgeAwareSceneResult,
    output_dir: str | Path,
) -> dict[str, Path]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, Path] = {}
    outputs["knowledge_aware_result"] = _write_json(
        path / "knowledge_aware_result.json",
        result.model_dump(mode="json"),
    )
    outputs["parsed_task"] = _write_json(
        path / "parsed_task.json",
        result.parsed_task.model_dump(mode="json"),
    )
    outputs["retrieved_knowledge"] = _write_json(
        path / "retrieved_knowledge.json",
        [item.model_dump(mode="json") for item in result.retrieved_knowledge],
    )
    outputs["predictive_scene_graph_graphml"] = export_predictive_scene_graph_graphml(
        result.predictive_scene_graph,
        path / "predictive_scene_graph.graphml",
    )
    outputs["hypotheses"] = _write_json(
        path / "hypotheses.json",
        [item.model_dump(mode="json") for item in result.hypotheses],
    )
    outputs["knowledge_updates"] = _write_json(
        path / "knowledge_updates.json",
        [item.model_dump(mode="json") for item in result.knowledge_updates],
    )
    outputs["reasoning_report"] = _write_report(
        path / "reasoning_report.md",
        result,
    )
    return outputs


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_report(path: Path, result: KnowledgeAwareSceneResult) -> Path:
    lines = [
        "# 场景推理报告",
        "",
        f"任务：{result.parsed_task.raw_text}",
        f"任务类型：{result.parsed_task.task_type}",
        "",
        "## 推理摘要",
        "",
        result.reasoning_summary_zh,
        "",
        "## 最终回答",
        "",
        result.final_answer_zh,
        "",
        "## 假设",
        "",
    ]
    for hypothesis in result.hypotheses:
        lines.append(
            f"- {hypothesis.hypothesis_id}: {hypothesis.possible_location} "
            f"(p={hypothesis.probability})"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
