#!/usr/bin/env bash

_go2w_setup_fail() {
  printf 'go2w setup error: %s\n' "$1" >&2
  return 1 2>/dev/null || exit 1
}

_GO2W_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export GO2W_CONTROL_ROOT="$(cd -- "$_GO2W_SCRIPT_DIR/.." && pwd)"
export GO2W_UNITREE_ROOT="${GO2W_UNITREE_ROOT:-$HOME/unitree_ros2}"
export GO2W_ROBOT_IP="${GO2W_ROBOT_IP:-192.168.123.18}"
export GO2W_ROBOT_INTERFACE="$("$_GO2W_SCRIPT_DIR/detect_unitree_interface.sh")" || \
  _go2w_setup_fail "cannot resolve the robot interface"

[[ -f /opt/ros/humble/setup.bash ]] || _go2w_setup_fail "ROS 2 Humble missing"
[[ -f "$GO2W_UNITREE_ROOT/cyclonedds_ws/install/setup.bash" ]] || \
  _go2w_setup_fail "Unitree message workspace missing"
[[ -f "$GO2W_CONTROL_ROOT/ros2_ws/install/setup.bash" ]] || \
  _go2w_setup_fail "control workspace is not built"

set +u
# shellcheck source=/dev/null
source /opt/ros/humble/setup.bash
# shellcheck source=/dev/null
source "$GO2W_UNITREE_ROOT/cyclonedds_ws/install/setup.bash"
# shellcheck source=/dev/null
source "$GO2W_CONTROL_ROOT/ros2_ws/install/setup.bash"
set -u

export GO2W_CONTROL_PYTHON="${GO2W_CONTROL_PYTHON:-$GO2W_CONTROL_ROOT/.venv/bin/python}"
export PYTHONPATH="$GO2W_CONTROL_ROOT/vendor/unitree_sdk2_python${PYTHONPATH:+:$PYTHONPATH}"

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export ROS_LOCALHOST_ONLY=0
_GO2W_CYCLONE_FILE="/tmp/go2w_cyclonedds_${UID}.xml"
{
  printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>'
  printf '%s\n' '<CycloneDDS xmlns="https://cdds.io/config"><Domain id="any"><General><Interfaces>'
  printf '  <NetworkInterface name="%s" priority="default" multicast="default"/>\n' "$GO2W_ROBOT_INTERFACE"
  printf '%s\n' '</Interfaces><AllowMulticast>true</AllowMulticast></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>120</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>'
} >"$_GO2W_CYCLONE_FILE"
chmod 600 "$_GO2W_CYCLONE_FILE"
export CYCLONEDDS_URI="file://$_GO2W_CYCLONE_FILE"

printf 'Go2-W ROS 2 ready: interface=%s domain=%s rmw=%s\n' \
  "$GO2W_ROBOT_INTERFACE" "$ROS_DOMAIN_ID" "$RMW_IMPLEMENTATION"
unset _GO2W_SCRIPT_DIR _GO2W_CYCLONE_FILE
