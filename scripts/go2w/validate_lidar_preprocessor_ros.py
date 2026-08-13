#!/usr/bin/env python3
"""Validate the read-only Go2-W LiDAR preprocessing ROS outputs."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import rclpy
import yaml
from geometry_msgs.msg import Vector3Stamped
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Bool


def finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def cloud_xyz(message: PointCloud2) -> np.ndarray:
    records = point_cloud2.read_points(
        message, field_names=("x", "y", "z"), skip_nans=True
    )
    return np.column_stack(
        tuple(np.asarray(records[name], dtype=np.float64) for name in ("x", "y", "z"))
    )


class Validator(Node):
    def __init__(self) -> None:
        super().__init__("go2w_lidar_preprocessor_readonly_validator")
        self.scans: list[LaserScan] = []
        self.obstacles: list[tuple[str, np.ndarray]] = []
        self.filtered: list[tuple[str, np.ndarray]] = []
        self.clearances: list[Vector3Stamped] = []
        self.freshness: list[bool] = []
        self.create_subscription(
            LaserScan, "/go2w/lidar/scan", self.scans.append, qos_profile_sensor_data
        )
        self.create_subscription(
            PointCloud2,
            "/go2w/lidar/obstacles",
            lambda message: self.obstacles.append(
                (message.header.frame_id, cloud_xyz(message))
            ),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            PointCloud2,
            "/go2w/lidar/cloud_filtered",
            lambda message: self.filtered.append(
                (message.header.frame_id, cloud_xyz(message))
            ),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            Vector3Stamped,
            "/go2w/lidar/clearance",
            self.clearances.append,
            10,
        )
        self.create_subscription(
            Bool,
            "/go2w/safety/lidar_fresh",
            lambda message: self.freshness.append(bool(message.data)),
            10,
        )


def result(node: Validator, minimum_samples: int, ground_height: float) -> dict:
    obstacle_points = np.concatenate(
        [points for _, points in node.obstacles], axis=0
    )
    self_points = (
        (np.abs(obstacle_points[:, 0]) <= 0.35 + 0.04)
        & (np.abs(obstacle_points[:, 1]) <= 0.215 + 0.04)
    )
    finite_ranges = [
        float(value)
        for scan in node.scans
        for value in scan.ranges
        if math.isfinite(value)
    ]
    checks = {
        "minimum_samples": min(
            len(node.scans),
            len(node.obstacles),
            len(node.filtered),
            len(node.clearances),
        )
        >= minimum_samples,
        "fresh_true_seen": sum(node.freshness) >= 5,
        "scan_frame_base_link": bool(node.scans)
        and all(scan.header.frame_id == "base_link" for scan in node.scans),
        "cloud_frames_base_link": bool(node.obstacles)
        and all(
            frame == "base_link"
            for frame, _ in node.obstacles + node.filtered
        ),
        "scan_has_720_bins": bool(node.scans)
        and all(len(scan.ranges) == 720 for scan in node.scans),
        "scan_increment_is_half_degree": bool(node.scans)
        and all(
            abs(scan.angle_increment - math.pi / 360.0) <= 1e-6
            for scan in node.scans
        ),
        "self_envelope_removed": not bool(np.any(self_points)),
        "obstacles_above_ground_threshold": float(np.min(obstacle_points[:, 2]))
        > ground_height - 1e-6,
        "finite_scan_obstacles_seen": bool(finite_ranges),
    }
    last_clearance = node.clearances[-1].vector
    return {
        "schema_version": "1.0",
        "validation_type": "live_read_only_lidar_preprocessor",
        "robot_motion_commanded": False,
        "samples": {
            "scan": len(node.scans),
            "obstacles": len(node.obstacles),
            "filtered": len(node.filtered),
            "clearance": len(node.clearances),
            "fresh_messages": len(node.freshness),
            "fresh_true": sum(node.freshness),
        },
        "obstacles": {
            "points": int(len(obstacle_points)),
            "z_min_m": float(np.min(obstacle_points[:, 2])),
            "z_median_m": float(np.median(obstacle_points[:, 2])),
            "z_max_m": float(np.max(obstacle_points[:, 2])),
            "inside_self_envelope_points": int(np.sum(self_points)),
        },
        "scan": {
            "finite_ranges": len(finite_ranges),
            "minimum_finite_m": min(finite_ranges),
            "maximum_finite_m": max(finite_ranges),
        },
        "last_clearance_m": {
            "front": finite_or_none(last_clearance.x),
            "left": finite_or_none(last_clearance.y),
            "right": finite_or_none(last_clearance.z),
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    if args.minimum_samples < 10:
        raise SystemExit("at least 10 samples are required")

    rclpy.init()
    node = Validator()
    deadline = time.monotonic() + args.timeout_seconds
    try:
        while (
            rclpy.ok()
            and time.monotonic() < deadline
            and (
                len(node.scans) < args.minimum_samples
                or len(node.obstacles) < args.minimum_samples
                or len(node.filtered) < args.minimum_samples
                or len(node.clearances) < args.minimum_samples
                or sum(node.freshness) < 5
            )
        ):
            rclpy.spin_once(node, timeout_sec=0.1)
        if not node.obstacles or not node.clearances:
            raise SystemExit("LiDAR preprocessor outputs were not received")
        lidar_config = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "configs/go2w/lidar_preprocess.yaml").read_text(
                encoding="utf-8"
            )
        ) or {}
        ground_height = float(lidar_config["ground_separation_height_m"])
        payload = result(node, args.minimum_samples, ground_height)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if payload["passed"] else 2
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
