"""RTAB-Map SpatialProvider adapter.

Consumes ROS2 ``/rtabmap/map`` and ``/rtabmap/odom`` when available and
converts them into the project's spatial models.  When RTAB-Map is not
running / not installed it degrades to ``CameraLocalSpatialProvider``.
"""

from __future__ import annotations

import math
from typing import Any

from app.spatial.camera_local_spatial_provider import CameraLocalSpatialProvider
from app.spatial.frontier_extractor import FrontierExtractor
from app.spatial.models import (
    SPATIAL_QUALITY_CAMERA_LOCAL,
    SPATIAL_QUALITY_RELATIVE_RGBD,
    FrontierCandidate,
    SpatialMapSnapshot,
    SpatialPose,
)


class RtabmapSpatialProvider:
    def __init__(
        self,
        *,
        fallback: CameraLocalSpatialProvider | None = None,
        enable_ros: bool = False,
        map_topic: str = "/rtabmap/map",
        odom_topic: str = "/rtabmap/odom",
    ) -> None:
        self.fallback = fallback or CameraLocalSpatialProvider()
        self.frontier_extractor = FrontierExtractor(min_component_size=1)
        self._map: SpatialMapSnapshot | None = None
        self._pose: SpatialPose | None = None
        self._available = False
        self._last_error: str | None = None
        self._node = None
        self._subs: list[Any] = []
        if enable_ros:
            self._enable_ros(map_topic=map_topic, odom_topic=odom_topic)

    def _enable_ros(self, *, map_topic: str, odom_topic: str) -> None:
        try:
            import rclpy
            from nav_msgs.msg import OccupancyGrid, Odometry

            if not rclpy.ok():
                rclpy.init()
            self._node = rclpy.create_node("go2w_rtabmap_spatial_provider")
            self._subs.append(
                self._node.create_subscription(
                    OccupancyGrid, map_topic, self._on_occupancy_grid, 10
                )
            )
            self._subs.append(
                self._node.create_subscription(
                    Odometry, odom_topic, self._on_odometry, 10
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"rtabmap ros init failed: {exc}"
            self._node = None

    def spin_once(self) -> None:
        if self._node is not None:
            try:
                import rclpy

                # Allow DDS discovery + latched map delivery.
                rclpy.spin_once(self._node, timeout_sec=0.05)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)

    def close(self) -> None:
        if self._node is not None:
            try:
                self._node.destroy_node()
            except Exception:  # noqa: BLE001
                pass
            self._node = None

    def quality(self) -> str:
        if self._map is not None and self.get_pose() is not None:
            return SPATIAL_QUALITY_RELATIVE_RGBD
        return SPATIAL_QUALITY_CAMERA_LOCAL

    def set_pose(self, pose: SpatialPose | None) -> None:
        # RTAB-Map pose comes from /rtabmap/odom; fallback keeps the robot
        # pose for relative frontier mode when RTAB-Map is not available.
        self.fallback.set_pose(pose)
        if pose is not None and self._pose is None:
            self._pose = pose

    def get_pose(self) -> SpatialPose | None:
        return self._pose or self.fallback.get_pose()

    def get_map(self) -> SpatialMapSnapshot | None:
        return self._map

    def get_frontiers(self) -> list[FrontierCandidate]:
        if self._map is not None:
            pose = self.get_pose()
            frontiers = self.frontier_extractor.extract(self._map, pose)
            if frontiers:
                return frontiers
        return self.fallback.get_frontiers()

    def camera_point_to_spatial(
        self,
        xyz_camera: tuple[float, float, float],
        pose: SpatialPose | None = None,
    ) -> tuple[float, float, float] | None:
        # Requires a calibrated camera-to-base transform; not claimed yet.
        return None

    def health(self) -> dict[str, Any]:
        return {
            "rtabmap_available": self._available,
            "map_received": self._map is not None,
            "pose_received": self._pose is not None,
            "quality": self.quality(),
            "last_error": self._last_error,
            "note": "RTAB-Map ROS2 topics",
        }

    # ------------------------------------------------------------------ #
    # ROS callbacks                                                       #
    # ------------------------------------------------------------------ #
    def _on_occupancy_grid(self, msg: Any) -> None:
        self._available = True
        width = int(msg.info.width)
        height = int(msg.info.height)
        res = float(msg.info.resolution)
        origin = (float(msg.info.origin.position.x), float(msg.info.origin.position.y))
        free: list[tuple[int, int]] = []
        occupied: list[tuple[int, int]] = []
        unknown: list[tuple[int, int]] = []
        data = list(msg.data)
        for index, value in enumerate(data):
            x = index % width
            y = index // width
            if value < 0:
                unknown.append((x, y))
            elif value == 0:
                free.append((x, y))
            else:
                occupied.append((x, y))
        self._map = SpatialMapSnapshot(
            revision=getattr(self._map, "revision", 0) + 1,
            resolution_m=res,
            origin=origin,
            width=width,
            height=height,
            free=free,
            occupied=occupied,
            unknown=unknown,
            quality=SPATIAL_QUALITY_RELATIVE_RGBD,
            source="rtabmap",
            provenance={"frame_id": msg.header.frame_id},
        )

    def _on_odometry(self, msg: Any) -> None:
        self._available = True
        pose = msg.pose.pose
        q = pose.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
        self._pose = SpatialPose(
            x=float(pose.position.x),
            y=float(pose.position.y),
            yaw=float(yaw),
            frame_id=msg.header.frame_id or "map",
            quality=SPATIAL_QUALITY_RELATIVE_RGBD,
            source="rtabmap_odom",
            provenance={"stamp_ns": msg.header.stamp.nanosec},
        )
