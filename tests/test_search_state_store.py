"""SearchStateStore tests (plan book §97: test_search_state_store)."""

from __future__ import annotations

from app.live_robot.search_event import (
    ACTION_FINISHED,
    ACTION_STARTED,
    CANDIDATES_GENERATED,
    GOAL_SELECTED,
    MAP_UPDATED,
    OBSERVATION_UPDATED,
    SESSION_CREATED,
    SESSION_STARTED,
    TARGET_CONFIRMED,
    TARGET_MATCH_UPDATED,
    SearchEvent,
)
from app.live_robot.search_state_store import (
    STATUS_TARGET_FOUND,
    SearchStateStore,
)


def _event(event_type: str, session_id: str = "search_test", cycle: int | None = None,
           payload: dict | None = None, event_id: int = 1, timestamp: float = 100.0) -> SearchEvent:
    return SearchEvent(
        event_id=event_id,
        session_id=session_id,
        timestamp=timestamp,
        event_type=event_type,
        cycle=cycle,
        payload=payload or {},
    )


def test_store_session_lifecycle() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={
        "target": "饮水机旁边的蓝色垃圾桶", "reasoner": "semantic_navigation", "backend": "mock",
    }))
    store.apply(_event(SESSION_STARTED))
    snapshot = store.snapshot()
    assert snapshot["session_id"] == "search_test"
    assert snapshot["target"] == "饮水机旁边的蓝色垃圾桶"
    assert snapshot["status"] == "RUNNING"
    assert snapshot["timeline"][0]["event_type"] == SESSION_CREATED


def test_store_observation_and_objects() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    store.apply(_event(OBSERVATION_UPDATED, cycle=1, payload={
        "bundle_id": "bundle_1",
        "scene_objects": [{"label_zh": "饮水机", "confidence": 0.9}],
        "detections": [{"label": "water dispenser", "bbox_2d": [0.1, 0.2, 0.3, 0.4]}],
        "target_present": False,
        "heading_sector": 3,
        "pose": {"x": 1.0, "y": 0.5, "yaw_rad": 0.5},
    }))
    snapshot = store.snapshot()
    assert snapshot["observation"]["bundle_id"] == "bundle_1"
    assert snapshot["observation"]["objects"][0]["label_zh"] == "饮水机"
    assert snapshot["observation"]["target_present"] is False
    assert snapshot["cycle"] == 1
    assert snapshot["candidates"] == []
    assert len(snapshot["timeline"]) == 2


def test_store_target_match_and_candidates_and_goal() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    store.apply(_event(TARGET_MATCH_UPDATED, payload={
        "target_match_level": "partial_match",
        "target_score": 0.81,
        "anchor_labels": ["饮水机"],
        "directive": {"kind": "inspect_anchor"},
    }))
    store.apply(_event(CANDIDATES_GENERATED, payload={
        "candidates": [{
            "goal": {"goal_id": "goal_1", "goal_type": "INSPECT_ANCHOR"},
            "score": 0.82,
            "components": {"semantic_relevance": 0.91},
        }],
        "selected_goal_id": "goal_1",
    }))
    store.apply(_event(GOAL_SELECTED, payload={
        "goal": {"goal_id": "goal_1", "goal_type": "INSPECT_ANCHOR"},
        "score": 0.82,
        "components": {"semantic_relevance": 0.91},
        "reasons": ["锚点"],
    }))
    store.apply(_event(ACTION_STARTED))
    store.apply(_event(ACTION_FINISHED, payload={"status": "succeeded"}))
    snapshot = store.snapshot()
    assert snapshot["target_match"]["level"] == "partial_match"
    assert snapshot["target_match"]["anchor_labels"] == ["饮水机"]
    assert snapshot["selected_goal"]["goal"]["goal_id"] == "goal_1"
    assert snapshot["candidates"][0]["selected"] is True
    assert snapshot["robot"]["motion_status"] == "SUCCEEDED"
    evidence = snapshot["objects"]["target_evidence"]
    assert evidence["anchor_labels"] == ["饮水机"]


def test_store_map_revision_monotonic() -> None:
    store = SearchStateStore()
    graph = {"nodes": [], "edges": []}
    for index in range(5):
        store.apply(_event(MAP_UPDATED, payload={
            "graph": {**graph, "nodes": [{"node_id": f"n{index}"}]},
            "map_mode": "topological",
        }))
        assert store.map_snapshot()["revision"] == index + 1
    final = store.map_snapshot()
    assert final["schema_version"] == "live_exploration_graph_v1"
    assert final["map_mode"] == "topological"
    assert len(final["nodes"]) == 1
    assert final["nodes"][0]["node_id"] == "n4"


def test_store_target_confirmed_status() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    store.apply(_event(TARGET_CONFIRMED, payload={"reason_zh": "verify pass"}))
    snapshot = store.snapshot()
    assert snapshot["status"] == STATUS_TARGET_FOUND
    assert snapshot["target_match"]["target_confirmed"] is True
    assert snapshot["target_match"]["level"] == "confirmed"
    assert snapshot["phase"] == "TARGET_FOUND"


def test_store_exhausted_and_error() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    store.apply(_event("SEARCH_EXHAUSTED", payload={"reason": "no candidates"}))
    assert store.snapshot()["status"] == "SEARCH_EXHAUSTED"
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    store.apply(_event("ERROR", payload={"error_type": "PERCEPTION_ERROR", "message": "no frame"}))
    snapshot = store.snapshot()
    assert snapshot["status"] == "FAILED"
    assert snapshot["error"]["error_type"] == "PERCEPTION_ERROR"


def test_store_snapshot_is_a_copy() -> None:
    store = SearchStateStore()
    store.apply(_event(SESSION_CREATED, payload={"target": "x"}))
    first = store.snapshot()
    first["target"] = "mutated"
    assert store.snapshot()["target"] == "x"