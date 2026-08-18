#!/usr/bin/env bash
# Start the D435 RGB-D ROS2 bridge + static TF + RTAB-Map SLAM stack.
#
# Usage:
#   bash scripts/go2w/start_rgbd_spatial_stack.sh start
#   bash scripts/go2w/start_rgbd_spatial_stack.sh stop
#
# This stack is used by `run_semantic_exploration.py --rtabmap`.

set -uo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
runtime_dir="${project_root}/runtime/go2w"
mkdir -p "${runtime_dir}"

source "${script_dir}/setup_environment.sh"

D435_BASE_URL="${D435_BASE_URL:-http://192.168.123.18:8080}"
BRIDGE_PID="${runtime_dir}/d435_rgbd_bridge.pid"
TF1_PID="${runtime_dir}/tf_odom_base.pid"
TF2_PID="${runtime_dir}/tf_base_camera.pid"
RTAB_PID="${runtime_dir}/rtabmap.pid"

start_bridge() {
  if [[ -f "$BRIDGE_PID" ]] && kill -0 "$(<"$BRIDGE_PID")" 2>/dev/null; then
    echo "bridge already running pid $(<"$BRIDGE_PID")"
    return
  fi
  setsid /usr/bin/python3 "${script_dir}/realsense_rgbd_bridge.py" \
    --base-url "$D435_BASE_URL" --rate 10 \
    > "${runtime_dir}/d435_rgbd_bridge.log" 2>&1 &
  echo $! > "$BRIDGE_PID"
  echo "bridge started pid $(<"$BRIDGE_PID")"
}

start_tf() {
  if [[ ! -f "$TF1_PID" ]] || ! kill -0 "$(<"$TF1_PID")" 2>/dev/null; then
    setsid ros2 run tf2_ros static_transform_publisher 0 0 0 0 0 0 odom_fused base_link \
      > "${runtime_dir}/tf_odom_base.log" 2>&1 &
    echo $! > "$TF1_PID"
  fi
  if [[ ! -f "$TF2_PID" ]] || ! kill -0 "$(<"$TF2_PID")" 2>/dev/null; then
    setsid ros2 run tf2_ros static_transform_publisher 0.10 0 0.30 0 0 0 base_link d435_color_optical_frame \
      > "${runtime_dir}/tf_base_camera.log" 2>&1 &
    echo $! > "$TF2_PID"
  fi
  echo "static TF started"
}

start_rtabmap() {
  if [[ -f "$RTAB_PID" ]] && kill -0 "$(<"$RTAB_PID")" 2>/dev/null; then
    echo "rtabmap already running pid $(<"$RTAB_PID")"
    return
  fi
  setsid ros2 run rtabmap_slam rtabmap --ros-args \
    -r __ns:=/rtabmap \
    -r rgb/image:=/go2w/d435/color/image_raw \
    -r depth/image:=/go2w/d435/depth/image_rect_raw \
    -r rgb/camera_info:=/go2w/d435/color/camera_info \
    -r odom:=/go2w/odom/fused \
    > "${runtime_dir}/rtabmap.log" 2>&1 &
  echo $! > "$RTAB_PID"
  echo "rtabmap started pid $(<"$RTAB_PID")"
}

stop_stack() {
  for pidfile in "$RTAB_PID" "$TF2_PID" "$TF1_PID" "$BRIDGE_PID"; do
    if [[ -f "$pidfile" ]]; then
      pid="$(<"$pidfile")"
      kill "$pid" 2>/dev/null || true
      rm -f "$pidfile"
    fi
  done
  echo "RGB-D spatial stack stopped"
}

case "${1:-start}" in
  start)
    start_bridge
    start_tf
    start_rtabmap
    echo "waiting for /rtabmap/map ..."
    for _ in $(seq 1 30); do
      if ros2 topic list 2>/dev/null | grep -q '^/rtabmap/map$'; then
        echo "RTAB-Map ready"
        exit 0
      fi
      sleep 1
    done
    echo "WARNING: /rtabmap/map not seen within 30s; check runtime/go2w/*.log"
    ;;
  stop)
    stop_stack
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
