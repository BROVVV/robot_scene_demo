#!/usr/bin/env python3
"""实体遥控器建图的语义侧车：周期采 RGB-D 关键帧并把物体落到 pslam_map。

计划书：docs/GO2W_遥控主动建图与周期物体落图_MVP实施计划_20260918.md §6.1

这个进程**只读**：订阅 SLAM 位姿 / TF，读 D435 的 HTTP RGB-D，写自己的
events.jsonl 与 status.json。它没有任何 motion client、Action client、速度
发布器或遥控器发布器 —— 机器狗怎么动完全由 Unitree 实体遥控器决定。

两个执行单元，刻意不写成一个 ``sleep(5)`` 串行循环：

* **关键帧采集器**（主线程）：持续 ``spin_once()`` 喂 ROS 位姿缓存，按
  「位移 / 转角 / 最长等待」三选一判断是否留帧，留帧后立刻返回继续 spin。
* **单实例推理消费者**（后台线程）：始终只有一个 GroundedSAM 在跑，上一帧
  处理完立刻取下一帧；队列空时才等待。

采帧与推理解耦保证的是**路径视觉覆盖**：GPU 忙时地图上的物体标记会滞后，
但已经采下来的中间关键帧仍会随后被处理，不会因为检测器忙就丢掉整段路。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ------------------------------------------------------------------ 状态常量
STATE_WAITING = "waiting"
STATE_LOADING = "loading"
STATE_RUNNING = "running"
STATE_DEGRADED = "degraded"
STATE_STOPPED = "stopped"

# 连续失败多少轮进入 degraded。取 3 是因为单帧 RGB-D 抖动、单次 GPU OOM 都
# 不该让页面显示成故障 —— 但连续三轮都失败就不是抖动了。
DEGRADED_AFTER_CONSECUTIVE_FAILURES = 3

# 采集时间戳可信度。D435 由机器狗侧服务打 `time.time()`，宿主侧 SLAM 位姿走
# ROS 时基 —— 两机时钟若不一致，SLAMBridge 的最近邻查找会**静默**取到错误的
# 位姿（本仓实测过一次 738.4s 的两机时钟差）。这个门限就是防那条假绿：
# 偏差超过它就不用拍摄时间戳，退回接收时刻（只有 HTTP 往返的毫秒级误差）。
DEFAULT_MAX_CLOCK_SKEW_S = 5.0

# RAM++ 场景词缓存时长，与 run_autonomous_loop 的取值一致。
DEFAULT_RAM_SCENE_TAG_INTERVAL_S = 8.0

# 「运动过快就跳过识别」的门限。只跳过识别，绝不干涉运动。取的是一眼能看出
# 「这不是在正常走」的量级，正常步行/转向不会触发。
DEFAULT_MAX_LINEAR_SPEED_MPS = 2.0
DEFAULT_MAX_ANGULAR_SPEED_DPS = 120.0

# 语义合并半径与确认观测数。与 run_semantic_exploration.py 里构造
# SemanticObjectMap 用的值保持一致 —— 计划书 §6.4 要求沿用现有语义合并半径，
# 不另起一套配置。
SEMANTIC_MAP_MERGE_DISTANCE_M = 0.4
SEMANTIC_MAP_CONFIRM_MIN_OBSERVATIONS = 2


def _wrap_pi(delta: float) -> float:
    """把角差（弧度）折到 (-pi, pi]。

    存在的理由：位姿里的 yaw 是弧度（SpatialPose 由 atan2 产出），而关键帧
    门限是按**度**配的。混用不会报错，只会让 15° 的门变成 15 rad —— 于是
    每一帧都被判成"转过了一个门限"。所以两条换算各自只走自己的入口。
    """
    return (float(delta) + math.pi) % (2.0 * math.pi) - math.pi


def _interpreter_has_torch(python: str) -> bool:
    """解释器是不是真的能 import torch（检测器子进程的最低要求）。

    只查文件存在是不够的：本机的 .venv/bin/python 存在但没有 torch，选中它
    会让 GroundedSAM 子进程起不来，而且失败面在子进程里，看起来像"识别不出
    东西"而不是"解释器选错了"。
    """
    try:
        completed = subprocess.run(
            [python, "-c", "import torch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass
class Keyframe:
    """一张待推理的关键帧。RGB / depth / intrinsics / 位姿在采帧瞬间一起冻结。"""

    frame_id: str
    capture_timestamp: float
    transform_timestamp: float
    timestamp_source: str
    clock_skew_seconds: float | None
    rgbd_frame: Any
    session_id: int
    robot_pose: dict[str, Any] = field(default_factory=dict)
    # 采帧时刻的 RGB-D 源健康快照，只作诊断。
    source_health: dict[str, Any] = field(default_factory=dict)

    def pose_xyz_yaw(self) -> tuple[float, float, float] | None:
        pose = self.robot_pose or {}
        if not all(_finite(pose.get(key)) for key in ("x", "y", "yaw")):
            return None
        return (float(pose["x"]), float(pose["y"]), float(pose["yaw"]))


class RemoteControlSemanticMapper:
    """采帧 + 推理 + 落图。所有对外可见的状态都写在 status.json 里。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.events_path = Path(args.events).resolve()
        self.status_path = Path(args.status).resolve()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)

        self._stop = threading.Event()
        # ROS 侧状态（provider 的 _map/_pose/_map_bridge）由采集线程独占更新，
        # 推理线程只在调用 camera_point_to_map 的瞬间借用。临界区必须短 ——
        # 推理线程持有它 1.9 秒会把 spin 饿死，反之会让变换用到半更新的位姿。
        self._provider_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._status_lock = threading.Lock()
        # 状态文件的临时文件序号（见 _publish_status：多线程写同一个 tmp 名会撞）。
        self._status_write_seq = 0

        self._queue: deque[Keyframe] = deque()
        self._session_id = 0
        self._last_keyframe_pose: tuple[float, float, float] | None = None
        self._accumulated_yaw_deg = 0.0
        self._last_keyframe_host_s = 0.0
        self._last_yaw_sample: float | None = None
        self._prev_pose_sample: tuple[float, tuple[float, float, float]] | None = None
        self._speed_warned = False

        self._events_handle = None
        self._inference_thread: threading.Thread | None = None

        self._provider = None
        self._rgbd_source = None
        self._detector = None
        self._localizer = None
        self._semantic_map = None
        self._settings = None
        # §2.3：三维对象的上屏发布器。会话轮换时把 last_revision 清掉，新的
        # （空的）实体表才会被写出去，覆盖掉上一会话留下的对象快照。
        self._object_publisher: Any = None
        self._object_revision = 0
        self._cached_ram_terms: list[str] = []
        self._last_ram_tag_monotonic = 0.0
        self._detect_lock = threading.Lock()

        self._launch_log_path = PROJECT_ROOT / "runtime/go2w/sessions/plain_slam_go2w.launch.log"
        self._launch_log_size: int | None = None

        self._status: dict[str, Any] = {
            "state": STATE_LOADING,
            "session_id": 0,
            "pid": os.getpid(),
            "events_path": str(self.events_path),
            "started_at": time.time(),
            "last_attempt_at": None,
            "last_success_at": None,
            "last_frame_id": None,
            "last_detection_count": 0,
            "last_localized_count": 0,
            "persistent_object_count": 0,
            "queue_depth": 0,
            "captured_keyframes": 0,
            "dropped_keyframes": 0,
            "dropped_redundant_keyframes": 0,
            "processed_keyframes": 0,
            "failed_keyframes": 0,
            "consecutive_failures": 0,
            "skipped_robot_moving_fast": 0,
            "inference_latency_seconds": None,
            "slam_ready": False,
            "quality": None,
            "map_frame": None,
            "clock_skew_seconds": None,
            "last_error": None,
            "last_error_at": None,
        }

    # ------------------------------------------------------------------ 日志
    def _log(self, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)

    def _host_s(self) -> float:
        return round(time.monotonic(), 6)

    # ------------------------------------------------------------ 事件落盘
    def _write_event(self, event: dict[str, Any]) -> None:
        if self._events_handle is None:
            return
        try:
            row = json.loads(json.dumps(event, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            self._log(f"WARNING: 事件含非法值，已丢弃: {exc}")
            return
        self._events_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._events_handle.flush()

    # -------------------------------------------------------------- 状态文件
    def _publish_status(self, **updates: Any) -> None:
        with self._status_lock:
            self._status.update(updates)
            payload = json.loads(
                json.dumps(self._status, ensure_ascii=False, allow_nan=False)
            )
        # 临时文件名必须**每次都不一样**，不能只用 pid：采集线程和推理线程都会
        # 发状态，两者共用同一个 tmp 名时，先跑完的那个 os.replace 会把文件改走，
        # 后一个就 ENOENT —— 表现为"状态偶尔写不进去"，而 _status_lock 只护住了
        # 内存里的 dict，护不住这两行文件操作。加序号即可，不需要再加一把锁。
        with self._status_lock:
            self._status_write_seq += 1
            seq = self._status_write_seq
        tmp_path = self.status_path.with_suffix(f".json.tmp.{os.getpid()}.{seq}")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_path, self.status_path)
        except OSError as exc:
            self._log(f"WARNING: 状态写入失败: {exc}")
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _bump(self, key: str, delta: int = 1) -> None:
        with self._status_lock:
            self._status[key] = int(self._status.get(key, 0)) + delta

    def _fail(self, message: str) -> None:
        with self._status_lock:
            self._status["last_error"] = str(message)[:2000]
            self._status["last_error_at"] = time.time()
            self._status["consecutive_failures"] = (
                int(self._status.get("consecutive_failures", 0)) + 1
            )
            failures = int(self._status["consecutive_failures"])
            self._status["state"] = (
                STATE_DEGRADED
                if failures >= DEGRADED_AFTER_CONSECUTIVE_FAILURES
                else self._status.get("state", STATE_LOADING)
            )
        self._log(f"WARNING: {message}")

    # ---------------------------------------------------------------- 初始化
    def setup(self) -> None:
        from app.config import get_settings
        from app.perception.depth_object_localizer import DepthObjectLocalizer
        from app.perception.realsense_http_rgbd_source import (
            RealSenseHTTPRGBDSource,
        )
        from app.spatial.plain_slam_spatial_provider import PlainSlamSpatialProvider
        from app.spatial.semantic_object_map import SemanticObjectMap

        self._publish_status(state=STATE_LOADING)
        settings = get_settings()
        self._settings = settings

        self._prepare_detector_env(settings)

        self._rgbd_source = RealSenseHTTPRGBDSource(
            self.args.rgbd_base_url,
            cache_dir=str(PROJECT_ROOT / "runtime/go2w/rgbd_cache"),
        )
        self._localizer = DepthObjectLocalizer()
        self._semantic_map = self._new_semantic_map()
        # 只订阅 map_2d / odom_base / /tf。enable_ros=True 会在内部建自己的
        # 节点，所以这个进程不再另建第二个节点。
        self._provider = PlainSlamSpatialProvider(
            enable_ros=True,
            map_topic="/go2w/slam/map_2d",
            odom_topic="/go2w/slam/odom_base",
        )
        self._events_handle = self.events_path.open("a", encoding="utf-8")
        self._launch_log_size = self._read_launch_log_size()

        from app.detectors.grounded_sam_subprocess import GroundedSAMSubprocessDetector

        # 构造本身不加载模型（worker 是在第一次 detect 时才 spawn 的），
        # 所以这里很快；真正的 cold start 8.5 秒发生在第一帧推理时。
        self._detector = GroundedSAMSubprocessDetector(settings)
        self._log(
            "语义侧车就绪："
            f"rgbd={self.args.rgbd_base_url} events={self.events_path}"
        )

    def _prepare_detector_env(self, settings: Any) -> None:
        """让常驻 GroundedSAM 子进程能 import ``app.*`` 并找到真解释器。

        detector 直接 ``os.environ.copy()`` 给子进程，而它是按绝对路径启动的，
        Python 不会自动把仓库根放进 sys.path —— 不补这一下，子进程里的
        ``from app.perception...`` 会 ImportError。
        """
        parts = [str(PROJECT_ROOT)]
        if settings.grounded_sam_pythonpath:
            parts.append(str(settings.grounded_sam_pythonpath))
        existing = os.environ.get("PYTHONPATH")
        if existing:
            parts.append(existing)
        os.environ["PYTHONPATH"] = os.pathsep.join(
            dict.fromkeys(part for part in parts if part)
        )
        python = str(settings.grounded_sam_python or "")
        if not python or not Path(python).is_file():
            # 回退链只收**真的有 torch** 的解释器。以前只判 ``is_file()``，
            # 于是在系统 Python 下会挑中同样存在的 .venv/bin/python —— 那个
            # 环境没有 torch，检测器子进程根本起不来，每一帧都失败：进程活着、
            # 状态显示 degraded、一个物体都不会有，日志里却没有任何一处写着
            # "解释器选错了"。宁可回退到没有候选、让 doctor 明确报错。
            checked: list[str] = []
            for candidate in (
                os.environ.get("GO2W_CONDA_PYTHON"),
                str(PROJECT_ROOT / ".runtime_venv/bin/python"),
                str(PROJECT_ROOT / ".venv/bin/python"),
                sys.executable,
            ):
                if not candidate or not Path(candidate).is_file():
                    continue
                if not _interpreter_has_torch(candidate):
                    checked.append(candidate)
                    continue
                os.environ["GROUNDED_SAM_PYTHON"] = candidate
                settings.grounded_sam_python = candidate
                self._log(f"检测器解释器回退到 {candidate}")
                break
            else:
                raise RuntimeError(
                    "GROUNDED_SAM_PYTHON 无效，且没有可用的回退解释器。"
                    f"已排除（没有 torch）：{checked or '无候选文件'}。"
                    "检查 .env 的 GROUNDED_SAM_PYTHON，"
                    "或确认启动脚本已经把 .env 灌进环境变量。"
                )

    def _new_semantic_map(self) -> Any:
        from app.spatial.semantic_object_map import SemanticObjectMap

        return SemanticObjectMap(
            merge_distance_m=SEMANTIC_MAP_MERGE_DISTANCE_M,
            confirm_min_observations=SEMANTIC_MAP_CONFIRM_MIN_OBSERVATIONS,
        )

    # ------------------------------------------------------------ 会话轮换
    def _read_launch_log_size(self) -> int | None:
        try:
            return self._launch_log_path.stat().st_size
        except OSError:
            return None

    def _check_session_rotation(self) -> None:
        """LIO launch 日志被截断 == ``start_plain_slam_mapping.sh`` 换会话。

        pslam_map 在新会话里可能重新定义，旧坐标系的物体会全部错位，所以必须
        清空实体图而不是继续合并。三态：文件读不到就**不轮换**（不动），
        只把大小记成 None —— 不能把"读不到"当成"轮换了"，那会在 SLAM 日志
        权限出问题时把整张图反复清空。
        """
        size = self._read_launch_log_size()
        if size is None:
            return
        previous = self._launch_log_size
        self._launch_log_size = size
        if previous is None or size >= previous:
            return
        self._rotate_session(reason="lio_launch_log_truncated")

    def _publish_display_objects(self, revision: int) -> None:
        """§2.3：实体图更新后把内存对象投影上屏（与自主搜索同一份投影函数）。

        拿不到地图桥的 ``map_epoch`` 时什么都不做 —— 对象坐标属于哪张地图无从
        声明就不该画，这是正确行为而不是失败。
        """
        if self._semantic_map is None:
            return
        if self._object_publisher is None:
            from app.spatial.object_display_snapshot import ObjectDisplayPublisher

            self._object_publisher = ObjectDisplayPublisher(
                meta_path=PROJECT_ROOT / str(self.args.map_meta_path),
                snapshot_path=PROJECT_ROOT / str(self.args.object_snapshot_path),
                producer_session_id=f"teleop_{self._session_id}",
            )
        self._object_publisher.publish(
            (entry.to_dict() for entry in self._semantic_map.objects.values()),
            graph_revision=revision,
        )

    def _rotate_session(self, *, reason: str) -> None:
        self._session_id += 1
        if self._object_publisher is not None:
            # 换会话后实体表是空的，版本号回到 0；不清掉上次的版本号，空表不会
            # 被写出去，页面上会继续显示上一张地图的物体。
            self._object_publisher.last_revision = None
        self._object_revision = 0
        with self._queue_lock:
            dropped = len(self._queue)
            self._queue.clear()
        self._semantic_map = self._new_semantic_map()
        self._last_keyframe_pose = None
        self._accumulated_yaw_deg = 0.0
        self._last_keyframe_host_s = 0.0
        self._last_yaw_sample = None
        self._prev_pose_sample = None
        self._write_event({
            "event": "semantic_session_reset",
            "reason": reason,
            "session_id": self._session_id,
            "discarded_keyframes": dropped,
            "host_s": self._host_s(),
        })
        self._publish_status(
            session_id=self._session_id,
            persistent_object_count=0,
            queue_depth=0,
        )
        self._log(f"SLAM 换会话 → 语义实体图已清空（丢弃 {dropped} 张待推理关键帧）")

    # ---------------------------------------------------------- 采帧判定
    def _current_speed(self, pose_xyz_yaw: tuple[float, float, float]) -> tuple[float, float]:
        """用相邻两个 poll 周期的位姿差估速度（m/s, deg/s）。

        provider 的 SpatialPose 不带 twist，为这个可选门再加一条 odom 订阅
        不值得 —— 位姿差分对"是不是在正常走"这个粗判够用。

        窗口固定成一个 poll 周期（而不是"上次位姿变化至今"）：后者在静止一段
        时间后突然起步时会把 dt 算成整段静止时长，从而**低估**速度、放行本该
        跳过的高速帧。位姿没变时距离≈0，下面的门限自然不会触发。
        """
        now = time.monotonic()
        previous = self._prev_pose_sample
        self._prev_pose_sample = (now, pose_xyz_yaw)
        if previous is None:
            return (0.0, 0.0)
        prev_t, (px, py, pyaw) = previous
        dt = now - prev_t
        if dt <= 1e-3:
            return (0.0, 0.0)
        distance = math.hypot(pose_xyz_yaw[0] - px, pose_xyz_yaw[1] - py)
        turned_deg = abs(math.degrees(_wrap_pi(pose_xyz_yaw[2] - pyaw)))
        if distance < 1e-4 and turned_deg < 0.5:
            return (0.0, 0.0)
        return (distance / dt, turned_deg / dt)

    def _should_capture(self, pose_xyz_yaw: tuple[float, float, float]) -> bool:
        if self._last_keyframe_pose is None:
            return True
        previous = self._last_keyframe_pose
        distance = math.hypot(pose_xyz_yaw[0] - previous[0], pose_xyz_yaw[1] - previous[1])
        if distance >= self.args.keyframe_distance_m:
            return True
        if self._accumulated_yaw_deg >= self.args.keyframe_yaw_deg:
            return True
        return (
            time.monotonic() - self._last_keyframe_host_s
            >= self.args.keyframe_max_seconds
        )

    # ---------------------------------------------------------- 采集线程
    def _capture_loop(self) -> None:
        poll = max(0.02, float(self.args.capture_poll_seconds))
        consecutive_rgbd_failures = 0
        while not self._stop.is_set():
            started = time.monotonic()
            # spin 里是 8 × 10ms 的有界批次，正常情况下远小于 poll 周期。
            with self._provider_lock:
                self._provider.spin_once()
            self._check_session_rotation()

            pose = self._provider.get_pose()
            quality = self._provider.quality()
            if pose is not None:
                pose_xyz_yaw = (float(pose.x), float(pose.y), float(pose.yaw))
                self._accumulated_yaw_deg += (
                    abs(math.degrees(_wrap_pi(pose_xyz_yaw[2] - self._last_yaw_sample)))
                    if self._last_yaw_sample is not None
                    else 0.0
                )
                self._last_yaw_sample = pose_xyz_yaw[2]
                linear, angular = self._current_speed(pose_xyz_yaw)
                if (
                    linear > self.args.max_linear_speed_mps
                    or angular > self.args.max_angular_speed_dps
                ):
                    self._bump("skipped_robot_moving_fast")
                    if not self._speed_warned:
                        self._speed_warned = True
                        self._log(
                            "WARNING: 运动速度超过识别门限 "
                            f"({linear:.2f} m/s, {angular:.1f} deg/s)，本帧跳过识别"
                            "（只跳过识别，不干涉运动）"
                        )
                    self._publish_status(**_status_counter_view(self))
                    self._sleep_remaining(started, poll)
                    continue
                self._publish_status(
                    quality=quality,
                    slam_ready=quality != "CAMERA_LOCAL",
                )
                if not self._should_capture(pose_xyz_yaw):
                    self._sleep_remaining(started, poll)
                    continue
            else:
                self._publish_status(quality=quality, slam_ready=False)
                self._sleep_remaining(started, poll)
                continue

            try:
                keyframe = self._capture_keyframe(pose_xyz_yaw)
                consecutive_rgbd_failures = 0
            except Exception as exc:  # noqa: BLE001 - 采帧失败不能拖垮侧车
                consecutive_rgbd_failures += 1
                if consecutive_rgbd_failures in (1, 10) or consecutive_rgbd_failures % 50 == 0:
                    self._log(f"WARNING: 采帧失败×{consecutive_rgbd_failures}: {exc}")
                self._fail(f"capture failed: {exc}")
                self._sleep_remaining(started, poll)
                continue
            if keyframe is not None:
                self._enqueue(keyframe)
            self._sleep_remaining(started, poll)

    def _sleep_remaining(self, started: float, poll: float) -> None:
        remaining = poll - (time.monotonic() - started)
        if remaining > 0:
            self._stop.wait(remaining)

    def _capture_keyframe(
        self, pose_xyz_yaw: tuple[float, float, float]
    ) -> Keyframe | None:
        """取一帧 RGB-D 并冻结成关键帧。失败抛异常，由调用方计数。"""
        frame = self._rgbd_source.get_latest(timeout_seconds=0.0)
        received_host_s = time.time()
        frame = self._rgbd_source.materialize(frame)

        # 时钟一致性门：拍摄时间戳只有在和宿主时钟对得上时才敢拿去查位姿。
        skew = float(frame.timestamp) - received_host_s
        if abs(skew) <= self.args.max_clock_skew_seconds:
            transform_timestamp = float(frame.timestamp)
            timestamp_source = "capture_timestamp"
        else:
            transform_timestamp = received_host_s
            timestamp_source = "receive_time_clock_skew"
            if self._status.get("clock_skew_seconds") is None:
                self._log(
                    "WARNING: D435 时间戳与宿主时钟相差 "
                    f"{skew:.1f}s（>{self.args.max_clock_skew_seconds}s），"
                    "改用接收时刻做位姿查找"
                )

        pose = self._provider.get_pose()
        robot_pose = {
            "x": round(pose_xyz_yaw[0], 4),
            "y": round(pose_xyz_yaw[1], 4),
            "yaw": round(pose_xyz_yaw[2], 4),
            "yaw_deg": round(math.degrees(pose_xyz_yaw[2]), 3),
            "source": getattr(pose, "source", None),
            "frame_id": getattr(pose, "frame_id", None),
        }
        keyframe = Keyframe(
            frame_id=str(frame.frame_id),
            capture_timestamp=received_host_s,
            transform_timestamp=transform_timestamp,
            timestamp_source=timestamp_source,
            clock_skew_seconds=round(skew, 3),
            rgbd_frame=frame,
            session_id=self._session_id,
            robot_pose=robot_pose,
            source_health=dict(self._rgbd_source.health() or {}),
        )

        self._last_keyframe_pose = pose_xyz_yaw
        self._accumulated_yaw_deg = 0.0
        self._last_keyframe_host_s = time.monotonic()
        self._bump("captured_keyframes")
        self._publish_status(
            last_frame_id=keyframe.frame_id,
            clock_skew_seconds=keyframe.clock_skew_seconds,
        )
        return keyframe

    # ---------------------------------------------------------- 有界队列
    def _enqueue(self, keyframe: Keyframe) -> None:
        limit = max(1, int(self.args.queue_size))
        with self._queue_lock:
            while len(self._queue) >= limit:
                self._evict_one_locked()
            self._queue.append(keyframe)
            depth = len(self._queue)
        self._publish_status(queue_depth=depth, **_status_counter_view(self))

    def _evict_one_locked(self) -> None:
        """队列满时优先删冗余帧，而不是无条件"只留最新帧"。

        「冗余」= 与相邻关键帧的归一化位姿差 < 1，也就是**它俩之间本来就不该
        各留一帧**（静止不动时每秒一帧的堆积就是这种）。删掉这一对里较旧的那
        张：新的那张已经站在几乎同一个位置，代表的是更近的状态。若 20 帧真的
        代表 20 个不同位置，才丢最旧帧并计入 dropped_keyframes。
        """
        if not self._queue:
            return
        best_index = -1
        best_score = None
        for index in range(len(self._queue) - 1):
            score = self._redundancy(self._queue[index], self._queue[index + 1])
            if best_score is None or score < best_score:
                best_score = score
                best_index = index
        if best_index >= 0 and best_score is not None and best_score < 1.0:
            del self._queue[best_index]
            self._bump("dropped_redundant_keyframes")
            return
        self._queue.popleft()
        self._bump("dropped_keyframes")

    def _redundancy(self, older: Keyframe, newer: Keyframe) -> float:
        a = older.pose_xyz_yaw()
        b = newer.pose_xyz_yaw()
        if a is None or b is None:
            # 没有位姿就没有判据 —— 当作**不冗余**，让调用方走"丢最旧帧"那条
            # 确定性的路，而不是凭一个猜出来的分数去删中间帧。
            return float("inf")
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        yaw_deg = abs(math.degrees(_wrap_pi(b[2] - a[2])))
        return (
            distance / max(1e-6, self.args.keyframe_distance_m)
            + yaw_deg / max(1e-6, self.args.keyframe_yaw_deg)
        )

    def _dequeue(self, timeout: float) -> Keyframe | None:
        deadline = time.monotonic() + timeout
        while not self._stop.is_set():
            with self._queue_lock:
                if self._queue:
                    keyframe = self._queue.popleft()
                    depth = len(self._queue)
                    break
            if time.monotonic() >= deadline:
                return None
            self._stop.wait(0.05)
        else:
            return None
        self._publish_status(queue_depth=depth)
        return keyframe

    # ---------------------------------------------------------- 推理线程
    def _inference_loop(self) -> None:
        while not self._stop.is_set():
            keyframe = self._dequeue(timeout=0.2)
            if keyframe is None:
                continue
            if keyframe.session_id != self._session_id:
                # 采帧之后 SLAM 换了会话：这张帧的位姿属于旧坐标系，不能落图。
                self._log(f"丢弃跨会话关键帧 {keyframe.frame_id}")
                continue
            try:
                self._process_keyframe(keyframe)
            except Exception as exc:  # noqa: BLE001 - 单帧失败不能停掉侧车
                self._bump("failed_keyframes")
                self._fail(f"inference failed on {keyframe.frame_id}: {exc}")
                self._publish_status(**_status_counter_view(self))

    def _process_keyframe(self, keyframe: Keyframe) -> None:
        from app.perception.depth_object_localizer import (
            enrich_scene_objects_with_spatial,
        )
        from app.spatial.dynamic_labels import drop_dynamic_objects
        from app.spatial.semantic_object_gate import filter_persistent_observations

        started = time.monotonic()
        self._publish_status(last_attempt_at=time.time(), state=STATE_RUNNING)

        frame = keyframe.rgbd_frame
        detections = self._detect(frame)

        scene_objects: list[dict[str, Any]] = []
        for index, obj in enumerate(detections, start=1):
            # detect_with_dynamic_terms 返回的是 DetectedObject **dataclass**，
            # `dict(obj)` 会抛 "'DetectedObject' object is not iterable" —— 必须是
            # asdict。参考实现 run_autonomous_loop._detect 之所以能写 dict(obj)，
            # 是因为它在上一层已经用 _gsam_to_dict 转过一次了。
            # 这里用 asdict 而不是照抄 _gsam_to_dict：后者只挑 9 个字段，会把
            # color / attributes / text_score 丢掉，而它们在 semantic_observer
            # 那条路上是真被读的；多带的键下游会忽略，少带的键查不出来。
            item = asdict(obj)
            # frame_object_id 与 id 同时写：逐帧事件是原始取证，而显示门的
            # raw_label 配对按 frame_object_id 查（见 semantic_object_gate）。
            item["id"] = f"gsam_obj_{index:03d}"
            item["frame_object_id"] = item["id"]
            if not item.get("category"):
                item["category"] = "object"
            scene_objects.append(item)

        localized = self._localizer.localize(scene_objects, frame)

        # §8.2：必须用**拍摄时刻**的位姿，绝不用"最新位姿"硬凑。变换失败
        # （map→odom 还没收到、位姿过期）就这一条不落图，而不是塞个近似值。
        with self._provider_lock:
            provenance = dict(self._provider.transform_provenance())
            map_projector = getattr(self._provider, "camera_point_to_map", None)
            localized_kept = []
            for obs_item in localized:
                if getattr(obs_item, "camera_xyz", None) is None:
                    continue
                if not callable(map_projector):
                    continue
                mapped = map_projector(obs_item.camera_xyz, keyframe.transform_timestamp)
                if mapped is None:
                    continue
                obs_item.map_xyz = mapped
                obs_item.box_center_map = obs_item.box_center_camera
                obs_item.box_size_map = obs_item.box_size_camera
                obs_item.spatial_quality = provenance.get("transform_quality")
                obs_item.provenance = {
                    **dict(obs_item.provenance or {}),
                    **provenance,
                    "map_frame": mapped_frame(provenance),
                    "timestamp": keyframe.transform_timestamp,
                    "capture_timestamp": keyframe.capture_timestamp,
                    "timestamp_source": keyframe.timestamp_source,
                    "clock_skew_seconds": keyframe.clock_skew_seconds,
                    "session_id": keyframe.session_id,
                }
                if _finite(mapped[0]) and _finite(mapped[1]) and _finite(mapped[2]):
                    localized_kept.append(obs_item)
            localized = localized_kept
            map_frame_value = mapped_frame(provenance)

        scene_objects = enrich_scene_objects_with_spatial(
            scene_objects, localized, image_timestamp=keyframe.capture_timestamp
        )

        # 动态物体（人/狗/猫）不写进持久地图；入口门挡低分碎片框。
        # 逐帧原始事件照旧一条不少 —— 它在这两道门之前就已经定稿。
        persistent = drop_dynamic_objects(localized)
        persistent, ingest_dropped = filter_persistent_observations(
            persistent,
            min_confidence=self._settings.semantic_map_min_object_confidence,
        )
        self._write_event({
            "event": "semantic_object_ingest_filter",
            "frame_id": keyframe.frame_id,
            "input": len(localized),
            "kept": len(persistent),
            "dropped": ingest_dropped,
            "min_confidence": self._settings.semantic_map_min_object_confidence,
            "host_s": self._host_s(),
        })

        update_result = self._semantic_map.update_with_associations(
            persistent,
            place_id=None,
            now=keyframe.capture_timestamp,
            frame_id=keyframe.frame_id,
        )

        association_by_index = {
            assoc.observation_index: assoc for assoc in update_result.associations
        }
        persistent_by_source_id = {
            str(assoc.source_object_id): assoc
            for assoc in update_result.associations
            if assoc.source_object_id
        }

        frame_objects: list[dict[str, Any]] = []
        for scene_object in scene_objects:
            event_item = dict(scene_object)
            assoc = persistent_by_source_id.get(
                str(event_item.get("source_object_id") or "")
            )
            if assoc is not None:
                event_item.update({
                    "persistent_object_id": assoc.persistent_object_id,
                    "association_score": assoc.association_score,
                    "association_action": assoc.action,
                })
            frame_objects.append(event_item)

        self._write_event({
            "event": "semantic_frame_objects_localized",
            "frame_id": keyframe.frame_id,
            "image_timestamp": keyframe.capture_timestamp,
            "timestamp_quality": frame.timestamp_quality,
            "transform_timestamp": keyframe.transform_timestamp,
            "timestamp_source": keyframe.timestamp_source,
            "robot_pose": keyframe.robot_pose,
            "map_frame": map_frame_value,
            "objects": frame_objects,
            "host_s": self._host_s(),
        })

        written_entities = 0
        for index, obs_item in enumerate(persistent):
            if getattr(obs_item, "map_xyz", None) is None:
                continue
            assoc = association_by_index.get(index)
            if assoc is None:
                # 本帧没建立可靠身份就不写实体事件 —— 绝不按 label 猜（两个
                # 同标签的物体必须保持各自的持久 ID）。
                continue
            entity = self._semantic_map.objects.get(assoc.persistent_object_id)
            event_object = entity.to_dict() if entity is not None else {}
            event_object.update({
                "object_id": assoc.persistent_object_id,
                "label": obs_item.label,
                "camera_xyz": (
                    list(obs_item.camera_xyz) if obs_item.camera_xyz else None
                ),
                "map_xyz": list(obs_item.map_xyz),
                "spatial_quality": obs_item.spatial_quality,
                "source_object_id": assoc.source_object_id,
                "association_score": assoc.association_score,
                "association_action": assoc.action,
                "map_frame": map_frame_value,
                "session_id": keyframe.session_id,
            })
            self._write_event({
                "event": "semantic_object_localized",
                "object": event_object,
                "host_s": self._host_s(),
            })
            written_entities += 1

        # §2.3：实体图刚更新完，把新版本投影到 WebUI 的显示对象快照。
        if update_result.created_ids or update_result.updated_ids:
            self._object_revision += 1
        self._publish_display_objects(self._object_revision)

        latency = time.monotonic() - started
        self._bump("processed_keyframes")
        # 成功的这一轮把连续失败计数**在内存里**也清零，不只是写进状态文件：
        # _fail() 是拿 self._status 里的旧值 +1 的，只改落盘值的话，下一次失败
        # 会接着很久以前的计数继续涨，degraded 会比实际早到。
        with self._status_lock:
            self._status["consecutive_failures"] = 0
        self._publish_status(
            state=STATE_RUNNING,
            last_success_at=time.time(),
            last_frame_id=keyframe.frame_id,
            last_detection_count=len(detections),
            last_localized_count=len(persistent),
            persistent_object_count=len(self._semantic_map.objects),
            inference_latency_seconds=round(latency, 3),
            map_frame=map_frame_value,
            last_error=None,
            **_status_counter_view(self, consecutive_failures=0),
        )
        self._log(
            f"{keyframe.frame_id}: 检测 {len(detections)} → 落图候选 {len(persistent)} "
            f"→ 实体事件 {written_entities}，实体总数 {len(self._semantic_map.objects)}，"
            f"耗时 {latency:.2f}s"
        )

    # ---------------------------------------------------------------- 检测
    def _ram_scene_terms(self, image_path: str) -> list[str]:
        settings = self._settings
        if not settings.enable_ram_plus:
            return []
        now = time.monotonic()
        interval = max(
            DEFAULT_RAM_SCENE_TAG_INTERVAL_S,
            float(getattr(settings, "ram_scene_tag_interval_seconds", 0.0) or 0.0),
        )
        if now - self._last_ram_tag_monotonic < interval:
            return self._cached_ram_terms
        try:
            self._cached_ram_terms = list(
                self._detector.discover_scene_terms(image_path)
            )
        except Exception as exc:  # noqa: BLE001 - RAM++ 失败不该终止识别
            self._log(f"WARNING: RAM++ 场景词获取失败，退到基础词表: {exc}")
            self._cached_ram_terms = []
        self._last_ram_tag_monotonic = now
        return self._cached_ram_terms

    def _detect(self, frame: Any) -> list[Any]:
        """开放词表的通用场景检测（本模式没有搜索目标）。

        常驻 GroundedSAM 只有一条 stdin/stdout 管道，加锁与仓库其它调用点
        保持同一约定，避免并发把管道写满。
        """
        with self._detect_lock:
            return self._detector.detect_with_dynamic_terms(
                frame.color_ref,
                "",
                [],
                self._ram_scene_terms(frame.color_ref),
            )

    # ---------------------------------------------------------------- 生命周期
    def run(self) -> int:
        self._inference_thread = threading.Thread(
            target=self._inference_loop, name="semantic-inference", daemon=True
        )
        self._inference_thread.start()
        self._publish_status(state=STATE_WAITING)
        self._capture_loop()
        self._inference_thread.join(timeout=5.0)
        return 0

    def run_once(self, timeout_seconds: float) -> int:
        """无运动烟雾检查：等一张有效 RGB-D + SLAM 位姿，处理一轮就退出。"""
        deadline = time.monotonic() + timeout_seconds
        pose_xyz_yaw = None
        while time.monotonic() < deadline and not self._stop.is_set():
            with self._provider_lock:
                self._provider.spin_once()
            pose = self._provider.get_pose()
            if pose is not None:
                pose_xyz_yaw = (float(pose.x), float(pose.y), float(pose.yaw))
                break
            self._stop.wait(0.2)
        if pose_xyz_yaw is None:
            self._fail(f"--once 超时：{timeout_seconds}s 内没有拿到 plain_slam 位姿")
            self._publish_status(**_status_counter_view(self))
            return 3

        # 位姿到手还不够：按 §8.2，落图要求 map→odom（只从 /tf 来）也已经在
        # bridge 里，否则 camera_point_to_map 一律返回 None，这一轮会"检测到了
        # 但一个都落不下来"。
        #
        # 正常模式下 TF 不是问题：采帧很快，但处理要等模型冷启动十几秒，这期间
        # 采集线程一直在 spin，TF 早到了。run_once 是同步的，所以这里显式等一次。
        # tf_ready 必须先在循环外初始化：等位姿那一步可能已经耗光 deadline，
        # 这个循环一次都不执行，直接落到下面的 if 上会 NameError。
        tf_ready = False
        tf_deadline = deadline
        while time.monotonic() < tf_deadline and not self._stop.is_set():
            with self._provider_lock:
                self._provider.spin_once()
                tf_ready = bool(
                    self._provider.transform_provenance().get("tf_available")
                )
            if tf_ready:
                break
            self._stop.wait(0.2)
        if not tf_ready:
            self._fail(
                f"--once 超时：{timeout_seconds}s 内没有收到 map→odom（/tf）。"
                "落图需要用拍摄时刻的 map 系位姿，拿不到就不落图 —— "
                "确认 plain_slam 的 slam_3d_node 在广播 pslam_map→pslam_odom。"
            )
            self._publish_status(**_status_counter_view(self))
            return 6

        self._publish_status(state=STATE_RUNNING, slam_ready=True)
        self._launch_log_size = self._read_launch_log_size()
        keyframe = self._capture_keyframe(pose_xyz_yaw)
        if keyframe is None:
            self._fail("--once 未能采到关键帧")
            return 4

        # 处理这一帧期间必须**有人继续 spin**，否则位姿在 pose_stale_s(3s) 之后就
        # 过期，PlainSlamSpatialProvider.camera_point_to_map 开头的
        # `if self.get_pose() is None: return None` 会直接短路 —— 连 bridge 都不进，
        # 于是"检测到了但一个都落不下来"，冒烟永远测不到 §10.2 的第三项判据。
        # 正常模式天然满足这个不变量（采集线程一直在 spin），run_once 是单线程，
        # 得自己把它补上。冷启动一轮推理十几秒，远大于 3 秒。
        #
        # 辅助线程只做 spin；_process_keyframe 里取 _provider_lock 的那一小段
        # （位姿快照，毫秒级）会被它短暂挡住，而十几秒的推理发生在取锁之前，
        # 所以不冲突。
        spin_stop = threading.Event()

        def _keep_spinning() -> None:
            while not spin_stop.is_set():
                with self._provider_lock:
                    self._provider.spin_once()
                spin_stop.wait(0.05)

        spinner = threading.Thread(target=_keep_spinning, name="once-spin", daemon=True)
        spinner.start()
        try:
            self._process_keyframe(keyframe)
        finally:
            spin_stop.set()
            spinner.join(timeout=2.0)
        status = self._status
        if status.get("last_error"):
            return 5
        self._log(
            f"--once 完成：{keyframe.frame_id} 检测 {status['last_detection_count']}，"
            f"落图 {status['last_localized_count']}，"
            f"实体总数 {status['persistent_object_count']}"
        )
        return 0

    def close(self) -> None:
        self._stop.set()
        self._publish_status(state=STATE_STOPPED, stopped_at=time.time())
        for name, closer in (
            ("detector", lambda: self._detector and self._detector.close()),
            ("provider", lambda: self._provider and self._provider.close()),
        ):
            try:
                closer()
            except Exception as exc:  # noqa: BLE001
                self._log(f"WARNING: 关闭 {name} 失败: {exc}")
        if self._events_handle is not None:
            try:
                self._events_handle.close()
            except OSError:
                pass
            self._events_handle = None


def mapped_frame(provenance: dict[str, Any]) -> str:
    return str(provenance.get("target_frame") or "unknown")


def _status_counter_view(
    mapper: RemoteControlSemanticMapper, **overrides: Any
) -> dict[str, Any]:
    """把计数器一次性推给状态文件，省得每个调用点各写一遍。

    ``overrides`` 是给调用点显式覆盖用的。没有它的话，调用点写
    ``_publish_status(consecutive_failures=0, **_status_counter_view(self))``
    会直接 TypeError（同一个关键字传了两次）—— 而那是在推理成功路径上，
    异常被推理循环兜住，于是"识别成功"被计成"识别失败"，一个物体都落不下来。
    走 overrides 之后调用点怎么写都不会撞。
    """
    with mapper._status_lock:
        keys = (
            "captured_keyframes",
            "dropped_keyframes",
            "dropped_redundant_keyframes",
            "processed_keyframes",
            "failed_keyframes",
            "consecutive_failures",
            "skipped_robot_moving_fast",
        )
        view = {key: mapper._status.get(key, 0) for key in keys}
    view.update(overrides)
    return view


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="实体遥控器建图的语义侧车（只读位姿，只写自己的事件与状态）"
    )
    parser.add_argument("--events", required=True, help="本次运行事件文件 (jsonl)")
    parser.add_argument("--status", required=True, help="当前状态 JSON")
    # 计划书 §2.3：本进程是遥控建图模式的三维对象生产者。它按地图桥写的
    # map_epoch 声明自己属于哪张地图，写到同一套显式路径上，Web 端才可能配对。
    parser.add_argument(
        "--map-meta-path",
        default="outputs/autonomous_search/runtime/slam_map_meta.json",
        help="point-cloud-free map metadata written by the map bridge",
    )
    parser.add_argument(
        "--object-snapshot-path",
        default="outputs/autonomous_search/runtime/semantic_objects.json",
        help="where this process publishes its display objects",
    )
    parser.add_argument("--capture-poll-seconds", type=float, default=0.2)
    parser.add_argument("--keyframe-max-seconds", type=float, default=1.0)
    parser.add_argument("--keyframe-distance-m", type=float, default=0.75)
    parser.add_argument("--keyframe-yaw-deg", type=float, default=15.0)
    parser.add_argument("--queue-size", type=int, default=20)
    parser.add_argument("--rgbd-base-url", default="http://192.168.123.18:8080")
    parser.add_argument("--max-clock-skew-seconds", type=float, default=DEFAULT_MAX_CLOCK_SKEW_S)
    parser.add_argument(
        "--max-linear-speed-mps", type=float, default=DEFAULT_MAX_LINEAR_SPEED_MPS
    )
    parser.add_argument(
        "--max-angular-speed-dps", type=float, default=DEFAULT_MAX_ANGULAR_SPEED_DPS
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只跑一轮（供无运动烟雾检查）；配合 --once-timeout-seconds",
    )
    parser.add_argument("--once-timeout-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mapper = RemoteControlSemanticMapper(args)

    def _handle_signal(signum: int, _frame: Any) -> None:
        mapper._log(f"收到信号 {signum}，准备退出")
        mapper._stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    exit_code = 0
    try:
        mapper.setup()
        if args.once:
            exit_code = mapper.run_once(args.once_timeout_seconds)
        else:
            exit_code = mapper.run()
    except Exception as exc:  # noqa: BLE001 - 把失败写进状态文件再退
        mapper._fail(f"fatal: {exc}")
        mapper._publish_status(state=STATE_DEGRADED)
        exit_code = 1
    finally:
        mapper.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
