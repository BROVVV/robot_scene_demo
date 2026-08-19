"""SearchSessionService: session lifecycle owner for autonomous semantic
search (plan book §37-§41, §56, §80).

One service instance per web process.  It owns at most one active search
session, connects the SearchExecutor (worker subprocess or in-process mock)
to the SearchEventBus / SearchStateStore / ExplorerSearchAdapter, and exposes
the REST-friendly operations used by ``search_routes``.

Control ownership is enforced through the shared ``ControlOwner`` so manual
and autonomous motion never conflict.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.live_robot.explorer_search_adapter import ExplorerSearchAdapter
from app.live_robot.search_event import (
    ERROR,
    OPERATOR_STOP,
    SEARCH_FINISHED,
    SESSION_CREATED,
    SearchEvent,
    make_event,
)
from app.live_robot.search_event_bus import SearchEventBus
from app.live_robot.search_state_store import (
    STATUS_IDLE,
    STATUS_RUNNING,
    SearchStateStore,
)
from app.manual_web_demo.control_ownership import ControlOwner, OwnerState
from app.manual_web_demo.search_executor import SearchExecutor
from app.manual_web_demo.search_models import (
    SearchSessionInfo,
    SearchStartRequest,
    new_session_id,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SESSION_DIR = "outputs/live_runs"


class SearchSessionService:
    """Lifecycle + state hub for one web process."""

    def __init__(
        self,
        *,
        owner: ControlOwner,
        executor_factory: Callable[[], SearchExecutor] | None = None,
        session_dir: str = _DEFAULT_SESSION_DIR,
        event_buffer: int = 500,
    ) -> None:
        self.owner = owner
        self._executor_factory = executor_factory or _default_executor_factory
        self._session_dir = session_dir
        self._bus = SearchEventBus(max_recent=int(event_buffer))
        self._store = SearchStateStore()
        self._adapter: ExplorerSearchAdapter | None = None
        self._executor: SearchExecutor | None = None
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._info: SearchSessionInfo | None = None
        self._started_at: float | None = None
        self._status = STATUS_IDLE

    # ------------------------------------------------------------------ #
    # queries                                                            #
    # ------------------------------------------------------------------ #
    def current_session(self) -> SearchSessionInfo | None:
        with self._lock:
            if self._info is None:
                return None
            # The store is the authoritative status source once a session is
            # active (PAUSED / RESUMED / TARGET_FOUND / ... all flow through
            # SearchEvents); the service-level status only tracks IDLE vs
            # active for the lifecycle gate.
            store_status = self._store.snapshot().get("status") or self._status
            store_result = self._store.snapshot().get("result") or ""
            info = SearchSessionInfo(
                session_id=self._info.session_id,
                target=self._info.target,
                status=store_status,
                result=store_result or self._info.result,
                started_at=self._info.started_at,
                finished_at=self._info.finished_at,
                backend=self._info.backend,
                reasoner=self._info.reasoner,
            )
            return info

    def state_snapshot(self) -> dict[str, Any]:
        snapshot = self._store.snapshot()
        session = self.current_session()
        if session is not None:
            snapshot["session_id"] = session.session_id
            snapshot["status"] = session.status
            snapshot["target"] = session.target
            snapshot["backend"] = session.backend
            snapshot["reasoner"] = session.reasoner
            snapshot["result"] = session.result
            snapshot["elapsed_seconds"] = self._elapsed(session)
        return snapshot

    def map_snapshot(self) -> dict[str, Any]:
        return self._store.map_snapshot()

    def spatial_snapshot(self) -> dict[str, Any]:
        return self._store.spatial_snapshot()

    def objects_snapshot(self) -> dict[str, Any]:
        return self._store.objects_snapshot()

    def recent_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self._bus.recent_events(limit)

    def subscribe_events(
        self, callback: Callable[[SearchEvent], None]
    ) -> Callable[[], None]:
        self._bus.subscribe(callback)
        return lambda: self._bus.unsubscribe(callback)

    def executor_state(self) -> dict[str, Any]:
        if self._executor is None:
            return {"state": "stopped", "session_id": None}
        status = dict(self._executor.status() or {})
        status["alive"] = bool(self._executor.alive())
        return status

    # ------------------------------------------------------------------ #
    # session commands                                                    #
    # ------------------------------------------------------------------ #
    def _active_status(self) -> str:
        """Status gate source: the store once a session exists (it tracks
        PAUSED / RUNNING / terminal states from SearchEvents), otherwise the
        service-level IDLE."""
        if self._info is not None:
            return self._store.snapshot().get("status") or self._status
        return self._status

    def start_search(self, request: SearchStartRequest) -> dict[str, Any]:
        error = request.validate()
        if error:
            return {"ok": False, "error": error}
        with self._lock:
            if self._status not in (STATUS_IDLE, "FINISHED", "SEARCH_EXHAUSTED",
                                    "FAILED", "OPERATOR_STOP", "TARGET_FOUND"):
                return {
                    "ok": False,
                    "error": f"search already active in state {self._status}",
                    "conflict": True,
                }
            ok, reason = self.owner.try_autonomous(detail="autonomous_search")
            if not ok:
                return {"ok": False, "error": reason, "conflict": True}
            session_id = new_session_id()
            self._session_id = session_id
            self._status = "STARTING"
            self._started_at = time.time()
            self._info = SearchSessionInfo(
                session_id=session_id,
                target=request.target,
                status="STARTING",
                backend=request.backend,
                reasoner=request.reasoner,
                started_at=self._started_at,
            )

            # One bus lives for the whole web process (the /ws/search hub
            # subscribes once); a new session only clears its event history.
            self._bus.clear()
            self._store = SearchStateStore()
            self._store.reset(
                session_id=session_id,
                target=request.target,
                reasoner=request.reasoner,
                backend=request.backend,
            )
            self._adapter = ExplorerSearchAdapter(
                self._bus, self._store, session_id=session_id,
            )
            executor = self._executor_factory()
            executor.set_on_message(self._on_executor_message)
            self._executor = executor
            session_dir_path = Path(self._session_dir)
            session_dir_path.mkdir(parents=True, exist_ok=True)
            run_dir = session_dir_path / session_id
            run_dir.mkdir(parents=True, exist_ok=True)
            params = {
                "target": request.target,
                "reasoner": request.reasoner,
                "backend": request.backend,
                "finish_on_visual_confirmation": request.finish_on_visual_confirmation,
                "turn_only": request.turn_only,
                "enable_autonomous_motion": request.enable_autonomous_motion,
                "operator_supervised_experiment": request.operator_supervised_experiment,
                "dry_run_motion": request.dry_run_motion,
                "rgbd_source": request.rgbd_source,
                "rgbd_base_url": request.rgbd_base_url,
                "spatial_v2": request.spatial_v2,
                "rtabmap": request.rtabmap,
                "session_dir": str(session_dir_path),
                "output": str(run_dir / "events.jsonl"),
            }
            for key in (
                "max_seconds", "max_planning_cycles", "max_motion_steps",
                "llm_model", "verify_min_confidence",
            ):
                value = getattr(request, key)
                if value is not None:
                    params[key] = value
            # Emit SESSION_CREATED from the web side so a page that connects
            # before the worker reports anything still sees a session.
            created = make_event(
                allocator=self._bus.allocator,
                session_id=session_id,
                event_type=SESSION_CREATED,
                payload={
                    "target": request.target,
                    "reasoner": request.reasoner,
                    "backend": request.backend,
                    "phase": "STARTING",
                },
            )
            self._bus.publish(created)
            self._store.apply(created)
            try:
                executor.start(params)
            except Exception as exc:  # noqa: BLE001
                self._status = "FAILED"
                self.owner.release(OwnerState.AUTONOMOUS)
                return {"ok": False, "error": f"executor start failed: {exc}"}
            return {
                "ok": True,
                "session_id": session_id,
                "status": "STARTING",
            }

    def pause_search(self) -> dict[str, Any]:
        with self._lock:
            if self._active_status() != STATUS_RUNNING:
                return {"ok": False, "error": f"not running (state={self._active_status()})"}
        if self._executor is not None:
            self._executor.pause()
        return {"ok": True, "status": "PAUSED"}

    def resume_search(self) -> dict[str, Any]:
        with self._lock:
            if self._active_status() != "PAUSED":
                return {"ok": False, "error": f"not paused (state={self._active_status()})"}
        if self._executor is not None:
            self._executor.resume()
        return {"ok": True, "status": "RUNNING"}

    def stop_search(self, *, reason: str = "operator_stop") -> dict[str, Any]:
        with self._lock:
            if self._active_status() == STATUS_IDLE:
                return {"ok": True, "status": "IDLE", "note": "no active session"}
        if self._executor is not None:
            self._executor.stop()
        return {"ok": True, "status": "STOPPING"}

    def estop_search(self) -> dict[str, Any]:
        """Estop overrides ownership and halts the search (plan book §42)."""
        self.owner.estop()
        with self._lock:
            active = self._status not in (STATUS_IDLE,)
        if self._executor is not None and active:
            self._executor.estop()
        return {"ok": True, "status": "ESTOP"}

    def shutdown(self) -> None:
        """Stop the owned search + worker and release ownership."""
        with self._lock:
            active = self._status not in (STATUS_IDLE, "FINISHED")
        if self._executor is not None and active:
            try:
                self._executor.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._executor is not None:
            try:
                self._executor.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.owner.release(OwnerState.AUTONOMOUS)

    # ------------------------------------------------------------------ #
    # history                                                            #
    # ------------------------------------------------------------------ #
    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        base = Path(self._session_dir)
        if not base.is_dir():
            return []
        sessions: list[dict[str, Any]] = []
        for directory in base.iterdir():
            if not directory.is_dir():
                continue
            summary_path = directory / "summary.json"
            if not summary_path.is_file():
                continue
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            sessions.append(
                {
                    "session_id": directory.name,
                    "target": summary.get("target"),
                    "result": summary.get("result"),
                    "finish_reason": summary.get("finish_reason"),
                    "duration_s": summary.get("duration_s"),
                    "planning_cycles": summary.get("planning_cycles"),
                    "observations": summary.get("observations"),
                    "unique_nodes": summary.get("unique_nodes"),
                    "updated_at": summary_path.stat().st_mtime,
                }
            )
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
        return sessions[: max(1, int(limit))]

    # ------------------------------------------------------------------ #
    # executor message routing                                            #
    # ------------------------------------------------------------------ #
    def _on_executor_message(self, message: dict[str, Any]) -> None:
        msg_type = str(message.get("type") or "")
        if msg_type == "event":
            raw = message.get("event")
            if isinstance(raw, dict) and self._adapter is not None:
                self._adapter.on_explorer_event(raw)
        elif msg_type == "session_result":
            self._apply_session_result(message.get("result") or {})
        elif msg_type == "worker_status":
            status = message.get("status") or {}
            if str(status.get("state")) == "running":
                self._mark_status(STATUS_RUNNING)
        elif msg_type == "error":
            self._publish_error(message.get("message") or "search worker error")

    def _apply_session_result(self, result: dict[str, Any]) -> None:
        if not isinstance(result, dict):
            return
        session_id = self._session_id or str(result.get("session_id") or "")
        finish_reason = str(result.get("finish_reason") or result.get("result") or "")
        finish = make_event(
            allocator=self._bus.allocator,
            session_id=session_id,
            event_type=SEARCH_FINISHED,
            payload={
                "result": finish_reason,
                "finish_reason": finish_reason,
                **result,
            },
        )
        self._bus.publish(finish)
        self._store.apply(finish)
        with self._lock:
            if self._info is not None:
                self._info.result = finish_reason
                self._info.finished_at = time.time()
            if finish_reason == "TARGET_FOUND":
                self._status = "TARGET_FOUND"
            elif finish_reason == "OPERATOR_STOP":
                self._status = "OPERATOR_STOP"
            elif finish_reason == "SEARCH_EXHAUSTED":
                self._status = "SEARCH_EXHAUSTED"
            else:
                self._status = "FINISHED"
            self.owner.release(OwnerState.AUTONOMOUS)
        # A subprocess worker is intentionally long-lived while a session is
        # active, but it must not survive a terminal session.  Retire it off
        # the executor callback thread: SubprocessSearchExecutor receives the
        # result from its stdout reader, while the in-process test executor
        # reports the result from its own worker thread.
        executor = self._executor
        if executor is not None and executor.alive():
            threading.Thread(
                target=self._retire_executor,
                args=(executor,),
                daemon=True,
                name="search-executor-retire",
            ).start()

    @staticmethod
    def _retire_executor(executor: SearchExecutor) -> None:
        try:
            executor.shutdown()
        except Exception:  # noqa: BLE001 - terminal cleanup is best effort
            pass

    def _publish_error(self, message: str) -> None:
        session_id = self._session_id or ""
        error_event = make_event(
            allocator=self._bus.allocator,
            session_id=session_id,
            event_type=ERROR,
            payload={"error_type": "SEARCH_ERROR", "message": message},
        )
        self._bus.publish(error_event)
        self._store.apply(error_event)
        self._mark_status("FAILED")
        self.owner.release(OwnerState.AUTONOMOUS)

    def _mark_status(self, status: str) -> None:
        with self._lock:
            self._status = status
            if self._info is not None:
                self._info.status = status

    def _elapsed(self, session: SearchSessionInfo) -> float:
        if session.finished_at is not None and session.started_at is not None:
            return round(max(0.0, session.finished_at - session.started_at), 2)
        if session.started_at is not None:
            return round(max(0.0, time.time() - session.started_at), 2)
        return 0.0


def _default_executor_factory() -> SearchExecutor:
    """Real deployment: subprocess worker under the ROS2 system Python."""
    from app.manual_web_demo.search_executor import SubprocessSearchExecutor

    return SubprocessSearchExecutor(
        log_path=_PROJECT_ROOT
        / "outputs"
        / "autonomous_search"
        / "logs"
        / "search_worker.log",
    )


def make_mock_executor_factory(
    *, scenario: str = "anchor_then_target", mock_target_after: int = 3,
    confirm_after_seen: int = 1, outcome_sequence: list[str] | None = None,
    backend_latency_sec: float = 0.0,
    scene_steps: list[dict[str, Any]] | None = None,
) -> Callable[[], SearchExecutor]:
    """Factory for tests / offline frontend dev (in-process mock)."""

    def factory() -> SearchExecutor:
        from app.manual_web_demo.search_executor import InProcessMockExecutor

        return InProcessMockExecutor(
            scenario=scenario,
            mock_target_after=mock_target_after,
            confirm_after_seen=confirm_after_seen,
            outcome_sequence=list(outcome_sequence or []),
            backend_latency_sec=backend_latency_sec,
            scene_steps=list(scene_steps or []),
        )

    return factory
