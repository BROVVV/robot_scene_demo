"""ESDF utilities for small BEV grids."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image


def compute_esdf(occupancy: np.ndarray, resolution: float) -> np.ndarray:
    occupied = occupancy > 0
    dist_to_obstacle = _distance_transform(~occupied) * resolution
    dist_to_free = _distance_transform(occupied) * resolution
    esdf = dist_to_obstacle.astype(np.float32)
    esdf[occupied] = -dist_to_free[occupied]
    return esdf


def save_esdf_png(esdf: np.ndarray, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    finite = np.nan_to_num(esdf, nan=0.0)
    positive = np.clip(finite, 0.0, np.percentile(np.maximum(finite, 0.0), 95) or 1.0)
    scaled = (positive / max(positive.max(), 1e-6) * 255).astype(np.uint8)
    scaled[finite <= 0] = 0
    Image.fromarray(scaled, mode="L").save(path)
    return path


def _distance_transform(mask: np.ndarray) -> np.ndarray:
    inf = 1e6
    dist = np.where(mask, inf, 0.0).astype(np.float32)
    height, width = dist.shape
    sqrt2 = math.sqrt(2.0)
    for row in range(height):
        for col in range(width):
            best = dist[row, col]
            if row > 0:
                best = min(best, dist[row - 1, col] + 1.0)
            if col > 0:
                best = min(best, dist[row, col - 1] + 1.0)
            if row > 0 and col > 0:
                best = min(best, dist[row - 1, col - 1] + sqrt2)
            if row > 0 and col + 1 < width:
                best = min(best, dist[row - 1, col + 1] + sqrt2)
            dist[row, col] = best
    for row in range(height - 1, -1, -1):
        for col in range(width - 1, -1, -1):
            best = dist[row, col]
            if row + 1 < height:
                best = min(best, dist[row + 1, col] + 1.0)
            if col + 1 < width:
                best = min(best, dist[row, col + 1] + 1.0)
            if row + 1 < height and col + 1 < width:
                best = min(best, dist[row + 1, col + 1] + sqrt2)
            if row + 1 < height and col > 0:
                best = min(best, dist[row + 1, col - 1] + sqrt2)
            dist[row, col] = best
    return dist
