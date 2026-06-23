"""Heuristic monocular geometry fallback for demos and tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.geometry.types import GeometryConfig, PointMapResult


FALLBACK_WARNING = "Using heuristic geometry fallback; not metric-safe."


def infer_heuristic_point_map(
    image_path: str | Path,
    config: GeometryConfig,
) -> PointMapResult:
    with Image.open(image_path) as image:
        width, height = image.size

    fx = config.camera_fx or 0.8 * width
    fy = config.camera_fy or 0.8 * width
    cx = config.camera_cx if config.camera_cx is not None else width / 2
    cy = config.camera_cy if config.camera_cy is not None else height / 2

    rows, cols = np.indices((height, width), dtype=np.float32)
    vertical = rows / max(1.0, float(height - 1))
    horizontal_center = 1.0 - np.minimum(1.0, np.abs(cols - cx) / max(1.0, width / 2))

    depth = 0.45 + (1.0 - vertical) * 4.4
    depth -= horizontal_center * vertical * 0.35
    depth = np.clip(depth, 0.25, 5.0).astype(np.float32)

    x_forward = depth
    y_left = -((cols - cx) / fx) * depth
    z_up = config.camera_height_m - ((rows - cy) / fy) * depth
    point_map = np.stack([x_forward, y_left, z_up], axis=-1).astype(np.float32)

    return PointMapResult(
        point_map=point_map,
        depth=depth,
        camera={"fx": float(fx), "fy": float(fy), "cx": float(cx), "cy": float(cy)},
        backend="heuristic_fallback",
        metric_reliable=False,
        warning=FALLBACK_WARNING,
    )
