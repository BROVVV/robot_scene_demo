"""Convert local path waypoints to ROS2 dry-run Twist commands."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.navigation.local_planner import LocalPlanResult
from app.schemas import Ros2MotionCommand, Ros2MotionPlan, Ros2Twist, Ros2Vector3


def motion_plan_from_path(
    plan: LocalPlanResult,
    *,
    linear_speed_mps: float,
    angular_speed_radps: float,
    command_rate_hz: float,
    waypoint_tolerance_m: float,
    generated_at: str | None = None,
) -> Ros2MotionPlan | None:
    if not plan.available or len(plan.path_xy) < 2:
        return None
    timestamp = generated_at or datetime.now(UTC).isoformat()
    commands: list[Ros2MotionCommand] = []
    heading = 0.0
    command_index = 1
    previous = plan.path_xy[0]
    for waypoint in plan.path_xy[1:]:
        dx = waypoint[0] - previous[0]
        dy = waypoint[1] - previous[1]
        distance = math.hypot(dx, dy)
        if distance < waypoint_tolerance_m:
            continue
        desired_heading = math.atan2(dy, dx)
        delta = _wrap_angle(desired_heading - heading)
        if abs(delta) > math.radians(3):
            commands.append(
                _turn_command(
                    command_index,
                    delta,
                    angular_speed_radps,
                )
            )
            command_index += 1
            heading = desired_heading
        commands.append(
            _move_command(command_index, distance, linear_speed_mps)
        )
        command_index += 1
        previous = waypoint
    commands.append(_stop_command(command_index))

    return Ros2MotionPlan(
        plan_id=f"ros2_local_plan_{timestamp.replace(':', '').replace('+', 'Z')}",
        generated_at=timestamp,
        dry_run=True,
        route_type="approach_visible_target",
        route_summary_zh="基于 BEV/ESDF 的局部路径 dry-run 指令。",
        command_rate_hz=command_rate_hz,
        commands=commands,
        safety_notes_zh=[
            "当前输出为 dry_run 数据，不会直接控制机器狗。",
            "heuristic 单目几何不可用于真机安全导航。",
            "真实执行必须接入深度、SLAM、避障、急停或厂商安全 SDK。",
            f"local_plan_status={plan.status}",
            f"collision_free={plan.collision_free}",
        ],
        integration_notes_zh=[
            "ROS2 节点可按 commands 顺序向 /cmd_vel 发布 geometry_msgs/msg/Twist。",
            "每条 command 持续发布 duration_sec 秒，发布频率使用 command_rate_hz。",
            "可先运行 python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json 预览指令。",
        ],
    )


def settings_kwargs(settings: Any) -> dict[str, float]:
    return {
        "linear_speed_mps": settings.cmd_vel_linear_speed,
        "angular_speed_radps": settings.cmd_vel_angular_speed,
        "command_rate_hz": settings.cmd_vel_command_rate_hz,
        "waypoint_tolerance_m": settings.cmd_vel_waypoint_tolerance_m,
    }


def _turn_command(index: int, angle_rad: float, angular_speed_radps: float) -> Ros2MotionCommand:
    action = "turn_left" if angle_rad >= 0 else "turn_right"
    return Ros2MotionCommand(
        command_id=f"cmd_{index:03d}",
        route_step_id=None,
        source_action=action,
        twist=Ros2Twist(
            angular=Ros2Vector3(z=angular_speed_radps if angle_rad >= 0 else -angular_speed_radps)
        ),
        duration_sec=round(max(0.2, abs(angle_rad) / angular_speed_radps), 3),
        turn_angle_deg=round(math.degrees(abs(angle_rad)), 2),
        description_zh=f"{'左' if angle_rad >= 0 else '右'}转 {math.degrees(abs(angle_rad)):.1f} 度",
    )


def _move_command(index: int, distance: float, linear_speed_mps: float) -> Ros2MotionCommand:
    return Ros2MotionCommand(
        command_id=f"cmd_{index:03d}",
        route_step_id=None,
        source_action="move_forward",
        twist=Ros2Twist(linear=Ros2Vector3(x=linear_speed_mps)),
        duration_sec=round(max(0.2, distance / linear_speed_mps), 3),
        distance_m=round(distance, 3),
        description_zh=f"沿局部路径前进 {distance:.2f} 米",
    )


def _stop_command(index: int) -> Ros2MotionCommand:
    return Ros2MotionCommand(
        command_id=f"cmd_{index:03d}",
        route_step_id=None,
        source_action="stop",
        twist=Ros2Twist(),
        duration_sec=1.0,
        description_zh="局部路径结束，保持停止。",
    )


def _wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle
