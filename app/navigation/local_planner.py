"""Lightweight A* local planner on BEV ESDF."""

from __future__ import annotations

import heapq
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.geometry.types import GeometryResult, grid_to_world, read_metadata, world_to_grid
from app.navigation.visualization import save_local_plan_visualization


@dataclass(frozen=True)
class PlannerConfig:
    backend: str = "astar"
    max_steps: int = 2000
    goal_tolerance_m: float = 0.20
    min_clearance_m: float = 0.20
    allow_partial: bool = True


@dataclass(frozen=True)
class LocalPlanResult:
    available: bool
    status: str
    path_xy: list[tuple[float, float]]
    goal_xy: tuple[float, float] | None
    used_goal_xy: tuple[float, float] | None
    progress_score: float | None
    collision_free: bool | None
    min_clearance_m: float | None
    planner_backend: str
    warning: str | None = None
    visualization_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_settings(settings: Any) -> PlannerConfig:
    return PlannerConfig(
        backend=settings.local_planner_backend,
        max_steps=settings.local_planner_max_steps,
        goal_tolerance_m=settings.local_planner_goal_tolerance_m,
        min_clearance_m=settings.local_planner_min_clearance_m,
        allow_partial=settings.local_planner_allow_partial,
    )


def plan_to_goal(
    geometry: GeometryResult,
    goal_xy: tuple[float, float],
    config: PlannerConfig,
) -> LocalPlanResult:
    if not geometry.available or not geometry.esdf_path or not geometry.bev_metadata_path:
        return LocalPlanResult(
            available=False,
            status="no_geometry",
            path_xy=[],
            goal_xy=goal_xy,
            used_goal_xy=None,
            progress_score=None,
            collision_free=None,
            min_clearance_m=None,
            planner_backend=config.backend,
            warning="Geometry/ESDF unavailable; falling back to route-plan commands.",
        )
    if config.backend != "astar":
        return LocalPlanResult(
            available=False,
            status="invalid_goal",
            path_xy=[],
            goal_xy=goal_xy,
            used_goal_xy=None,
            progress_score=None,
            collision_free=None,
            min_clearance_m=None,
            planner_backend=config.backend,
            warning=f"Unsupported local planner backend: {config.backend}",
        )

    esdf = np.load(geometry.esdf_path)
    metadata = read_metadata(geometry.bev_metadata_path)
    start = _nearest_free_cell(esdf, metadata, (0.0, 0.0), config.min_clearance_m)
    goal = _nearest_free_cell(esdf, metadata, goal_xy, config.min_clearance_m)
    if start is None:
        return _no_path("invalid_goal", goal_xy, config, "No free start cell found.")
    if goal is None:
        return _no_path("invalid_goal", goal_xy, config, "No free goal cell found.")

    path_cells, status, warning = _astar(esdf, metadata, start, goal, config)
    if not path_cells:
        return _no_path("no_path", goal_xy, config, warning or "A* found no path.")

    path_xy = [grid_to_world(row, col, metadata) for row, col in path_cells]
    used_goal_xy = path_xy[-1]
    goal_distance = math.dist((0.0, 0.0), goal_xy)
    remaining = math.dist(used_goal_xy, goal_xy)
    progress = None if goal_distance <= 1e-6 else max(0.0, min(1.0, 1.0 - remaining / goal_distance))
    clearances = [float(esdf[row, col]) for row, col in path_cells]
    min_clearance = min(clearances) if clearances else None
    collision_free = min_clearance is not None and min_clearance >= config.min_clearance_m

    visualization_path = None
    if geometry.bev_metadata_path:
        out_dir = Path(geometry.bev_metadata_path).parent
        visualization_path = str(
            save_local_plan_visualization(
                esdf,
                metadata,
                path_xy,
                goal_xy,
                out_dir / "local_plan.png",
            )
        )

    return LocalPlanResult(
        available=True,
        status=status,
        path_xy=[(round(x, 3), round(y, 3)) for x, y in path_xy],
        goal_xy=(round(goal_xy[0], 3), round(goal_xy[1], 3)),
        used_goal_xy=(round(used_goal_xy[0], 3), round(used_goal_xy[1], 3)),
        progress_score=None if progress is None else round(progress, 3),
        collision_free=collision_free,
        min_clearance_m=None if min_clearance is None else round(min_clearance, 3),
        planner_backend=config.backend,
        warning=warning,
        visualization_path=visualization_path,
    )


def _astar(
    esdf: np.ndarray,
    metadata: dict[str, Any],
    start: tuple[int, int],
    goal: tuple[int, int],
    config: PlannerConfig,
) -> tuple[list[tuple[int, int]], str, str | None]:
    queue: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    cost_so_far: dict[tuple[int, int], float] = {start: 0.0}
    best = start
    best_h = _heuristic(start, goal)
    steps = 0

    while queue and steps < config.max_steps:
        _, current = heapq.heappop(queue)
        steps += 1
        h = _heuristic(current, goal)
        if h < best_h:
            best = current
            best_h = h
        if _world_distance(current, goal, metadata) <= config.goal_tolerance_m:
            return _reconstruct(came_from, current), "success", None
        for neighbor, move_cost in _neighbors(current, esdf.shape):
            if esdf[neighbor] < config.min_clearance_m:
                continue
            clearance_penalty = 1.0 / max(float(esdf[neighbor]), 0.05)
            new_cost = cost_so_far[current] + move_cost + 0.03 * clearance_penalty
            if neighbor not in cost_so_far or new_cost < cost_so_far[neighbor]:
                cost_so_far[neighbor] = new_cost
                priority = new_cost + _heuristic(neighbor, goal)
                heapq.heappush(queue, (priority, neighbor))
                came_from[neighbor] = current

    if config.allow_partial and best != start:
        return _reconstruct(came_from, best), "partial", "A* reached max steps; using best partial path."
    return [], "no_path", "A* failed to find a collision-free path."


def _nearest_free_cell(
    esdf: np.ndarray,
    metadata: dict[str, Any],
    xy: tuple[float, float],
    min_clearance: float,
) -> tuple[int, int] | None:
    cell = world_to_grid(xy[0], xy[1], metadata)
    if cell is not None and esdf[cell] >= min_clearance:
        return cell
    candidates = np.argwhere(esdf >= min_clearance)
    if candidates.size == 0:
        candidates = np.argwhere(esdf > 0.0)
    if candidates.size == 0:
        return None
    best = min(
        ((int(row), int(col)) for row, col in candidates),
        key=lambda item: math.dist(grid_to_world(item[0], item[1], metadata), xy),
    )
    return best


def _neighbors(
    current: tuple[int, int],
    shape: tuple[int, int],
) -> list[tuple[tuple[int, int], float]]:
    row, col = current
    items = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr = row + dr
            nc = col + dc
            if 0 <= nr < shape[0] and 0 <= nc < shape[1]:
                items.append(((nr, nc), math.sqrt(2.0) if dr and dc else 1.0))
    return items


def _heuristic(left: tuple[int, int], right: tuple[int, int]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _world_distance(
    left: tuple[int, int],
    right: tuple[int, int],
    metadata: dict[str, Any],
) -> float:
    return math.dist(grid_to_world(left[0], left[1], metadata), grid_to_world(right[0], right[1], metadata))


def _reconstruct(
    came_from: dict[tuple[int, int], tuple[int, int]],
    current: tuple[int, int],
) -> list[tuple[int, int]]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def _no_path(
    status: str,
    goal_xy: tuple[float, float],
    config: PlannerConfig,
    warning: str,
) -> LocalPlanResult:
    return LocalPlanResult(
        available=False,
        status=status,
        path_xy=[],
        goal_xy=goal_xy,
        used_goal_xy=None,
        progress_score=None,
        collision_free=False,
        min_clearance_m=None,
        planner_backend=config.backend,
        warning=warning,
    )
