"""Shared models for video-to-navigation planning."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


SCALE_METRIC = "metric"
SCALE_RELATIVE = "relative"
SCALE_UNKNOWN = "unknown"


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float = 0.0
    frame_id: str = "video_map"
    source: str = "video_visual_odometry"
    scale_status: str = SCALE_RELATIVE
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(math.isfinite(float(v)) for v in (self.x, self.y, self.yaw)):
            raise ValueError("Pose2D coordinates must be finite")
        if self.scale_status not in {SCALE_METRIC, SCALE_RELATIVE, SCALE_UNKNOWN}:
            raise ValueError(f"Unsupported scale_status: {self.scale_status}")

    def distance_to(self, other: "Pose2D") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Pose2D":
        return cls(
            x=float(value.get("x", 0.0)),
            y=float(value.get("y", 0.0)),
            yaw=float(value.get("yaw", value.get("yaw_rad", 0.0))),
            frame_id=str(value.get("frame_id", "video_map")),
            source=str(value.get("source", "video_visual_odometry")),
            scale_status=str(value.get("scale_status", SCALE_RELATIVE)),
            provenance=dict(value.get("provenance") or {}),
        )


@dataclass
class VideoFramePose:
    frame_id: int
    timestamp_sec: float
    pose: Pose2D
    confidence: float = 1.0
    tracking_status: str = "tracked"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["pose"] = self.pose.to_dict()
        return value


@dataclass
class NavigationWaypoint:
    waypoint_id: str
    pose: Pose2D
    source_frame_id: int | None = None
    semantic_label: str = ""
    waypoint_type: str = "trajectory"
    confidence: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["pose"] = self.pose.to_dict()
        return value


@dataclass
class NavigationPlan:
    plan_id: str
    mode: str
    planning_frame: str
    scale_status: str
    start_pose: Pose2D
    goal_pose: Pose2D | None
    waypoints: list[NavigationWaypoint]
    path: list[Pose2D]
    path_length: float | None
    estimated_time_sec: float | None
    navigation_strategy: str
    target_status: str
    confidence: float
    executable: bool
    executable_reason: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "mode": self.mode,
            "planning_frame": self.planning_frame,
            "scale_status": self.scale_status,
            "start_pose": self.start_pose.to_dict(),
            "goal_pose": self.goal_pose.to_dict() if self.goal_pose else None,
            "waypoints": [item.to_dict() for item in self.waypoints],
            "path": [item.to_dict() for item in self.path],
            "path_length": self.path_length,
            "estimated_time_sec": self.estimated_time_sec,
            "navigation_strategy": self.navigation_strategy,
            "target_status": self.target_status,
            "confidence": self.confidence,
            "executable": self.executable,
            "executable_reason": self.executable_reason,
            "provenance": self.provenance,
        }


def path_length(path: list[Pose2D]) -> float:
    return sum(a.distance_to(b) for a, b in zip(path, path[1:]))
