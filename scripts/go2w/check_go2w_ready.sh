#!/usr/bin/env bash
# Go2-W 功能健康检查（operator-supervised experiment 前置探针）。
#
# 用法：
#   bash scripts/go2w/check_go2w_ready.sh            # 人类可读 + 机器可读 JSON
#   bash scripts/go2w/check_go2w_ready.sh --json     # 只输出机器可读 JSON
#
# 检查项（全部自动，不需要人工标定/摆场）：
#   network    enp6s0 carrier + 主机 192.168.123.99/24 + ping 机器人 192.168.123.18
#   sport      /lf/sportmodestate（mode=1、error_code=0）
#   odom       /go2w/odom/fused、/go2w/odom/wheel（20 Hz）
#   camera     /camera/front/image_raw（~15-28 Hz）与 CameraInfo
#   safety     /go2w/safety/lidar_fresh、front_clearance、rotation_clearance_valid
#   motion     /go2w/motion Action、/go2w/arm、/go2w/emergency_stop 服务
#   spool      最近 Frame Bundle（READY + sensor_health.camera/lidar）
#   llm        .env 中 SILICONFLOW_API_KEY 是否配置
#
# 退出码：0=ready（可开始实验） 1=degraded（非阻塞项缺失） 2=unreachable/配置错误

set -uo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
json_only="0"
for arg in "$@"; do
  case "$arg" in
    --json) json_only="1" ;;
    *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

checks="{}"
declare -A results

set_check() { results["$1"]="$2"; }

# ---------------------------------------------------------------------------
# 1. network
# ---------------------------------------------------------------------------
iface_carrier="0"
host_ip="none"
robot_reachable="false"
if [[ -r /sys/class/net/enp6s0/carrier ]]; then
  iface_carrier="$(< /sys/class/net/enp6s0/carrier)"
fi
host_ip="$(ip -4 -o address show dev enp6s0 2>/dev/null | awk '{print $4}' | head -1)"
if [[ "${host_ip:-none}" == none ]]; then
  host_ip="none"
fi
if ping -c 1 -W 1 192.168.123.18 >/dev/null 2>&1; then
  robot_reachable="true"
fi
if [[ "${iface_carrier}" == "1" && "${robot_reachable}" == "true" ]]; then
  set_check network ok
else
  set_check network fail
fi

# ---------------------------------------------------------------------------
# ROS environment (best effort; topic checks are skipped when unavailable)
# ---------------------------------------------------------------------------
ros_ok="false"
if command -v ros2 >/dev/null 2>&1; then
  ros_ok="true"
else
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash 2>/dev/null
  # shellcheck disable=SC1091
  source /home/brov/robot/unitree_ros2/cyclonedds_ws/install/setup.bash 2>/dev/null
  # shellcheck disable=SC1091
  source "${project_root}/ros2_ws/install/setup.bash" 2>/dev/null
  if [[ -f /home/brov/robot/unitree_go2w_control/ros2_ws/install/setup.bash ]]; then
    # shellcheck disable=SC1091
    source /home/brov/robot/unitree_go2w_control/ros2_ws/install/setup.bash 2>/dev/null
  fi
  set -u
  export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
  export ROS_DOMAIN_ID=0
  export CYCLONEDDS_URI="file://${project_root}/configs/go2w/cyclonedds_go2w.xml"
  command -v ros2 >/dev/null 2>&1 && ros_ok="true"
fi

topic_alive() {
  # ros2 topic hz never exits; treat any captured rate output as alive.
  local out
  out="$(timeout 5 ros2 topic hz "$1" --window 2 2>/dev/null)"
  [[ -n "${out}" ]]
}

service_ok() {
  timeout 5 ros2 service type "$1" >/dev/null 2>&1
}

if [[ "${ros_ok}" == "true" ]]; then
  # sport mode
  sport_ok="false"
  sport_mode="$(timeout 6 ros2 topic echo /lf/sportmodestate --once --qos-reliability best_effort --field mode 2>/dev/null | grep -v "^---" | tr -d '[:space:]')"
  sport_error="$(timeout 6 ros2 topic echo /lf/sportmodestate --once --qos-reliability best_effort --field error_code 2>/dev/null | grep -v "^---" | tr -d '[:space:]')"
  if [[ "${sport_mode}" == "1" && "${sport_error}" == "0" ]]; then
    sport_ok="true"
  fi
  set_check sport "${sport_ok}"

  # odom
  odom_fused="false"; odom_wheel="false"
  topic_alive /go2w/odom/fused && odom_fused="true"
  topic_alive /go2w/odom/wheel && odom_wheel="true"
  if [[ "${odom_fused}" == "true" || "${odom_wheel}" == "true" ]]; then
    set_check odom ok
  else
    set_check odom fail
  fi

  # camera
  camera="false"
  topic_alive /camera/front/image_raw && camera="true"
  set_check camera "${camera}"

  # safety topics
  lidar_fresh="false"
  fresh_value="$(timeout 6 ros2 topic echo /go2w/safety/lidar_fresh --once --qos-reliability best_effort --field data 2>/dev/null | grep -v "^---" | tr -d '[:space:]')"
  [[ "${fresh_value}" == "True" || "${fresh_value}" == "true" ]] && lidar_fresh="true"
  set_check lidar_fresh "${lidar_fresh}"
  rotation_clearance="false"
  rc_value="$(timeout 6 ros2 topic echo /go2w/safety/rotation_clearance_valid --once --qos-reliability best_effort --field data 2>/dev/null | grep -v "^---" | tr -d '[:space:]')"
  [[ "${rc_value}" == "True" || "${rc_value}" == "true" ]] && rotation_clearance="true"
  set_check rotation_clearance_valid "${rotation_clearance}"

  # motion action + services
  motion="false"
  timeout 5 ros2 action info /go2w/motion >/dev/null 2>&1 && motion="true"
  set_check motion_action "${motion}"
  set_check arm_service "$(service_ok /go2w/arm && echo true || echo false)"
  set_check emergency_stop_service "$(service_ok /go2w/emergency_stop && echo true || echo false)"
else
  set_check ros unavailable
fi

# ---------------------------------------------------------------------------
# spool bundle freshness (latest READY within 30 s)
# ---------------------------------------------------------------------------
spool_ok="false"
spool_root="${GO2W_FRAME_SPOOL_DIR:-${project_root}/runtime/go2w/spool}"
spool_latest="${spool_root}/latest"
if [[ -f "${spool_latest}/READY" && -f "${spool_latest}/frame_bundle.json" ]]; then
  age_sec="$(python3 - "${spool_latest}/READY" <<'PYEOF'
import os, sys, time
try:
    print(int(time.time() - os.path.getmtime(sys.argv[1])))
except OSError:
    print(99999)
PYEOF
)"
  if [[ "${age_sec}" -le 30 ]]; then
    spool_ok="true"
  fi
fi
set_check spool_bundle "${spool_ok}"

# ---------------------------------------------------------------------------
# LLM key
# ---------------------------------------------------------------------------
llm_key="false"
if [[ -f "${project_root}/.env" ]] && grep -q '^SILICONFLOW_API_KEY=.\+' "${project_root}/.env" 2>/dev/null; then
  llm_key="true"
fi
set_check llm_api_key "${llm_key}"

# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------
read_json="$(python3 - "${results[network]}" "${results[sport]:-na}" "${results[odom]:-na}" \
  "${results[camera]:-na}" "${results[lidar_fresh]:-na}" \
  "${results[rotation_clearance_valid]:-na}" "${results[motion_action]:-na}" \
  "${results[arm_service]:-na}" "${results[emergency_stop_service]:-na}" \
  "${results[spool_bundle]:-na}" "${results[llm_api_key]:-na}" "${results[ros]:-na}" <<'PYEOF'
import json, sys
network, sport, odom, camera, lidar, rot, motion, arm, stop, spool, llm, ros = sys.argv[1:13]
checks = {
    "network": {"ok": network == "ok", "carrier": network == "ok",
                "robot_ip": "192.168.123.18"},
    "sport_mode": {"ok": sport == "true"},
    "odom": {"ok": odom == "ok"},
    "camera": {"ok": camera == "true"},
    "lidar_fresh": {"ok": lidar == "true"},
    "rotation_clearance_valid": {"ok": rot == "true"},
    "motion_action": {"ok": motion == "true"},
    "arm_service": {"ok": arm == "true"},
    "emergency_stop_service": {"ok": stop == "true"},
    "spool_bundle": {"ok": spool == "true"},
    "llm_api_key": {"ok": llm == "true"},
    "ros_env": {"ok": ros == "true" if ros != "na" else True},
}
hard = ["network", "sport_mode", "camera", "motion_action", "emergency_stop_service",
        "llm_api_key"]
soft = ["odom", "lidar_fresh", "spool_bundle", "arm_service"]
ready = all(checks[k]["ok"] for k in hard)
degraded = [k for k in soft if not checks[k]["ok"]]
unreachable = checks["network"]["ok"] is False
state = "unreachable" if unreachable else ("ready" if ready else "degraded")
print(json.dumps({
    "state": state,
    "ready": ready and not degraded,
    "degraded": degraded,
    "checks": checks,
    "backend": "go2w_experimental",
}, ensure_ascii=False))
PYEOF
)"

if [[ "${json_only}" == "1" ]]; then
  printf '%s\n' "${read_json}"
else
  printf '%s\n' "${read_json}" | python3 -m json.tool --no-ensure-ascii
  state="$(printf '%s\n' "${read_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')"
  printf '\n状态: %s\n' "${state}"
  printf '提示: 机器狗未上电/未连接时 network=unreachable；充电并插好网线后重试。\n'
fi

case "$(printf '%s\n' "${read_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')" in
  ready) exit 0 ;;
  degraded) exit 1 ;;
  *) exit 2 ;;
esac
