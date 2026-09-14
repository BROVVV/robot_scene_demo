"""Read the latest mapping-assist point-cloud snapshot for FastAPI."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def unavailable_snapshot(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "go2w_slam_web_cloud_v2",
        "available": False,
        "reason": reason,
        "points": [],
        "point_count": 0,
        "mapping_mode": "mapping_assist",
        "motion_authorized": False,
        "safety_authorized": False,
        "mapping_health": "UNAVAILABLE",
        "health_reason": "建图快照还没生成",
        "lio_pose_valid": False,
        "motion_odom_seen": False,
        "canonical_frame": "pslam_map",
        "permanent_source": "/go2w/slam/map_3d",
        "mapping_session_id": 0,
        "map_revision": 0,
        "source_map_points": 0,
        "global_cached_voxels": 0,
        "web_display_points": 0,
        "map_extent_m": [0.0, 0.0, 0.0],
        "capacity_limited": False,
        "preview": {"frame_id": "", "ros_stamp": 0.0, "point_count": 0, "points": []},
        "rejected_counts": {},
    }


def load_slam_map_snapshot(path: str | Path) -> dict[str, Any]:
    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        return unavailable_snapshot("snapshot_not_ready")
    try:
        value = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return unavailable_snapshot("snapshot_unreadable")
    if not isinstance(value, dict) or not isinstance(value.get("points"), list):
        return unavailable_snapshot("snapshot_invalid")
    now = time.time()
    generated_at = value.get("generated_at")
    try:
        age = max(0.0, now - float(generated_at))
    except (TypeError, ValueError):
        age = None
    result = dict(value)
    result["age_seconds"] = age
    # fresh 是"写端心跳还活着"（generated_at 每 period_s 重写一次必然刷新），
    # 不是"地图数据新"。写端 period_s 默认 5.0 s，门限必须大于它 —— 原来的
    # 3.0 s 比节拍还短，于是页面每隔几秒就绿黄跳一次，实测 12 次采样 5 次 STALE。
    result["fresh"] = age is not None and age <= 6.0
    result["available"] = bool(value.get("available", True)) and bool(value["points"])
    result["point_count"] = len(value["points"])
    result["web_display_points"] = len(value["points"])
    # §9.3：全局地图和当前 scan 是两个时间轴，网页必须分别显示，不能混成一个。
    # 用地图自己的 ros_stamp 算年龄，不用 map_updated_at（= 写端收到消息的时刻）：
    # map_3d 是 transient_local 发布，写端一重启就会立刻收到重投的那一帧，
    # map_updated_at 于是变成"刚刚"，而数据其实还是几十钟头前那张 —— 页面上会
    # 显示"地图 18.1s 前"这种假 LIVE。ros_stamp 跟着数据走，重启也不会变。
    map_stamp = value.get("ros_stamp")
    if not isinstance(map_stamp, (int, float)) or map_stamp <= 0.0:
        map_stamp = value.get("map_updated_at")
    result["map_age_seconds"] = _age(now, map_stamp)
    result["preview_age_seconds"] = _age(
        now, (value.get("preview") or {}).get("wall_time"))
    # These are facts about this isolated display path, not inferred flags.
    result["mapping_mode"] = "mapping_assist"
    result["motion_authorized"] = False
    result["safety_authorized"] = False
    return result


def _age(now: float, stamp: Any) -> float | None:
    try:
        value = float(stamp)
    except (TypeError, ValueError):
        return None
    return max(0.0, now - value) if value > 0.0 else None
