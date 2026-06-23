"""Persist scene analysis outputs to disk."""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas import SceneAnalysisResult
from app.services.image_annotator import export_annotated_image
from app.services.relation_enricher import enrich_scene_relations
from app.services.ros2_command_exporter import export_ros2_motion_plan
from app.services.scene_normalizer import normalize_scene_labels
from app.services.table_exporter import export_object_table, export_relation_table
from app.services.topology_builder import export_topology_graph


def prepare_analysis_result(result: SceneAnalysisResult) -> SceneAnalysisResult:
    return enrich_scene_relations(normalize_scene_labels(result))


def write_analysis_outputs(
    result: SceneAnalysisResult,
    output_dir: str | Path,
    image_path: str | Path | None = None,
) -> dict[str, Path]:
    result = prepare_analysis_result(result)
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)

    scene_result_path = path / "scene_result.json"
    scene_result_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    object_table_path = export_object_table(result, path / "object_table.csv")
    relation_table_path = export_relation_table(result, path / "relation_table.csv")
    topology_png_path, topology_graphml_path = export_topology_graph(result, path)
    ros2_motion_plan_path = export_ros2_motion_plan(
        result,
        path / "ros2_motion_plan.json",
    )
    annotated_image_path = None
    if image_path is not None:
        annotated_image_path = export_annotated_image(
            result,
            image_path,
            path / "annotated_scene.png",
        )

    outputs = {
        "scene_result": scene_result_path,
        "object_table": object_table_path,
        "relation_table": relation_table_path,
        "topology_png": topology_png_path,
        "topology_graphml": topology_graphml_path,
        "ros2_motion_plan": ros2_motion_plan_path,
    }
    if annotated_image_path is not None:
        outputs["annotated_image"] = annotated_image_path
    _add_optional_geometry_outputs(outputs, path)
    return outputs


def _add_optional_geometry_outputs(outputs: dict[str, Path], output_dir: Path) -> None:
    optional_files = {
        "point_map": output_dir / "point_map.npy",
        "depth": output_dir / "depth.npy",
        "bev_occupancy": output_dir / "bev_occupancy.png",
        "free_space_mask": output_dir / "free_space_mask.png",
        "esdf": output_dir / "esdf.npy",
        "esdf_png": output_dir / "esdf.png",
        "bev_metadata": output_dir / "bev_metadata.json",
        "geometry_debug": output_dir / "geometry_debug.png",
        "local_plan": output_dir / "local_plan.png",
        "object_goal_projection": output_dir / "object_goal_projection.json",
    }
    for key, file_path in optional_files.items():
        if file_path.is_file():
            outputs[key] = file_path
