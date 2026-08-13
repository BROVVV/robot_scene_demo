#!/usr/bin/env bash
set -eo pipefail

# Read-only live perception. No motion package, Sport request, lease holder,
# cmd_vel bridge, or Nav2 controller is launched by this script.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
session_id="live_$(date +%Y%m%d_%H%M%S)"
spool_root="${GO2W_FRAME_SPOOL_DIR:-${project_root}/runtime/go2w/spool}"
log_root="${project_root}/runtime/go2w/sessions/${session_id}"
pid_root="${project_root}/runtime/go2w/pids"
mkdir -p "${spool_root}" "${log_root}" "${pid_root}"

source /opt/ros/humble/setup.bash
source /home/brov/robot/unitree_ros2/cyclonedds_ws/install/setup.bash
source "${project_root}/ros2_ws/install/setup.bash"
set -u
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://${project_root}/configs/go2w/cyclonedds_go2w.xml"

if [[ ! -r /sys/class/net/enp6s0/carrier ]] \
  || [[ "$(< /sys/class/net/enp6s0/carrier)" != "1" ]]; then
  printf '%s\n' 'ERROR: enp6s0 has no Ethernet carrier; refusing a stale read-only session.' >&2
  exit 2
fi
if ! ip -4 -o address show dev enp6s0 \
  | awk '$4 ~ /^192[.]168[.]123[.][0-9]+\// { found=1 } END { exit !found }'; then
  printf '%s\n' 'ERROR: enp6s0 has no 192.168.123.0/24 host address.' >&2
  exit 2
fi

process_ids=()
start_read_only_node() {
  local name="$1"
  shift
  setsid "$@" >"${log_root}/${name}.log" 2>&1 &
  local process_id=$!
  process_ids+=("${process_id}")
  printf '%s\n' "${process_id}" >"${pid_root}/${name}.pid"
}

cleanup() {
  # ros2 run is a Python wrapper; its actual node can outlive the wrapper while
  # retaining the same owned process group. Address groups, verify them, and
  # use KILL only after bounded graceful INT/TERM windows.
  for process_id in "${process_ids[@]}"; do
    kill -INT -- "-${process_id}" 2>/dev/null || true
  done
  for _ in {1..25}; do
    groups_alive=0
    for process_id in "${process_ids[@]}"; do
      if kill -0 -- "-${process_id}" 2>/dev/null; then
        groups_alive=1
      fi
    done
    (( groups_alive == 0 )) && break
    sleep 0.1
  done
  for process_id in "${process_ids[@]}"; do
    kill -TERM -- "-${process_id}" 2>/dev/null || true
  done
  for _ in {1..25}; do
    groups_alive=0
    for process_id in "${process_ids[@]}"; do
      if kill -0 -- "-${process_id}" 2>/dev/null; then
        groups_alive=1
      fi
    done
    (( groups_alive == 0 )) && break
    sleep 0.1
  done
  for process_id in "${process_ids[@]}"; do
    if kill -0 -- "-${process_id}" 2>/dev/null; then
      kill -KILL -- "-${process_id}" 2>/dev/null || true
    fi
    wait "${process_id}" 2>/dev/null || true
  done
  for name in description camera time lidar fusion live_bridge; do
    rm -f "${pid_root}/${name}.pid"
  done
}
trap cleanup EXIT INT TERM

start_read_only_node description ros2 launch go2w_description official_sensor_frames.launch.py \
  "reference_file:=${project_root}/configs/go2w/official_reference.yaml"
start_read_only_node camera ros2 run go2w_camera_bridge camera_bridge --ros-args \
  -p source:=rpc \
  -p interface:=enp6s0 \
  -p "calibration_file:=${project_root}/configs/go2w/camera_intrinsics.yaml"
start_read_only_node time ros2 run go2w_sensor_time_bridge time_bridge --ros-args \
  -p "config_file:=${project_root}/configs/go2w/time_sync.yaml"
start_read_only_node lidar ros2 run go2w_lidar_preprocessor lidar_preprocessor --ros-args \
  -p "config_file:=${project_root}/configs/go2w/lidar_preprocess.yaml" \
  -p "geometry_file:=${project_root}/configs/go2w/official_reference.yaml"
start_read_only_node fusion ros2 run go2w_rgb_lidar_fusion fusion_node --ros-args \
  -p "fusion_config:=${project_root}/configs/go2w/rgb_lidar_fusion.yaml" \
  -p "camera_config:=${project_root}/configs/go2w/camera_intrinsics.yaml" \
  -p "extrinsics_config:=${project_root}/configs/go2w/sensor_extrinsics.yaml" \
  -p "cloud_topic:=/go2w/sensors/cloud"
start_read_only_node live_bridge ros2 run robot_scene_live_bridge live_bridge --ros-args \
  -p "spool_root:=${spool_root}" \
  -p "session_id:=${session_id}" \
  -p sensor_timeout_seconds:=0.3

printf 'Read-only Go2-W perception session: %s\n' "${session_id}"
printf 'Spool: %s\nLogs: %s\n' "${spool_root}" "${log_root}"
printf 'Motion/lease/Nav2 execution nodes: NOT STARTED\n'
wait
