"""计划书 §6.1：自主搜索的 Nav2 后端。

直接实现 ``app.navigation.robot_backend.RobotBackend`` 需要的最小接口，不新增抽象
层。它只接受三种动作：

* ``NAVIGATE_POSE``：先 ``ComputePathToPose``，路径非空才做走廊分析，分析通过才
  ``NavigateToPose``；
* ``STOP``：取消当前目标并让控制链输出停止；
* 取消：``cancel()``。

``RELATIVE_MOVE`` 一律明确拒绝，绝不翻译成底层前进命令。

也不搬 ``scripts/nav2_bridge_worker.py`` 的文件 IPC：本后端通过 ROS 2 Action
直接连 Nav2，搜索进程里本来就有 node 和 TF buffer。

测试用的注入缝
--------------

后端只通过一个很小的端口跟 Action 打交道（``send`` / ``poll`` / ``cancel`` /
``server_ready``）。真实实现是 ``create_nav2_action_clients()`` 返回的 ROS 2
ActionClient 包装，测试用 ``tests/test_nav2_search_backend.py`` 里的假服务器。
这是**一个端口 + 一个真实实现**，不是可插拔后端注册表。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from .corridor_analyzer import (
    DECISION_OK,
    OCCUPIED_THRESHOLD,
    CorridorDecision,
    CorridorGrid,
    classify_path_clearance,
    measure_path_clearance,
)
from .models import ExplorationGoal
from .nav2_path_utils import compute_path_length
from .robot_backend import (
    TERMINAL_NAVIGATION_STATUSES,
    BackendHealth,
    NavigationHandle,
    NavigationResult,
    NavigationStatus,
    PoseQuality,
    RobotCapabilities,
    RobotPose,
    navigation_result,
)

BACKEND_NAME = "nav2"

# 计划书 §6.1：只接受这三种动作类型（CANCEL 走 cancel()，不占 goal_type）。
SUPPORTED_GOAL_TYPES = ("NAVIGATE_POSE", "STOP")

# 计划书 §14 固定的候选级拒绝原因。
REJECT_NO_MAP = "NO_MAP"
REJECT_NO_LOCALIZATION = "NO_LOCALIZATION"
REJECT_NO_PATH = "NO_PATH"
REJECT_GOAL_OCCUPIED = "GOAL_OCCUPIED"
REJECT_TOO_NARROW = "TOO_NARROW"
REJECT_NAV2_NOT_READY = "NAV2_NOT_READY"
# 调用方违反了「Nav2 模式下目标必须是 map 系」的契约，不属于候选判定。
REJECT_NOT_MAP_FRAME = "NOT_MAP_FRAME"

# §14 允许写进候选记录的那六个。收口用它，别处不要再列一遍。
CANDIDATE_REJECT_REASONS = (
    REJECT_NO_MAP,
    REJECT_NO_LOCALIZATION,
    REJECT_NO_PATH,
    REJECT_GOAL_OCCUPIED,
    REJECT_TOO_NARROW,
    REJECT_NAV2_NOT_READY,
)

MAP_FRAME = "map"

# action_msgs/msg/GoalStatus 的常量，这里用字符串以便离线断言。
ACTION_UNKNOWN = "UNKNOWN"
ACTION_ACCEPTED = "ACCEPTED"
ACTION_EXECUTING = "EXECUTING"
ACTION_CANCELING = "CANCELING"
ACTION_SUCCEEDED = "SUCCEEDED"
ACTION_CANCELED = "CANCELED"
ACTION_ABORTED = "ABORTED"

_GOAL_STATUS_CODES = {
    0: ACTION_UNKNOWN,
    1: ACTION_ACCEPTED,
    2: ACTION_EXECUTING,
    3: ACTION_CANCELING,
    4: ACTION_SUCCEEDED,
    5: ACTION_CANCELED,
    6: ACTION_ABORTED,
}

_PHASE_PLANNING = "PLANNING"
_PHASE_NAVIGATING = "NAVIGATING"


@dataclass(frozen=True)
class Nav2GoalRequest:
    """发往 Nav2 的最小目标描述；``x``/``y`` 为米，``yaw`` 为弧度。"""

    x: float
    y: float
    yaw: float
    frame_id: str = MAP_FRAME

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "yaw": round(self.yaw, 6),
        }


@dataclass(frozen=True)
class ActionPoll:
    """一次 ActionClient 轮询的结果。"""

    status: str = ACTION_UNKNOWN
    path: tuple[tuple[float, float], ...] = ()
    error: str = ""

    @classmethod
    def from_goal_status_code(
        cls, code: int, *, path: Sequence[Any] = (), error: str = ""
    ) -> "ActionPoll":
        return cls(
            status=_GOAL_STATUS_CODES.get(int(code), ACTION_UNKNOWN),
            path=tuple((float(item[0]), float(item[1])) for item in path),
            error=error,
        )


class ActionClientPort(Protocol):
    """后端需要的全部 Action 能力。"""

    def server_ready(self) -> bool: ...

    def send(self, request: Nav2GoalRequest) -> None: ...

    def poll(self) -> ActionPoll: ...

    def cancel(self) -> bool: ...


class RosActionClientPort:
    """``ActionClientPort`` 的唯一真实实现：包一层 rclpy 的 ActionClient。

    rclpy 只在构造与发送时导入，模块顶层保持无 ROS 依赖，离线测试才能直接
    import 这个文件。``poll()`` 读的是回调里记下的状态，因此**必须有人持续
    spin 这个 node**（搜索进程的循环本来就在 spin），否则状态永远停在
    ``UNKNOWN``，最终由后端的规划超时兜底。
    """

    def __init__(
        self,
        node: Any,
        action_name: str,
        action_type: Any,
        *,
        server_wait_s: float = 0.1,
        goal_timeout_s: float = 5.0,
    ) -> None:
        import rclpy.action  # noqa: PLC0415

        self._node = node
        self._action_name = action_name
        self._action_type = action_type
        self._client = rclpy.action.ActionClient(node, action_type, action_name)
        self._server_wait_s = float(server_wait_s)
        self._goal_timeout_s = float(goal_timeout_s)
        self._goal_handle: Any = None
        self._status = ACTION_UNKNOWN
        self._path: tuple[tuple[float, float], ...] = ()
        self._error = ""

    def server_ready(self) -> bool:
        if self._client.server_is_ready():
            return True
        # 只在没就绪时才等一小会儿，避免每次健康检查都卡住搜索循环。
        return bool(self._client.wait_for_server(timeout_sec=self._server_wait_s))

    def send(self, request: Nav2GoalRequest) -> None:
        from geometry_msgs.msg import PoseStamped  # noqa: PLC0415

        goal = self._action_type.Goal()
        pose = PoseStamped()
        pose.header.frame_id = request.frame_id
        pose.header.stamp = self._node.get_clock().now().to_msg()
        pose.pose.position.x = float(request.x)
        pose.pose.position.y = float(request.y)
        pose.pose.orientation.z = math.sin(request.yaw / 2.0)
        pose.pose.orientation.w = math.cos(request.yaw / 2.0)
        _attach_pose(goal, pose)

        self._goal_handle = None
        self._status = ACTION_UNKNOWN
        self._path = ()
        self._error = ""
        future = self._client.send_goal_async(goal)
        future.add_done_callback(self._on_goal_response)

    def poll(self) -> ActionPoll:
        return ActionPoll(status=self._status, path=self._path, error=self._error)

    def cancel(self) -> bool:
        if self._goal_handle is None:
            return False
        self._goal_handle.cancel_goal_async()
        return True

    # ------------------------------------------------------------ callbacks
    def _on_goal_response(self, future: Any) -> None:
        try:
            self._goal_handle = future.result()
        except Exception as error:  # noqa: BLE001 - Action 层的异常不能让搜索循环崩
            self._status = ACTION_ABORTED
            self._error = f"{self._action_name}: {error}"
            return
        if self._goal_handle is None or not self._goal_handle.accepted:
            self._status = ACTION_ABORTED
            self._error = f"{self._action_name}: goal rejected by the server"
            return
        self._status = ACTION_ACCEPTED
        result_future = self._goal_handle.get_result_async()
        result_future.add_done_callback(self._on_result)

    def _on_result(self, future: Any) -> None:
        try:
            response = future.result()
        except Exception as error:  # noqa: BLE001
            self._status = ACTION_ABORTED
            self._error = f"{self._action_name}: {error}"
            return
        self._status = _GOAL_STATUS_CODES.get(int(response.status), ACTION_UNKNOWN)
        self._path = _extract_path(getattr(response, "result", None))


def _attach_pose(goal: Any, pose: Any) -> None:
    """ComputePathToPose 与 NavigateToPose 的目标字段名不同，这里各自适配。"""
    if hasattr(goal, "goal"):
        goal.goal = pose
    else:
        goal.pose = pose


def _extract_path(result: Any) -> tuple[tuple[float, float], ...]:
    path = getattr(result, "path", None)
    if path is None:
        return ()
    poses = getattr(path, "poses", None) or ()
    return tuple(
        (float(item.pose.position.x), float(item.pose.position.y)) for item in poses
    )


def create_nav2_action_clients(node: Any) -> tuple[RosActionClientPort, RosActionClientPort]:
    """用搜索进程已有的 node 建出 ComputePathToPose / NavigateToPose 两个客户端。"""
    from nav2_msgs.action import ComputePathToPose, NavigateToPose  # noqa: PLC0415

    return (
        RosActionClientPort(node, "compute_path_to_pose", ComputePathToPose),
        RosActionClientPort(node, "navigate_to_pose", NavigateToPose),
    )


def create_nav2_search_backend(
    node: Any,
    *,
    tf_buffer: Any = None,
    map_topic: str = "/map",
    base_frame: str = "base_link",
    stop_topic: str = "/go2w/search_cmd_vel",
    controller_node: str = "controller_server",
    localization_valid_topic: str = "/go2w/nav/localization_valid",
    tf_lookup_timeout_s: float = 0.2,
) -> Nav2SearchBackend:
    """把 Nav2 后端接到真实的 ROS 图上。

    各外部输入都是**读到就用、读不到就判不通过**，不缓存、不沿用上一次的值：
    地图、TF、``localization_valid`` 任何一项缺失都会让 ``execute_goal`` 返回
    ``REJECTED``（计划书 §3.2 / §14）。
    """
    # 延迟导入：模块顶层必须保持无 ROS 依赖。
    from geometry_msgs.msg import Twist  # noqa: PLC0415
    from nav_msgs.msg import OccupancyGrid  # noqa: PLC0415
    from rcl_interfaces.msg import Parameter as ParameterMsg  # noqa: PLC0415
    from rcl_interfaces.msg import ParameterType, ParameterValue  # noqa: PLC0415
    from rcl_interfaces.srv import SetParameters  # noqa: PLC0415
    from rclpy.qos import (  # noqa: PLC0415
        DurabilityPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )
    from std_msgs.msg import Bool  # noqa: PLC0415
    from tf2_ros import Buffer, TransformListener  # noqa: PLC0415

    if tf_buffer is None:
        tf_buffer = Buffer()
        TransformListener(tf_buffer, node)

    state: dict[str, Any] = {"grid": None, "localization_valid": False}

    def _on_map(message: Any) -> None:
        info = message.info
        state["grid"] = CorridorGrid(
            resolution=float(info.resolution),
            origin_x=float(info.origin.position.x),
            origin_y=float(info.origin.position.y),
            width=int(info.width),
            height=int(info.height),
            data=tuple(int(value) for value in message.data),
        )

    def _on_localization(message: Any) -> None:
        state["localization_valid"] = bool(message.data)

    latched = QoSProfile(
        depth=1,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )
    node.create_subscription(OccupancyGrid, map_topic, _on_map, latched)
    node.create_subscription(Bool, localization_valid_topic, _on_localization, 10)

    stop_publisher = node.create_publisher(Twist, stop_topic, 10)
    parameter_client = node.create_client(
        SetParameters, f"/{controller_node}/set_parameters"
    )

    def _speed_limit_setter(speed_limit_mps: float) -> bool:
        """把这一段的速度上限写进 RPP 的 desired_linear_vel（计划书 §6.4）。

        只在每段新路径开始时调一次，不每帧改。服务不可用就返回 False，让上层
        知道这一段的限速没生效——不静默吞掉。
        """
        if not parameter_client.service_is_ready():
            return False
        request = SetParameters.Request()
        request.parameters = [
            ParameterMsg(
                name="FollowPath.desired_linear_vel",
                value=ParameterValue(
                    type=ParameterType.PARAMETER_DOUBLE,
                    double_value=float(speed_limit_mps),
                ),
            )
        ]
        parameter_client.call_async(request)
        return True

    def _pose_provider() -> RobotPose | None:
        try:
            transform = tf_buffer.lookup_transform(
                MAP_FRAME, base_frame, node.get_clock().now(), timeout=tf_lookup_timeout_s
            )
        except Exception:  # noqa: BLE001 - TF 没到就是没位姿，不编造
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return RobotPose(
            x=float(translation.x),
            y=float(translation.y),
            yaw=yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w),
            frame_id=MAP_FRAME,
            quality=PoseQuality.METRIC,
            timestamp=node.get_clock().now().nanoseconds / 1e9,
            source="tf:map->base_link",
        )

    path_client, navigate_client = create_nav2_action_clients(node)
    return Nav2SearchBackend(
        path_client=path_client,
        navigate_client=navigate_client,
        occupancy_grid_provider=lambda: state["grid"],
        localization_valid_provider=lambda: bool(state["localization_valid"]),
        pose_provider=_pose_provider,
        speed_limit_setter=_speed_limit_setter,
        stop_publisher=lambda: stop_publisher.publish(Twist()),
        # 单调钟：node 的时钟可能被仿真时间或外部对时改动，超时不能跟着跳。
        now=time.monotonic,
    )


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """把四元数压成绕 Z 的偏航角。"""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


@dataclass
class _ActiveGoal:
    goal: ExplorationGoal
    handle: NavigationHandle
    phase: str
    request: Nav2GoalRequest
    started_at: float
    plan_sent_at: float
    path_length_m: float = 0.0
    speed_limit_mps: float = 0.0
    corridor: CorridorDecision | None = None
    reject_reason: str = ""


class Nav2SearchBackend:
    """计划书 §6.1 的 Nav2 后端。"""

    def __init__(
        self,
        *,
        path_client: ActionClientPort,
        navigate_client: ActionClientPort,
        occupancy_grid_provider: Callable[[], CorridorGrid | None] | None = None,
        localization_valid_provider: Callable[[], bool] | None = None,
        pose_provider: Callable[[], RobotPose | None] | None = None,
        speed_limit_setter: Callable[[float], bool] | None = None,
        stop_publisher: Callable[[], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        plan_timeout_s: float = 10.0,
    ) -> None:
        self._path_client = path_client
        self._navigate_client = navigate_client
        self._grid_provider = occupancy_grid_provider
        self._localization_valid = localization_valid_provider or (lambda: True)
        self._pose_provider = pose_provider
        self._speed_limit_setter = speed_limit_setter
        self._stop_publisher = stop_publisher
        self._now = now
        self._plan_timeout_s = float(plan_timeout_s)
        self._active: _ActiveGoal | None = None
        self._last_decision: dict[str, Any] | None = None

    @property
    def occupancy_grid(self) -> CorridorGrid | None:
        """后端订阅到的最新占据栅格（计划书 §7.5）。

        候选排序要沿候选路径量左右机身净空，那份数据只有这里持有；没有地图时
        返回 ``None``，调用方按「这条证据为空」处理，不编造。
        """
        return self._grid_provider() if self._grid_provider is not None else None

    # ------------------------------------------------------------ RobotBackend
    def capabilities(self) -> RobotCapabilities:
        """支撑能力只暴露地图系导航：相对平移/旋转一律不声明。

        候选生成器按这里的能力表过滤，所以「不再产生相对移动」是靠这张表落地的，
        而不是靠在下游到处加 if。
        """
        return RobotCapabilities(
            supports_global_pose=True,
            supports_metric_navigation=True,
            supports_relative_translation=False,
            supports_relative_rotation=False,
            supports_heading_control=False,
            supports_navigation_cancel=True,
            supports_navigation_feedback=True,
            supports_platform_obstacle_avoidance=True,
            emergency_collision_stop_supported=True,
            clearance_aware_navigation_supported=True,
            autonomous_obstacle_bypass_supported=False,
            allowed_motion_primitives=SUPPORTED_GOAL_TYPES,
        )

    def get_pose(self) -> RobotPose | None:
        if self._pose_provider is None:
            return None
        return self._pose_provider()

    def execute_goal(self, goal: ExplorationGoal) -> NavigationHandle:
        goal_type = str(goal.goal_type or "").strip().upper()
        if goal_type == "STOP":
            self._finish_canceled(goal, "STOP goal: canceled the active Nav2 goal")
            self.stop()
            return NavigationHandle(
                goal_id=goal.goal_id,
                result=self._result(
                    goal.goal_id,
                    NavigationStatus.SUCCEEDED,
                    message="nav2 stop issued",
                    provenance={"backend": BACKEND_NAME, "stop": True},
                ),
            )
        if goal_type not in SUPPORTED_GOAL_TYPES:
            # 计划书 §6.1：RELATIVE_MOVE 必须明确返回不支持。
            return NavigationHandle(
                goal_id=goal.goal_id,
                result=self._result(
                    goal.goal_id,
                    NavigationStatus.REJECTED,
                    message=(
                        f"nav2 backend does not support {goal_type or 'unknown'}; "
                        f"only {', '.join(SUPPORTED_GOAL_TYPES)} are accepted"
                    ),
                    provenance={"backend": BACKEND_NAME, "reject_reason": "UNSUPPORTED_GOAL_TYPE"},
                ),
            )
        if self._active is not None:
            return NavigationHandle(
                goal_id=goal.goal_id,
                result=self._result(
                    goal.goal_id,
                    NavigationStatus.REJECTED,
                    message="nav2 backend already has an active goal",
                    provenance={"backend": BACKEND_NAME, "reject_reason": "GOAL_IN_FLIGHT"},
                ),
            )

        ready, reason = self._readiness()
        if not ready:
            return NavigationHandle(
                goal_id=goal.goal_id,
                result=self._rejected(goal, reason, f"nav2 not usable: {reason}"),
            )

        request, error = _request_from_goal(goal)
        if request is None:
            return NavigationHandle(
                goal_id=goal.goal_id,
                result=self._result(
                    goal.goal_id,
                    NavigationStatus.REJECTED,
                    message=error,
                    provenance={
                        "backend": BACKEND_NAME,
                        "reject_reason": REJECT_NOT_MAP_FRAME
                        if "frame" in error
                        else "INVALID_GOAL",
                    },
                ),
            )

        # 计划书 §3.2：目标点本身落在占据栅格里就直接拒，别指望规划器兜底。
        # NavFn 对占据终点通常返回空路径，结果是同一个「不发 NavigateToPose」，
        # 但拒因会被记成 NO_PATH —— 现场看不出是「过不去」还是「目标就在墙里」，
        # 而 GOAL_OCCUPIED 这个枚举此前没有任何产生点（§14 只允许这六个拒因）。
        # 未知栅格不在这里拦：拦住会把正常的 frontier 目标一起误杀，交给
        # costmap 的 allow_unknown=false 处理。
        grid = self._grid_provider() if self._grid_provider is not None else None
        if grid is not None:
            cell = grid.value_at(request.x, request.y)
            if cell is not None and cell >= OCCUPIED_THRESHOLD:
                return NavigationHandle(
                    goal_id=goal.goal_id,
                    result=self._rejected(
                        goal,
                        REJECT_GOAL_OCCUPIED,
                        f"goal ({request.x:.2f}, {request.y:.2f}) is in an occupied cell "
                        f"(cost={cell})",
                    ),
                )

        handle = NavigationHandle(goal_id=goal.goal_id)
        started = float(self._now())
        self._active = _ActiveGoal(
            goal=goal,
            handle=handle,
            phase=_PHASE_PLANNING,
            request=request,
            started_at=started,
            plan_sent_at=started,
        )
        self._path_client.send(request)
        return handle

    def get_navigation_status(self, handle: NavigationHandle) -> NavigationResult:
        if handle.result is not None:
            return handle.result
        active = self._active
        if active is None or active.handle is not handle:
            return self._result(
                handle.goal_id,
                NavigationStatus.FAILED,
                message="unknown nav2 handle",
                provenance={"backend": BACKEND_NAME},
            )
        if active.phase == _PHASE_PLANNING:
            result = self._advance_planning(active)
        else:
            result = self._advance_navigating(active)
        if result is not None:
            handle.result = result
            self._active = None
            self._last_decision = result.provenance.get("candidate_decision")
            return result
        return self._result(
            handle.goal_id,
            NavigationStatus.RUNNING,
            message=f"nav2 {active.phase.lower()}",
            provenance={"backend": BACKEND_NAME},
        )

    def cancel(self, handle: NavigationHandle | None = None) -> bool:
        canceled = False
        try:
            if self._path_client.cancel():
                canceled = True
        except Exception:  # noqa: BLE001 - 取消失败也必须继续往下取消导航
            canceled = False
        try:
            if self._navigate_client.cancel():
                canceled = True
        except Exception:  # noqa: BLE001
            canceled = False
        active = self._active
        if active is not None and (handle is None or active.handle is handle):
            active.handle.result = self._result(
                active.goal.goal_id,
                NavigationStatus.CANCELLED,
                message="nav2 goal canceled",
                provenance=self._decision_provenance(active, REJECT_NAV2_NOT_READY, False)
                | {"backend": BACKEND_NAME, "canceled": True},
            )
        return canceled

    def stop(self) -> None:
        """取消目标并走已有控制链输出停止。"""
        self.cancel()
        if self._stop_publisher is not None:
            self._stop_publisher()

    def health(self) -> BackendHealth:
        ready, reason = self._readiness()
        degraded = [] if ready else [reason]
        return BackendHealth(
            ready=ready,
            backend=BACKEND_NAME,
            degraded=degraded,
            capabilities=self.capabilities(),
            pose_quality=PoseQuality.METRIC if ready else PoseQuality.UNAVAILABLE,
            details={
                "reject_reason": reason,
                "localization_valid": self._localization_valid(),
                "last_candidate_decision": self._last_decision,
            },
        )

    # ------------------------------------------------------------ internals
    def _readiness(self) -> tuple[bool, str]:
        if not self._path_client.server_ready() or not self._navigate_client.server_ready():
            return False, REJECT_NAV2_NOT_READY
        if not self._localization_valid():
            return False, REJECT_NO_LOCALIZATION
        if self._grid_provider is None or self._grid_provider() is None:
            return False, REJECT_NO_MAP
        return True, ""

    def _advance_planning(self, active: _ActiveGoal) -> NavigationResult | None:
        poll = self._path_client.poll()
        if poll.status in (ACTION_UNKNOWN, ACTION_ACCEPTED, ACTION_EXECUTING, ACTION_CANCELING):
            if float(self._now()) - active.plan_sent_at > self._plan_timeout_s:
                return self._rejected(active.goal, REJECT_NO_PATH, "compute path timeout")
            return None
        if poll.status == ACTION_CANCELED:
            return self._result(
                active.goal.goal_id,
                NavigationStatus.CANCELLED,
                message="compute path canceled",
                provenance={"backend": BACKEND_NAME},
            )
        if poll.status != ACTION_SUCCEEDED:
            return self._rejected(
                active.goal, REJECT_NO_PATH, f"compute path {poll.status.lower()}"
            )
        path = list(poll.path)
        active.path_length_m = _path_length_m(path) if path else 0.0
        if not path:
            return self._rejected(active.goal, REJECT_NO_PATH, "nav2 returned an empty path")

        grid = self._grid_provider() if self._grid_provider is not None else None
        if grid is None:
            return self._rejected(active.goal, REJECT_NO_MAP, "map unavailable")
        decision = classify_path_clearance(measure_path_clearance(path, grid))
        active.corridor = decision
        active.speed_limit_mps = decision.speed_limit_mps
        if decision.decision != DECISION_OK:
            # 计划书 §6.1 / §12.2：不安全就不发 NavigateToPose。
            # 走廊分析器内部还有更细的原因串（例如 NO_PATH_SAMPLES：路径一个
            # 采样点都取不到）。§14 把候选记录的 reject_reason 限定在固定枚举
            # 里，所以这里做一次收口；原始串不丢，它就在 message 里。
            return self._rejected(
                active.goal,
                _candidate_reject_reason(decision.reject_reason),
                f"corridor rejected the path: {decision.reject_reason}",
            )

        if self._speed_limit_setter is not None and not self._speed_limit_setter(
            decision.speed_limit_mps
        ):
            # 计划书 §7.4：限速只有「在导航目标开始前写 RPP 的
            # desired_linear_vel」这一条通路。写不进去（例如控制器没起来，
            # set_parameters 服务不在）就不能照发 NavigateToPose——那样走廊
            # 算出来的 0.05~0.10 只是纸面数字，控制器会按 YAML 的 0.10 跑。
            # §3.2：Nav2 失败就停下重选，不静默降级。
            return self._rejected(
                active.goal,
                REJECT_NAV2_NOT_READY,
                f"corridor speed limit {decision.speed_limit_mps:.3f} m/s could not be "
                "applied to controller_server",
            )
        active.phase = _PHASE_NAVIGATING
        self._navigate_client.send(active.request)
        return None

    def _advance_navigating(self, active: _ActiveGoal) -> NavigationResult | None:
        poll = self._navigate_client.poll()
        if poll.status in (ACTION_UNKNOWN, ACTION_ACCEPTED, ACTION_EXECUTING, ACTION_CANCELING):
            return None
        mapping = {
            ACTION_SUCCEEDED: NavigationStatus.SUCCEEDED,
            ACTION_CANCELED: NavigationStatus.CANCELLED,
            ACTION_ABORTED: NavigationStatus.FAILED,
        }
        status = mapping.get(poll.status, NavigationStatus.FAILED)
        message = poll.error or f"navigate to pose {poll.status.lower()}"
        return self._result(
            active.goal.goal_id,
            status,
            message=message,
            requested_motion=_requested_motion(active),
            observed_motion=self._observed_motion(active, status),
            elapsed_sec=float(self._now()) - active.started_at,
            provenance=self._decision_provenance(
                active, "" if status == NavigationStatus.SUCCEEDED else REJECT_NO_PATH,
                status == NavigationStatus.SUCCEEDED,
            ),
        )

    def _rejected(
        self, goal: ExplorationGoal, reason: str, message: str
    ) -> NavigationResult:
        active = self._active
        if active is not None and active.handle.goal_id == goal.goal_id:
            active.reject_reason = reason
            provenance = self._decision_provenance(active, reason, False)
        else:
            provenance = {"backend": BACKEND_NAME}
        # 顶层也放一份：候选记录里的原因在 candidate_decision 下，但读取这条
        # 结果的日志与闸门只关心「为什么被拒」，不该每处都往里钻一层。
        provenance["reject_reason"] = reason
        return self._result(
            goal.goal_id,
            NavigationStatus.REJECTED,
            message=message,
            provenance=provenance,
        )

    def _decision_provenance(
        self, active: _ActiveGoal, reject_reason: str, accepted: bool
    ) -> dict[str, Any]:
        """计划书 §14 要求的每个候选一行结构化记录。"""
        decision = active.corridor
        goal = active.goal
        request = active.request
        return {
            "backend": BACKEND_NAME,
            "candidate_decision": {
                "candidate_id": goal.goal_id,
                "goal_x": round(request.x, 3),
                "goal_y": round(request.y, 3),
                "goal_yaw": round(math.degrees(request.yaw), 3),
                "path_length_m": round(active.path_length_m, 3),
                "min_left_body_gap_m": _round_or_none(
                    decision.min_left_body_gap_m if decision else None
                ),
                "min_right_body_gap_m": _round_or_none(
                    decision.min_right_body_gap_m if decision else None
                ),
                "min_side_gap_m": _round_or_none(
                    decision.min_side_gap_m if decision else None
                ),
                "corridor_decision": decision.decision if decision else "",
                "speed_limit_mps": round(active.speed_limit_mps, 4),
                "center_score": round(decision.center_score, 4) if decision else 0.0,
                "accepted": bool(accepted),
                "reject_reason": reject_reason,
            },
        }

    def _observed_motion(
        self, active: _ActiveGoal, status: NavigationStatus
    ) -> dict[str, Any]:
        return {
            "path_length_m": round(active.path_length_m, 3),
            "speed_limit_mps": round(active.speed_limit_mps, 4),
            "goal_x": round(active.request.x, 3),
            "goal_y": round(active.request.y, 3),
            "goal_yaw_deg": round(math.degrees(active.request.yaw), 3),
            "status": status.value,
        }

    def _finish_canceled(self, goal: ExplorationGoal, message: str) -> None:
        active = self._active
        if active is None:
            return
        active.handle.result = self._result(
            active.goal.goal_id,
            NavigationStatus.CANCELLED,
            message=message,
            provenance={"backend": BACKEND_NAME},
        )
        self._active = None

    def _result(
        self,
        goal_id: str,
        status: NavigationStatus,
        *,
        message: str = "",
        requested_motion: dict[str, Any] | None = None,
        observed_motion: dict[str, Any] | None = None,
        elapsed_sec: float | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> NavigationResult:
        return navigation_result(
            goal_id,
            status,
            message=message,
            requested_motion=requested_motion,
            observed_motion=observed_motion,
            elapsed_sec=elapsed_sec,
            provenance=provenance or {"backend": BACKEND_NAME},
        )


def _candidate_reject_reason(reason: str) -> str:
    """把上游更细的原因串收口到 §14 的固定枚举。

    枚举外的一律记成 ``TOO_NARROW``：走到这里的都是「路径不能安全通过」这一个
    失败类，而走廊分析对取不到采样点的路径本来就是 fail-closed 判窄的。
    """
    return reason if reason in CANDIDATE_REJECT_REASONS else REJECT_TOO_NARROW


def _path_length_m(path: Sequence[tuple[float, float]]) -> float:
    """端口里的路径是 ``(x, y)`` 元组，而长度工具吃 dict / 带属性的对象。"""
    return compute_path_length([{"x": x, "y": y} for x, y in path])


def _request_from_goal(goal: ExplorationGoal) -> tuple[Nav2GoalRequest | None, str]:
    if goal.position is None:
        return None, "NAVIGATE_POSE requires a position"
    frame = str(goal.frame or MAP_FRAME).strip().lower()
    if frame not in (MAP_FRAME, "map_frame"):
        return None, f"nav2 backend only accepts map-frame goals, got frame={goal.frame!r}"
    x = float(goal.position[0])
    y = float(goal.position[1])
    yaw = math.radians(float(goal.yaw or 0.0))
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(yaw)):
        return None, "NAVIGATE_POSE goal is not finite"
    return Nav2GoalRequest(x=x, y=y, yaw=yaw), ""


def _requested_motion(active: _ActiveGoal) -> dict[str, Any]:
    return {
        "goal_type": active.goal.goal_type,
        "frame": MAP_FRAME,
        "position": [round(active.request.x, 3), round(active.request.y, 3)],
        "yaw_deg": round(math.degrees(active.request.yaw), 3),
    }


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


__all__ = [
    "ACTION_ABORTED",
    "ACTION_ACCEPTED",
    "ACTION_CANCELED",
    "ACTION_CANCELING",
    "ACTION_EXECUTING",
    "ACTION_SUCCEEDED",
    "ACTION_UNKNOWN",
    "ActionClientPort",
    "ActionPoll",
    "BACKEND_NAME",
    "CANDIDATE_REJECT_REASONS",
    "MAP_FRAME",
    "Nav2GoalRequest",
    "Nav2SearchBackend",
    "REJECT_GOAL_OCCUPIED",
    "REJECT_NAV2_NOT_READY",
    "REJECT_NOT_MAP_FRAME",
    "REJECT_NO_LOCALIZATION",
    "REJECT_NO_MAP",
    "REJECT_NO_PATH",
    "REJECT_TOO_NARROW",
    "RosActionClientPort",
    "SUPPORTED_GOAL_TYPES",
    "create_nav2_action_clients",
    "create_nav2_search_backend",
    "yaw_from_quaternion",
]
