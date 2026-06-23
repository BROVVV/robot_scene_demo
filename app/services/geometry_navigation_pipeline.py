"""Optional geometry, object projection, and local planning integration."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from app.config import Settings
from app.geometry import build_scene_geometry
from app.geometry.types import config_from_settings as geometry_config_from_settings
from app.geometry.types import read_metadata, grid_to_world
from app.navigation.local_planner import config_from_settings as planner_config_from_settings
from app.navigation.local_planner import plan_to_goal
from app.navigation.object_goal_projection import ProjectionConfig, project_objects_to_bev
from app.schemas import SceneAnalysisResult, SceneObject


def enrich_with_geometry_and_navigation(
    result: SceneAnalysisResult,
    image_path: str | Path | None,
    output_dir: str | Path,
    settings: Settings,
) -> SceneAnalysisResult:
    if image_path is None or not settings.enable_geometry:
        return result
    image = Path(image_path)
    if not image.is_file():
        return result.model_copy(
            update={
                "warnings": [
                    *result.warnings,
                    f"Geometry skipped because image file is missing: {image}",
                ]
            }
        )

    geometry = build_scene_geometry(
        str(image),
        str(output_dir),
        geometry_config_from_settings(settings),
    )
    objects = project_objects_to_bev(
        result.objects,
        geometry,
        str(output_dir),
        ProjectionConfig(),
    )
    warnings = list(result.warnings)
    if geometry.warning:
        warnings.append(geometry.warning)

    selected_goal, selection_reason = _select_goal(
        objects,
        result.target_decision.matched_object_ids,
        result.target_decision.target_text,
        geometry,
    )
    local_plan_payload = None
    if settings.enable_local_planner and selected_goal is not None:
        local_plan = plan_to_goal(
            geometry,
            (float(selected_goal["goal_bev_x"]), float(selected_goal["goal_bev_y"])),
            planner_config_from_settings(settings),
        )
        local_plan_payload = local_plan.to_dict()
        if local_plan.warning:
            warnings.append(local_plan.warning)

    return result.model_copy(
        update={
            "objects": objects,
            "geometry": geometry.to_dict(),
            "local_plan": local_plan_payload,
            "selected_goal": selected_goal,
            "selection_reason": selection_reason,
            "warnings": warnings,
        }
    )


def _select_goal(
    objects: list[SceneObject],
    matched_object_ids: list[str],
    target_text: str,
    geometry: Any,
) -> tuple[dict[str, object] | None, str | None]:
    candidates = [obj for obj in objects if obj.goal_bev_x is not None and obj.goal_bev_y is not None]
    by_id = {obj.id: obj for obj in candidates}
    for object_id in matched_object_ids:
        if object_id in by_id:
            obj = by_id[object_id]
            return _goal_payload(obj), f"使用目标匹配结果 {obj.id}:{obj.name_zh} 作为局部规划目标。"

    target = target_text.lower()
    scored = [
        (obj, _text_score(obj, target_text, target))
        for obj in candidates
    ]
    scored = [(obj, score) for obj, score in scored if score > 0]
    if scored:
        scored.sort(key=lambda item: (item[1], item[0].confidence), reverse=True)
        obj = scored[0][0]
        return _goal_payload(obj), f"根据目标文本与物体标签重叠选择 {obj.id}:{obj.name_zh}。"

    reachable = [obj for obj in candidates if obj.reachable is not False]
    if reachable:
        obj = min(reachable, key=lambda item: (item.distance_m or 99.0, -item.confidence))
        return _goal_payload(obj), f"未找到文本匹配目标，选择最近可达物体 {obj.id}:{obj.name_zh}。"

    free_goal = _frontier_goal(geometry)
    if free_goal is not None:
        return free_goal, "未找到可投影物体，选择前方 clearance 最大的 free-space waypoint。"

    return None, "未找到可用 BEV 目标，保留原路线计划。"


def _goal_payload(obj: SceneObject) -> dict[str, object]:
    return {
        "object_id": obj.id,
        "label": obj.name,
        "label_zh": obj.name_zh,
        "goal_bev_x": obj.goal_bev_x,
        "goal_bev_y": obj.goal_bev_y,
        "bev_x": obj.bev_x,
        "bev_y": obj.bev_y,
        "reachable": obj.reachable,
        "clearance_m": obj.clearance_m,
    }


def _text_score(obj: SceneObject, target_text: str, target_lower: str) -> int:
    haystack = " ".join([obj.name, obj.name_zh, obj.category, *obj.attributes]).lower()
    score = 0
    if obj.name.lower() in target_lower or obj.name_zh in target_text:
        score += 4
    rules = {
        "手机": ["phone", "手机"],
        "椅": ["chair", "椅"],
        "桌": ["desk", "table", "桌"],
        "门": ["door", "门"],
        "通行": ["floor", "地面"],
        "障碍": ["object", "物体"],
    }
    for token, labels in rules.items():
        if token in target_text and any(label in haystack for label in labels):
            score += 2
    return score


def _frontier_goal(geometry: Any) -> dict[str, object] | None:
    if not geometry.available or not geometry.esdf_path or not geometry.bev_metadata_path:
        return None
    esdf = np.load(geometry.esdf_path)
    metadata = read_metadata(geometry.bev_metadata_path)
    candidates = np.argwhere(esdf > 0.0)
    if candidates.size == 0:
        return None
    best_row, best_col = max(
        ((int(row), int(col)) for row, col in candidates),
        key=lambda cell: (float(esdf[cell]), grid_to_world(cell[0], cell[1], metadata)[0]),
    )
    x, y = grid_to_world(best_row, best_col, metadata)
    return {
        "object_id": None,
        "label": "free_space",
        "label_zh": "可通行区域",
        "goal_bev_x": round(x, 3),
        "goal_bev_y": round(y, 3),
        "bev_x": round(x, 3),
        "bev_y": round(y, 3),
        "distance_m": round(math.hypot(x, y), 3),
        "reachable": True,
        "clearance_m": round(float(esdf[best_row, best_col]), 3),
    }
