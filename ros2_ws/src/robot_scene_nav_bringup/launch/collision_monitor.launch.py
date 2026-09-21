"""碰撞监视器（计划书 §6.3）。

单独一个文件是因为 Nav2 官方也是这么组织的；它由 robot_scene_nav2.launch.py 在
``execution_enabled=true`` 时 include，并由同一个 lifecycle manager 一起拉起。

它是 ``/go2w/nav2_cmd_vel`` 的唯一发布者：输入 ``/cmd_vel_nav_smoothed``，
输出 ``/go2w/nav2_cmd_vel``（两个话题名都写在 config/collision_monitor.yaml 里）。
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            Node(
                package="nav2_collision_monitor",
                executable="collision_monitor",
                name="collision_monitor",
                output="screen",
                parameters=[
                    PathJoinSubstitution(
                        [
                            FindPackageShare("robot_scene_nav_bringup"),
                            "config",
                            "collision_monitor.yaml",
                        ]
                    ),
                    {"use_sim_time": use_sim_time},
                ],
            ),
        ]
    )
