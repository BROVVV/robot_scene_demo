"""Geometry debug visualizations."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def save_geometry_debug(
    depth: np.ndarray,
    occupancy: np.ndarray,
    esdf: np.ndarray,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(10, 3), constrained_layout=True)
    axes[0].imshow(depth, cmap="magma")
    axes[0].set_title("depth")
    axes[1].imshow(occupancy, cmap="gray_r")
    axes[1].set_title("occupancy")
    axes[2].imshow(np.maximum(esdf, 0), cmap="viridis")
    axes[2].set_title("esdf")
    for axis in axes:
        axis.axis("off")
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
