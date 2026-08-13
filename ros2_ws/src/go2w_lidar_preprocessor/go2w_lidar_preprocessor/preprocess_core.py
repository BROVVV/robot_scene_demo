"""Geometry-safe LiDAR processing in REP-103 base_link coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PreprocessParameters:
    minimum_range: float
    maximum_range: float
    minimum_height: float
    maximum_height: float
    ground_height: float
    self_half_length: float
    self_half_width: float
    self_filter_margin: float
    front_corridor_half_width: float
    rotation_envelope_radius: float
    self_regions: tuple[tuple[float, float, float, float, float, float], ...] = ()


@dataclass(frozen=True)
class Clearance:
    front: float
    left: float
    right: float


def transform_points(
    points: np.ndarray,
    translation_xyz,
    quaternion_xyzw,
) -> np.ndarray:
    """Apply a geometry_msgs Transform to Nx3 points without rewriting fields."""

    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("points must have shape Nx3")
    translation = np.asarray(translation_xyz, dtype=np.float64)
    quaternion = np.asarray(quaternion_xyzw, dtype=np.float64)
    if translation.shape != (3,) or quaternion.shape != (4,):
        raise ValueError("transform must contain XYZ translation and XYZW quaternion")
    if not np.isfinite(translation).all() or not np.isfinite(quaternion).all():
        raise ValueError("transform must be finite")
    norm = float(np.linalg.norm(quaternion))
    if norm <= 1e-12:
        raise ValueError("transform quaternion has zero norm")
    x, y, z, w = quaternion / norm
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
    return values @ rotation.T + translation


def filter_points_base_link(points: np.ndarray, p: PreprocessParameters):
    """Filter Nx3 points after they have already been transformed to base_link."""
    values = np.asarray(points, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("points must have shape Nx3")
    finite = np.isfinite(values).all(axis=1)
    ranges = np.linalg.norm(values, axis=1)
    bounded = (
        finite
        & (ranges >= p.minimum_range)
        & (ranges <= p.maximum_range)
        & (values[:, 2] >= p.minimum_height)
        & (values[:, 2] <= p.maximum_height)
    )
    self_points = (
        (np.abs(values[:, 0]) <= p.self_half_length + p.self_filter_margin)
        & (np.abs(values[:, 1]) <= p.self_half_width + p.self_filter_margin)
    )
    for (x_min, x_max, y_min, y_max, z_min, z_max) in p.self_regions:
        self_points |= (
            (values[:, 0] >= x_min)
            & (values[:, 0] <= x_max)
            & (values[:, 1] >= y_min)
            & (values[:, 1] <= y_max)
            & (values[:, 2] >= z_min)
            & (values[:, 2] <= z_max)
        )
    filtered = values[bounded & ~self_points]
    obstacles = filtered[filtered[:, 2] > p.ground_height]
    return filtered, obstacles


def directional_clearance(obstacles: np.ndarray, p: PreprocessParameters) -> Clearance:
    values = np.asarray(obstacles, dtype=np.float64)
    if values.size == 0:
        return Clearance(math.inf, math.inf, math.inf)
    forward = values[:, 0]
    left_axis = values[:, 1]
    radial = np.hypot(forward, left_axis)
    front_mask = (forward > 0.0) & (np.abs(left_axis) <= p.front_corridor_half_width)
    left_mask = (left_axis > 0.0) & (radial <= p.rotation_envelope_radius)
    right_mask = (left_axis < 0.0) & (radial <= p.rotation_envelope_radius)

    def minimum(values_: np.ndarray) -> float:
        return float(np.min(values_)) if values_.size else math.inf

    return Clearance(
        front=minimum(forward[front_mask]),
        left=minimum(radial[left_mask]),
        right=minimum(radial[right_mask]),
    )


def laser_scan_ranges(
    obstacles: np.ndarray,
    *,
    angle_min: float,
    angle_max: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
) -> np.ndarray:
    count = int(math.ceil((angle_max - angle_min) / angle_increment))
    ranges = np.full(count, np.inf, dtype=np.float32)
    if len(obstacles) == 0:
        return ranges
    angles = np.arctan2(obstacles[:, 1], obstacles[:, 0])
    distances = np.hypot(obstacles[:, 0], obstacles[:, 1])
    valid = (
        (angles >= angle_min)
        & (angles < angle_max)
        & (distances >= range_min)
        & (distances <= range_max)
    )
    bins = ((angles[valid] - angle_min) / angle_increment).astype(np.int64)
    for index, distance in zip(bins, distances[valid]):
        ranges[index] = min(ranges[index], float(distance))
    return ranges
