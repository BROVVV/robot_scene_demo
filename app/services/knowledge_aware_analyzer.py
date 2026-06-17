"""Knowledge-aware orchestration on top of the existing SceneAnalyzer."""

from __future__ import annotations

from pathlib import Path

from app.config import DEFAULT_OUTPUT_DIR
from app.knowledge.kb_updater import update_knowledge_from_scene
from app.knowledge.retrieval import retrieve_relevant_knowledge
from app.planning.task_planner import plan_task
from app.reasoning.scene_reasoner import reason_about_scene
from app.reasoning.task_parser import parse_robot_task
from app.schemas import KnowledgeAwareSceneResult, SceneAnalysisResult
from app.services.psg_builder import build_predictive_scene_graph
from app.services.scene_analyzer import SceneAnalyzer


class KnowledgeAwareAnalyzer:
    def __init__(
        self,
        scene_analyzer: SceneAnalyzer | None = None,
        kb_dir: str | Path = "data/scene_kb",
        output_dir: str | Path = DEFAULT_OUTPUT_DIR,
        update_kb: bool = True,
    ) -> None:
        self.scene_analyzer = scene_analyzer
        self.kb_dir = Path(kb_dir)
        self.output_dir = Path(output_dir)
        self.update_kb = update_kb

    def analyze(self, image_path: str, target_text: str) -> KnowledgeAwareSceneResult:
        if self.scene_analyzer is None:
            raise ValueError("KnowledgeAwareAnalyzer.analyze requires a SceneAnalyzer.")
        base_scene = self.scene_analyzer.analyze(image_path, target_text)
        return self.enrich_base_scene(base_scene, target_text)

    def enrich_base_scene(
        self,
        base_scene: SceneAnalysisResult,
        target_text: str,
    ) -> KnowledgeAwareSceneResult:
        parsed_task = parse_robot_task(target_text)
        retrieved_knowledge = retrieve_relevant_knowledge(
            target_text=target_text,
            room_type=_infer_room_type_hint(base_scene),
            location_hint=parsed_task.target_location or parsed_task.scope,
            kb_dir=self.kb_dir,
        )
        psg = build_predictive_scene_graph(base_scene, retrieved_knowledge, parsed_task)
        reasoning = reason_about_scene(base_scene, parsed_task, retrieved_knowledge, psg)
        task_plan = plan_task(base_scene, parsed_task, reasoning.hypotheses, psg)
        knowledge_updates = (
            update_knowledge_from_scene(base_scene, parsed_task, kb_dir=self.kb_dir)
            if self.update_kb
            else []
        )

        return KnowledgeAwareSceneResult(
            base_scene=base_scene,
            parsed_task=parsed_task,
            retrieved_knowledge=retrieved_knowledge,
            predictive_scene_graph=psg,
            hypotheses=reasoning.hypotheses,
            reasoning_summary_zh=reasoning.reasoning_summary_zh,
            task_plan=task_plan,
            knowledge_updates=knowledge_updates,
            final_answer_zh=_final_answer(reasoning.reasoning_summary_zh, task_plan.summary_zh),
        )


def _infer_room_type_hint(scene: SceneAnalysisResult) -> str | None:
    names = {obj.name.lower() for obj in scene.objects}
    if names & {"desk", "table", "monitor", "keyboard", "chair"}:
        return "office"
    return None


def _final_answer(reasoning_summary_zh: str, plan_summary_zh: str) -> str:
    return f"{reasoning_summary_zh} 任务计划：{plan_summary_zh}"
