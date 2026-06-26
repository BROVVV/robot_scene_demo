"""Backward-compatible exports for the video tracker."""

from app.video.tracking import (
    aggregate_track_score,
    track_objects,
    track_observation_counts,
)

__all__ = ["aggregate_track_score", "track_objects", "track_observation_counts"]
