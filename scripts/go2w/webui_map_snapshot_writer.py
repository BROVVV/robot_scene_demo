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
from nav_msgs.msg import Odometry
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
        #   min_z_m/max_z_m  —— 高度过滤。原点在雷达。2026-09-11 实测：
        #     地面在 z≈-0.23（低层点拟合平面 z@原点=-0.234，倾角仅 0.08°，
        #     说明不是整体倾斜；但它不是一张干净平面，而是 -0.336 / -0.097
        #     两张相距 0.24 m 的平行面，残差 std 0.13 m）。
        #     俯视投影时地面回波把房间内部填成一整团，z<0 的点占 30%，
        #     完全不提供结构信息，只会糊住画面。默认取 0.15（在两张地面
        #     之上、家具与墙之下）；要连地面一起看就设回 -0.45。
        #     上限 2.80 去掉天花板与灯。
        self.declare_parameter("max_range_m", 25.0)
        self.declare_parameter("min_range_m", 1.0)
        self.declare_parameter("voxel_size_m", 0.05)
        self.declare_parameter("min_z_m", 0.15)
        self.declare_parameter("max_z_m", 2.80)
        # 轨迹距离过滤 —— 这是唯一有物理依据的幽灵点判据。
        # 2026-09-11 实测（用户遥控走完房间后）：
        #   单帧 /go2w/slam/deskewed_scan 最远只到 9.9 m（半径 p50=1.9 p99=6.4），
        #   而累积 /go2w/slam/map_3d 铺到 x[-60.9,27.6] y[-59.2,34.4]。
        #   地图 = 45 个关键帧点云按优化位姿摆放的并集，其中的误回环会把整帧
        #   点云扔到几十米外 —— 密度过滤分不掉它们（它们自己就是密云，
        #   0.5 m 邻域 p50 有 68 个邻居），但"被观测到的点必在某个位姿的
        #   雷达量程内"是硬约束。实测距轨迹 >12 m 的点占 3.9%（5803 个），
        #   却把 WebUI 的自动缩放宽到 60 m，房间只占画幅 7%，这才是"发糊"。
        # 不给 0 表示不启用；轨迹点不足 20 个时也不启用（避免半截轨迹误删真点）。
        self.declare_parameter("odom_topic", "/go2w/slam/odom_base")
        self.declare_parameter("trajectory_radius_m", 12.0)
        # 2026-09-11 二次核验（分层渲染）修正了上面的判断：>12 m 的点只占
        # 3.9%，砍掉后画幅 60 m→27 m，但房间仍只占 26%。分层画出来才看清：
        # 5-12 m 那层是**空间上完全分离的走廊结构**（矩形边界、栏杆条纹），
        # 不是与房间错位叠加的整帧幽灵；0-5 m 那层本身就是一张轮廓清晰的
        # 房间图。所以"发糊"的主因不在数据，在渲染（见下面的 focus_bounds）。
        # 过滤器保留：它挡的是真实的误回环离群帧，只是量没当初以为的大。
        #
        # 画幅聚焦边距（focus_bounds）。WebUI 用点的 bbox 自动定缩放，而地图
        # 含 12 m 内的走廊，bbox 有 27×31 m，房间（8 m）只占画幅 26%，细节
        # 全挤在一起。轨迹是"狗真正建过图的区域"，拿轨迹 bbox 加这个边距当
        # 画幅，房间能占约 60%。给 0 表示不提供（前端退回用点云 bbox）；
        # 轨迹不足 20 点时同样不提供。
        self.declare_parameter("focus_margin_m", 3.0)
        # 轨迹落盘：writer 重启后不至于丢掉整条轨迹（否则要么过滤器失效，
        # 要么拿半截轨迹误删真点）。SLAM 会话重启后地图换系，旧轨迹会
        # 让过滤比例飙到接近 100%，由下面的 50% 护栏自动停用。
        self.declare_parameter("trajectory_path", os.path.join(
            os.path.dirname(DEFAULT_SNAPSHOT), "slam_map_trajectory.json"))
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
        self._min_z = float(self.get_parameter("min_z_m").value)
        self._max_z = float(self.get_parameter("max_z_m").value)
        self._traj_radius = float(self.get_parameter("trajectory_radius_m").value)
        self._trajectory_path = str(self.get_parameter("trajectory_path").value)
        self._focus_margin = float(self.get_parameter("focus_margin_m").value)
        self._trajectory_saved = 0
        self._trajectory: list = self._load_trajectory()
        self._snapshot_path = str(self.get_parameter("snapshot_path").value)
        self._max_points = int(self.get_parameter("max_points").value)
        self._latest = None
        self._received = 0
        # 地图**数据**最后一次变化的墙钟时间（不是文件写入时间）。_flush 每
        # period_s 秒重写一次快照，generated_at 会跟着刷新；拿它当地图年龄等于
        # 恒报新鲜。前端"slam_map_3d.js"的"地图 X 前"读的是读取端算出的
        # map_age_seconds，而读取端取的就是 map_updated_at 这个键。
        self._map_updated_at = None
        self._last_ros_stamp = None
        topic = str(self.get_parameter("map_topic").value)
        # map_3d 多为 RELIABLE 发布（transient_local），两个 QoS 都订阅以兼容。
        self.create_subscription(PointCloud2, topic, self._on_cloud, reliable(3))
        self.create_subscription(PointCloud2, topic, self._on_cloud, best_effort(3))
        self.create_subscription(PointCloud2, topic, self._on_cloud, latched(1))
        odom_topic = str(self.get_parameter("odom_topic").value)
        if odom_topic and self._traj_radius > 0.0:
            # plain_slam_odom_adapter 以 BEST_EFFORT 发布；再挂一个 RELIABLE 订阅
            # 以兼容换成其它发布端的情况（重复帧由 _on_odom 的距离去重吸收）。
            self.create_subscription(Odometry, odom_topic, self._on_odom, best_effort(10))
            self.create_subscription(Odometry, odom_topic, self._on_odom, reliable(10))
        else:
            odom_topic = ""
        self.create_timer(float(self.get_parameter("period_s").value), self._flush)
        self.get_logger().info(f"snapshot writer: {topic} -> {self._snapshot_path}")
        if odom_topic:
            self.get_logger().info(
                f"trajectory filter: {odom_topic} (radius {self._traj_radius} m)")

    def _focus_bounds(self):
        """给 WebUI 定画幅的聚焦框（轨迹 bbox + 边距，见参数处说明）。"""
        if self._focus_margin <= 0.0 or len(self._trajectory) < 20:
            return None
        trajectory = np.asarray(self._trajectory, dtype=np.float64)
        low, high = trajectory.min(axis=0), trajectory.max(axis=0)
        return {
            "min": [float(low[0] - self._focus_margin),
                    float(low[1] - self._focus_margin), self._min_z],
            "max": [float(high[0] + self._focus_margin),
                    float(high[1] + self._focus_margin), self._max_z],
        }

    def _load_trajectory(self) -> list:
        try:
            with open(self._trajectory_path, encoding="utf-8") as handle:
                return [(float(x), float(y)) for x, y in json.load(handle)]
        except (OSError, ValueError, TypeError):
            return []

    def _save_trajectory(self) -> None:
        if len(self._trajectory) == self._trajectory_saved:
            return
        try:
            os.makedirs(os.path.dirname(self._trajectory_path), exist_ok=True)
            temporary = f"{self._trajectory_path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump([[round(x, 3), round(y, 3)] for x, y in self._trajectory], handle)
            os.replace(temporary, self._trajectory_path)
            self._trajectory_saved = len(self._trajectory)
        except OSError as error:
            self.get_logger().warn(f"cannot persist trajectory: {error}")

    def _on_odom(self, message: Odometry) -> None:
        """记录轨迹（同 pslam_map 系）。0.25 m 去重，避免静止时堆积。"""
        position = message.pose.pose.position
        x, y = float(position.x), float(position.y)
        if self._trajectory:
            last_x, last_y = self._trajectory[-1]
            if (x - last_x) ** 2 + (y - last_y) ** 2 < 0.0625:
                return
        self._trajectory.append((x, y))

    def _drop_far_from_trajectory(self, points):
        """丢掉离轨迹超过 _traj_radius 的点（幽灵点判据，见参数处说明）。"""
        if self._traj_radius <= 0.0 or len(self._trajectory) < 20 or not len(points):
            return points
        trajectory = np.asarray(self._trajectory, dtype=np.float64)
        keep = np.zeros(len(points), dtype=bool)
        radius2 = self._traj_radius ** 2
        # 双向分块，内存有界（2000×256 点对）
        for start in range(0, len(points), 2000):
            block = points[start:start + 2000, :2]
            best = np.full(len(block), np.inf)
            for t0 in range(0, len(trajectory), 256):
                chunk = trajectory[t0:t0 + 256]
                delta = block[:, None, :] - chunk[None, :, :]
                best = np.minimum(best, (delta ** 2).sum(axis=-1).min(axis=1))
            keep[start:start + 2000] = best <= radius2
        if not keep.any():
            return points
        # 护栏：正常只滤掉个位数百分比；一旦超过一半，说明轨迹与当前地图不同系
        # （SLAM 会话重启后地图换原点），此时过滤会删掉真地图，直接停用。
        if (~keep).mean() > 0.5:
            self.get_logger().warn(
                f"trajectory filter would drop {100.0 * (~keep).mean():.0f}% of the map "
                f"— trajectory likely from another SLAM session, filter disabled")
            return points
        self.get_logger().info(
            f"trajectory filter dropped {int((~keep).sum())} ghost point(s) "
            f"(>{self._traj_radius} m from any pose)")
        return points[keep]

    def _on_cloud(self, message: PointCloud2) -> None:
        self._latest = message
        self._received += 1
        stamp = (float(message.header.stamp.sec)
                 + float(message.header.stamp.nanosec) * 1e-9)
        if stamp != self._last_ros_stamp:
            # 只有地图数据真的换了一帧才更新，重复投递同一帧（latched/多 QoS）
            # 不算"地图在长"。
            self._last_ros_stamp = stamp
            self._map_updated_at = time.time()

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
        # 高度过滤（去掉地面回波层与天花板/灯，见参数处说明）
        if len(points):
            points = points[(points[:, 2] >= self._min_z)
                            & (points[:, 2] <= self._max_z)]
        # 轨迹距离过滤（去掉误回环扔到远处的整帧点云，见参数处说明）
        points = self._drop_far_from_trajectory(points)
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
            "voxel_size_m": self._voxel,
            "map_updated_at": self._map_updated_at,
            "bounds": {"min": [float(v) for v in points.min(axis=0)],
                       "max": [float(v) for v in points.max(axis=0)]},
            # 前端 centerAndSpan() 优先用这个定缩放；null 时退回 bounds。
            "focus_bounds": self._focus_bounds(),
            "points": [[round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3)]
                       for p in points],
            "objects": self._load_objects(),
            "mapping_mode": "mapping_assist",
            "motion_authorized": False,
            "safety_authorized": False,
            # 这两个是占位：读取端 load_slam_map_snapshot() 会用 generated_at
            # 重算并覆盖它们，改这里不影响前端。真正的地图年龄看 map_updated_at。
            "age_seconds": 0.0,
            "fresh": True,
            "web_display_points": int(len(points)),
            "map_age_seconds": None,
            "preview_age_seconds": None,
        }
        self._save_trajectory()
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
