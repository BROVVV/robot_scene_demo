"""计划书 §12.2：Nav2 搜索后端测试。

用假的 Action 服务器驱动后端，重点验证**顺序**与**拒绝路径**：
先 ComputePathToPose，路径非空且走廊达标才发 NavigateToPose；
``RELATIVE_MOVE`` 明确拒绝且不产出任何底层前进命令。
"""

from __future__ import annotations

import math

import pytest

from app.navigation.corridor_analyzer import CorridorGrid
from app.navigation.models import ExplorationGoal
from app.navigation.nav2_search_backend import (
    ACTION_ABORTED,
    ACTION_CANCELED,
    ACTION_EXECUTING,
    ACTION_SUCCEEDED,
    CANDIDATE_REJECT_REASONS,
    REJECT_GOAL_OCCUPIED,
    REJECT_NAV2_NOT_READY,
    REJECT_NO_LOCALIZATION,
    REJECT_NO_MAP,
    REJECT_NO_PATH,
    REJECT_NOT_MAP_FRAME,
    REJECT_TOO_NARROW,
    SUPPORTED_GOAL_TYPES,
    ActionPoll,
    Nav2SearchBackend,
)
from app.navigation.robot_backend import NavigationStatus

RESOLUTION = 0.05
# 机身半宽 + footprint padding：走廊量出来的净空要再扣掉它才是机身净空。
BODY_INSET_M = 0.215 + 0.04


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeActionClient:
    """记录收发顺序的假 Action server。"""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.sent: list[object] = []
        self.cancel_calls = 0
        self._polls: list[ActionPoll] = []

    def queue(self, *polls: ActionPoll) -> None:
        """排入若干次轮询结果；最后一个会一直重复到被替换。"""
        self._polls.extend(polls)

    def server_ready(self) -> bool:
        return self.ready

    def send(self, request: object) -> None:
        self.sent.append(request)

    def poll(self) -> ActionPoll:
        if not self._polls:
            return ActionPoll()
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return True


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SpeedRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, speed_limit_mps: float) -> bool:
        self.calls.append(float(speed_limit_mps))
        return True


# ---------------------------------------------------------------------------
# 地图
# ---------------------------------------------------------------------------


def _free_grid(size_m: float = 6.0) -> CorridorGrid:
    side = int(round(size_m / RESOLUTION))
    return CorridorGrid(
        resolution=RESOLUTION,
        origin_x=-size_m / 2.0,
        origin_y=-size_m / 2.0,
        width=side,
        height=side,
        data=tuple([0] * (side * side)),
    )


def _paint(grid: CorridorGrid, x_min, x_max, y_min, y_max, value: int) -> CorridorGrid:
    data = list(grid.data)
    col_lo = max(0, int(math.floor((x_min - grid.origin_x) / grid.resolution)))
    col_hi = min(
        grid.width, int(math.floor((x_max - grid.origin_x) / grid.resolution)) + 1
    )
    row_lo = max(0, int(math.floor((y_min - grid.origin_y) / grid.resolution)))
    row_hi = min(
        grid.height, int(math.floor((y_max - grid.origin_y) / grid.resolution)) + 1
    )
    for row in range(row_lo, row_hi):
        for column in range(col_lo, col_hi):
            data[row * grid.width + column] = value
    return CorridorGrid(
        resolution=grid.resolution,
        origin_x=grid.origin_x,
        origin_y=grid.origin_y,
        width=grid.width,
        height=grid.height,
        data=tuple(data),
    )


def _corridor_grid(half_width_m: float, *, narrow_from_x: float | None = None,
                   narrow_half_width_m: float = 0.30) -> CorridorGrid:
    """沿 y=0 造一条走廊，两侧墙在 ``±half_width_m``。

    ``narrow_from_x`` 之后收窄，用来构造「路径中段过不去」的情形。
    """
    grid = _free_grid()
    grid = _paint(grid, -10.0, 10.0, half_width_m, 10.0, 100)
    grid = _paint(grid, -10.0, 10.0, -10.0, -half_width_m, 100)
    if narrow_from_x is not None:
        grid = _paint(grid, narrow_from_x, 10.0, -10.0, narrow_half_width_m, 100)
        grid = _paint(grid, narrow_from_x, 10.0, -narrow_half_width_m, 10.0, 100)
    return grid


def _straight_path(length_m: float = 2.0, step_m: float = 0.10):
    count = int(round(length_m / step_m))
    return tuple((index * step_m, 0.0) for index in range(count + 1))


# ---------------------------------------------------------------------------
# 后端装配
# ---------------------------------------------------------------------------


def _backend(
    *,
    grid: CorridorGrid | None = None,
    localization_valid: bool = True,
    path_ready: bool = True,
    navigate_ready: bool = True,
    clock: Clock | None = None,
    speed_setter: SpeedRecorder | None = None,
    stop_publisher=None,
):
    path_client = FakeActionClient(ready=path_ready)
    navigate_client = FakeActionClient(ready=navigate_ready)
    backend = Nav2SearchBackend(
        path_client=path_client,
        navigate_client=navigate_client,
        occupancy_grid_provider=lambda: grid,
        localization_valid_provider=lambda: localization_valid,
        speed_limit_setter=speed_setter,
        stop_publisher=stop_publisher,
        now=clock or Clock(),
        plan_timeout_s=10.0,
    )
    return backend, path_client, navigate_client


def _goal(goal_id: str = "g1", *, yaw_deg: float = 0.0, frame: str = "map",
          position=(2.0, 0.0)) -> ExplorationGoal:
    return ExplorationGoal(
        goal_id=goal_id,
        goal_type="NAVIGATE_POSE",
        position=position,
        yaw=yaw_deg,
        frame=frame,
    )


def _drive_to_navigating(backend, path_client, navigate_client, grid=None):
    """跑到「已经发出 NavigateToPose」这一步，返回 handle。"""
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    handle = backend.execute_goal(_goal())
    status = backend.get_navigation_status(handle)
    assert status.status == NavigationStatus.RUNNING
    assert len(navigate_client.sent) == 1
    return handle


# ---------------------------------------------------------------------------
# §12.2 先规划后导航
# ---------------------------------------------------------------------------


def test_navigate_pose_plans_first_then_navigates() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))

    handle = backend.execute_goal(_goal(yaw_deg=90.0))
    # 规划已发出，导航还没发。
    assert len(path_client.sent) == 1
    assert navigate_client.sent == []
    assert backend.get_navigation_status(handle).status == NavigationStatus.RUNNING

    request = navigate_client.sent[0]
    assert request.x == pytest.approx(2.0)
    assert request.y == pytest.approx(0.0)
    # ExplorationGoal.yaw 是角度，Nav2 要弧度。
    assert request.yaw == pytest.approx(math.pi / 2.0)
    assert request.frame_id == "map"


def test_the_planned_request_keeps_the_goal_position() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    handle = backend.execute_goal(_goal(position=(1.5, -0.75), yaw_deg=-45.0))
    backend.get_navigation_status(handle)
    sent = path_client.sent[0]
    assert sent.x == pytest.approx(1.5)
    assert sent.y == pytest.approx(-0.75)
    assert sent.yaw == pytest.approx(math.radians(-45.0))


def test_success_maps_to_succeeded() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_SUCCEEDED))
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.SUCCEEDED
    assert result.provenance["candidate_decision"]["accepted"] is True


def test_abort_maps_to_failed() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_ABORTED, error="stuck"))
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.FAILED
    assert result.message == "stuck"


def test_cancel_maps_to_cancelled() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_CANCELED))
    assert backend.get_navigation_status(handle).status == NavigationStatus.CANCELLED


def test_executing_keeps_running() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_EXECUTING))
    assert backend.get_navigation_status(handle).status == NavigationStatus.RUNNING


# ---------------------------------------------------------------------------
# §12.2 拒绝路径绝不发 NavigateToPose
# ---------------------------------------------------------------------------


def test_empty_path_never_calls_navigate_to_pose() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=()))
    handle = backend.execute_goal(_goal())

    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NO_PATH
    assert navigate_client.sent == []


def test_occupied_goal_is_rejected_before_planning() -> None:
    """计划书 §3.2 / §14：目标落在占据栅格里 → ``GOAL_OCCUPIED``。

    不在这里拦的话，NavFn 对占据终点多半返回空路径，拒因会被记成 ``NO_PATH``
    —— 结果是同一个「不发 NavigateToPose」，但现场分不清是「过不去」还是
    「目标点本身就在墙里」。这个枚举此前没有任何产生点。
    """
    # (2.0, 2.0) 落在 _corridor_grid 画出来的上侧墙里。
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = backend.execute_goal(_goal(position=(2.0, 2.0)))

    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_GOAL_OCCUPIED
    assert path_client.sent == []
    assert navigate_client.sent == []


def test_unknown_goal_cell_is_left_to_the_planner() -> None:
    """未知栅格不在这里拦 —— 拦了会把正常的 frontier 目标一起误杀。

    未知区域的处理归 costmap（``allow_unknown: false``）与走廊分析器
    （§7.3），本处只判「已探明是障碍」这一种。
    """
    grid = _paint(_free_grid(), -1.0, 1.0, -1.0, 1.0, -1)
    backend, path_client, _ = _backend(grid=grid)
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    backend.execute_goal(_goal(position=(0.0, 0.0)))

    assert len(path_client.sent) == 1, "未知目标点必须照常送去规划"


def test_too_narrow_never_calls_navigate_to_pose() -> None:
    # 半宽 0.55 m ⇒ 机身净空约 0.30 m，低于 0.40 m 硬门。
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(0.55))
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    handle = backend.execute_goal(_goal())

    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_TOO_NARROW
    assert navigate_client.sent == []


def test_path_that_narrows_in_the_middle_is_rejected() -> None:
    """整条路径取最窄处判定：前 1.5 m 宽、之后收窄成 0.30 m 半宽。"""
    grid = _corridor_grid(1.25, narrow_from_x=1.50, narrow_half_width_m=0.30)
    backend, path_client, navigate_client = _backend(grid=grid)
    path_client.queue(
        ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path(length_m=3.0))
    )
    handle = backend.execute_goal(_goal(position=(3.0, 0.0)))
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_TOO_NARROW
    assert navigate_client.sent == []


def test_planning_abort_never_calls_navigate_to_pose() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    path_client.queue(ActionPoll(status=ACTION_ABORTED, error="no plan"))
    handle = backend.execute_goal(_goal())
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NO_PATH
    assert navigate_client.sent == []


def test_navigation_failure_has_no_experimental_fallback() -> None:
    """导航失败就失败，不转投实验后端、也不重发相对前进命令。"""
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_ABORTED, error="blocked"))
    assert backend.get_navigation_status(handle).status == NavigationStatus.FAILED

    assert len(path_client.sent) == 1
    assert len(navigate_client.sent) == 1
    assert not hasattr(backend, "experimental_backend")
    assert not hasattr(backend, "fallback_backend")


# ---------------------------------------------------------------------------
# §12.2 RELATIVE_MOVE 明确拒绝
# ---------------------------------------------------------------------------


def test_relative_move_is_rejected_and_sends_nothing() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    goal = ExplorationGoal(goal_id="r1", goal_type="RELATIVE_MOVE", relative_dx=0.3)
    handle = backend.execute_goal(goal)

    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert "RELATIVE_MOVE" in result.message
    # 关键：没有把它翻译成任何底层前进命令。
    assert path_client.sent == []
    assert navigate_client.sent == []


def test_relative_rotation_is_rejected_too() -> None:
    backend, path_client, _ = _backend(grid=_corridor_grid(1.25))
    goal = ExplorationGoal(goal_id="r2", goal_type="ROTATE_VIEW", relative_dyaw=15.0)
    result = backend.get_navigation_status(backend.execute_goal(goal))
    assert result.status == NavigationStatus.REJECTED
    assert path_client.sent == []


def test_capabilities_do_not_advertise_relative_motion() -> None:
    backend, _, _ = _backend(grid=_corridor_grid(1.25))
    capabilities = backend.capabilities()
    assert capabilities.supports_metric_navigation is True
    assert capabilities.supports_relative_translation is False
    assert capabilities.supports_relative_rotation is False
    assert capabilities.allowed_motion_primitives == SUPPORTED_GOAL_TYPES


# ---------------------------------------------------------------------------
# 就绪门
# ---------------------------------------------------------------------------


def test_missing_action_server_rejects_before_planning() -> None:
    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(1.25), navigate_ready=False
    )
    result = backend.get_navigation_status(backend.execute_goal(_goal()))
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NAV2_NOT_READY
    assert path_client.sent == []


def test_invalid_localization_rejects() -> None:
    backend, path_client, _ = _backend(
        grid=_corridor_grid(1.25), localization_valid=False
    )
    result = backend.get_navigation_status(backend.execute_goal(_goal()))
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NO_LOCALIZATION
    assert path_client.sent == []


def test_missing_map_rejects() -> None:
    backend, path_client, _ = _backend(grid=None)
    result = backend.get_navigation_status(backend.execute_goal(_goal()))
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NO_MAP
    assert path_client.sent == []


def test_health_reports_the_blocking_reason() -> None:
    backend, _, _ = _backend(grid=None)
    health = backend.health()
    assert not health.ready
    assert REJECT_NO_MAP in health.degraded
    assert health.details["reject_reason"] == REJECT_NO_MAP


def test_health_is_ready_when_everything_is_present() -> None:
    backend, _, _ = _backend(grid=_corridor_grid(1.25))
    assert backend.health().ready


def test_odom_frame_goals_are_refused() -> None:
    backend, path_client, _ = _backend(grid=_corridor_grid(1.25))
    result = backend.get_navigation_status(backend.execute_goal(_goal(frame="odom")))
    assert result.status == NavigationStatus.REJECTED
    assert result.provenance["reject_reason"] == REJECT_NOT_MAP_FRAME
    assert path_client.sent == []


# ---------------------------------------------------------------------------
# 速度上限与候选记录
# ---------------------------------------------------------------------------


def test_speed_limit_follows_the_corridor_width() -> None:
    recorder = SpeedRecorder()
    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(1.25), speed_setter=recorder
    )
    _drive_to_navigating(backend, path_client, navigate_client)
    assert recorder.calls == [pytest.approx(0.10)]


def test_narrow_but_passable_corridor_lowers_the_speed_limit() -> None:
    recorder = SpeedRecorder()
    # 半宽 0.90 m ⇒ 净空约 0.645 m，落在 [0.40, 0.80) 之间。
    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(0.90), speed_setter=recorder
    )
    _drive_to_navigating(backend, path_client, navigate_client)
    assert len(recorder.calls) == 1
    assert 0.05 <= recorder.calls[0] < 0.10


def test_unwritable_speed_limit_stops_before_navigate_to_pose() -> None:
    """写不进 RPP 的限速就不许发 NavigateToPose（计划书 §7.4 / §3.2）。

    否则走廊算出的 0.05~0.10 只是纸面数字，控制器照 YAML 的 0.10 跑——
    这类「算出来但没生效」的静默降级正是要消灭的东西。
    """

    def refusing_setter(speed_limit_mps: float) -> bool:
        return False

    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(0.90), speed_setter=refusing_setter
    )
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    handle = backend.execute_goal(_goal())
    result = backend.get_navigation_status(handle)

    assert result.status == NavigationStatus.REJECTED
    assert navigate_client.sent == []
    record = result.provenance["candidate_decision"]
    assert record["reject_reason"] == REJECT_NAV2_NOT_READY
    assert record["accepted"] is False
    # 候选记录里的限速必须还是走廊算出来的那个，不能被悄悄改成 0 或 None。
    assert 0.05 <= record["speed_limit_mps"] < 0.10


def test_rejected_candidate_never_sets_a_speed_limit() -> None:
    recorder = SpeedRecorder()
    backend, path_client, _ = _backend(grid=_corridor_grid(0.55), speed_setter=recorder)
    path_client.queue(ActionPoll(status=ACTION_SUCCEEDED, path=_straight_path()))
    backend.get_navigation_status(backend.execute_goal(_goal()))
    assert recorder.calls == []


def test_candidate_decision_carries_the_plan_section_14_fields() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_SUCCEEDED))
    record = backend.get_navigation_status(handle).provenance["candidate_decision"]
    for key in (
        "candidate_id",
        "goal_x",
        "goal_y",
        "goal_yaw",
        "path_length_m",
        "min_left_body_gap_m",
        "min_right_body_gap_m",
        "min_side_gap_m",
        "corridor_decision",
        "speed_limit_mps",
        "center_score",
        "accepted",
        "reject_reason",
    ):
        assert key in record, key
    assert record["candidate_id"] == "g1"
    assert record["corridor_decision"] == "OK"
    assert record["path_length_m"] == pytest.approx(2.0, abs=0.05)


# ---------------------------------------------------------------------------
# 并发、取消与停止
# ---------------------------------------------------------------------------


def test_a_second_goal_while_busy_is_rejected() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    _drive_to_navigating(backend, path_client, navigate_client)
    second = backend.execute_goal(_goal(goal_id="g2", position=(1.0, 1.0)))
    assert second.result is not None
    assert second.result.status == NavigationStatus.REJECTED
    assert len(path_client.sent) == 1


def test_cancel_settles_the_active_goal() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    assert backend.cancel() is True
    assert handle.result is not None
    assert handle.result.status == NavigationStatus.CANCELLED
    # 两个 Action 都要收到取消：规划阶段的 goal 可能还挂着。
    assert path_client.cancel_calls == 1
    assert navigate_client.cancel_calls == 1


def test_cancel_frees_the_slot_for_the_next_goal() -> None:
    """取消之后必须能发下一个 goal。

    现场（2026-10-08 真机验收）栽在这里：``_wait_result`` 等 15 s 超时后
    explorer 调 ``cancel(handle)`` 重试，但 ``cancel`` 只填了 ``handle.result``、
    没让出 ``self._active``，而 ``get_navigation_status`` 对已结算的 handle 会在
    开头就 return ⇒ ``_active`` 永远清不掉，之后每一轮 cycle 的候选都是
    ``rejected / "nav2 backend already has an active goal"``，
    ``motion_steps`` 全程 0。

    分辨力：把 ``cancel`` 里那句 ``self._active = None`` 去掉，本测试失败
    （``assert second.result is None`` 挂），同文件里其余 cancel 用例照过
    —— 它们只查 handle 是否被结算，查不出槽位没让出来。
    """
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    first = _drive_to_navigating(backend, path_client, navigate_client)

    backend.cancel(first)

    # 光调 get_navigation_status 救不回来：handle 已结算，走的是开头那句 return。
    assert backend.get_navigation_status(first).status == NavigationStatus.CANCELLED

    second = backend.execute_goal(_goal(goal_id="g2", position=(1.0, 1.0)))
    assert second.result is None, second.result and second.result.message
    assert len(path_client.sent) == 2


def test_stop_goal_cancels_and_publishes_a_zero_velocity() -> None:
    calls: list[int] = []
    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(1.25), stop_publisher=lambda: calls.append(1)
    )
    active = _drive_to_navigating(backend, path_client, navigate_client)

    stop_goal = ExplorationGoal(goal_id="s1", goal_type="STOP")
    handle = backend.execute_goal(stop_goal)
    assert handle.result is not None
    assert handle.result.status == NavigationStatus.SUCCEEDED
    assert active.result is not None
    assert active.result.status == NavigationStatus.CANCELLED
    assert calls == [1]


def test_settled_result_is_returned_again_without_re_advancing() -> None:
    backend, path_client, navigate_client = _backend(grid=_corridor_grid(1.25))
    handle = _drive_to_navigating(backend, path_client, navigate_client)
    navigate_client.queue(ActionPoll(status=ACTION_SUCCEEDED))
    first = backend.get_navigation_status(handle)
    assert first.status == NavigationStatus.SUCCEEDED
    # 再问一次不该把已经结束的目标重新推进一步。
    assert backend.get_navigation_status(handle) is first
    assert len(navigate_client.sent) == 1


def test_planning_timeout_rejects() -> None:
    clock = Clock()
    backend, path_client, navigate_client = _backend(
        grid=_corridor_grid(1.25), clock=clock
    )
    path_client.queue(ActionPoll(status=ACTION_EXECUTING))
    handle = backend.execute_goal(_goal())
    assert backend.get_navigation_status(handle).status == NavigationStatus.RUNNING
    clock.advance(11.0)
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    assert "timeout" in result.message
    assert navigate_client.sent == []


def test_candidate_reject_reason_is_closed_over_the_plan_enum() -> None:
    """计划书 §14：候选记录的 reject_reason 必须是固定六项之一。

    走廊分析器内部还有更细的原因串（``NO_PATH_SAMPLES``：路径一个采样点
    都取不到）。它不能被原样写进候选记录——现场的判读脚本按枚举分箱，
    多一个值就多一个没人认识的分箱。
    """
    from app.navigation.nav2_search_backend import (
        CANDIDATE_REJECT_REASONS,
        _candidate_reject_reason,
    )

    assert CANDIDATE_REJECT_REASONS == (
        "NO_MAP",
        "NO_LOCALIZATION",
        "NO_PATH",
        "GOAL_OCCUPIED",
        "TOO_NARROW",
        "NAV2_NOT_READY",
    )
    for reason in CANDIDATE_REJECT_REASONS:
        assert _candidate_reject_reason(reason) == reason
    # 枚举外的一律收口，且不能变成空串。
    assert _candidate_reject_reason("NO_PATH_SAMPLES") == REJECT_TOO_NARROW
    assert _candidate_reject_reason("") == REJECT_TOO_NARROW


def test_blocked_path_is_reported_inside_the_enum() -> None:
    """整条路径都在可通行区之外的候选，记录里的原因也必须落在 §14 枚举内。"""
    backend, path_client, _ = _backend(grid=_corridor_grid(1.25))
    path_client.queue(
        ActionPoll(status=ACTION_SUCCEEDED, path=[(900.0, 900.0), (901.0, 900.0)])
    )
    handle = backend.execute_goal(_goal(position=(901.0, 900.0)))
    result = backend.get_navigation_status(handle)
    assert result.status == NavigationStatus.REJECTED
    record = result.provenance["candidate_decision"]
    assert record["reject_reason"] in CANDIDATE_REJECT_REASONS
    assert record["min_side_gap_m"] < 0.0
