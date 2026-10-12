#!/usr/bin/env bash
# 实体遥控器主动建图：定向停止。
#
# 计划书：docs/GO2W_遥控主动建图与周期物体落图_MVP实施计划_20260918.md §6.3
#
# 停止顺序：
#   1. 只读 WebUI
#   2. 地图快照写入器 webui_map_snapshot_writer.py
#   3. 语义侧车 remote_control_semantic_mapping_worker.py
#   4. 只有存在 ownership marker 时才停 plain_slam（说明是本模式启动的）
#      没有 marker 说明 SLAM 是外部已有进程 —— 只打印 "reused, left running"
#
# 先停机器狗：这个脚本**不会**停机器狗运动，也绝不下发任何运动/急停指令。
# 操作员必须先用 Unitree 实体遥控器把机器狗停稳。
#
# 明确不使用：pkill ros2 / killall python / 宽泛的 pkill -f python /
# 任何 Unitree 运动停止 API。每个 PID 都先读 /proc/<pid>/cmdline 校验。
#
# 选项：
#   --stop-slam-too   即使没有 marker 也一并停 plain_slam（默认不）
#   --keep-web        不停 WebUI
#   --debug           详细输出
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"

runtime_root="${project_root}/outputs/remote_control_mapping"
runtime_dir="${runtime_root}/runtime"
slam_owner_marker="${runtime_dir}/slam_started_by_remote_mapping"

stop_slam_too=0
stop_web=1
debug=0
while (( $# )); do
  case "$1" in
    --stop-slam-too) stop_slam_too=1; shift ;;
    --keep-web) stop_web=0; shift ;;
    --debug) debug=1; shift ;;
    *) printf 'ERROR: 未知选项: %s\n' "$1" >&2; exit 2 ;;
  esac
done

ok()   { printf '[OK]   %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1" >&2; }
info() { printf '[INFO] %s\n' "$1"; }

# 定向停止：先验明正身再杀。pid 文件里的数字可能是死的、也可能是被系统回收给
# 别的进程的 —— 不校验 cmdline 就可能杀掉无关进程。$2 是需要出现在 cmdline 里的
# 关键字（空串表示不校验，仅用于已知是 shell 包装的 pid 文件时）。
stop_pid_file() {
  local name="$1" pidfile="$2" expect="${3:-}"
  if [[ ! -f "${pidfile}" ]]; then
    info "${name}: 没有 pid 文件，跳过"
    return 0
  fi
  local pid
  pid="$(tr -cd '0-9' <"${pidfile}")"
  if [[ -z "${pid}" ]]; then
    warn "${name}: pid 文件内容不是数字，删除它"
    rm -f "${pidfile}"
    return 0
  fi
  if ! kill -0 "${pid}" 2>/dev/null; then
    info "${name}: pid ${pid} 已不存在，删掉 pid 文件"
    rm -f "${pidfile}"
    return 0
  fi
  if [[ ! -r "/proc/${pid}/cmdline" ]]; then
    warn "${name}: 读不到 /proc/${pid}/cmdline，不敢杀，保留 pid 文件"
    return 1
  fi
  local cmdline
  cmdline="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  if [[ -n "${expect}" && "${cmdline}" != *"${expect}"* ]]; then
    warn "${name}: pid ${pid} 的 cmdline 不含 '${expect}'，判为已回收给别的进程，不杀：${cmdline:0:120}"
    rm -f "${pidfile}"
    return 1
  fi

  printf 'Stopping %s (pid %s)\n' "${name}" "${pid}"
  kill -TERM "${pid}" 2>/dev/null || true
  local waited=0
  while (( waited < 10 )); do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 1; waited=$((waited + 1))
  done
  if kill -0 "${pid}" 2>/dev/null; then
    warn "${name}: SIGTERM 后 ${waited}s 仍在，发 SIGKILL"
    kill -KILL "${pid}" 2>/dev/null || true
    sleep 1
  fi
  rm -f "${pidfile}"
  if kill -0 "${pid}" 2>/dev/null; then
    warn "${name}: pid ${pid} 仍然存在"
    return 1
  fi
  ok "${name}: 已停止"
  return 0
}

printf '%s\n' '停止实体遥控器主动建图软件。'
printf '%s\n' '注意：本脚本不停止机器狗运动 —— 请确认已用实体遥控器把机器狗停稳。'
printf '\n'

# ---------------------------------------------------------------------------
# 1. 只读 WebUI
# ---------------------------------------------------------------------------
if [[ "${stop_web}" == "1" ]]; then
  # 预期 cmdline 里同时有 uvicorn 和 manual_web_demo，避免误杀别的 uvicorn。
  # SIGTERM 走 uvicorn 的优雅退出 → lifespan shutdown → DemoRuntime.stop()，
  # 它会收掉自己 spawn 的 manual_web_demo_ros_worker 子进程；只读模式下
  # DemoRuntime.stop() 不下发任何 stop/estop（计划书 §6.2 F / §9.4）。
  stop_pid_file 'WebUI(只读)' "${runtime_dir}/web.pid" 'manual_web_demo' || true
else
  info 'WebUI: --keep-web，跳过'
fi

# ---------------------------------------------------------------------------
# 2. 地图快照写入器
# ---------------------------------------------------------------------------
stop_pid_file '地图快照写入器' "${runtime_dir}/snapshot_writer.pid" 'webui_map_snapshot_writer' || true

# ---------------------------------------------------------------------------
# 3. 语义侧车
# ---------------------------------------------------------------------------
stop_pid_file '语义侧车' "${runtime_dir}/semantic_mapper.pid" 'remote_control_semantic_mapping_worker' || true

# ---------------------------------------------------------------------------
# 4. plain_slam（仅当本模式启动的）
# ---------------------------------------------------------------------------
if [[ "${stop_slam_too}" == "1" ]]; then
  info '--stop-slam-too：停止 plain_slam'
  bash "${script_dir}/stop_plain_slam_mapping.sh" || warn 'stop_plain_slam_mapping.sh 返回非零'
elif [[ -f "${slam_owner_marker}" ]]; then
  printf '%s\n' '本模式启动过 plain_slam（ownership marker 存在），一并停止。'
  bash "${script_dir}/stop_plain_slam_mapping.sh" || warn 'stop_plain_slam_mapping.sh 返回非零'
  rm -f "${slam_owner_marker}"
else
  info 'plain_slam: reused, left running（没有 ownership marker，说明是外部已有进程）'
fi

printf '\n%s\n' '实体遥控器主动建图软件已停止。'
printf '%s\n' '产物保留在 outputs/remote_control_mapping/（events.jsonl / status.json / 日志）。'
