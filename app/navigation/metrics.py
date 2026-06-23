"""Navigation metrics helpers."""

from __future__ import annotations

import math


def path_length(path_xy: list[tuple[float, float]]) -> float:
    if len(path_xy) < 2:
        return 0.0
    return sum(
        math.dist(path_xy[index - 1], path_xy[index])
        for index in range(1, len(path_xy))
    )


def min_clearance(values: list[float]) -> float | None:
    if not values:
        return None
    return min(values)
