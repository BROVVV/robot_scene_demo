"""ExplorerSearchAdapter: bridges AutonomousExplorer's on_event dict stream
into SearchEvents (plan book §35).

The explorer emits flat dicts (``{"event": "...", "state": "...", "host_s",
...}``) from its ``_emit`` hook.  This adapter maps them onto the unified
``SearchEvent`` vocabulary, updates the ``SearchStateStore`` and publishes on
the ``SearchEventBus``.  The adapter itself never talks to FastAPI /
WebSockets, so CLI mode and tests can consume the same stream.
"""

from __future__ import annotations

from typing import Any, Callable

from app.live_robot.search_event import (
    ACTION_FINISHED,
    ACTION_STARTED,
    CANDIDATES_GENERATED,
    ERROR,
    GOAL_SELECTED,
    MAP_UPDATED,
    MEMORY_UPDATED,
    OBJECTS_UPDATED,
    OBSERVATION_UPDATED,
    OPERATOR_STOP,
    PAUSED,
    REPLAN,
    RESUMED,
    SEARCH_EXHAUSTED,
    SEARCH_FINISHED,
    SEARCH_STATE_CHANGED,
    SESSION_CREATED,
    SESSION_STARTED,
    TARGET_CONFIRMED,
    TARGET_MATCH_UPDATED,
    VERIFICATION_FINISHED,
    VERIFICATION_STARTED,
    SearchEvent,
    make_event,
)
from app.live_robot.search_event_bus import SearchEventBus
from app.live_robot.search_state_store import SearchStateStore

# Explorer events that are map-relevant: their payload may carry a "graph"
# field (injected by the search worker) which becomes MAP_UPDATED.
_MAP_RELEVANT = frozenset({"observation", "memory_update", "navigation_result"})

# Explorer events that become error SearchEvents.
_ERROR_EVENTS = {
    "perception_failure": ("PERCEPTION_ERROR", "perception"),
    "observer_error": ("PERCEPTION_ERROR", "observer"),
    "observer_retry": ("PERCEPTION_ERROR", "observer_retry"),
    "matcher_error": ("SEARCH_ERROR", "matcher"),
    "verification_error": ("LLM_ERROR", "verifier"),
    "candidate_generator_error": ("SEARCH_ERROR", "candidate_generator"),
}


class ExplorerSearchAdapter:
    """Maps explorer events to SearchEvents and keeps the store in sync."""

    def __init__(
        self,
        bus: SearchEventBus,
        store: SearchStateStore,
        *,
        session_id: str,
        source: str = "autonomous_search",
        now: Callable[[], float] | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._session_id = session_id
        self._source = source
        self._now = now
        self._cycle = 0
        self._pending_verification: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # explorer hook                                                      #
    # ------------------------------------------------------------------ #
    def on_explorer_event(self, event: dict[str, Any]) -> None:
        """Handle one raw explorer event and publish SearchEvents."""
        events = self._convert(event)
        for search_event in events:
            self._bus.publish(search_event)
            self._store.apply(search_event)

    def _emit(self, event_type: str, *, payload: dict[str, Any] | None = None,
              cycle: int | None = None) -> SearchEvent:
        return make_event(
            allocator=self._bus.allocator,
            session_id=self._session_id,
            event_type=event_type,
            cycle=cycle if cycle is not None else (self._cycle or None),
            payload=payload or {},
            now=self._now,
        )

    # ------------------------------------------------------------------ #
    # mapping                                                            #
    # ------------------------------------------------------------------ #
    def _convert(self, event: dict[str, Any]) -> list[SearchEvent]:
        name = str(event.get("event") or "")
        state = str(event.get("state") or "")
        payload = dict(event)
        payload.pop("event", None)
        payload.pop("state", None)
        payload.pop("host_s", None)
        payload["phase"] = state or payload.get("phase")

        if name == "session_start":
            self._store.reset(
                session_id=self._session_id,
                target=str(payload.get("target") or ""),
                reasoner=str(payload.get("reasoner") or "unigoal"),
                backend=str(payload.get("backend") or "mock"),
            )
            # SESSION_CREATED is emitted by the web service; the explorer's
            # session_start only marks the transition to running.
            return [
                self._emit(SESSION_STARTED, payload={"phase": "BOOTSTRAP"}),
            ]
        if name == "backend_health":
            health = {
                "backend": payload.get("backend"),
                "ready": payload.get("ready"),
                "degraded": payload.get("degraded"),
                "attempt": payload.get("attempt"),
                "health": payload.get("health"),
            }
            return [
                self._emit(SEARCH_STATE_CHANGED, payload={
                    "phase": "BOOTSTRAP", "health": health,
                })
            ]
        if name == "observation":
            self._cycle += 1
            return [
                self._emit(OBSERVATION_UPDATED, cycle=self._cycle, payload={
                    "bundle_id": payload.get("bundle_id"),
                    "timestamp": payload.get("host_s") or payload.get("timestamp"),
                    "objects": payload.get("objects") or [],
                    "scene_objects": payload.get("scene_objects") or [],
                    "scene_relations": payload.get("scene_relations") or [],
                    "target_present": payload.get("target_present", False),
                    "heading_sector": payload.get("heading_sector"),
                    "pose": payload.get("pose"),
                    "image_ref": payload.get("image_ref"),
                    "sensor_health": payload.get("sensor_health") or {},
                    "detections": payload.get("detections") or [],
                    "phase": "OBSERVE",
                }),
                self._emit(OBJECTS_UPDATED, cycle=self._cycle, payload={
                    "current": payload.get("scene_objects") or [],
                    "phase": "OBSERVE",
                }),
                *self._map_events(event, payload, state),
            ]
        if name == "match":
            return [
                self._emit(TARGET_MATCH_UPDATED, payload={
                    "has_candidate": payload.get("has_candidate", False),
                    "target_match_level": payload.get("target_match_level") or "none",
                    "target_score": payload.get("target_score", 0.0),
                    "anchor_labels": payload.get("anchor_labels") or [],
                    "explicit_anchor_found": bool(payload.get("anchor_labels")),
                    "directive": payload.get("directive"),
                    "graph_match": payload.get("graph_match"),
                    "phase": "MATCH",
                })
            ]
        if name == "verification":
            attempt = int(payload.get("attempt") or 1)
            if attempt == 1:
                self._pending_verification = payload
                return [
                    self._emit(VERIFICATION_STARTED, payload={
                        "attempt": attempt, "phase": "VERIFY",
                    })
                ]
            return [
                self._emit(VERIFICATION_FINISHED, payload={
                    "attempt": attempt,
                    "confirmed": bool(payload.get("confirmed", False)),
                    "reason_zh": payload.get("reason_zh") or "",
                    "phase": "VERIFY",
                })
            ]
        if name == "verification_rejected":
            return [
                self._emit(VERIFICATION_FINISHED, payload={
                    "confirmed": False,
                    "reason_zh": payload.get("reason_zh") or "",
                    "phase": "VERIFY",
                })
            ]
        if name == "target_found":
            return [
                self._emit(TARGET_CONFIRMED, payload={
                    "reason_zh": payload.get("reason_zh") or "",
                    "attempts": payload.get("attempts"),
                    "phase": "TARGET_FOUND",
                })
            ]
        if name == "memory_update":
            return [
                self._emit(MEMORY_UPDATED, payload={
                    "node_id": payload.get("node_id"),
                    "new_labels": payload.get("new_labels") or [],
                    "new_relations": payload.get("new_relations") or [],
                    "new_sector": payload.get("new_sector", False),
                    "information_gain": payload.get("information_gain", 0.0),
                    "no_information_cycles": payload.get("no_information_cycles", 0),
                    "unique_nodes": payload.get("unique_nodes", 0),
                    "phase": "UPDATE_MEMORY",
                }),
                *self._map_events(event, payload, state),
            ]
        if name == "candidates":
            candidates = list(payload.get("candidates") or [])
            return [
                self._emit(CANDIDATES_GENERATED, payload={
                    "candidates": candidates,
                    "selected_goal_id": payload.get("selected_goal_id"),
                    "phase": "PLAN",
                })
            ]
        if name == "selected_goal":
            return [
                self._emit(GOAL_SELECTED, payload={
                    "goal": payload.get("goal") or {},
                    "score": payload.get("score"),
                    "components": payload.get("components") or {},
                    "reasons": payload.get("reasons") or [],
                    "planning_cycles": payload.get("planning_cycles"),
                    "phase": "PLAN",
                })
            ]
        if name == "action_start":
            return [
                self._emit(ACTION_STARTED, payload={
                    "goal": payload.get("goal") or {},
                    "phase": "EXECUTE",
                })
            ]
        if name == "navigation_result":
            return [
                self._emit(ACTION_FINISHED, payload={
                    "goal_id": payload.get("goal_id"),
                    "status": payload.get("status"),
                    "message": payload.get("message") or "",
                    "requested_motion": payload.get("requested_motion") or {},
                    "observed_motion": payload.get("observed_motion") or {},
                    "elapsed_sec": payload.get("elapsed_sec"),
                    "phase": "WAIT_RESULT",
                }),
                *self._map_events(event, payload, state),
            ]
        if name == "replan":
            return [
                self._emit(REPLAN, payload={
                    "goal_id": payload.get("goal_id"),
                    "status": payload.get("status"),
                    "navigation_failures": payload.get("navigation_failures", 0),
                    "phase": "RECOVER",
                })
            ]
        if name == "paused":
            return [self._emit(PAUSED, payload={"phase": "PAUSED"})]
        if name == "resumed":
            return [self._emit(RESUMED, payload={"phase": "OBSERVE"})]
        if name == "search_exhausted":
            return [
                self._emit(SEARCH_EXHAUSTED, payload={
                    "reason": payload.get("reason") or "",
                    "phase": "SEARCH_EXHAUSTED",
                })
            ]
        if name == "session_finish":
            result = str(payload.get("result") or "")
            finish_payload = {
                "result": result,
                "finish_reason": result,
                "reason": payload.get("reason") or "",
                "planning_cycles": payload.get("planning_cycles"),
                "motion_steps": payload.get("motion_steps"),
                "observations": payload.get("observations"),
                "unique_nodes": payload.get("unique_nodes"),
                "replans": payload.get("replans"),
                "navigation_failures": payload.get("navigation_failures"),
                "verify_attempts": payload.get("verify_attempts"),
                "duration_s": payload.get("duration_s"),
                "phase": result,
            }
            events: list[SearchEvent] = []
            if result == "OPERATOR_STOP":
                events.append(self._emit(OPERATOR_STOP, payload={"phase": "OPERATOR_STOP"}))
            elif result in {"TIMEOUT", "MAX_STEPS_REACHED",
                            "MAX_PLANNING_CYCLES_REACHED", "BACKEND_FAILURE",
                            "PERCEPTION_FAILURE"}:
                events.append(self._emit(ERROR, payload={
                    "error_type": _finish_to_error_type(result),
                    "message": payload.get("reason") or result,
                    "phase": "FAILED",
                }))
            events.append(self._emit(SEARCH_FINISHED, payload=finish_payload))
            return events
        if name in _ERROR_EVENTS:
            error_type, source = _ERROR_EVENTS[name]
            return [
                self._emit(ERROR, payload={
                    "error_type": error_type,
                    "source": source,
                    "message": str(
                        payload.get("error") or payload.get("reason") or name
                    ),
                    "phase": "FAILED",
                })
            ]
        # Unknown explorer events still surface as state changes.
        return [self._emit(SEARCH_STATE_CHANGED, payload=payload)]

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #
    def _map_events(self, raw: dict[str, Any], payload: dict[str, Any],
                    state: str) -> list[SearchEvent]:
        """MAP_UPDATED for map-relevant explorer events (payload carries the
        full graph snapshot injected by the search worker)."""
        graph = payload.get("graph")
        if not isinstance(graph, dict) or not graph.get("session_id"):
            return []
        nodes = list(graph.get("nodes") or [])
        if not nodes:
            # Pre-memory observation (e.g. target found on the first frame):
            # reflect the current observation node so the map is never empty.
            bundle_id = payload.get("bundle_id")
            pose = payload.get("pose")
            nodes = [
                {
                    "node_id": f"node_{bundle_id}" if bundle_id else "node",
                    "timestamp": payload.get("host_s") or 0.0,
                    "objects": list(
                        payload.get("objects") or payload.get("scene_objects") or []
                    ),
                    "pose": pose,
                    "pose_quality": "relative" if isinstance(pose, dict) else "unavailable",
                    "reachable_state": "OBSERVED",
                    "visited_count": 0,
                    "target_match_level": "none",
                    "semantic_relevance": 0.0,
                    "information_gain": 0.0,
                }
            ]
            graph = {**graph, "nodes": nodes}
        current_node_id = None
        bundle_id = payload.get("bundle_id")
        if bundle_id:
            current_node_id = f"node_{bundle_id}"
        robot = None
        pose = payload.get("pose")
        if isinstance(pose, dict):
            robot = {
                "x": pose.get("x"),
                "y": pose.get("y"),
                "yaw": pose.get("yaw_rad", pose.get("yaw")),
                "pose_quality": "relative",
            }
        # Flatten x/y/yaw to node top level (plan book §47) for consumers that
        # expect the documented node schema; pose stays nested for depth.
        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            item = dict(node)
            node_pose = item.get("pose")
            if isinstance(node_pose, dict):
                item["x"] = node_pose.get("x")
                item["y"] = node_pose.get("y")
                item["yaw"] = node_pose.get("yaw")
            normalized_nodes.append(item)
        return [
            self._emit(MAP_UPDATED, payload={
                "graph": {**graph, "nodes": normalized_nodes},
                "map_mode": "topological",
                "current_node_id": current_node_id,
                "robot": robot,
                "phase": state,
            })
        ]


def _finish_to_error_type(result: str) -> str:
    if result in {"BACKEND_FAILURE", "BACKEND_UNAVAILABLE"}:
        return "BACKEND_ERROR"
    if result == "PERCEPTION_FAILURE":
        return "PERCEPTION_ERROR"
    return "SEARCH_ERROR"