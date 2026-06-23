"""Project detected objects into BEV robot-frame goals."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.geometry.types import GeometryResult, read_metadata, world_to_grid, grid_to_world


@dataclass(frozen=True)
class ProjectionConfig:
    min_valid_points: int = 8
    trim_ratio: float = 0.1


def project_objects_to_bev(
    objects: list[Any],
    geometry: GeometryResult,
    output_dir: str,
    config: ProjectionConfig,
) -> list[Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not geometry.available or not geometry.point_map_path or not geometry.bev_metadata_path:
        projected = [
            _copy_object(
                obj,
                {
                    "projection_warning": "Geometry unavailable; object was not projected.",
                    "geometry_backend": geometry.backend,
                    "metric_reliable": geometry.metric_reliable,
                },
            )
            for obj in objects
        ]
        _write_projection_json(projected, output / "object_goal_projection.json")
        return projected

    point_map = np.load(geometry.point_map_path)
    esdf = np.load(geometry.esdf_path) if geometry.esdf_path else None
    metadata = read_metadata(geometry.bev_metadata_path)
    projected = [
        _project_one(obj, point_map, esdf, metadata, geometry, config)
        for obj in objects
    ]
    _write_projection_json(projected, output / "object_goal_projection.json")
    return projected


def _project_one(
    obj: Any,
    point_map: np.ndarray,
    esdf: np.ndarray | None,
    metadata: dict[str, Any],
    geometry: GeometryResult,
    config: ProjectionConfig,
) -> Any:
    bbox = getattr(obj, "bbox_2d", None)
    if bbox is None:
        return _copy_object(obj, {"projection_warning": "Object has no bbox_2d."})

    height, width = point_map.shape[:2]
    x1 = max(0, min(width - 1, int(float(bbox.x1) * width)))
    y1 = max(0, min(height - 1, int(float(bbox.y1) * height)))
    x2 = max(x1 + 1, min(width, int(float(bbox.x2) * width)))
    y2 = max(y1 + 1, min(height, int(float(bbox.y2) * height)))
    crop = point_map[y1:y2, x1:x2].reshape(-1, 3)
    valid = crop[np.isfinite(crop).all(axis=1)]
    if valid.shape[0] < config.min_valid_points:
        return _copy_object(
            obj,
            {
                "projection_warning": "Too few valid 3D points in bbox crop.",
                "geometry_backend": geometry.backend,
                "metric_reliable": geometry.metric_reliable,
            },
        )

    center = _trimmed_median(valid, config.trim_ratio)
    bev_x = float(center[0])
    bev_y = float(center[1])
    distance = math.hypot(bev_x, bev_y)
    bearing = math.degrees(math.atan2(bev_y, bev_x))
    goal_x = bev_x
    goal_y = bev_y
    reachable = None
    clearance = None
    warning = None

    if esdf is not None:
        cell = world_to_grid(bev_x, bev_y, metadata)
        if cell is None:
            reachable = False
            nearest = _nearest_free(esdf, metadata, bev_x, bev_y)
            if nearest is not None:
                goal_x, goal_y, clearance = nearest
                warning = "Object center is out of BEV; using nearest free goal."
        else:
            row, col = cell
            clearance = float(esdf[row, col])
            reachable = clearance > 0.0
            if not reachable:
                nearest = _nearest_free(esdf, metadata, bev_x, bev_y)
                if nearest is not None:
                    goal_x, goal_y, clearance = nearest
                    reachable = True
                    warning = "Object center is not free; using nearest free goal."

    return _copy_object(
        obj,
        {
            "bev_x": round(bev_x, 3),
            "bev_y": round(bev_y, 3),
            "distance_m": round(distance, 3),
            "bearing_deg": round(bearing, 2),
            "reachable": reachable,
            "clearance_m": None if clearance is None else round(clearance, 3),
            "goal_bev_x": round(goal_x, 3),
            "goal_bev_y": round(goal_y, 3),
            "geometry_backend": geometry.backend,
            "metric_reliable": geometry.metric_reliable,
            "projection_warning": warning,
        },
    )


def _trimmed_median(points: np.ndarray, trim_ratio: float) -> np.ndarray:
    if trim_ratio <= 0:
        return np.median(points, axis=0)
    count = points.shape[0]
    low = int(count * trim_ratio)
    high = max(low + 1, int(count * (1.0 - trim_ratio)))
    trimmed_axes = []
    for axis in range(points.shape[1]):
        values = np.sort(points[:, axis])
        trimmed_axes.append(float(np.median(values[low:high])))
    return np.array(trimmed_axes, dtype=np.float32)


def _nearest_free(
    esdf: np.ndarray,
    metadata: dict[str, Any],
    x: float,
    y: float,
) -> tuple[float, float, float] | None:
    free = np.argwhere(esdf > 0.0)
    if free.size == 0:
        return None
    best = None
    best_distance = float("inf")
    for row, col in free:
        wx, wy = grid_to_world(int(row), int(col), metadata)
        dist = math.hypot(wx - x, wy - y)
        if dist < best_distance:
            best_distance = dist
            best = (wx, wy, float(esdf[row, col]))
    return best


def _copy_object(obj: Any, updates: dict[str, Any]) -> Any:
    if hasattr(obj, "model_copy"):
        return obj.model_copy(update=updates)
    if isinstance(obj, dict):
        return {**obj, **updates}
    for key, value in updates.items():
        setattr(obj, key, value)
    return obj


def _write_projection_json(objects: list[Any], output_path: Path) -> None:
    rows = [
        obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
        for obj in objects
    ]
    output_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
