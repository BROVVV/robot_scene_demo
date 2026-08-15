"""Pure geometric gates for operator-scoped small-range motion."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MotionBoundaryDecision:
    allowed: bool
    reason: str = ""
    predicted_position: tuple[float, float] | None = None


def evaluate_lidar_motion_readiness(
    *,
    lidar_fresh: bool | None,
    front_clearance_m: float | None,
    minimum_clearance_m: float,
) -> MotionBoundaryDecision:
    """Fail closed on stale, missing or non-numeric forward safety data."""

    if lidar_fresh is not True:
        return MotionBoundaryDecision(False, "LiDAR clearance is stale or unavailable")
    if front_clearance_m is None or math.isnan(front_clearance_m):
        return MotionBoundaryDecision(False, "front clearance is unavailable")
    if front_clearance_m < minimum_clearance_m:
        return MotionBoundaryDecision(
            False,
            f"front clearance {front_clearance_m:.3f}m < "
            f"{minimum_clearance_m:.3f}m",
        )
    return MotionBoundaryDecision(True)


def evaluate_step_boundary(
    step: str,
    *,
    origin: tuple[float, float, float],
    current: tuple[float, float, float],
    max_radius_m: float,
    front_half_plane_only: bool,
    turn_only: bool,
    forward_distance_m: float,
    tolerance_m: float = 0.05,
) -> MotionBoundaryDecision:
    """Fail closed before a step leaves the operator-authorized region.

    The front half-plane is fixed to the robot's heading at authorization
    time, not its current heading after scan turns.
    """
    current_check = position_within_boundary(
        origin=origin,
        position=current[:2],
        max_radius_m=max_radius_m,
        front_half_plane_only=front_half_plane_only,
        tolerance_m=tolerance_m,
    )
    if not current_check.allowed:
        return current_check
    if step != "f":
        return MotionBoundaryDecision(True, predicted_position=current[:2])
    if turn_only:
        return MotionBoundaryDecision(
            False,
            "forward rejected by operator-scoped turn-only gate",
            predicted_position=current[:2],
        )
    predicted = (
        current[0] + max(0.0, forward_distance_m) * math.cos(current[2]),
        current[1] + max(0.0, forward_distance_m) * math.sin(current[2]),
    )
    return position_within_boundary(
        origin=origin,
        position=predicted,
        max_radius_m=max_radius_m,
        front_half_plane_only=front_half_plane_only,
        tolerance_m=tolerance_m,
    )


def position_within_boundary(
    *,
    origin: tuple[float, float, float],
    position: tuple[float, float],
    max_radius_m: float,
    front_half_plane_only: bool,
    tolerance_m: float = 0.05,
) -> MotionBoundaryDecision:
    dx = position[0] - origin[0]
    dy = position[1] - origin[1]
    radius = math.hypot(dx, dy)
    if max_radius_m > 0.0 and radius > max_radius_m + tolerance_m:
        return MotionBoundaryDecision(
            False,
            f"position radius {radius:.3f}m exceeds {max_radius_m:.3f}m boundary",
            predicted_position=position,
        )
    if front_half_plane_only:
        forward_projection = dx * math.cos(origin[2]) + dy * math.sin(origin[2])
        if forward_projection < -tolerance_m:
            return MotionBoundaryDecision(
                False,
                "position enters the half-plane behind the authorization pose",
                predicted_position=position,
            )
    return MotionBoundaryDecision(True, predicted_position=position)


def evaluate_rotation_clearance(
    step: str,
    *,
    left_clearance_m: float | None,
    right_clearance_m: float | None,
    minimum_clearance_m: float,
    clearance_valid: bool | None = None,
) -> MotionBoundaryDecision:
    """Require both sides of the full-body rotation envelope to be clear."""
    if not (step.startswith("l") or step.startswith("r")):
        return MotionBoundaryDecision(True)
    if clearance_valid is not True:
        return MotionBoundaryDecision(
            False,
            "rotation clearance is not validated for the LiDAR near-field zone",
        )
    if minimum_clearance_m <= 0.0:
        return MotionBoundaryDecision(True)
    if left_clearance_m is None or right_clearance_m is None:
        return MotionBoundaryDecision(
            False, "rotation clearance unavailable on one or both sides"
        )
    if not (
        math.isfinite(left_clearance_m) or math.isinf(left_clearance_m)
    ) or not (
        math.isfinite(right_clearance_m) or math.isinf(right_clearance_m)
    ):
        return MotionBoundaryDecision(False, "rotation clearance is non-numeric")
    minimum = min(left_clearance_m, right_clearance_m)
    if minimum < minimum_clearance_m:
        return MotionBoundaryDecision(
            False,
            f"rotation envelope blocked: {minimum:.3f}m < {minimum_clearance_m:.3f}m",
        )
    return MotionBoundaryDecision(True)


def evaluate_dual_lidar_rotation_gate(
    *,
    fused_state: str | None,
    dual_lidar_enabled: bool,
    unknown_is_clear: bool,
    occupied_sources: list[str] | None = None,
) -> MotionBoundaryDecision:
    """Fail-closed dual-LiDAR rotation gate.

    Only applies when dual-lidar safety fusion is enabled. When disabled the
    gate passes and the existing formal/lease rotation gate continues to
    govern. When enabled:

    * OCCUPIED            -> reject (any occupied wins)
    * UNKNOWN             -> reject unless ``unknown_is_clear`` (never by default)
    * STALE / blind /
      self_occluded /
      unvalidated_geometry -> reject (fail-closed)
    * CLEAR               -> pass

    ``fused_state`` is the string value of
    ``go2w_lidar_preprocessor.lidar_evidence.EvidenceState``.
    """
    if not dual_lidar_enabled:
        return MotionBoundaryDecision(
            True, "dual-lidar safety fusion is disabled; existing gates govern"
        )
    sources = ", ".join(occupied_sources or [])
    if fused_state == "occupied":
        return MotionBoundaryDecision(
            False, f"dual-lidar rotation occupied: {sources}"
        )
    if fused_state == "clear":
        return MotionBoundaryDecision(True, "dual-lidar rotation clear")
    if fused_state == "unknown":
        if unknown_is_clear:
            return MotionBoundaryDecision(
                True, "dual-lidar unknown overridden as clear by operator"
            )
        return MotionBoundaryDecision(
            False, "dual-lidar rotation is unknown; unknown is not clear"
        )
    return MotionBoundaryDecision(
        False, f"dual-lidar rotation not clear ({fused_state or 'no evidence'})"
    )
