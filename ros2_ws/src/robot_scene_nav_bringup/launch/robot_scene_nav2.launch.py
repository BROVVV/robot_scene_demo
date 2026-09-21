"""GO2-W 自主搜索单链路 Nav2 bringup（计划书 §6.3）。

坐标链固定为 ``map -> odom_fused -> base_link``，由
``plain_slam_nav_frame_bridge.py`` 独家发布（``/map`` 也是它发；旧的地图 relay
不再启动，避免 ``/map`` 出现两个发布者）。

``execution_enabled`` 默认 ``false``：此时起坐标桥、规划器和 ``bt_navigator``，
地图、TF、``/go2w/nav/localization_valid`` 都在，规划链路完整可查（后端在发
``ComputePathToPose`` 之前会先确认 ``NavigateToPose`` 的 action server 在，
少了 ``bt_navigator`` 就一次规划都不会发 —— 计划书 §9-6）；但控制器、平滑器、
碰撞监视器一个都不起，**没有任何节点会把路径变成速度**，``/go2w/nav2_cmd_vel``
连发布者都没有。用户现场验收后才由用户自己改为 ``true``。

速度话题链是明确的，且 ``/go2w/nav2_cmd_vel`` 只有 collision_monitor 一个发布者：

    controller_server  /cmd_vel              -> /cmd_vel_nav_raw
    velocity_smoother  /cmd_vel              -> /cmd_vel_nav_smoothed
    collision_monitor  /cmd_vel_nav_smoothed -> /go2w/nav2_cmd_vel
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


EXECUTE_NODES = [
    "planner_server",
    "controller_server",
    "bt_navigator",
    "behavior_server",
    "velocity_smoother",
    "collision_monitor",
]
# bt_navigator 不产生速度（它只把目标派给 controller_server），但它是
# /navigate_to_pose 的宿主。后端 _readiness() 要求它在线才肯发 ComputePathToPose，
# 所以只规划模式也必须起它，否则「只规划」退化成「什么都不做」。
PLAN_ONLY_NODES = ["planner_server", "bt_navigator"]


def generate_launch_description():
    share = FindPackageShare("robot_scene_nav_bringup")
    params_file = LaunchConfiguration("params_file")
    smoother_params = LaunchConfiguration("velocity_smoother_params_file")
    use_sim_time = LaunchConfiguration("use_sim_time")
    execution_enabled = LaunchConfiguration("execution_enabled")
    navigation_bt = PathJoinSubstitution(
        [share, "behavior_trees", "navigate_to_pose_robot_scene.xml"]
    )
    common = {"use_sim_time": use_sim_time}

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            DeclareLaunchArgument("execution_enabled", default_value="false"),
            DeclareLaunchArgument(
                "params_file",
                default_value=PathJoinSubstitution(
                    [share, "config", "nav2_params_humble.yaml"]
                ),
            ),
            DeclareLaunchArgument(
                "velocity_smoother_params_file",
                default_value=PathJoinSubstitution(
                    [share, "config", "velocity_smoother.yaml"]
                ),
            ),
            LogInfo(
                condition=UnlessCondition(execution_enabled),
                msg="GO2-W Nav2 只规划：execution_enabled=false。坐标桥、规划器与"
                    "bt_navigator 已启动（规划链路可查），控制器/平滑器/碰撞监视器"
                    "未启动，不会有速度进入机器人。",
            ),
            LogInfo(
                condition=IfCondition(execution_enabled),
                msg="GO2-W Nav2 执行链路已启动（execution_enabled=true）。真正的运动"
                    "授权仍在 go2w_cmd_vel_bridge 与 navigation_gate.yaml 里单独把关。",
            ),
            # 唯一的 /map、map->odom_fused、odom_fused->base_link 与
            # /go2w/nav/localization_valid 发布者。
            Node(
                package="robot_scene_nav_bringup",
                executable="plain_slam_nav_frame_bridge.py",
                name="plain_slam_nav_frame_bridge",
                output="screen",
                parameters=[common],
            ),
            Node(
                package="nav2_planner",
                executable="planner_server",
                name="planner_server",
                output="screen",
                parameters=[params_file, common],
            ),
            Node(
                package="nav2_controller",
                executable="controller_server",
                name="controller_server",
                output="screen",
                condition=IfCondition(execution_enabled),
                parameters=[params_file, common],
                remappings=[("cmd_vel", "/cmd_vel_nav_raw")],
            ),
            Node(
                package="nav2_velocity_smoother",
                executable="velocity_smoother",
                name="velocity_smoother",
                output="screen",
                condition=IfCondition(execution_enabled),
                parameters=[smoother_params, common],
                remappings=[
                    ("cmd_vel", "/cmd_vel_nav_raw"),
                    ("cmd_vel_smoothed", "/cmd_vel_nav_smoothed"),
                ],
            ),
            Node(
                package="nav2_bt_navigator",
                executable="bt_navigator",
                name="bt_navigator",
                output="screen",
                # 刻意不挂 execution_enabled：bt_navigator 自身不产生速度，
                # 只提供 /navigate_to_pose，两种模式都要在（见 PLAN_ONLY_NODES）。
                parameters=[
                    params_file,
                    common,
                    {"default_nav_to_pose_bt_xml": navigation_bt},
                ],
            ),
            Node(
                package="nav2_behaviors",
                executable="behavior_server",
                name="behavior_server",
                output="screen",
                condition=IfCondition(execution_enabled),
                parameters=[params_file, common],
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution([share, "launch", "collision_monitor.launch.py"])
                ),
                condition=IfCondition(execution_enabled),
                launch_arguments={"use_sim_time": use_sim_time}.items(),
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_navigation",
                output="screen",
                condition=IfCondition(execution_enabled),
                parameters=[common, {"autostart": True, "node_names": EXECUTE_NODES}],
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_plan_only",
                output="screen",
                condition=UnlessCondition(execution_enabled),
                parameters=[common, {"autostart": True, "node_names": PLAN_ONLY_NODES}],
            ),
        ]
    )
