"""Build BEV occupancy and free-space masks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.geometry.types import BevMetadata, GeometryConfig, make_metadata, world_to_grid


def build_bev_layers(
    point_map: np.ndarray,
    config: GeometryConfig,
    *,
    backend: str,
    metric_reliable: bool,
) -> tuple[np.ndarray, np.ndarray, BevMetadata]:
    metadata = make_metadata(config, backend=backend, metric_reliable=metric_reliable)
    occupancy = np.full(
        (metadata.height, metadata.width),
        255 if config.unknown_as_occupied else 0,
        dtype=np.uint8,
    )
    free_space = np.zeros_like(occupancy, dtype=np.uint8)

    _paint_heuristic_free_wedge(free_space, occupancy, metadata, config)
    if metric_reliable:
        _paint_point_obstacles(point_map, occupancy, metadata, config)
    return occupancy, free_space, metadata


def save_mask(mask: np.ndarray, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, mode="L").save(path)
    return path


def _paint_heuristic_free_wedge(
    free_space: np.ndarray,
    occupancy: np.ndarray,
    metadata: BevMetadata,
    config: GeometryConfig,
) -> None:
    for row in range(metadata.height):
        x = metadata.x_min + (row + 0.5) * metadata.resolution
        max_abs_y = min(1.2 + 0.25 * x, metadata.y_max)
        for col in range(metadata.width):
            y = metadata.y_min + (col + 0.5) * metadata.resolution
            if x >= 0 and abs(y) <= max_abs_y:
                free_space[row, col] = 255
                occupancy[row, col] = 0

    radius = config.robot_radius_m + config.safety_margin_m
    for row in range(metadata.height):
        for col in range(metadata.width):
            x = metadata.x_min + (row + 0.5) * metadata.resolution
            y = metadata.y_min + (col + 0.5) * metadata.resolution
            if (x * x + y * y) ** 0.5 <= radius:
                free_space[row, col] = 255
                occupancy[row, col] = 0


def _paint_point_obstacles(
    point_map: np.ndarray,
    occupancy: np.ndarray,
    metadata: BevMetadata,
    config: GeometryConfig,
) -> None:
    sample = max(1, min(point_map.shape[0], point_map.shape[1]) // 120)
    points = point_map[::sample, ::sample].reshape(-1, 3)
    for x, y, z in points:
        if not np.isfinite([x, y, z]).all():
            continue
        if z < config.obstacle_min_height_m or z > config.obstacle_max_height_m:
            continue
        cell = world_to_grid(float(x), float(y), metadata)
        if cell is None:
            continue
        row, col = cell
        occupancy[row, col] = 255
