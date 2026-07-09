from __future__ import annotations

import unittest

from app.video.object_tracker import VideoObjectTracker
from app.video.observed_scene_graph_builder import ObservedSceneGraphBuilder
from app.video.place_segmenter import PlaceSegmenter
from app.video.psg_graph_merger import PSGGraphMerger
from app.video.schemas import (
    FrameObject,
    FrameObservation,
    PSGLayer,
    SceneGraph,
    SceneGraphEdge,
    SceneGraphNode,
)
from app.video.video_navigation_topology_builder import VideoNavigationTopologyBuilder
from app.video.video_psg_predictor import VideoPSGPredictor


def _observation(frame_id: int, x_offset: float = 0.0) -> FrameObservation:
    return FrameObservation(
        frame_id=frame_id,
        timestamp_sec=float(frame_id),
        frame_path=f"frame_{frame_id}.jpg",
        scene_type="corridor",
        summary_zh="走廊右前方有一扇门。",
        objects=[
            FrameObject(
                frame_object_id=f"frame_{frame_id}_door",
                label="door",
                label_zh="门",
                category="structure",
                bbox=[0.5 + x_offset, 0.1, 0.8 + x_offset, 0.9],
                mask_area_ratio=None,
                confidence=0.9,
                position_2d="right-front",
                navigation_role="passage",
                is_obstacle=False,
                is_landmark=True,
                evidence_type="bbox",
            )
        ],
    )


class VideoFullSceneComponentsTest(unittest.TestCase):
    def test_tracker_merges_adjacent_same_object(self) -> None:
        tracks = VideoObjectTracker().build_tracks([_observation(1), _observation(2, 0.01)])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].seen_frame_ids, [1, 2])

    def test_observed_graph_nodes_are_observed(self) -> None:
        observations = [_observation(1)]
        tracks = VideoObjectTracker().build_tracks(observations)
        graph = ObservedSceneGraphBuilder().build(observations, tracks)
        self.assertTrue(graph.nodes)
        self.assertTrue(all(node.source == "observed" for node in graph.nodes))

    def test_observed_graph_uses_place_backbone_without_video_root(self) -> None:
        observations = [_observation(1), _observation(3, 0.02)]
        tracks = VideoObjectTracker().build_tracks(observations)
        places = PlaceSegmenter().segment(observations, tracks)
        graph = ObservedSceneGraphBuilder().build(observations, tracks, places)
        self.assertTrue(any(node.node_type == "place" for node in graph.nodes))
        self.assertFalse(any("root" in node.node_id or node.node_id == "video" for node in graph.nodes))
        self.assertTrue(
            all(
                not ("root" in edge.source_node_id or edge.source_node_id == "video")
                for edge in graph.edges
            )
        )

    def test_psg_predictor_enforces_predicted_safety(self) -> None:
        observations = [_observation(1)]
        tracks = VideoObjectTracker().build_tracks(observations)
        graph = ObservedSceneGraphBuilder().build(observations, tracks)
        layer = VideoPSGPredictor().predict(graph)
        self.assertGreaterEqual(len(layer.predicted_nodes), 1)
        for node in layer.predicted_nodes:
            self.assertEqual(node.source, "predicted")
            self.assertFalse(node.can_confirm_target)
            self.assertTrue(node.based_on)
        for edge in layer.predicted_edges:
            self.assertNotIn(edge.relation, {"target_is_at", "safe_to_enter"})

    def test_merger_filters_low_confidence_prediction(self) -> None:
        observed = SceneGraph(
            nodes=[
                SceneGraphNode(
                    node_id="obj_door_001",
                    node_type="object",
                    label="door",
                    label_zh="门",
                    category="structure",
                    source="observed",
                    confidence=0.9,
                    evidence_level="observed_confirmed",
                    based_on=["frame_000001"],
                    can_confirm_target=True,
                )
            ],
            edges=[],
        )
        psg = PSGLayer(
            predicted_nodes=[
                SceneGraphNode(
                    node_id="pred_room_001",
                    node_type="region",
                    label="room",
                    label_zh="房间",
                    category="region",
                    source="predicted",
                    confidence=0.1,
                    evidence_level="predicted_explorable",
                    based_on=["obj_door_001"],
                    can_confirm_target=True,
                )
            ],
            predicted_edges=[
                SceneGraphEdge(
                    edge_id="pred_edge_001",
                    source_node_id="obj_door_001",
                    target_node_id="pred_room_001",
                    relation="may_connect_to",
                    source="predicted",
                    confidence=0.1,
                    evidence_level="predicted_explorable",
                )
            ],
        )
        hybrid, report = PSGGraphMerger(confidence_threshold=0.45).merge(observed, psg)
        self.assertEqual(len(hybrid.nodes), 1)
        self.assertIn("pred_room_001", report["dropped_predicted_nodes"])

    def test_navigation_topology_keeps_predictions_as_explore_candidates(self) -> None:
        observations = [_observation(1)]
        tracks = VideoObjectTracker().build_tracks(observations)
        observed = ObservedSceneGraphBuilder().build(observations, tracks)
        psg = VideoPSGPredictor().predict(observed)
        hybrid, _ = PSGGraphMerger().merge(observed, psg)
        topology = VideoNavigationTopologyBuilder().build(hybrid, psg.next_best_views)
        self.assertTrue(topology["exploration_candidates"])
        self.assertTrue(
            all(item["requires_visual_confirmation"] for item in topology["exploration_candidates"])
        )
        self.assertTrue(topology["validation"]["has_place_backbone"])


if __name__ == "__main__":
    unittest.main()
