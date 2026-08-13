from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    params=LaunchConfiguration("params_file"); map_yaml=LaunchConfiguration("map")
    execution_enabled=LaunchConfiguration("execution_enabled")
    return LaunchDescription([
        DeclareLaunchArgument("map"), DeclareLaunchArgument("use_sim_time",default_value="false"),
        DeclareLaunchArgument("execution_enabled", default_value="false"),
        DeclareLaunchArgument("params_file",default_value=PathJoinSubstitution([FindPackageShare("robot_scene_nav_bringup"),"config","nav2_params_humble.yaml"])),
        LogInfo(
            condition=UnlessCondition(execution_enabled),
            msg="Go2-W Nav2 execute bringup blocked: execution_enabled is false. Use the plan-only launch for planning.",
        ),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(PathJoinSubstitution([FindPackageShare("nav2_bringup"),"launch","bringup_launch.py"])),
            condition=IfCondition(execution_enabled),
            launch_arguments={"map":map_yaml,"params_file":params,"use_sim_time":LaunchConfiguration("use_sim_time")}.items()),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(PathJoinSubstitution([FindPackageShare("robot_scene_nav_bringup"),"launch","collision_monitor.launch.py"])),
            condition=IfCondition(execution_enabled),
        ),
    ])
