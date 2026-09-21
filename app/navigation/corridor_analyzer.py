"""计划书 §7：走廊宽度识别与适配。

只做三件事：沿 Nav2 返回的路径逐点测左右净空、按最小单侧机身净空判定这条
路径能不能过、以及算出该段路径允许的最高线速度。刻意不修改机器人
footprint、``inflation_radius`` 和 Collision Monitor 停止区 —— 那些是全局常量，
导航中途改它们只会让路径抖动。

UNKNOWN 一律按障碍物处理（fail-closed）：地图上没探到的地方不能当成空走廊。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

# 计划书 §7.3 / §8 固定的几何常量。
HALF_BODY_WIDTH_M = 0.215
FOOTPRINT_PADDING_M = 0.04
OCCUPIED_THRESHOLD = 65
MAX_LATERAL_SEARCH_M = 2.0
SAMPLE_STEP_M = 0.20
EDGE_TRIM_M = 0.20

# 计划书 §7.4 固定的安全规则。
HARD_SIDE_GAP_M = 0.40
PREFERRED_SIDE_GAP_M = 0.80
MIN_SPEED_MPS = 0.05
MAX_SPEED_MPS = 0.10

DECISION_OK = "OK"
DECISION_TOO_NARROW = "TOO_NARROW"

# 候选评分的对称净空权重（计划书 §7.5，固定 0.25）。
CENTER_SCORE_WEIGHT = 0.25


@dataclass(frozen=True)
class CorridorGrid:
    """Nav2 OccupancyGrid 的最小只读视图。

    ``data`` 为行主序整数，``-1`` 表示未知；``value_at`` 返回 ``None`` 表示
    坐标落在地图之外（同样按障碍物处理）。
    """

    resolution: float
    origin_x: float
    origin_y: float
    width: int
    height: int
    data: Sequence[int]

    def value_at(self, x: float, y: float) -> int | None:
        if self.resolution <= 0.0:
            raise ValueError("resolution 必须大于 0")
        column = int(math.floor((float(x) - self.origin_x) / self.resolution))
        row = int(math.floor((float(y) - self.origin_y) / self.resolution))
        if column < 0 or row < 0 or column >= self.width or row >= self.height:
            return None
        index = row * self.width + column
        if index >= len(self.data):
            return None
        return int(self.data[index])

    def blocked(self, x: float, y: float) -> bool:
        value = self.value_at(x, y)
        if value is None:
            return True
        if value < 0:
            return True
        return value >= OCCUPIED_THRESHOLD


@dataclass(frozen=True)
class PathClearanceSample:
    s_m: float
    x: float
    y: float
    left_distance_m: float
    right_distance_m: float
    corridor_width_m: float
    left_body_gap_m: float
    right_body_gap_m: float
    min_side_gap_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "s_m": round(self.s_m, 3),
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "left_distance_m": round(self.left_distance_m, 3),
            "right_distance_m": round(self.right_distance_m, 3),
            "corridor_width_m": round(self.corridor_width_m, 3),
            "left_body_gap_m": round(self.left_body_gap_m, 3),
            "right_body_gap_m": round(self.right_body_gap_m, 3),
            "min_side_gap_m": round(self.min_side_gap_m, 3),
        }


@dataclass(frozen=True)
class CorridorDecision:
    decision: str
    reject_reason: str
    min_side_gap_m: float | None
    min_left_body_gap_m: float | None
    min_right_body_gap_m: float | None
    corridor_width_m: float | None
    center_error_m: float | None
    center_score: float
    speed_limit_mps: float
    samples: tuple[PathClearanceSample, ...] = ()

    @property
    def passable(self) -> bool:
        return self.decision == DECISION_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "corridor_decision": self.decision,
            "reject_reason": self.reject_reason,
            "min_side_gap_m": _round(self.min_side_gap_m),
            "min_left_body_gap_m": _round(self.min_left_body_gap_m),
            "min_right_body_gap_m": _round(self.min_right_body_gap_m),
            "corridor_width_m": _round(self.corridor_width_m),
            "center_error_m": _round(self.center_error_m),
            "center_score": round(self.center_score, 4),
            "speed_limit_mps": round(self.speed_limit_mps, 4),
            "samples": len(self.samples),
        }


def speed_limit_from_clearance(min_side_gap_m: float | None) -> float:
    """计划书 §7.4 的唯一速度公式。

    ``speed = clamp(0.05 + (gap - 0.40) / 0.40 * 0.05, 0.05, 0.10)``。
    低于硬净空返回 0（该路径本来就该被拒）。
    """
    if min_side_gap_m is None:
        return 0.0
    gap = float(min_side_gap_m)
    if gap < HARD_SIDE_GAP_M:
        return 0.0
    speed = MIN_SPEED_MPS + (gap - HARD_SIDE_GAP_M) / (
        PREFERRED_SIDE_GAP_M - HARD_SIDE_GAP_M
    ) * (MAX_SPEED_MPS - MIN_SPEED_MPS)
    return max(MIN_SPEED_MPS, min(MAX_SPEED_MPS, speed))


def center_score(left_body_gap_m: float, right_body_gap_m: float) -> float:
    """计划书 §7.5：路径离走廊中线有多近，1 表示正中。"""
    width = max(0.0, float(left_body_gap_m)) + max(0.0, float(right_body_gap_m))
    error = abs(float(left_body_gap_m) - float(right_body_gap_m))
    return max(0.0, 1.0 - error / max(width, 0.01))


def measure_path_clearance(
    path: Sequence[Any],
    occupancy_grid: CorridorGrid,
) -> list[PathClearanceSample]:
    """沿路径每 ``0.20 m`` 采样一次，量左右到障碍的距离。

    ``path`` 接受 ``(x, y)`` 序列，也接受带 ``x`` / ``y`` 属性的对象或字典
    （Nav2 的 ``PoseStamped`` 序列解包后即可）。
    """
    points = [_xy(item) for item in path]
    if not points:
        return []
    samples = _sample_polyline(points, SAMPLE_STEP_M)
    total_length = samples[-1][0] if samples else 0.0
    if total_length > 2.0 * EDGE_TRIM_M:
        trimmed = [
            item for item in samples if EDGE_TRIM_M <= item[0] <= total_length - EDGE_TRIM_M
        ]
        if trimmed:
            samples = trimmed
    result: list[PathClearanceSample] = []
    for s_m, x, y, heading in samples:
        left, right = _lateral_distances(x, y, heading, occupancy_grid)
        left_gap = left - HALF_BODY_WIDTH_M - FOOTPRINT_PADDING_M
        right_gap = right - HALF_BODY_WIDTH_M - FOOTPRINT_PADDING_M
        result.append(
            PathClearanceSample(
                s_m=s_m,
                x=x,
                y=y,
                left_distance_m=left,
                right_distance_m=right,
                corridor_width_m=left + right,
                left_body_gap_m=left_gap,
                right_body_gap_m=right_gap,
                min_side_gap_m=min(left_gap, right_gap),
            )
        )
    return result


def classify_path_clearance(samples: Sequence[PathClearanceSample]) -> CorridorDecision:
    """沿整条路径取最小单侧机身净空判定；找不到采样点时 fail-closed。"""
    if not samples:
        return CorridorDecision(
            decision=DECISION_TOO_NARROW,
            reject_reason="NO_PATH_SAMPLES",
            min_side_gap_m=None,
            min_left_body_gap_m=None,
            min_right_body_gap_m=None,
            corridor_width_m=None,
            center_error_m=None,
            center_score=0.0,
            speed_limit_mps=0.0,
            samples=(),
        )
    binding = min(samples, key=lambda item: item.min_side_gap_m)
    min_side_gap = binding.min_side_gap_m
    score = center_score(binding.left_body_gap_m, binding.right_body_gap_m)
    if min_side_gap < HARD_SIDE_GAP_M:
        return CorridorDecision(
            decision=DECISION_TOO_NARROW,
            reject_reason="TOO_NARROW",
            min_side_gap_m=min_side_gap,
            min_left_body_gap_m=binding.left_body_gap_m,
            min_right_body_gap_m=binding.right_body_gap_m,
            corridor_width_m=binding.corridor_width_m,
            center_error_m=abs(binding.left_body_gap_m - binding.right_body_gap_m),
            center_score=score,
            speed_limit_mps=0.0,
            samples=tuple(samples),
        )
    return CorridorDecision(
        decision=DECISION_OK,
        reject_reason="",
        min_side_gap_m=min_side_gap,
        min_left_body_gap_m=binding.left_body_gap_m,
        min_right_body_gap_m=binding.right_body_gap_m,
        corridor_width_m=binding.corridor_width_m,
        center_error_m=abs(binding.left_body_gap_m - binding.right_body_gap_m),
        center_score=score,
        speed_limit_mps=speed_limit_from_clearance(min_side_gap),
        samples=tuple(samples),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _xy(item: Any) -> tuple[float, float]:
    if isinstance(item, dict):
        return (float(item["x"]), float(item["y"]))
    if hasattr(item, "x") and hasattr(item, "y"):
        return (float(item.x), float(item.y))
    return (float(item[0]), float(item[1]))


def _sample_polyline(
    points: Sequence[tuple[float, float]], step_m: float
) -> list[tuple[float, float, float, float]]:
    """等距采样；返回 ``(s_m, x, y, 切线朝向)``。

    切线用相邻路径点差分求得，所以弯道用的是局部法向而不是全局固定横轴。
    退化路径（单点或原地转向）没有可用的切线，取 0 弧度，左右即 ±y。
    """
    step = max(1e-3, float(step_m))
    if len(points) == 1:
        return [(0.0, points[0][0], points[0][1], 0.0)]
    samples: list[tuple[float, float, float, float]] = []
    cumulative = 0.0
    for index in range(len(points) - 1):
        ax, ay = points[index]
        bx, by = points[index + 1]
        span = math.hypot(bx - ax, by - ay)
        if span <= 1e-9:
            continue
        heading = math.atan2(by - ay, bx - ax)
        count = max(1, int(math.ceil(span / step)))
        for k in range(count):
            t = k / count
            samples.append(
                (cumulative + span * t, ax + (bx - ax) * t, ay + (by - ay) * t, heading)
            )
        cumulative += span
    if not samples:
        return [(0.0, points[0][0], points[0][1], 0.0)]
    last = points[-1]
    samples.append((cumulative, last[0], last[1], samples[-1][3]))
    return samples


def _lateral_distances(
    x: float, y: float, heading: float, grid: CorridorGrid
) -> tuple[float, float]:
    step = max(1e-3, float(grid.resolution))
    # 左法向 = 切线逆时针转 90°；右法向为反方向。
    left_x, left_y = -math.sin(heading), math.cos(heading)
    right_x, right_y = math.sin(heading), -math.cos(heading)
    return (
        _ray_distance(x, y, left_x, left_y, step, grid),
        _ray_distance(x, y, right_x, right_y, step, grid),
    )


def _ray_distance(
    x: float, y: float, dx: float, dy: float, step: float, grid: CorridorGrid
) -> float:
    distance = step
    while distance <= MAX_LATERAL_SEARCH_M:
        if grid.blocked(x + dx * distance, y + dy * distance):
            return distance
        distance += step
    return MAX_LATERAL_SEARCH_M


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


__all__ = [
    "CENTER_SCORE_WEIGHT",
    "CorridorDecision",
    "CorridorGrid",
    "DECISION_OK",
    "DECISION_TOO_NARROW",
    "FOOTPRINT_PADDING_M",
    "HALF_BODY_WIDTH_M",
    "HARD_SIDE_GAP_M",
    "MAX_LATERAL_SEARCH_M",
    "MAX_SPEED_MPS",
    "MIN_SPEED_MPS",
    "OCCUPIED_THRESHOLD",
    "PREFERRED_SIDE_GAP_M",
    "PathClearanceSample",
    "SAMPLE_STEP_M",
    "center_score",
    "classify_path_clearance",
    "measure_path_clearance",
    "speed_limit_from_clearance",
]
