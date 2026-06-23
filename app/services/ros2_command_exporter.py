"""Export route plans as ROS2-friendly Twist command data."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.navigation.local_planner import LocalPlanResult
from app.navigation.path_to_cmd_vel import motion_plan_from_path, settings_kwargs
from app.schemas import (
    Ros2MotionCommand,
    Ros2MotionPlan,
    Ros2Twist,
    Ros2Vector3,
    RouteStep,
    SceneAnalysisResult,
)


DEFAULT_CMD_VEL_TOPIC = "/cmd_vel"
DEFAULT_FRAME_ID = "base_link"
DEFAULT_COMMAND_RATE_HZ = 10.0
DEFAULT_LINEAR_SPEED_MPS = 0.25
DEFAULT_ANGULAR_SPEED_RADPS = 0.5
DEFAULT_STOP_DURATION_SEC = 1.0
MIN_COMMAND_DURATION_SEC = 0.2


def build_ros2_motion_plan(
    result: SceneAnalysisResult,
    *,
    generated_at: str | None = None,
    topic: str = DEFAULT_CMD_VEL_TOPIC,
    frame_id: str = DEFAULT_FRAME_ID,
    command_rate_hz: float = DEFAULT_COMMAND_RATE_HZ,
    linear_speed_mps: float = DEFAULT_LINEAR_SPEED_MPS,
    angular_speed_radps: float = DEFAULT_ANGULAR_SPEED_RADPS,
) -> Ros2MotionPlan:
    """Convert a scene route plan to dry-run ROS2 /cmd_vel command data."""

    local_motion_plan = _build_from_local_plan(result, generated_at=generated_at)
    if local_motion_plan is not None:
        return local_motion_plan

    if linear_speed_mps <= 0:
        raise ValueError("linear_speed_mps must be positive")
    if angular_speed_radps <= 0:
        raise ValueError("angular_speed_radps must be positive")
    if command_rate_hz <= 0:
        raise ValueError("command_rate_hz must be positive")

    route_plan = result.route_plan
    steps = sorted(route_plan.steps, key=lambda item: item.step_id)
    commands = [
        _command_from_step(
            index=index,
            step=step,
            topic=topic,
            linear_speed_mps=linear_speed_mps,
            angular_speed_radps=angular_speed_radps,
        )
        for index, step in enumerate(steps, start=1)
    ]
    if not commands:
        commands.append(_safe_stop_command(topic=topic))

    timestamp = generated_at or datetime.now(UTC).isoformat()
    return Ros2MotionPlan(
        plan_id=f"ros2_motion_{timestamp.replace(':', '').replace('+', 'Z')}",
        generated_at=timestamp,
        dry_run=True,
        topic=topic,
        frame_id=frame_id,
        route_type=route_plan.route_type,
        route_summary_zh=route_plan.summary_zh,
        command_rate_hz=command_rate_hz,
        commands=commands,
        safety_notes_zh=[
            *route_plan.safety_notes_zh,
            "当前输出为 dry_run 数据，不会直接控制机器狗。",
            "真实执行前需要接入深度/避障/急停，并限制速度与可行走区域。",
        ],
        integration_notes_zh=[
            "ROS2 节点可按 commands 顺序向 /cmd_vel 发布 geometry_msgs/msg/Twist。",
            "每条 command 持续发布 duration_sec 秒，发布频率使用 command_rate_hz。",
            "可先运行 python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json 预览指令。",
            "接入 ROS2 后可运行 python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json --execute --allow-dry-run-plan 发布指令。",
            "所有 command 执行完毕后应再次发布零速度 Twist，确保机器狗停止。",
        ],
    )


def _build_from_local_plan(
    result: SceneAnalysisResult,
    *,
    generated_at: str | None,
) -> Ros2MotionPlan | None:
    if not result.local_plan:
        return None
    payload = result.local_plan
    path_xy = [
        (float(item[0]), float(item[1]))
        for item in payload.get("path_xy", [])
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    plan = LocalPlanResult(
        available=bool(payload.get("available")),
        status=str(payload.get("status") or "unknown"),
        path_xy=path_xy,
        goal_xy=_tuple_or_none(payload.get("goal_xy")),
        used_goal_xy=_tuple_or_none(payload.get("used_goal_xy")),
        progress_score=payload.get("progress_score"),
        collision_free=payload.get("collision_free"),
        min_clearance_m=payload.get("min_clearance_m"),
        planner_backend=str(payload.get("planner_backend") or "astar"),
        warning=payload.get("warning"),
        visualization_path=payload.get("visualization_path"),
    )
    settings = get_settings()
    return motion_plan_from_path(
        plan,
        generated_at=generated_at,
        **settings_kwargs(settings),
    )


def _tuple_or_none(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return float(value[0]), float(value[1])


def export_ros2_motion_plan(
    result: SceneAnalysisResult,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    plan = build_ros2_motion_plan(result)
    path.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _command_from_step(
    *,
    index: int,
    step: RouteStep,
    topic: str,
    linear_speed_mps: float,
    angular_speed_radps: float,
) -> Ros2MotionCommand:
    linear_x = 0.0
    angular_z = 0.0
    duration_sec = DEFAULT_STOP_DURATION_SEC

    if step.action == "move_forward":
        distance = max(0.0, step.distance_m or 0.0)
        linear_x = linear_speed_mps
        duration_sec = _duration(distance / linear_speed_mps)
    elif step.action == "move_backward":
        distance = max(0.0, step.distance_m or 0.0)
        linear_x = -linear_speed_mps
        duration_sec = _duration(distance / linear_speed_mps)
    elif step.action == "turn_left":
        angle_rad = math.radians(abs(step.turn_angle_deg or 0.0))
        angular_z = angular_speed_radps
        duration_sec = _duration(angle_rad / angular_speed_radps)
    elif step.action == "turn_right":
        angle_rad = math.radians(abs(step.turn_angle_deg or 0.0))
        angular_z = -angular_speed_radps
        duration_sec = _duration(angle_rad / angular_speed_radps)
    elif step.action == "stop":
        duration_sec = DEFAULT_STOP_DURATION_SEC

    return Ros2MotionCommand(
        command_id=f"cmd_{index:03d}",
        route_step_id=step.step_id,
        source_action=step.action,
        topic=topic,
        twist=Ros2Twist(
            linear=Ros2Vector3(x=linear_x),
            angular=Ros2Vector3(z=angular_z),
        ),
        duration_sec=duration_sec,
        distance_m=step.distance_m,
        turn_angle_deg=step.turn_angle_deg,
        description_zh=step.description_zh,
    )


def _safe_stop_command(topic: str) -> Ros2MotionCommand:
    return Ros2MotionCommand(
        command_id="cmd_001",
        route_step_id=None,
        source_action="stop",
        topic=topic,
        twist=Ros2Twist(),
        duration_sec=DEFAULT_STOP_DURATION_SEC,
        description_zh="无可执行路线，保持停止。",
    )


def _duration(value: float) -> float:
    return round(max(MIN_COMMAND_DURATION_SEC, value), 3)
