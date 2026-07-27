#!/usr/bin/env bash
set -euo pipefail
. /etc/os-release
if [[ "${VERSION_ID:-}" != "22.04" ]]; then
  echo "仅支持 Ubuntu 22.04 + ROS2 Humble，当前 ${VERSION_ID:-unknown}" >&2
  exit 2
fi
if [[ -n "${ROS_DISTRO:-}" && "$ROS_DISTRO" != "humble" ]]; then
  echo "当前 ROS_DISTRO=$ROS_DISTRO，不会安装其他版本。" >&2
  exit 2
fi
sudo apt update
sudo apt install -y ros-humble-navigation2 ros-humble-nav2-bringup \
  ros-humble-nav2-simple-commander ros-humble-nav2-collision-monitor \
  ros-humble-nav2-velocity-smoother ros-humble-tf2-ros \
  ros-humble-tf-transformations
