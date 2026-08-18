"""REST + WebSocket routes for the autonomous search WebUI
(plan book §10, §21, §22-§24, §29, §51, §98).

Mounts a FastAPI ``APIRouter`` with ``/api/search/*`` and ``/ws/search`` on
top of the existing manual-demo FastAPI server (no second server).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Callable

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.live_robot.search_event import SearchEvent
from app.manual_web_demo.search_models import SearchStartRequest
from app.manual_web_demo.search_session_service import SearchSessionService

HEARTBEAT_INTERVAL_SEC = 15.0


# --------------------------------------------------------------------------- #
# WebSocket hub: bridges the SearchEventBus to every /ws/search client         #
# --------------------------------------------------------------------------- #
class SearchEventHub:
    """Subscribes the service event bus once and fans events out to all
    connected ``/ws/search`` sockets (thread-safe)."""

    def __init__(self, service: SearchSessionService) -> None:
        self._service = service
        self._connections: set[WebSocket] = set()
        self._lock = threading.Lock()
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._unsubscribe = service.subscribe_events(self._on_event)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain_loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            self._unsubscribe()
        except Exception:  # noqa: BLE001
            pass

    def add(self, websocket: WebSocket) -> None:
        with self._lock:
            self._connections.add(websocket)

    def remove(self, websocket: WebSocket) -> None:
        with self._lock:
            self._connections.discard(websocket)

    def _on_event(self, event: SearchEvent) -> None:
        self._outbox.put_nowait({"type": "event", "event": event.to_dict()})

    async def _drain_loop(self) -> None:
        while True:
            try:
                message = await self._outbox.get()
            except asyncio.CancelledError:
                break
            with self._lock:
                targets = list(self._connections)
            text = json.dumps(message, ensure_ascii=False)
            for websocket in targets:
                try:
                    await websocket.send_text(text)
                except Exception:  # noqa: BLE001
                    pass


# --------------------------------------------------------------------------- #
# Router factory                                                              #
# --------------------------------------------------------------------------- #
def create_search_router(
    service: SearchSessionService,
    *,
    on_estop: Callable[[], None] | None = None,
    readiness_provider: Callable[[], dict[str, Any]] | None = None,
) -> tuple[APIRouter, SearchEventHub, Any]:
    """Returns (api_router, hub, ws_handler).

    The WebSocket handler is returned separately because ``/ws/search`` must
    live outside the ``/api/search`` prefix (plan book §22).
    """
    router = APIRouter(prefix="/api/search")
    hub = SearchEventHub(service)

    def _estop() -> None:
        service.estop_search()
        if on_estop is not None:
            on_estop()

    @router.post("/start")
    async def search_start(request: Request) -> JSONResponse:
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        req = SearchStartRequest.from_dict(raw)
        result = service.start_search(req)
        if result.get("ok"):
            return JSONResponse(
                {
                    "ok": True,
                    "session_id": result["session_id"],
                    "status": result["status"],
                }
            )
        status_code = 409 if result.get("conflict") else 400
        return JSONResponse({"ok": False, "error": result["error"]}, status_code=status_code)

    @router.post("/pause")
    async def search_pause() -> JSONResponse:
        result = service.pause_search()
        return JSONResponse(result, status_code=200 if result.get("ok") else 409)

    @router.post("/resume")
    async def search_resume() -> JSONResponse:
        result = service.resume_search()
        return JSONResponse(result, status_code=200 if result.get("ok") else 409)

    @router.post("/stop")
    async def search_stop() -> JSONResponse:
        result = service.stop_search()
        return JSONResponse(result)

    @router.post("/estop")
    async def search_estop() -> JSONResponse:
        _estop()
        return JSONResponse({"ok": True, "status": "ESTOP"})

    @router.get("/state")
    async def search_state() -> dict[str, Any]:
        return service.state_snapshot()

    @router.get("/map")
    async def search_map() -> dict[str, Any]:
        return service.map_snapshot()

    @router.get("/objects")
    async def search_objects() -> dict[str, Any]:
        return service.objects_snapshot()

    @router.get("/events")
    async def search_events(limit: int = 200) -> dict[str, Any]:
        return {"events": service.recent_events(max(1, min(limit, 2000)))}

    @router.get("/history")
    async def search_history(limit: int = 50) -> dict[str, Any]:
        return {"sessions": service.history(limit)}

    @router.get("/readiness")
    async def search_readiness() -> dict[str, Any]:
        if readiness_provider is not None:
            return readiness_provider()
        return service.executor_state()

    @router.get("/executor")
    async def search_executor() -> dict[str, Any]:
        return service.executor_state()

    async def ws_search(websocket: WebSocket) -> None:
        await websocket.accept()
        hub.add(websocket)
        # 1. full snapshot, 2. recent events, 3. live increments.
        try:
            await websocket.send_text(
                json.dumps(
                    {"type": "snapshot", "state": service.state_snapshot()},
                    ensure_ascii=False,
                )
            )
            await websocket.send_text(
                json.dumps(
                    {"type": "events", "events": service.recent_events(200)},
                    ensure_ascii=False,
                )
            )
            last_heartbeat = time.monotonic()
            while True:
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                    last_heartbeat = now
                    await websocket.send_text(
                        json.dumps({"type": "heartbeat"}, ensure_ascii=False)
                    )
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(websocket)

    return router, hub, ws_search
