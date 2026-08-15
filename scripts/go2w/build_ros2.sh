#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
UNITREE_SETUP="${GO2W_UNITREE_SETUP:-/home/brov/robot/unitree_ros2/cyclonedds_ws/install/setup.bash}"
if [[ -f "$UNITREE_SETUP" ]]; then
  # shellcheck disable=SC1090
  source "$UNITREE_SETUP"
fi
CONTROL_SETUP="${GO2W_CONTROL_SETUP:-/home/brov/robot/unitree_go2w_control/ros2_ws/install/setup.bash}"
if [[ -f "$CONTROL_SETUP" ]]; then
  # Provides the existing leased MotionCommand Action interface only.
  # Sourcing it does not start the lease holder or any control node.
  # shellcheck disable=SC1090
  source "$CONTROL_SETUP"
fi
set -u

cd "$PROJECT_ROOT/ros2_ws"
colcon build \
  --symlink-install \
  --event-handlers console_cohesion+ \
  --cmake-args \
    -DPython3_EXECUTABLE=/usr/bin/python3 \
    -DPYTHON_EXECUTABLE=/usr/bin/python3 \
    -DWITH_PTCS_USE=OFF

printf 'ROS 2 workspace built with system Python: %s\n' \
  "$PROJECT_ROOT/ros2_ws/install/setup.bash"
