from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Velocity:
    linear_x: float = 0.0
    angular_z: float = 0.0

    @property
    def is_zero(self) -> bool:
        return self.linear_x == 0.0 and self.angular_z == 0.0


@dataclass(frozen=True)
class Limits:
    maximum_linear_x: float = 0.15
    maximum_angular_z: float = 0.20
    maximum_linear_acceleration: float = 0.20
    maximum_angular_acceleration: float = 0.40
    watchdog_seconds: float = 0.30


# 计划书 §6.5：来源取自 /go2w/control_source，由控制仲裁器发布。取值集合与
# arbiter_core.PRIORITY 加上仲裁器的几个中止态一致。
KNOWN_CONTROL_SOURCES = (
    "manual",
    "nav2",
    "search",
    "watchdog",
    "emergency_stop",
    "remote_override",
    "disarmed",
)


@dataclass(frozen=True)
class SafetyState:
    execution_enabled: bool = False
    operator_armed: bool = False
    lease_alive: bool = False
    lidar_fresh: bool = False
    rotation_clearance_valid: bool = False
    # 计划书 §6.5：Nav2 执行不再看 /lio/valid，改看坐标桥发布的
    # /go2w/nav/localization_valid（map -> odom_fused -> base_link 的新鲜度）。
    navigation_localization_fresh: bool = False
    robot_error_zero: bool = False
    emergency_stop: bool = True
    remote_override: bool = True


@dataclass(frozen=True)
class BridgeDecision:
    velocity: Velocity
    allowed: bool
    reason: str
    cancel_active_action: bool


def decide_velocity(
    requested: Velocity,
    previous: Velocity,
    *,
    command_age_seconds: float,
    dt_seconds: float,
    source: str,
    safety: SafetyState,
    limits: Limits = Limits(),
) -> BridgeDecision:
    blockers = []
    if not safety.execution_enabled:
        blockers.append("execution_disabled")
    if not safety.operator_armed:
        blockers.append("operator_not_armed")
    if not safety.lease_alive:
        blockers.append("lease_unavailable")
    if not safety.lidar_fresh:
        blockers.append("lidar_stale")
    if abs(requested.angular_z) > 1e-9 and not safety.rotation_clearance_valid:
        blockers.append("rotation_clearance_unvalidated")
    # 计划书 §6.5：控制来源必须是已知取值。来源字符串一旦拼错或为空，下面几条
    # 按来源分流的闸门会全部静默跳过——那正是最危险的一类失效。
    if source not in KNOWN_CONTROL_SOURCES:
        blockers.append("control_source_unknown")
    # Nav2 命令额外要求导航定位新鲜。这条只对 nav2 来源生效：手动遥控不该
    # 因为 Nav2 的坐标桥没起来而失去控制。
    if source == "nav2" and not safety.navigation_localization_fresh:
        blockers.append("navigation_localization_stale")
    if not safety.robot_error_zero:
        blockers.append("robot_error_or_unknown")
    if safety.emergency_stop:
        blockers.append("emergency_stop")
    if safety.remote_override:
        blockers.append("remote_override")
    if command_age_seconds < 0.0 or command_age_seconds > limits.watchdog_seconds:
        blockers.append("command_watchdog_expired")
    if not all(math.isfinite(item) for item in (requested.linear_x, requested.angular_z)):
        blockers.append("nonfinite_command")
    if blockers:
        return BridgeDecision(Velocity(), False, ",".join(blockers), True)
    dt = max(0.0, min(float(dt_seconds), limits.watchdog_seconds))
    target_linear = max(-limits.maximum_linear_x, min(limits.maximum_linear_x, requested.linear_x))
    target_angular = max(
        -limits.maximum_angular_z, min(limits.maximum_angular_z, requested.angular_z)
    )
    linear_delta = limits.maximum_linear_acceleration * dt
    angular_delta = limits.maximum_angular_acceleration * dt
    shaped = Velocity(
        _approach(previous.linear_x, target_linear, linear_delta),
        _approach(previous.angular_z, target_angular, angular_delta),
    )
    return BridgeDecision(shaped, True, "allowed", False)


def _approach(current: float, target: float, maximum_delta: float) -> float:
    return current + max(-maximum_delta, min(maximum_delta, target - current))
