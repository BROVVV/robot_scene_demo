"""Tests for PlaceGraph: rotations stay in one place, relocations create new
places."""

from __future__ import annotations

from app.spatial.models import SpatialPose
from app.spatial.place_graph import PlaceGraph


def test_in_place_rotation_keeps_one_place():
    graph = PlaceGraph()
    pose = SpatialPose(x=0.0, y=0.0, yaw=0.0)
    for i in range(4):
        place_id, created = graph.register_observation(
            observation_id=f"obs_{i}",
            heading_sector=i,
            objects=["door"],
            pose=pose,
        )
        assert place_id == "P1"
        assert created is (i == 0)
    assert len(graph.places) == 1
    assert graph.places["P1"].heading_coverage == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert len(graph.edges) == 0


def test_translation_creates_new_place():
    graph = PlaceGraph()
    graph.register_observation(
        observation_id="obs_0", heading_sector=0, objects=[], pose=SpatialPose(x=0, y=0)
    )
    place_id, created = graph.register_observation(
        observation_id="obs_1", heading_sector=0, objects=[],
        pose=SpatialPose(x=0.3, y=0.0),
    )
    assert created is True
    assert place_id == "P2"
    assert len(graph.places) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].from_place == "P1"
    assert graph.edges[0].to_place == "P2"


def test_relative_displacement_creates_new_place_without_pose():
    graph = PlaceGraph()
    graph.register_observation(observation_id="obs_0", heading_sector=0, objects=[])
    place_id, created = graph.register_observation(
        observation_id="obs_1", heading_sector=0, objects=[],
        observed_displacement_m=0.20,
    )
    assert created is True
    assert place_id == "P2"


def test_small_displacement_stays_same_place():
    graph = PlaceGraph()
    graph.register_observation(
        observation_id="obs_0", heading_sector=0, objects=[], pose=SpatialPose(x=0, y=0)
    )
    place_id, created = graph.register_observation(
        observation_id="obs_1", heading_sector=0, objects=[],
        pose=SpatialPose(x=0.05, y=0.0),
    )
    assert created is False
    assert place_id == "P1"
