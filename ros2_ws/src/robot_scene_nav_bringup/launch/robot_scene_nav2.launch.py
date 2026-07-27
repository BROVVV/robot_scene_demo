from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    params=LaunchConfiguration("params_file"); map_yaml=LaunchConfiguration("map")
    return LaunchDescription([
        DeclareLaunchArgument("map"), DeclareLaunchArgument("use_sim_time",default_value="false"),
        DeclareLaunchArgument("params_file",default_value=PathJoinSubstitution([FindPackageShare("robot_scene_nav_bringup"),"config","nav2_params_humble.yaml"])),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(PathJoinSubstitution([FindPackageShare("nav2_bringup"),"launch","bringup_launch.py"])),
            launch_arguments={"map":map_yaml,"params_file":params,"use_sim_time":LaunchConfiguration("use_sim_time")}.items())
    ])
