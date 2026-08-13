from __future__ import annotations

import math
from pathlib import Path

import yaml
from go2w_description.description_config import load_official_reference

from .preprocess_core import PreprocessParameters


def load_safety_ready_config(lidar_path: str, geometry_path: str):
    lidar = yaml.safe_load(Path(lidar_path).read_text(encoding="utf-8")) or {}
    geometry = yaml.safe_load(Path(geometry_path).read_text(encoding="utf-8")) or {}
    if lidar.get("validation_status") != "validated" or not lidar.get("confirmed"):
        raise ValueError("LiDAR preprocessing thresholds are not measured and confirmed")
    if lidar.get("robot_model") != "Unitree Go2-W":
        raise ValueError("LiDAR thresholds are not identified as Unitree Go2-W")

    if geometry.get("reference_status") == "manufacturer_published":
        reference = load_official_reference(geometry_path)
        envelope = (reference.get("dimensions") or {}).get("standing_envelope_m") or {}
        footprint_length = float(envelope.get("length", 0.0))
        footprint_width = float(envelope.get("width", 0.0))
    else:
        if geometry.get("measurement_status") != "measured" or not geometry.get(
            "confirmed"
        ):
            raise ValueError("Go2-W physical geometry is not measured and confirmed")
        measurements = geometry.get("measurements") or {}

        def measured(key):
            value = (measurements.get(key) or {}).get("value")
            if value is None:
                raise ValueError(f"missing physical measurement: {key}")
            return float(value)

        footprint_length = measured("wheel_outer_envelope_length_m")
        footprint_width = measured("wheel_outer_envelope_width_m")

    required = (
        (lidar.get("height_m") or {}).get("minimum"),
        (lidar.get("height_m") or {}).get("maximum"),
        lidar.get("ground_separation_height_m"),
        lidar.get("self_filter_margin_m"),
        lidar.get("front_corridor_half_width_m"),
        lidar.get("rotation_envelope_radius_m"),
    )
    if any(value is None for value in required):
        raise ValueError("one or more LiDAR geometry thresholds are unmeasured")
    numeric = tuple(float(value) for value in required)
    minimum_range = float(lidar["range_m"]["minimum"])
    maximum_range = float(lidar["range_m"]["maximum"])
    all_numeric = numeric + (
        minimum_range,
        maximum_range,
        footprint_length,
        footprint_width,
    )
    if not all(math.isfinite(value) for value in all_numeric):
        raise ValueError("LiDAR preprocessing parameters must be finite")
    if (
        minimum_range <= 0.0
        or maximum_range <= minimum_range
        or numeric[1] <= numeric[0]
        or footprint_length <= 0.0
        or footprint_width <= 0.0
        or numeric[3] < 0.0
        or numeric[4] <= footprint_width / 2.0
        or numeric[5]
        < math.hypot(footprint_length / 2.0, footprint_width / 2.0)
    ):
        raise ValueError("LiDAR preprocessing geometry is unsafe or inconsistent")
    self_regions: list[tuple[float, float, float, float, float, float]] = []
    for region in lidar.get("self_regions") or []:
        if not isinstance(region, dict):
            raise ValueError("self region must be a mapping")
        bounds = tuple(
            float(region.get(key))
            for key in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
        )
        if len(bounds) != 6 or not all(math.isfinite(value) for value in bounds):
            raise ValueError("self region bounds must be finite")
        if not (
            bounds[0] < bounds[1]
            and bounds[2] < bounds[3]
            and bounds[4] < bounds[5]
        ):
            raise ValueError("self region bounds must be ordered min < max")
        self_regions.append(bounds)
    parameters = PreprocessParameters(
        minimum_range=minimum_range,
        maximum_range=maximum_range,
        minimum_height=numeric[0],
        maximum_height=numeric[1],
        ground_height=numeric[2],
        self_half_length=footprint_length / 2.0,
        self_half_width=footprint_width / 2.0,
        self_filter_margin=numeric[3],
        front_corridor_half_width=numeric[4],
        rotation_envelope_radius=numeric[5],
        self_regions=tuple(self_regions),
    )
    return lidar, parameters
