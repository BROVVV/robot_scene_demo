"""Scene geometry build pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.geometry.bev_builder import build_bev_layers, save_mask
from app.geometry.depth_fallback import infer_heuristic_point_map
from app.geometry.esdf import compute_esdf, save_esdf_png
from app.geometry.moge_worker import MoGeSubprocessBackend
from app.geometry.types import GeometryConfig, GeometryResult
from app.geometry.visualization import save_geometry_debug


def build_scene_geometry(
    image_path: str,
    output_dir: str,
    config: GeometryConfig,
) -> GeometryResult:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not config.enabled:
        return GeometryResult(
            available=False,
            backend="disabled",
            warning="Geometry is disabled.",
        )

    point_result = None
    warning = None
    if config.backend in {"auto", "moge"} and config.moge_root:
        try:
            backend = MoGeSubprocessBackend(config)
            point_result = backend.infer_point_map(image_path)
        except Exception as exc:
            if config.backend == "moge":
                raise
            warning = f"MoGe unavailable, using heuristic fallback: {exc}"

    if point_result is None:
        point_result = infer_heuristic_point_map(image_path, config)
        warning = warning or point_result.warning

    point_map_path = output / "point_map.npy"
    depth_path = output / "depth.npy"
    np.save(point_map_path, point_result.point_map)
    np.save(depth_path, point_result.depth)

    occupancy, free_space, metadata = build_bev_layers(
        point_result.point_map,
        config,
        backend=point_result.backend,
        metric_reliable=point_result.metric_reliable,
    )
    occupancy_path = save_mask(occupancy, output / "bev_occupancy.png")
    free_space_path = save_mask(free_space, output / "free_space_mask.png")

    esdf = compute_esdf(occupancy, metadata.resolution)
    esdf_path = output / "esdf.npy"
    np.save(esdf_path, esdf.astype(np.float32))
    esdf_png_path = save_esdf_png(esdf, output / "esdf.png")

    metadata_path = output / "bev_metadata.json"
    metadata_payload = {
        **metadata.to_dict(),
        "camera": point_result.camera,
        "warning": warning,
    }
    metadata_path.write_text(
        json.dumps(metadata_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    debug_path = save_geometry_debug(
        point_result.depth,
        occupancy,
        esdf,
        output / "geometry_debug.png",
    )

    return GeometryResult(
        point_map_path=str(point_map_path),
        depth_path=str(depth_path),
        bev_occupancy_path=str(occupancy_path),
        free_space_mask_path=str(free_space_path),
        esdf_path=str(esdf_path),
        esdf_visualization_path=str(esdf_png_path),
        bev_metadata_path=str(metadata_path),
        debug_overlay_path=str(debug_path),
        available=True,
        backend=point_result.backend,
        metric_reliable=point_result.metric_reliable,
        warning=warning,
    )
