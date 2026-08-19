#!/usr/bin/env bash

# Source this file from a ROS 2 shell. It intentionally does not source the
# Conda application environment into ROS workers.
set -euo pipefail

GO2W_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GO2W_PROJECT_ROOT="$(cd -- "$GO2W_SCRIPT_DIR/../.." && pwd)"
GO2W_ROS_SETUP="${GO2W_ROS_SETUP:-/opt/ros/humble/setup.bash}"
GO2W_WORKSPACE_SETUP="${GO2W_WORKSPACE_SETUP:-$GO2W_PROJECT_ROOT/ros2_ws/install/setup.bash}"
GO2W_CONTROL_ROOT="${GO2W_CONTROL_ROOT:-$GO2W_PROJECT_ROOT/unitree_go2w_control}"
GO2W_UNITREE_ROOT="${GO2W_UNITREE_ROOT:-$HOME/unitree_ros2}"
GO2W_CONTROL_SETUP="${GO2W_CONTROL_SETUP:-$GO2W_CONTROL_ROOT/ros2_ws/install/setup.bash}"
GO2W_UNITREE_SETUP="${GO2W_UNITREE_SETUP:-$GO2W_UNITREE_ROOT/cyclonedds_ws/install/setup.bash}"

if [[ ! -f "$GO2W_ROS_SETUP" ]]; then
  printf 'ERROR: ROS 2 setup not found: %s\n' "$GO2W_ROS_SETUP" >&2
  return 2 2>/dev/null || exit 2
fi

set +u
# shellcheck disable=SC1090
source "$GO2W_ROS_SETUP"
set -u
if [[ -f "$GO2W_UNITREE_SETUP" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$GO2W_UNITREE_SETUP"
  set -u
fi
if [[ -f "$GO2W_WORKSPACE_SETUP" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$GO2W_WORKSPACE_SETUP"
  set -u
fi
if [[ -f "$GO2W_CONTROL_SETUP" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "$GO2W_CONTROL_SETUP"
  set -u
fi

export GO2W_CONTROL_ROOT GO2W_UNITREE_ROOT GO2W_CONTROL_SETUP GO2W_UNITREE_SETUP
export GO2W_CONTROL_PYTHON="${GO2W_CONTROL_PYTHON:-$GO2W_CONTROL_ROOT/.venv/bin/python}"
if [[ -d "$GO2W_CONTROL_ROOT/vendor/unitree_sdk2_python" ]]; then
  export PYTHONPATH="$GO2W_CONTROL_ROOT/vendor/unitree_sdk2_python${PYTHONPATH:+:$PYTHONPATH}"
fi

export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://$GO2W_PROJECT_ROOT/configs/go2w/cyclonedds_go2w.xml}"
export GO2W_PROJECT_ROOT
export GO2W_RUNTIME_ROOT="${GO2W_RUNTIME_ROOT:-$GO2W_PROJECT_ROOT/runtime/go2w}"

if [[ "${CONDA_PREFIX:-}" == *go2_robot_scene_demo* ]]; then
  printf '%s\n' \
    'WARNING: Conda is active. ROS workers still use /usr/bin/python3.' >&2
fi

printf 'GO2-W ROS environment ready: distro=%s rmw=%s domain=%s\n' \
  "${ROS_DISTRO:-unknown}" "$RMW_IMPLEMENTATION" "$ROS_DOMAIN_ID"
