"""计划书 §12.4 / §12.5：自主搜索的行为与配置合同测试。

这些断言盯的是**配置与默认值**，不是实现细节：坐标系、话题、速度链顺序、
「默认不运动」，以及生产路径里不再出现实验后端与相对前进。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
import yaml

from app.navigation.backend_factory import MockBackend
from app.navigation.robot_backend import NavigationStatus

REPO = Path(__file__).resolve().parents[1]
BRINGUP = REPO / "ros2_ws/src/robot_scene_nav_bringup"
NAV2_PARAMS = BRINGUP / "config/nav2_params_humble.yaml"
SMOOTHER_PARAMS = BRINGUP / "config/velocity_smoother.yaml"
COLLISION_PARAMS = BRINGUP / "config/collision_monitor.yaml"
NAV2_LAUNCH = BRINGUP / "launch/robot_scene_nav2.launch.py"
COLLISION_LAUNCH = BRINGUP / "launch/collision_monitor.launch.py"
GATE = REPO / "configs/go2w/navigation_gate.yaml"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _code_lines(path: Path) -> list[str]:
    """去掉空行与整行注释后的 launch 源码。

    只做这一步：launch 是 Python，宿主机的 .venv 里没有 launch/launch_ros，
    不能在测试里 import 它。剩下的匹配都锚在完整语句上，不靠模糊正则。
    """
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _remappings(path: Path) -> set[tuple[str, str]]:
    """抓出 ``remappings=[("a", "b"), ...]`` 里的每一对。"""
    source = path.read_text(encoding="utf-8")
    return set(re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', source))


# ---------------------------------------------------------------------------
# §12.5 坐标系
# ---------------------------------------------------------------------------


def test_frames_are_the_fixed_chain() -> None:
    params = _yaml(NAV2_PARAMS)
    assert params["bt_navigator"]["ros__parameters"]["global_frame"] == "map"
    assert params["global_costmap"]["global_costmap"]["ros__parameters"][
        "global_frame"
    ] == "map"
    # 局部代价地图与行为服务器都在连续的 odom_fused 上，不跨不连续的跳变。
    assert params["local_costmap"]["local_costmap"]["ros__parameters"][
        "global_frame"
    ] == "odom_fused"
    assert params["behavior_server"]["ros__parameters"]["global_frame"] == "odom_fused"
    for section in (
        params["bt_navigator"]["ros__parameters"],
        params["global_costmap"]["global_costmap"]["ros__parameters"],
        params["local_costmap"]["local_costmap"]["ros__parameters"],
        params["behavior_server"]["ros__parameters"],
    ):
        assert section["robot_base_frame"] == "base_link"


def test_amcl_and_map_server_are_absent() -> None:
    """位姿来自 plain_slam，/map 来自坐标桥；不引入第二套定位。"""
    params = _yaml(NAV2_PARAMS)
    assert "amcl" not in params
    assert "map_server" not in params


# ---------------------------------------------------------------------------
# §12.5 话题
# ---------------------------------------------------------------------------


def test_map_scan_and_odom_topics() -> None:
    params = _yaml(NAV2_PARAMS)
    global_costmap = params["global_costmap"]["global_costmap"]["ros__parameters"]
    local_costmap = params["local_costmap"]["local_costmap"]["ros__parameters"]
    assert global_costmap["static_layer"]["map_topic"] == "/map"
    assert global_costmap["obstacle_layer"]["scan"]["topic"] == "/go2w/lidar/scan"
    assert local_costmap["obstacle_layer"]["scan"]["topic"] == "/go2w/lidar/scan"
    assert params["bt_navigator"]["ros__parameters"]["odom_topic"] == "/go2w/odom/fused"
    assert (
        params["controller_server"]["ros__parameters"]["odom_topic"]
        == "/go2w/odom/fused"
    )
    assert _yaml(SMOOTHER_PARAMS)["velocity_smoother"]["ros__parameters"][
        "odom_topic"
    ] == "/go2w/odom/fused"


def test_launcher_awaits_the_same_lidar_topic_the_nav2_configs_consume() -> None:
    """启动脚本的前置检查必须点名 Nav2 真正消费的那条激光话题。

    写成 /scan 这类别的名字时，前置检查会因为「没有发布者」永远失败，把
    现场堵在启动阶段——而且报出来的提示脚本也是错的。这条断言把两处名字
    钉在一起，改一边就红。
    """
    params = _yaml(NAV2_PARAMS)
    consumed = params["global_costmap"]["global_costmap"]["ros__parameters"][
        "obstacle_layer"
    ]["scan"]["topic"]
    assert consumed == "/go2w/lidar/scan"

    launcher = (REPO / "scripts/go2w/start_nav2_autonomous_search.sh").read_text(
        encoding="utf-8"
    )
    assert f'GO2W_SCAN_TOPIC:-{consumed}' in launcher
    # 提示里给的那条启动命令也得是真起这条话题的那个脚本。
    assert "start_live_perception.sh" in launcher


def test_global_costmap_does_not_invent_free_space() -> None:
    """UNKNOWN 不算可通行：走廊分析本来就是 fail-closed 的。"""
    params = _yaml(NAV2_PARAMS)
    assert params["planner_server"]["ros__parameters"]["GridBased"]["allow_unknown"] is False
    assert (
        params["global_costmap"]["global_costmap"]["ros__parameters"][
            "track_unknown_space"
        ]
        is False
    )


# ---------------------------------------------------------------------------
# §12.5 膨胀参数与 footprint
# ---------------------------------------------------------------------------


def test_inflation_and_footprint_match_the_plan() -> None:
    params = _yaml(NAV2_PARAMS)
    for key in ("global_costmap", "local_costmap"):
        section = params[key][key]["ros__parameters"]
        assert section["resolution"] == 0.05
        assert section["inflation_layer"]["inflation_radius"] == 1.00
        assert section["inflation_layer"]["cost_scaling_factor"] == 2.0
        assert section["footprint_padding"] == 0.04
        # 0.70 x 0.43 m 的矩形外廓，以 base_link 为中心。
        assert (
            section["footprint"]
            == "[[0.35, 0.215], [0.35, -0.215], [-0.35, -0.215], [-0.35, 0.215]]"
        )


def test_lateral_velocity_is_disabled() -> None:
    """侧移不可用（轮子空转车体不横移），速度链里 y 分量必须是死的。"""
    smoother = _yaml(SMOOTHER_PARAMS)["velocity_smoother"]["ros__parameters"]
    assert smoother["max_velocity"][1] == 0.0
    assert smoother["min_velocity"][1] == 0.0
    assert smoother["max_accel"][1] == 0.0


# ---------------------------------------------------------------------------
# §12.5 速度链
# ---------------------------------------------------------------------------


def test_velocity_chain_order_in_the_launch() -> None:
    remaps = _remappings(NAV2_LAUNCH)
    # controller_server 的 cmd_vel 出口 → 未平滑
    assert ("cmd_vel", "/cmd_vel_nav_raw") in remaps
    # velocity_smoother 吃未平滑、出平滑
    assert ("cmd_vel", "/cmd_vel_nav_raw") in remaps
    assert ("cmd_vel_smoothed", "/cmd_vel_nav_smoothed") in remaps

    collision = _yaml(COLLISION_PARAMS)["collision_monitor"]["ros__parameters"]
    assert collision["cmd_vel_in_topic"] == "/cmd_vel_nav_smoothed"
    assert collision["cmd_vel_out_topic"] == "/go2w/nav2_cmd_vel"
    assert collision["base_frame_id"] == "base_link"
    assert collision["odom_frame_id"] == "odom_fused"


def test_collision_monitor_is_the_only_writer_of_the_nav2_command() -> None:
    """``/go2w/nav2_cmd_vel`` 只能有一个发布源，否则仲裁会打架。"""
    topic = "/go2w/nav2_cmd_vel"

    # 1) 配置层：只有 collision_monitor 把它写成输出话题。
    output_owners = [
        path.name
        for path in BRINGUP.rglob("*.yaml")
        if _yaml(path)
        .get("collision_monitor", {})
        .get("ros__parameters", {})
        .get("cmd_vel_out_topic")
        == topic
    ]
    assert output_owners == ["collision_monitor.yaml"]

    # 2) launch 层：没有任何 remap 的**目标**是它。
    for path in sorted(BRINGUP.rglob("launch/*.py")):
        targets = {target for _, target in _remappings(path)}
        assert topic not in targets, path.name

    # 3) 脚本层：没有任何节点自己发布它。
    pattern = re.compile(
        r"create_publisher\(\s*[^,]+,\s*[\"']" + re.escape(topic) + r"[\"']"
    )
    for path in sorted(BRINGUP.rglob("scripts/*.py")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


def test_collision_monitor_stop_zone_is_ahead_of_the_body() -> None:
    """停止区必须完全在机身前方，不能把机身自己框进去。"""
    collision = _yaml(COLLISION_PARAMS)["collision_monitor"]["ros__parameters"]
    assert collision["polygons"] == ["Stop", "Slowdown"]
    stop_points = collision["Stop"]["points"]
    # 平面数组，x1,y1,x2,y2...
    xs = stop_points[0::2]
    ys = stop_points[1::2]
    # 机身前后各 0.35 m，停止区前边 0.60 m ⇒ 留 0.25 m 余量；后边 -0.40 m 也
    # 在机身之外，避免倒车时自己触发停止。
    assert max(xs) == pytest.approx(0.60)
    assert min(xs) == pytest.approx(-0.40)
    assert min(ys) == pytest.approx(-0.35)
    assert max(ys) == pytest.approx(0.35)
    assert collision["Stop"]["action_type"] == "stop"
    assert collision["Slowdown"]["action_type"] == "slowdown"
    slowdown_xs = collision["Slowdown"]["points"][0::2]
    # 减速区把停止区整个包住，否则会出现「已进减速区但还没停」的盲带。
    assert min(slowdown_xs) < min(xs) and max(slowdown_xs) > max(xs)


def test_behavior_server_registers_no_translating_behaviour() -> None:
    """backup / drive_on_heading 会在没有走廊分析的情况下位移，不得注册。"""
    params = _yaml(NAV2_PARAMS)
    plugins = params["behavior_server"]["ros__parameters"]["behavior_plugins"]
    assert plugins == ["spin", "wait"]
    for forbidden in ("backup", "drive_on_heading", "assisted_teleop"):
        assert forbidden not in plugins


def test_velocity_smoother_parameters_are_not_duplicated() -> None:
    """同一份参数只写一处（计划书 §3.1 禁止重复 Nav2 配置）。"""
    assert "velocity_smoother" not in _yaml(NAV2_PARAMS)
    assert "velocity_smoother" in _yaml(SMOOTHER_PARAMS)


# ---------------------------------------------------------------------------
# §12.5 默认不运动
# ---------------------------------------------------------------------------


def test_execution_is_off_by_default() -> None:
    launch = NAV2_LAUNCH.read_text(encoding="utf-8")
    assert 'DeclareLaunchArgument("execution_enabled", default_value="false")' in launch
    # 「只规划」模式起 planner_server 和 bt_navigator。bt_navigator 不含速度链路，
    # 但后端 _readiness() 要求 /navigate_to_pose 在线才肯发 ComputePathToPose，
    # 少了它「只规划」就一次规划都不做（§9-6）。
    assert 'PLAN_ONLY_NODES = ["planner_server", "bt_navigator"]' in launch


def _node_block(source: str, name: str) -> str:
    """截出 ``name="<name>"`` 所在的那个 ``Node(...)`` 文本块。

    Node 的参数行缩进 16 空格、收尾的 ``),`` 缩进 12 空格，按这个边界切。
    数 ``IfCondition`` 的总数会把 LogInfo 和 IncludeLaunchDescription 一起算进来，
    所以必须按块判。
    """
    start = source.index(f'name="{name}"')
    return source[start:source.index("\n            ),", start)]


def test_the_execute_only_nodes_are_gated_on_execution_enabled() -> None:
    """每个能产生速度的节点都必须挂 IfCondition，否则只规划模式会漏速度。"""
    launch = NAV2_LAUNCH.read_text(encoding="utf-8")
    for node in ("controller_server", "velocity_smoother", "behavior_server",
                 "lifecycle_manager_navigation"):
        assert "IfCondition(execution_enabled)" in _node_block(launch, node), node
    # collision_monitor 是 IncludeLaunchDescription，不是 Node（它自己的 launch
    # 文件里刻意不带条件，见 test_collision_monitor_launch_is_a_plain_includable），
    # 所以按 include 块判：路径名往后紧跟着的几行里必须有 IfCondition。
    # 刻意不数全文出现次数——LogInfo 也挂着同一个条件，数总数会把它算进来。
    include_at = launch.index("collision_monitor.launch.py")
    assert "condition=IfCondition(execution_enabled)" in launch[include_at:include_at + 200]
    assert "condition=UnlessCondition(execution_enabled)" in launch


def test_bt_navigator_starts_in_both_modes() -> None:
    """bt_navigator 不产生速度，但只规划模式少了它就连规划都做不了。

    后端 ``_readiness()`` 要求 ``/navigate_to_pose`` 在线才肯发
    ``ComputePathToPose``（nav2_search_backend 里两个 client 都必须 ready）。
    它一旦被挂上 IfCondition，只规划模式就静默退化成"连规划都不做"，
    §14 的候选记录也就一行为空 —— 这种回归必须在这里挡住。
    """
    launch = NAV2_LAUNCH.read_text(encoding="utf-8")
    block = _node_block(launch, "bt_navigator")
    assert "IfCondition" not in block and "UnlessCondition" not in block
    # 只规划模式的 lifecycle_manager 必须把它一起拉起来。
    assert 'PLAN_ONLY_NODES = ["planner_server", "bt_navigator"]' in launch


def test_collision_monitor_launch_is_a_plain_includable() -> None:
    """碰撞监视器只做「起一个节点」，是否启动交给主 launch 的 condition 决定。"""
    source = COLLISION_LAUNCH.read_text(encoding="utf-8")
    assert source.count("Node(") == 1
    assert "IfCondition" not in source and "UnlessCondition" not in source
    assert "use_sim_time" in source


def test_navigation_gate_keeps_execution_off() -> None:
    gate = _yaml(GATE)
    assert gate["current_evidence"]["nav2_allow_execute"] is False
    # 关闸必须是 fail-closed：缺证据不等于放行。
    assert gate["policy"] == "fail_closed"


def test_enable_motion_requires_the_gate_file_authorization() -> None:
    """计划书 §10：nav2_allow_execute 是用户手改的那一道，必须真的被读。

    只断言文件里写着 false 是不够的——那只能证明「配置没被打开」，证明不了
    「打开它才算数」。真正要钉的是启动脚本在 --enable-motion 分支里读了这个
    键，否则谁都能用一条命令行绕过 §10。
    """
    launcher = (REPO / "scripts/go2w/start_nav2_autonomous_search.sh").read_text(
        encoding="utf-8"
    )
    assert "configs/go2w/navigation_gate.yaml" in launcher
    assert "nav2_allow_execute" in launcher
    # 读到的值不是 true 就必须退出，且是 fail-closed 的非零退出码。
    assert '"${allow_execute}" != "true"' in launcher
    # 授权检查必须在拉起任何东西（ros2 launch / ros2 run）之前。
    assert launcher.index("allow_execute") < launcher.index("Starting Nav2 bringup")


# ---------------------------------------------------------------------------
# §12.4 生产路径里不再有实验后端
# ---------------------------------------------------------------------------


def test_production_defaults_select_nav2_only() -> None:
    web_config = _yaml(REPO / "configs/go2w/autonomous_search_web.yaml")
    assert web_config["search"]["backend"] == "nav2"

    from app.manual_web_demo.search_models import SearchStartRequest, load_search_defaults

    assert SearchStartRequest().backend == "nav2"
    assert load_search_defaults()["backend"] == "nav2"
    assert SearchStartRequest(task_text="找垃圾桶").validate() is None
    # 旧后端名不再被生产请求接受。这里必须断言到原因串本身：validate()
    # 返回的是错误文本，只要写 `assert ...validate()` 就永远为真——旧后端
    # 一旦被重新放行，这条断言照样通过。
    rejected = SearchStartRequest(task_text="找垃圾桶", backend="go2w_experimental").validate()
    assert rejected is not None and "unsupported backend" in rejected, rejected
    # §6.6-2：mock 也不能从生产请求参数进来。默认（allow_mock=False）一律拒，
    # 只有测试台（SearchSessionService._executor_factory_is_mock）才放行。
    # 这条挡的不是「跑不起来」，是「给真机 WebUI 发个 backend=mock 就能跑出
    # 一场编造的搜索、界面上还看不出区别」。
    for fake in ("mock", "mock_metric"):
        rejected = SearchStartRequest(task_text="找垃圾桶", backend=fake).validate()
        assert rejected is not None and "unsupported backend" in rejected, (fake, rejected)
        assert SearchStartRequest(
            task_text="找垃圾桶", backend=fake
        ).validate(allow_mock=True) is None, fake
    # 默认请求必须不带运动授权。run_go2w 的授权分支在没有
    # --operator-supervised-experiment 且没有 --dry-run-motion 时直接拒绝
    # （返回 3），所以「默认只规划、不执行」靠的是这两个默认值，而不是
    # dry_run_motion 被偷偷置真。
    default = SearchStartRequest(task_text="找垃圾桶")
    assert default.enable_autonomous_motion is False
    assert default.operator_supervised_experiment is False
    assert default.dry_run_motion is False


def test_search_worker_defaults_to_nav2() -> None:
    """§12.4：生产启动参数里不允许再出现实验后端。

    搜索 worker 是 WebUI 与真机之间的那一层，它的默认值就是生产启动参数。
    """
    from scripts.go2w.autonomous_search_worker import build_argv

    def _backend(argv: list[str]) -> str:
        return argv[argv.index("--backend") + 1]

    assert _backend(build_argv({"target": "找垃圾桶"})) == "nav2"
    # WebUI 显式传参时照传，不做「悄悄换成别的后端」这种事。
    assert _backend(build_argv({"target": "找垃圾桶", "backend": "mock"})) == "mock"


def test_backend_factory_has_no_silent_fallback() -> None:
    from app.navigation.backend_factory import create_backend

    with pytest.raises(ValueError):
        create_backend("totally_unknown_backend")
    # nav2 需要真实 node；没有 node 也必须显式报错，不能退回 mock。
    with pytest.raises(ValueError):
        create_backend("nav2")


def test_nav2_backend_is_the_only_metric_capable_real_backend() -> None:
    from app.navigation.nav2_search_backend import (
        SUPPORTED_GOAL_TYPES,
        Nav2SearchBackend,
    )

    assert SUPPORTED_GOAL_TYPES == ("NAVIGATE_POSE", "STOP")
    assert "RELATIVE_MOVE" not in SUPPORTED_GOAL_TYPES
    assert "ROTATE_VIEW" not in SUPPORTED_GOAL_TYPES
    assert Nav2SearchBackend is not None

# ---------------------------------------------------------------------------
# §12.4 行为合同：Nav2 能力下，生成器只产出地图系 NAVIGATE_POSE
#
# 上面那条断言的是**声明**（SUPPORTED_GOAL_TYPES），这里把生成器真跑一遍。
# 声明对了但调用点还写死相对原语，是这次改造最容易漏的一种回归。
# ---------------------------------------------------------------------------


def _nav2_capabilities():
    from app.navigation.robot_backend import RobotCapabilities

    return RobotCapabilities(
        supports_metric_navigation=True,
        supports_relative_translation=False,
        supports_relative_rotation=False,
        allowed_motion_primitives=("NAVIGATE_POSE", "STOP"),
    )


def test_local_goal_executor_expresses_observation_as_a_map_pose() -> None:
    """观察转向必须是「站在原地转到新朝向」，不能退回相对转动。"""
    from app.navigation.local_goal_executor import LocalGoalExecutor
    from app.spatial.models import INTENT_EXPLORE_FRONTIER, ExplorationIntent

    capabilities = _nav2_capabilities()

    def _intent() -> object:
        return ExplorationIntent(
            intent_id="intent_contract",
            intent_type=INTENT_EXPLORE_FRONTIER,
            preferred_bearing_deg=90.0,
        )

    executor = LocalGoalExecutor()
    executor.begin(_intent())
    goal = executor.next_goal(
        current_yaw_deg=0.0, current_position=(1.0, 2.0), capabilities=capabilities
    )
    assert goal is not None
    assert goal.goal_type == "NAVIGATE_POSE"
    assert goal.position == (1.0, 2.0)
    assert goal.yaw == 90.0
    assert goal.frame == "map"

    # 没有当前位置：表达不出地图系目标就什么都不发，绝不相对转动。
    executor = LocalGoalExecutor()
    executor.begin(_intent())
    assert (
        executor.next_goal(
            current_yaw_deg=0.0, current_position=None, capabilities=capabilities
        )
        is None
    )


def test_topology_route_executor_emits_a_map_pose_for_the_next_edge() -> None:
    from app.navigation.topology_route_executor import (
        TopologicalRoute,
        TopologyRouteExecutor,
    )
    from app.spatial.models import (
        MovementEdge,
        PlaceNode,
        SpatialPose,
        SPATIAL_QUALITY_METRIC_LIDAR,
    )
    from app.spatial.place_graph import PlaceGraph

    graph = PlaceGraph()
    graph.places["P1"] = PlaceNode(
        place_id="P1",
        pose=SpatialPose(x=0.0, y=0.0, yaw=0.0, frame_id="map",
                         quality=SPATIAL_QUALITY_METRIC_LIDAR),
    )
    graph.places["P2"] = PlaceNode(
        place_id="P2",
        pose=SpatialPose(x=3.0, y=4.0, yaw=0.5, frame_id="map",
                         quality=SPATIAL_QUALITY_METRIC_LIDAR),
    )
    graph.edges.append(
        MovementEdge(
            edge_id="E1", from_place="P1", to_place="P2", observed_displacement_m=5.0
        )
    )
    route = TopologicalRoute(
        route_id="r1", place_path=["P1", "P2"], edge_path=["E1"], total_cost=5.0
    )
    goal = TopologyRouteExecutor().next_goal(
        route=route,
        current_place_id="P1",
        place_graph=graph,
        current_yaw_deg=0.0,
        capabilities=_nav2_capabilities(),
        current_position=(0.0, 0.0),
    )
    assert goal is not None
    assert goal.goal_type == "NAVIGATE_POSE"
    assert goal.position == (3.0, 4.0)
    assert goal.yaw == pytest.approx(math.degrees(0.5))
    assert goal.frame == "map"


def test_recovery_backs_out_to_the_breadcrumb_pose_not_a_relative_step() -> None:
    """倒车恢复走的正是刚走过的面包屑，所以它的起点就是地图系目标。"""
    from app.live_robot.recovery_motion import SafeMotionSegment
    from app.navigation.recovery_manager import RecoveryManager

    breadcrumb = SafeMotionSegment(
        start_pose=(1.0, 1.0, 0.0),
        end_pose=(1.3, 1.0, 0.0),
        signed_distance_m=0.3,
        heading_rad=0.0,
    )
    decision = RecoveryManager().decide(
        breadcrumb=breadcrumb,
        current_pose=(1.3, 1.0, 0.0),
        stuck_state="FRONT_BLOCKED",
        metric_navigation=True,
    )
    assert decision.backward_allowed, decision.reason
    goal = decision.backward_goal
    assert goal is not None
    assert goal.goal_type == "NAVIGATE_POSE"
    assert goal.position == (1.0, 1.0)
    assert goal.frame == "map"


def test_recovery_without_a_breadcrumb_refuses_instead_of_reversing() -> None:
    """没证明退路安全就不退：返回拒绝，而不是相对后退一段。

    这一条比 ``NO_METRIC_BACKWARD_TARGET`` 更早拦住——面包屑缺失时安全门就
    否决了，轮不到度量后端去表达目标。
    """
    from app.navigation.recovery_manager import RecoveryManager

    decision = RecoveryManager().decide(
        breadcrumb=None,
        current_pose=(1.3, 1.0, 0.0),
        stuck_state="FRONT_BLOCKED",
        metric_navigation=True,
    )
    assert decision.backward_allowed is False
    assert decision.backward_goal is None
    assert "NO_VALID_REVERSE_CORRIDOR" in decision.reason


def test_candidate_generator_has_no_relative_goals_under_nav2() -> None:
    """「nav2 下没有相对目标」要能被证明有分辨力，不能靠候选为空蒙混过关。

    所以同一份输入跑两遍：相对能力那遍必须真的产出 ``RELATIVE_MOVE``，
    nav2 那遍必须非空且只有地图系目标。少了后半句，一个恒返回空列表的
    生成器也能让这条测试变绿。
    """
    from app.navigation.candidate_goal_generator import (
        generate_live_exploration_candidates,
    )
    from app.navigation.exploration_graph import ExplorationGraph
    from app.navigation.models import LiveObservation
    from app.navigation.robot_backend import RobotCapabilities

    observation = LiveObservation(
        bundle_id="obs_contract",
        timestamp=1_700_000_000.0,
        scene_objects=[
            {"label": "桌子", "label_zh": "桌子", "confidence": 0.9, "object_id": "o1"}
        ],
        pose={"x": 0.0, "y": 0.0, "yaw_rad": 0.0},
    )

    def _types(capabilities) -> set[str]:
        return {
            goal.goal_type
            for goal in generate_live_exploration_candidates(
                observation=observation,
                graph=ExplorationGraph(session_id="contract"),
                capabilities=capabilities,
                current_yaw_deg=0.0,
            )
        }

    # 对照组：相对后端下这里确实会生成 RELATIVE_MOVE。
    relative_types = _types(
        RobotCapabilities(
            supports_relative_translation=True,
            supports_relative_rotation=True,
            allowed_motion_primitives=("FORWARD", "ROTATE"),
        )
    )
    assert "RELATIVE_MOVE" in relative_types, relative_types

    nav2_types = _types(_nav2_capabilities())
    assert nav2_types, "候选为空，这条断言就没有分辨力"
    assert nav2_types <= {"NAVIGATE_POSE", "STOP"}, nav2_types


class _ScriptedBackend(MockBackend):
    """能力表可换、每步都拒答的假后端，并记下真正收到过的目标类型。

    继承 ``MockBackend`` 只为了复用它的 ``execute_goal`` 与结果封装；能力表必须
    由调用方给，否则这里量的就不是「nav2 模式下会不会退回相对前进」。
    """

    def __init__(self, capabilities, **kwargs) -> None:
        super().__init__(outcome_sequence=[NavigationStatus.REJECTED], **kwargs)
        self._capabilities = capabilities
        self.goal_types: list[str] = []

    def capabilities(self):
        return self._capabilities

    def execute_goal(self, goal):
        self.goal_types.append(str(goal.goal_type))
        return super().execute_goal(goal)


def _relative_capabilities():
    """候选生成器会给出 ``RELATIVE_MOVE`` 的那份相对能力表。

    与 ``test_candidate_generator_has_no_relative_goals_under_nav2`` 的对照组
    同一份。注意不能直接用 ``MockBackend`` 自带的那份：它带 heading control，
    候选生成器会改出 ``ROTATE_VIEW``，对照组就验不到「会向前走」这件事。
    """
    from app.navigation.robot_backend import RobotCapabilities

    return RobotCapabilities(
        supports_relative_translation=True,
        supports_relative_rotation=True,
        allowed_motion_primitives=("FORWARD", "ROTATE"),
    )


def test_no_path_never_falls_back_to_a_forward_step() -> None:
    """计划书 §12.4：路径不可达时不许改按 bearing 直接向前走。

    两遍跑同一个场景、同样每步都拒答，只换能力表：相对那遍必须真的选中
    ``RELATIVE_MOVE``，nav2 那遍一个相对目标都不许有。少了前半句，一个恒不选
    目标的搜索器也能让这条测试变绿。

    分辨力是注错校过的：把候选生成器 ``add()`` 里的原语合同闸、F 段的
    ``supports_metric_navigation`` 闸和兜底前进闸一起拆掉，nav2 那遍的选中序列
    立刻变成 ``['NAVIGATE_POSE', 'RELATIVE_MOVE', ...]``，这条断言随之变红；
    还原后 sha256 一致、测试复绿。
    """
    from app.live_robot.autonomous_explorer import AutonomousExplorer
    from app.live_robot.mock_observation_scene import scenario_no_target
    from app.navigation.exploration_config import ExplorationBudget, load_exploration_policy
    from app.navigation.exploration_graph import ExplorationGraph

    def _selected_types(backend) -> list[str]:
        scene = scenario_no_target(empty_scenes=3)
        policy = load_exploration_policy()
        policy.budget = ExplorationBudget(
            max_motion_steps=12, max_planning_cycles=8, max_search_seconds=60.0
        )
        explorer = AutonomousExplorer(
            target="蓝色垃圾桶",
            observer=scene.observer(),
            matcher=scene.matcher(),
            verifier=scene.verifier(),
            backend=backend,
            policy=policy,
            graph=ExplorationGraph(session_id="contract_no_path"),
            session_id="contract_no_path",
            negative_target_key="蓝色垃圾桶",
        )
        explorer.run()
        return [
            event["goal"]["goal_type"]
            for event in explorer.events
            if event.get("event") == "selected_goal"
        ]

    relative_backend = _ScriptedBackend(_relative_capabilities())
    relative_types = _selected_types(relative_backend)
    assert "RELATIVE_MOVE" in relative_types, relative_types

    backend = _ScriptedBackend(_nav2_capabilities())
    nav2_types = _selected_types(backend)
    assert nav2_types, "一个目标都没选中，这条断言就没有分辨力"
    assert "RELATIVE_MOVE" not in nav2_types, nav2_types
    assert set(nav2_types) <= {"NAVIGATE_POSE", "STOP"}, nav2_types
    # 拒答的每一条都真的走到了后端（不是被前端过滤掉了）。
    assert backend.goal_types, "后端一条目标都没收到"


def test_target_approach_never_emits_a_relative_step_under_nav2() -> None:
    """计划书 §12.4 后半句：**目标接近**也不生成 ``RELATIVE_MOVE``。

    上面两条盯的是搜索循环；确认目标之后一步步走近的那条链（§6.7）是另一个
    生成点，此前一条测试都没有。这里直接量那一层的产物：转向要表达成「站在原地
    转到新朝向」、前进要表达成 standoff 候选的地图系位姿，表达不出来（没有候选）
    就返回 ``None`` 让调用方停车 —— 而不是退回相对位移。
    """
    from app.live_robot.autonomous_explorer import AutonomousExplorer
    from app.navigation.approach_controller import (
        ACTION_APPROACH_FORWARD,
        ACTION_ROTATE_SMALL,
        ApproachDecision,
    )
    from app.navigation.models import LiveObservation
    from app.navigation.viewpoint_models import ViewpointCandidate

    observation = LiveObservation(
        bundle_id="obs_approach",
        timestamp=1_700_000_000.0,
        scene_objects=[],
        pose={"x": 2.0, "y": -1.5, "yaw_rad": 0.0},
    )

    def _goal(decision, best):
        return AutonomousExplorer._metric_approach_goal(
            decision, observation, best, object_id="o1", steps=1
        )

    turn = _goal(ApproachDecision(action=ACTION_ROTATE_SMALL, relative_dyaw_deg=25.0), None)
    assert turn is not None and turn.goal_type == "NAVIGATE_POSE", turn
    # 原地转：位置就是机器人自己，只有 yaw 变。
    assert (turn.position[0], turn.position[1]) == (2.0, -1.5), turn.position
    assert turn.frame == "map" and abs(turn.yaw - 25.0) < 1e-6, turn.yaw

    viewpoint = ViewpointCandidate(
        viewpoint_id="vp1", pose=(3.25, -1.5, 0.0), hard_safe=True
    )
    forward = _goal(ApproachDecision(action=ACTION_APPROACH_FORWARD, relative_dx=0.2), viewpoint)
    assert forward is not None and forward.goal_type == "NAVIGATE_POSE", forward
    # 前进的终点是候选位姿，不是「当前位姿 + 0.2m」。
    assert (forward.position[0], forward.position[1]) == (3.25, -1.5), forward.position
    assert forward.frame == "map", forward.frame

    # 没有可用 standoff 候选时必须拒答，让调用方停车——这里最容易被写成
    # 「那就按 bearing 向前走 0.2m」。
    assert _goal(ApproachDecision(action=ACTION_APPROACH_FORWARD, relative_dx=0.2), None) is None
    for decision in (
        ApproachDecision(action=ACTION_ROTATE_SMALL, relative_dyaw_deg=-10.0),
        ApproachDecision(action=ACTION_APPROACH_FORWARD, relative_dx=0.2),
    ):
        goal = _goal(decision, viewpoint)
        assert goal is None or goal.goal_type == "NAVIGATE_POSE", (decision.action, goal)


def test_nav2_bootstrap_goal_is_relative_to_the_robot_not_the_map_origin() -> None:
    """``frame="map"`` 的目标必须落在机器人前方，而不是地图原点旁边。

    图里还没有任何节点时会走 bootstrap 分支。它曾按「机器人在原点」算，
    于是机器人在 (10, 5) 时那个 0.5m 的引导点落在原点旁边——不是「前方
    一步」，是让它横穿整张地图。
    """
    from app.navigation.candidate_goal_generator import (
        generate_live_exploration_candidates,
    )
    from app.navigation.exploration_graph import ExplorationGraph
    from app.navigation.models import LiveObservation

    observation = LiveObservation(
        bundle_id="obs_bootstrap",
        timestamp=1_700_000_000.0,
        scene_objects=[],
        pose={"x": 10.0, "y": 5.0, "yaw_rad": 0.0},
    )
    candidates = generate_live_exploration_candidates(
        observation=observation,
        graph=ExplorationGraph(session_id="contract"),
        capabilities=_nav2_capabilities(),
        current_yaw_deg=0.0,
        robot_pose=[10.0, 5.0, 0.0],
    )
    bootstrap = [
        goal for goal in candidates
        if goal.provenance.get("source") == "metric_bootstrap"
    ]
    assert bootstrap, [goal.provenance for goal in candidates]
    assert bootstrap[0].frame == "map"
    assert bootstrap[0].position == (pytest.approx(10.5), pytest.approx(5.0))
