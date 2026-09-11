#!/usr/bin/env python3
"""把当前权威 SLAM 地图写成 WebUI 的地图快照（go2w_slam_web_cloud_v1）。

背景：manual_web_demo 的 ``/api/slam/map3d`` **不订阅实时话题**，而是直接读快照文件
``outputs/autonomous_search/runtime/slam_map_3d.json``。该文件在本机已停止更新
（现存副本的时间戳是 2026-09-02，且其中 pose 是异常值 2.28e11）——这就是 WebUI 上
"地图重叠"的直接来源。本节点周期性地把**当前选择的地图话题**写成该快照格式。

用法（先 source scripts/go2w/setup_environment.sh）：

    /usr/bin/python3 scripts/go2w/webui_map_snapshot_writer.py --ros-args \
      -p map_topic:=/go2w/rko_slam/map_3d -p max_points:=60000
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from sensor_msgs.msg import PointCloud2

DEFAULT_SNAPSHOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "outputs/autonomous_search/runtime/slam_map_3d.json")

# 三维视图的置信度门限。检测端工作在 GroundingDINO box_threshold=0.12 这种偏召回
# 的阈值上，上游没有任何一层按分数拦过，于是碎片框全落到画面上：2026-09-11 实测
# 一次搜索出 17 个"垃圾桶"，只有 1 个 0.82，其余 16 个在 0.12~0.33 —— 里面有
# 2.7cm 宽的、8cm 立方的、悬空 z=0.74 的，还有同一物体同帧分裂出的两个框
# （相距 3mm 各自建了实体）。这里只做显示层过滤，不动 events.jsonl 里的原始记录，
# 取证与融合链路不受影响。
MIN_OBJECT_CONFIDENCE = 0.35


def best_effort(depth: int = 5) -> QoSProfile:
    return QoSProfile(depth=depth, reliability=ReliabilityPolicy.BEST_EFFORT,
                      history=HistoryPolicy.KEEP_LAST,
                      durability=DurabilityPolicy.VOLATILE)


def reliable(depth: int = 5) -> QoSProfile:
    return QoSProfile(depth=depth, reliability=ReliabilityPolicy.RELIABLE,
                      history=HistoryPolicy.KEEP_LAST,
                      durability=DurabilityPolicy.VOLATILE)


def latched(depth: int = 1) -> QoSProfile:
    """plain_slam 的 map_3d 是 transient_local 发布：新订阅者要收到"最后一帧"必须匹配。"""
    return QoSProfile(depth=depth, reliability=ReliabilityPolicy.RELIABLE,
                      history=HistoryPolicy.KEEP_LAST,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


class WebuiSnapshotWriter(Node):
    def __init__(self) -> None:
        super().__init__("webui_map_snapshot_writer")
        self.declare_parameter("map_topic", "/go2w/rko_slam/map_3d")
        self.declare_parameter("snapshot_path", DEFAULT_SNAPSHOT)
        self.declare_parameter("max_points", 60000)
        self.declare_parameter("period_s", 5.0)
        # 显示层清理（对应用户反馈的 B/C 类问题）：
        #   max_range_m      —— 去掉远处噪声/窗外散点
        #   min_range_m      —— 去掉机身自遮挡的近点
        #   voxel_size_m     —— 体素降采样，压掉重复叠加的冗余点
        self.declare_parameter("max_range_m", 25.0)
        self.declare_parameter("min_range_m", 1.0)
        self.declare_parameter("voxel_size_m", 0.05)
        # Dataset D 的语义观测（events.jsonl）：把物体标注一并写进快照，
        # WebUI 的三维视图会把这些物体画在地图上（见 static/slam_map_3d.js）。
        self.declare_parameter("objects_events", "")
        self.declare_parameter(
            "objects_glob",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "artifacts/slam_validation/semanticD_*/events.jsonl"))
        self._latest_bounds = None
        self._max_range = float(self.get_parameter("max_range_m").value)
        self._min_range = float(self.get_parameter("min_range_m").value)
        self._voxel = float(self.get_parameter("voxel_size_m").value)
        self._snapshot_path = str(self.get_parameter("snapshot_path").value)
        self._max_points = int(self.get_parameter("max_points").value)
        self._latest = None
        self._received = 0
        topic = str(self.get_parameter("map_topic").value)
        # map_3d 多为 RELIABLE 发布（transient_local），两个 QoS 都订阅以兼容。
        self.create_subscription(PointCloud2, topic, self._on_cloud, reliable(3))
        self.create_subscription(PointCloud2, topic, self._on_cloud, best_effort(3))
        self.create_subscription(PointCloud2, topic, self._on_cloud, latched(1))
        self.create_timer(float(self.get_parameter("period_s").value), self._flush)
        self.get_logger().info(f"snapshot writer: {topic} -> {self._snapshot_path}")

    def _on_cloud(self, message: PointCloud2) -> None:
        self._latest = message
        self._received += 1

    def _load_objects(self) -> list:
        """从 Dataset D 的 events.jsonl 读语义物体（label + map_xyz）。

        注意坐标系统一：物体坐标是"采集它那一次 SLAM 会话"的 pslam_map 坐标。
        若 SLAM 映射栈之后被重启，pslam_map 原点改变，旧物体就与当前地图不同系 ——
        因此这里每次写快照都**重新发现最新的 Dataset D**，并且只保留落在当前地图
        包围盒（带余量）内的物体，避免把过期坐标画到新地图上。
        """
        import glob
        path = str(self.get_parameter("objects_events").value)
        if not path or not os.path.exists(path):
            pattern = str(self.get_parameter("objects_glob").value)
            candidates = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            if not candidates:
                return []
            path = candidates[0]
        # 按 object_id 去重：events.jsonl 里同一个物体每次被观测/更新都会再写一条
        # `semantic_object_localized`（observation_count 递增，object_id 不变）。
        # 若把每条事件都当成新物体，就会把同一张桌子/主机/垃圾桶画两遍
        # （2026-09-10 实际发生过）。这里保留**最后一次**出现的坐标（最新观测）。
        deduped: dict = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") != "semantic_object_localized":
                    continue
                obj = event.get("object") or {}
                xyz = obj.get("map_xyz")
                if not xyz:
                    continue
                if (obj.get("confidence") or 0.0) < MIN_OBJECT_CONFIDENCE:
                    continue
                key = obj.get("object_id") or f"{obj.get('label')}|{xyz[0]:.2f}|{xyz[1]:.2f}"
                box_size = obj.get("box_size")
                size = None
                if isinstance(box_size, (list, tuple)) and len(box_size) == 3:
                    try:
                        size = [float(v) for v in box_size]
                    except (TypeError, ValueError):
                        size = None
                deduped[key] = {
                    "label": obj.get("label"),
                    "x": round(float(xyz[0]), 3),
                    "y": round(float(xyz[1]), 3),
                    "z": round(float(xyz[2]), 3),
                    "confidence": obj.get("confidence"),
                    "observation_count": obj.get("observation_count"),
                    "size": size,
                }
        objects = list(deduped.values())
        return self._filter_by_map_bounds(objects)

    def _filter_by_map_bounds(self, objects: list) -> list:
        """丢掉落在当前地图包围盒之外的物体（多为旧会话的过期坐标）。"""
        latest = self._latest_bounds
        if not objects or latest is None:
            return objects
        low, high = latest
        margin = 5.0
        kept = []
        for item in objects:
            if (low[0] - margin <= item["x"] <= high[0] + margin
                    and low[1] - margin <= item["y"] <= high[1] + margin):
                kept.append(item)
        if len(kept) != len(objects):
            self.get_logger().warn(
                f"dropped {len(objects) - len(kept)} object(s) outside current map bounds "
                f"(stale SLAM session coordinates?)")
        return kept

    def _flush(self) -> None:
        if self._latest is None:
            return
        message = self._latest
        count = int(message.width) * int(message.height)
        if count == 0:
            return
        raw = np.frombuffer(message.data, dtype=np.uint8).reshape(count, int(message.point_step))
        points = np.ascontiguousarray(raw[:, 0:12]).view(np.float32).reshape(-1, 3)
        points = points[np.isfinite(points).all(axis=1)]
        # range filter（去掉远处噪声与近处自遮挡）
        radius = np.linalg.norm(points, axis=1)
        points = points[(radius >= self._min_range) & (radius <= self._max_range)]
        # voxel 降采样（压掉"同一面墙重复叠加"的冗余点，让墙看着更薄更干净）
        if self._voxel > 0.0 and len(points):
            keys = np.floor(points / self._voxel).astype(np.int64)
            _, unique = np.unique(keys, axis=0, return_index=True)
            points = points[np.sort(unique)]
        step = max(1, len(points) // self._max_points)
        points = points[::step]
        self._latest_bounds = (
            [float(v) for v in points.min(axis=0)],
            [float(v) for v in points.max(axis=0)],
        ) if len(points) else None
        stamp = float(message.header.stamp.sec) + float(message.header.stamp.nanosec) * 1e-9
        payload = {
            "schema_version": "go2w_slam_web_cloud_v1",
            "available": bool(len(points)),
            "source": f"map_3d:{self.get_parameter('map_topic').value}",
            "frame_id": message.header.frame_id or "pslam_map",
            "target_map_frame": message.header.frame_id or "pslam_map",
            "ros_stamp": stamp,
            "generated_at": time.time(),
            "point_count": int(len(points)),
            "accumulated_voxels": int(len(points)),
            "received_scans": self._received,
            "accumulated_scans": self._received,
            "dropped_scans": 0,
            "dropped_reason_counts": {},
            "last_dropped_reason": None,
            "stationary": True,
            "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
            "delta_yaw_rad": 0.0,
            "voxel_size_m": 0.12,
            "bounds": {"min": [float(v) for v in points.min(axis=0)],
                       "max": [float(v) for v in points.max(axis=0)]},
            "points": [[round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3)]
                       for p in points],
            "objects": self._load_objects(),
            "mapping_mode": "mapping_assist",
            "motion_authorized": False,
            "safety_authorized": False,
            "age_seconds": 0.0,
            "fresh": True,
            "web_display_points": int(len(points)),
            "map_age_seconds": None,
            "preview_age_seconds": None,
        }
        os.makedirs(os.path.dirname(self._snapshot_path), exist_ok=True)
        temporary = f"{self._snapshot_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, self._snapshot_path)
        self.get_logger().info(
            f"snapshot written: {len(points)} pts frame={payload['frame_id']} "
            f"({os.path.getsize(self._snapshot_path)} bytes)")


def main() -> int:
    rclpy.init()
    node = WebuiSnapshotWriter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
