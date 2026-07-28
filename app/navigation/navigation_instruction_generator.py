"""Generate human-readable instructions from a visual navigation plan."""

from __future__ import annotations

import math
from typing import Any

from .models import NavigationPlan, Pose2D


def generate_navigation_instructions(plan: NavigationPlan) -> list[dict[str, Any]]:
    unit = "m" if plan.scale_status == "metric" else "个相对单位"
    distance_word = "前进约" if plan.scale_status == "metric" else "沿视频轨迹移动约"
    steps = [
        {
            "step": 1,
            "instruction": "从视频第一帧起点出发。",
            "state": "planned",
            "waypoint_type": "start",
        }
    ]
    counter = 2
    for previous, current in zip(plan.path, plan.path[1:]):
        turn = _turn_instruction(previous, current)
        distance = previous.distance_to(current)
        instruction = f"{turn}{distance_word} {distance:.2f} {unit}。"
        steps.append(
            {
                "step": counter,
                "instruction": instruction,
                "state": "planned",
                "distance": round(distance, 4),
                "scale_status": plan.scale_status,
            }
        )
        counter += 1
    if plan.navigation_strategy == "exploration":
        final = "到达探索 frontier 后重新观察，若仍未发现目标则选择下一个探索点。"
    elif plan.navigation_strategy == "last_known_reobserve":
        final = "到达目标最后已知观察区域后重新观察；若目标仍丢失则重新规划。"
    elif plan.navigation_strategy == "candidate_navigation":
        final = "到达疑似目标观察位姿后重新检测目标。"
    else:
        final = "到达目标前方观察位姿后停止并重新确认目标。"
    steps.append(
        {
            "step": counter,
            "instruction": final,
            "state": "reobserve",
            "waypoint_type": plan.waypoints[-1].waypoint_type if plan.waypoints else "goal",
        }
    )
    return steps


def _turn_instruction(previous: Pose2D, current: Pose2D) -> str:
    heading = math.atan2(current.y - previous.y, current.x - previous.x)
    delta = _normalize_angle(heading - previous.yaw)
    if delta > 0.45:
        return "向左调整方向，"
    if delta < -0.45:
        return "向右调整方向，"
    return "沿当前方向"


def _normalize_angle(value: float) -> float:
    while value > math.pi:
        value -= math.tau
    while value < -math.pi:
        value += math.tau
    return value
