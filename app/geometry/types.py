"""Shared geometry dataclasses and BEV coordinate helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class GeometryConfig:
    enabled: bool = True
    backend: str = "auto"
    moge_root: str = ""
    moge_python: str = ""
    moge_model_id: str = ""
    depth_fallback_backend: str = "heuristic"
    camera_fx: float | None = None
    camera_fy: float | None = None
    camera_cx: float | None = None
    camera_cy: float | None = None
    camera_height_m: float = 0.45
    camera_pitch_deg: float = 0.0
    bev_x_min_m: float = 0.0
    bev_x_max_m: float = 5.0
    bev_y_min_m: float = -2.5
    bev_y_max_m: float = 2.5
    bev_resolution_m: float = 0.05
    obstacle_min_height_m: float = 0.08
    obstacle_max_height_m: float = 1.8
    unknown_as_occupied: bool = True
    robot_radius_m: float = 0.25
    safety_margin_m: float = 0.10
    timeout_seconds: float = 180.0


@dataclass(frozen=True)
class PointMapResult:
    point_map: np.ndarray
    depth: np.ndarray
    camera: dict[str, float]
    backend: str
    metric_reliable: bool
    warning: str | None = None


@dataclass(frozen=True)
class GeometryResult:
    point_map_path: str | None = None
    depth_path: str | None = None
    bev_occupancy_path: str | None = None
    free_space_mask_path: str | None = None
    esdf_path: str | None = None
    esdf_visualization_path: str | None = None
    bev_metadata_path: str | None = None
    debug_overlay_path: str | None = None
    available: bool = False
    backend: str = "none"
    metric_reliable: bool = False
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BevMetadata:
    resolution: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    width: int
    height: int
    robot_radius_m: float
    safety_margin_m: float
    geometry_backend: str
    metric_reliable: bool
    coordinate_transform: str = (
        "robot frame: x forward, y left; grid row indexes x, grid col indexes y"
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_settings(settings: Any) -> GeometryConfig:
    return GeometryConfig(
        enabled=settings.enable_geometry,
        backend=settings.geometry_backend,
        moge_root=settings.moge_root,
        moge_python=settings.moge_python,
        moge_model_id=settings.moge_model_id,
        depth_fallback_backend=settings.depth_fallback_backend,
        camera_fx=settings.camera_fx,
        camera_fy=settings.camera_fy,
        camera_cx=settings.camera_cx,
        camera_cy=settings.camera_cy,
        camera_height_m=settings.camera_height_m,
        camera_pitch_deg=settings.camera_pitch_deg,
        bev_x_min_m=settings.bev_x_min_m,
        bev_x_max_m=settings.bev_x_max_m,
        bev_y_min_m=settings.bev_y_min_m,
        bev_y_max_m=settings.bev_y_max_m,
        bev_resolution_m=settings.bev_resolution_m,
        obstacle_min_height_m=settings.obstacle_min_height_m,
        obstacle_max_height_m=settings.obstacle_max_height_m,
        unknown_as_occupied=settings.unknown_as_occupied,
        robot_radius_m=settings.robot_radius_m,
        safety_margin_m=settings.safety_margin_m,
        timeout_seconds=settings.geometry_timeout_seconds,
    )


def make_metadata(config: GeometryConfig, backend: str, metric_reliable: bool) -> BevMetadata:
    width = int(round((config.bev_y_max_m - config.bev_y_min_m) / config.bev_resolution_m))
    height = int(round((config.bev_x_max_m - config.bev_x_min_m) / config.bev_resolution_m))
    return BevMetadata(
        resolution=config.bev_resolution_m,
        x_min=config.bev_x_min_m,
        x_max=config.bev_x_max_m,
        y_min=config.bev_y_min_m,
        y_max=config.bev_y_max_m,
        width=max(1, width),
        height=max(1, height),
        robot_radius_m=config.robot_radius_m,
        safety_margin_m=config.safety_margin_m,
        geometry_backend=backend,
        metric_reliable=metric_reliable,
    )


def world_to_grid(x: float, y: float, metadata: BevMetadata | dict[str, Any]) -> tuple[int, int] | None:
    meta = _meta_dict(metadata)
    resolution = float(meta["resolution"])
    row = int((x - float(meta["x_min"])) / resolution)
    col = int((y - float(meta["y_min"])) / resolution)
    if row < 0 or col < 0 or row >= int(meta["height"]) or col >= int(meta["width"]):
        return None
    return row, col


def grid_to_world(row: int, col: int, metadata: BevMetadata | dict[str, Any]) -> tuple[float, float]:
    meta = _meta_dict(metadata)
    resolution = float(meta["resolution"])
    x = float(meta["x_min"]) + (row + 0.5) * resolution
    y = float(meta["y_min"]) + (col + 0.5) * resolution
    return x, y


def read_metadata(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def _meta_dict(metadata: BevMetadata | dict[str, Any]) -> dict[str, Any]:
    return metadata.to_dict() if isinstance(metadata, BevMetadata) else metadata
