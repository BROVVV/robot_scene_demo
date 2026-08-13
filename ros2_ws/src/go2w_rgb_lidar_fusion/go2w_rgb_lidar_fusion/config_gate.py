from __future__ import annotations

from pathlib import Path

import yaml

from .fusion_core import FusionParameters


def load_fusion_gate(fusion_path: str, camera_path: str, extrinsics_path: str):
    fusion = yaml.safe_load(Path(fusion_path).read_text(encoding="utf-8")) or {}
    if not fusion.get("enabled") or fusion.get("validation_status") != "validated":
        raise ValueError("RGB-LiDAR fusion is disabled or unvalidated")
    load_extrinsics_gate(camera_path, extrinsics_path)
    parameters = FusionParameters(
        maximum_timestamp_delta_ms=float(fusion["maximum_timestamp_delta_ms"]),
        minimum_mask_points=int(fusion["minimum_mask_points"]),
        mask_boundary_margin_px=int(fusion["mask_boundary_margin_px"]),
        depth_mad_multiplier=float(fusion["depth_mad_multiplier"]),
        cluster_tolerance_m=float(fusion["cluster_tolerance_m"]),
        minimum_cluster_points=int(fusion["minimum_cluster_points"]),
        maximum_cluster_extent_m=float(fusion["maximum_cluster_extent_m"]),
    )
    return fusion, parameters


def load_extrinsics_gate(camera_path: str, extrinsics_path: str):
    camera = yaml.safe_load(Path(camera_path).read_text(encoding="utf-8")) or {}
    extrinsics = yaml.safe_load(Path(extrinsics_path).read_text(encoding="utf-8")) or {}
    if camera.get("calibration_status") != "calibrated":
        raise ValueError("camera intrinsics are not calibrated")
    if extrinsics.get("calibration_status") != "calibrated" or not extrinsics.get("confirmed"):
        raise ValueError("camera-LiDAR extrinsics are not calibrated and confirmed")
    validation = extrinsics.get("validation") or {}
    if int(validation.get("completed_scene_count", 0)) < int(
        validation.get("required_scene_count", 5)
    ):
        raise ValueError("fewer than five extrinsic validation scenes")
    error = validation.get("mean_edge_error_px")
    maximum = validation.get("maximum_allowed_mean_edge_error_px")
    if error is None or maximum is None or float(error) > float(maximum):
        raise ValueError("extrinsic overlay edge error gate failed")
    if not validation.get("moved_position_recheck_passed"):
        raise ValueError("moved-position extrinsic recheck has not passed")
    return camera, extrinsics
