#!/usr/bin/env python3
"""Autonomous search worker for the WebUI (plan book §12, §86).

Spawned by the FastAPI process as a separate subprocess so the search chain
(real Go2-W backend: rclpy + /go2w/motion) runs under the ROS2 system Python
while the Web process stays in the Conda environment.

Protocol (stdin, JSONL):
    {"cmd":"start","params":{...}}
    {"cmd":"pause"} / {"cmd":"resume"} / {"cmd":"stop"} / {"cmd":"estop"}
    {"cmd":"status"} / {"cmd":"shutdown"}

Protocol (stdout, JSONL):
    {"type":"ready", ...}
    {"type":"event","event":{explorer event dict}}   (realtime, graph-augmented)
    {"type":"worker_status","status":{...}}
    {"type":"error","message":...}
    {"result": {...}}  (SessionResult printed by run_semantic_exploration)

The worker reuses the CLI wiring in ``run_semantic_exploration.py`` via an
``event_hook`` that forwards every explorer event to stdout in realtime.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_STATE: dict[str, Any] = {
    "holder": None,
    "thread": None,
    "session_id": None,
    "finish_reason": "",
    "shutdown": False,
}


def emit(message: dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass


def make_event_hook() -> Any:
    def hook(event: dict[str, Any], holder: dict[str, Any]) -> dict[str, Any]:
        _STATE["holder"] = holder
        explorer = holder.get("explorer")
        if explorer is not None:
            _STATE["session_id"] = explorer.session_id
        if event.get("event") == "session_finish":
            _STATE["finish_reason"] = str(event.get("result") or "")
        emit({"type": "event", "event": event})
        return event

    return hook


def build_argv(params: dict[str, Any]) -> list[str]:
    argv = [
        "--target", str(params.get("target") or ""),
        "--backend", str(params.get("backend") or "go2w_experimental"),
        "--reasoner", str(params.get("reasoner") or "unigoal"),
    ]
    if params.get("enable_autonomous_motion"):
        argv.append("--operator-supervised-experiment")
    if params.get("dry_run_motion"):
        argv.append("--dry-run-motion")
    if params.get("turn_only"):
        argv.append("--turn-only")
    if params.get("finish_on_visual_confirmation") is False:
        argv.append("--no-finish-on-visual-confirmation")
    for key, flag in (
        ("max_seconds", "--max-seconds"),
        ("max_planning_cycles", "--max-planning-cycles"),
        ("max_motion_steps", "--max-motion-steps"),
        ("max_turn_deg", "--max-turn-deg"),
        ("forward_step_m", "--forward-step-m"),
    ):
        value = params.get(key)
        if value is not None:
            argv += [flag, str(value)]
    for key, flag in (
        ("llm_model", "--llm-model"),
        ("detector", "--detector"),
        ("spool_root", "--spool-root"),
        ("odom_topic", "--odom-topic"),
        ("mock_scenario", "--mock-scenario"),
        ("output", "--output"),
        ("session_dir", "--session-dir"),
        ("replay", "--replay"),
        ("record_video", "--record-video"),
    ):
        value = params.get(key)
        if value:
            argv += [flag, str(value)]
    if params.get("verify_min_confidence") is not None:
        argv += ["--verify-min-confidence", str(params["verify_min_confidence"])]
    if params.get("mock_target_after") is not None:
        argv += ["--mock-target-after", str(params["mock_target_after"])]
    if params.get("mock_confirm_after_seen") is not None:
        argv += ["--mock-confirm-after-seen", str(params["mock_confirm_after_seen"])]
    if params.get("allow_degraded"):
        argv.append("--allow-degraded")
    return argv


def run_session(params: dict[str, Any]) -> None:
    import run_semantic_exploration as rse

    argv = build_argv(params)
    parser = rse.build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        emit({"type": "error", "message": f"bad start params: {exc}"})
        return
    hook = make_event_hook()
    try:
        if args.replay:
            rc = rse.run_replay(args, hook)
        elif args.backend == "go2w_experimental":
            rc = rse.run_go2w(args, hook)
        else:
            rc = rse._run_offline(args, hook)
    except Exception as exc:  # noqa: BLE001
        import traceback

        emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        emit({"type": "error", "message": traceback.format_exc()[-2000:]})
        rc = 4
    emit(
        {
            "type": "session_result",
            "result": {
                "exit_code": rc,
                "session_id": _STATE.get("session_id"),
                "finish_reason": _STATE.get("finish_reason") or "",
            },
        }
    )


def handle_command(command: dict[str, Any]) -> None:
    cmd = str(command.get("cmd") or "")
    if cmd == "start":
        if _STATE["thread"] is not None and _STATE["thread"].is_alive():
            emit({"type": "error", "message": "search already running"})
            return
        thread = threading.Thread(
            target=run_session, args=(dict(command.get("params") or {}),),
            daemon=True, name="search-session",
        )
        _STATE["thread"] = thread
        _STATE["finish_reason"] = ""
        thread.start()
    elif cmd == "pause":
        explorer = _explorer()
        if explorer is not None:
            explorer.request_pause()
    elif cmd == "resume":
        explorer = _explorer()
        if explorer is not None:
            explorer.request_resume()
    elif cmd == "stop":
        explorer = _explorer()
        if explorer is not None:
            explorer.request_stop()
    elif cmd == "estop":
        explorer = _explorer()
        if explorer is not None:
            explorer.request_stop()
    elif cmd == "status":
        emit(
            {
                "type": "worker_status",
                "status": {
                    "state": "running" if _running() else "idle",
                    "session_id": _STATE.get("session_id"),
                    "finish_reason": _STATE.get("finish_reason"),
                },
            }
        )
    elif cmd == "shutdown":
        explorer = _explorer()
        if explorer is not None:
            explorer.request_stop()
        _STATE["shutdown"] = True


def _explorer():
    holder = _STATE.get("holder")
    return holder.get("explorer") if holder else None


def _running() -> bool:
    thread = _STATE.get("thread")
    return thread is not None and thread.is_alive()


def main() -> int:
    emit({"type": "ready", "pid": str(Path("/proc/self/stat").read_text().split()[0])
          if Path("/proc/self/stat").is_file() else "unknown"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(command, dict):
            continue
        handle_command(command)
        if _STATE.get("shutdown"):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
