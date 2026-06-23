"""Local planner visualization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from app.geometry.types import world_to_grid


def save_local_plan_visualization(
    esdf: np.ndarray,
    metadata: dict[str, Any],
    path_xy: list[tuple[float, float]],
    goal_xy: tuple[float, float] | None,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(5, 5), constrained_layout=True)
    axis.imshow(np.maximum(esdf, 0), cmap="viridis", origin="upper")
    if path_xy:
        cells = [world_to_grid(x, y, metadata) for x, y in path_xy]
        cells = [cell for cell in cells if cell is not None]
        if cells:
            rows = [cell[0] for cell in cells]
            cols = [cell[1] for cell in cells]
            axis.plot(cols, rows, color="white", linewidth=2)
            axis.scatter(cols[0], rows[0], c="lime", s=40, label="start")
            axis.scatter(cols[-1], rows[-1], c="cyan", s=40, label="used goal")
    if goal_xy is not None:
        cell = world_to_grid(goal_xy[0], goal_xy[1], metadata)
        if cell is not None:
            axis.scatter([cell[1]], [cell[0]], c="red", s=45, marker="x", label="goal")
    axis.set_title("local plan on BEV ESDF")
    axis.axis("off")
    axis.legend(loc="lower right")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
