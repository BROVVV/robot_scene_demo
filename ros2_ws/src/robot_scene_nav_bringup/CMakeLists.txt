cmake_minimum_required(VERSION 3.8)
project(robot_scene_nav_bringup)
find_package(ament_cmake REQUIRED)
install(DIRECTORY launch config behavior_trees DESTINATION share/${PROJECT_NAME})
install(PROGRAMS scripts/plain_slam_nav_frame_bridge.py
  DESTINATION lib/${PROJECT_NAME})
# 旧的地图 relay 保留在仓库里，但不再由任何 launch 启动：/map 现在由
# plain_slam_nav_frame_bridge.py 独家发布（计划书 §6.2）。
install(PROGRAMS scripts/plain_slam_map_relay.py
  DESTINATION lib/${PROJECT_NAME})
ament_package()
