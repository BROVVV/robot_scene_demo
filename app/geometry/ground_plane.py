"""Ground-plane helpers reserved for metric geometry backends."""

from __future__ import annotations

import numpy as np


def estimate_ground_height(point_map: np.ndarray) -> float:
    z_values = point_map[..., 2]
    finite = z_values[np.isfinite(z_values)]
    if finite.size == 0:
        return 0.0
    return float(np.percentile(finite, 15))
