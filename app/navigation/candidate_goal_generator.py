"""Generate candidate re-observation goals from target search regions."""

from __future__ import annotations

from typing import Any

from .models import NavigationWaypoint, VideoFramePose


def generate_candidate_goals(
    target_search_result: dict[str, Any],
    trajectory: list[VideoFramePose],
    max_candidates: int = 5,
) -> list[NavigationWaypoint]:
    regions = target_search_result.get("candidate_regions") or []
    goals: list[NavigationWaypoint] = []
    for index, region in enumerate(regions[:max_candidates]):
        frame_pose = _nearest_pose(trajectory, region.get("frame_id"))
        if frame_pose is None:
            continue
        goals.append(
            NavigationWaypoint(
                waypoint_id=f"candidate_{index:02d}",
                pose=frame_pose.pose,
                source_frame_id=frame_pose.frame_id,
                semantic_label=str(region.get("reason") or "候选区域重新观察"),
                waypoint_type="candidate",
                confidence=float(region.get("priority", 0.45) or 0.45),
                provenance={
                    "source": "candidate_regions",
                    "region": region,
                    "scale_verified": frame_pose.pose.scale_status == "metric",
                },
            )
        )
    return goals


def _nearest_pose(trajectory: list[VideoFramePose], frame_id) -> VideoFramePose | None:
    if not trajectory:
        return None
    if frame_id is None:
        return trajectory[-1]
    target = int(frame_id)
    return min(trajectory, key=lambda item: abs(item.frame_id - target))
