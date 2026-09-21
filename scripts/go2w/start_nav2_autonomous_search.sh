#!/usr/bin/env bash
set -euo pipefail

# GO2-W Nav2 自主搜索唯一入口（计划书 §9）。
#
#   bash scripts/go2w/start_nav2_autonomous_search.sh
#       # 默认：只规划、不执行。坐标桥 + planner_server + bt_navigator 起来，
#       # 地图、TF、/go2w/nav/localization_valid 都在，规划链路可查；但控制器/
#       # 平滑器/碰撞监视器一个都不起，任何速度都不可能进入机器人。
#
#   bash scripts/go2w/start_nav2_autonomous_search.sh --enable-motion
#       # 现场验收通过后才用。即便传了它，真正的运动仍要再过
#       # go2w_cmd_vel_bridge 的九道闸门（运动授权/急停/租约/雷达/定位/…）。
#
# 只有这一个开关。没有它一律只规划；不接受环境变量或第二个隐藏参数绕过。
#
# 本脚本不重复实现已有的启动逻辑：感知、plain_slam、运动控制由既有脚本负责，
# WebUI 与 ROS 侧车交给 start_autonomous_search_web.sh。这里只补齐 Nav2 这
# 一层，并负责按顺序等齐 4 项就绪证据。

ENABLE_MOTION=0
for arg in "$@"; do
  case "$arg" in
    --enable-motion) ENABLE_MOTION=1 ;;
    -h|--help)
      sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$arg" >&2
      printf '只接受 --enable-motion（默认只规划）。\n' >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
runtime_root="${project_root}/outputs/autonomous_search/runtime"
log_root="${project_root}/outputs/autonomous_search/logs"
mkdir -p "${runtime_root}" "${log_root}" "${runtime_root}/nav2"

nav2_pidfile="${runtime_root}/nav2_bringup.pid"
bridge_pidfile="${runtime_root}/cmd_vel_bridge.pid"
arbiter_pidfile="${runtime_root}/control_arbiter.pid"

# ---- 0. 参数与前置检查 ---------------------------------------------------- #
if [[ "$ENABLE_MOTION" == 0 ]]; then
  printf '%s\n' '=============================================================='
  printf '%s\n' ' GO2-W Nav2 自主搜索：只规划、不执行'
  printf '%s\n' ' 未传 --enable-motion。控制器/平滑器/碰撞监视器不会启动，'
  printf '%s\n' ' 不会有速度进入机器人。'
  printf '%s\n' '=============================================================='
else
  printf '%s\n' '=============================================================='
  printf '%s\n' ' GO2-W Nav2 自主搜索：执行链路已启用（--enable-motion）'
  printf '%s\n' ' 现场验收尚未由本项目代为完成；运动仍由 cmd_vel bridge 的'
  printf '%s\n' ' 九道闸门把关。确认场地无人、急停在手后再继续。'
  printf '%s\n' '=============================================================='

  # 计划书 §10：执行授权是 configs/go2w/navigation_gate.yaml 里的
  # nav2_allow_execute，由用户在现场完成 §13.3/§13.4 验收后**手动**改为
  # true。光传 --enable-motion 不足以开执行——否则那个开关只是纸面约定，
  # 谁都能用一条命令行绕过去。这里读它，就是为了让 §10 那句话真的有效。
  allow_execute="$(sed -n 's/^[[:space:]]*nav2_allow_execute:[[:space:]]*\([A-Za-z]*\).*/\1/p' \
    "${project_root}/configs/go2w/navigation_gate.yaml" | head -n 1)"
  if [[ "${allow_execute}" != "true" ]]; then
    printf '%s\n' 'ERROR: 执行链路未获授权（计划书 §10）。' >&2
    printf '%s\n' '完成 §13.3/§13.4 现场验收后，由用户手动把 configs/go2w/navigation_gate.yaml' >&2
    printf '%s\n' '的 nav2_allow_execute 改为 true，再传 --enable-motion。' >&2
    printf '%s\n' '代码修改 AI 不得代替用户做这一步。' >&2
    exit 3
  fi

  # 计划书 §9：--enable-motion 仍须过「现有的人工运动确认」。这条确认必须
  # 发生在**拉起任何东西之前**——否则在提示符上按 Ctrl-C 会留下一台已经
  # execution_enabled:=true 的 bridge 和一套起着的 Nav2 控制器。
  # 与 start_autonomous_search_web.sh / start_manual_web_demo.sh 用同一个
  # 口令，不另立第二套。
  if [[ "${GO2W_AREA_CLEARED:-}" != "I_HAVE_CLEARED_THE_AREA" ]]; then
    printf '%s\n' '执行链路需要现场清场确认。' >&2
    printf '%s\n' '清场后可以：export GO2W_AREA_CLEARED=I_HAVE_CLEARED_THE_AREA' >&2
    if [[ ! -t 0 ]]; then
      printf '%s\n' '当前不是交互终端，无法口头确认，退出。' >&2
      exit 2
    fi
    read -r -p 'Type I_CONFIRM to authorize autonomous motion: ' answer
    if [[ "$answer" != "I_CONFIRM" ]]; then
      printf '%s\n' '未确认，退出。' >&2
      exit 2
    fi
  fi
fi

source "${script_dir}/setup_environment.sh"

# 不要再 source ros2_ws/install/setup.bash —— setup_environment.sh 已经装好了 ROS
# 与整个工作区，而且它是**故意绕开 setup.bash** 的：这个工作区的 setup.bash 里记着
# 两个链式前缀（/opt/ros/noetic 与 /opt/ros/humble），直接 source 会把 ROS 1 的
# dist-packages 顶到 PYTHONPATH 最前面，之后任何 rclpy 节点
# `from nav_msgs.msg import Odometry` 拿到的是 ROS 1 的消息类。
# setup_environment.sh 的 source_overlay_safely() 检测到这种「记录了别的 distro」
# 的 setup.bash 时会改 source local_setup.bash，正是为此。
# 顺带：那一遍裸 source 还没被 `set +u` 包住，会在 colcon 那段无引号保护的
# `[ -n "$COLCON_TRACE" ]` 上以 "unbound variable" 直接终止本脚本——症状看着像
# 缺环境变量，真因是这里多 source 了一遍。

owns_pidfile() {
  # 只认本项目自己起的进程：pidfile + cmdline 双重核对，避免误杀。
  local pidfile="$1" needle="$2" pid cmd
  [[ -f "$pidfile" ]] || return 1
  pid="$(<"$pidfile")"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == *"$needle"* ]]
}

stop_owned() {
  # pidfile 里存的是 setsid 出来的会话首进程，它的 pgid 就等于 pid，所以要对着
  # **整个进程组**发信号，不能只发那一个 pid。
  # `ros2 launch` 收到 SIGINT 后只是尽力把信号转发给子节点；只要有子节点没在下面
  # 这 10 秒里退出，本函数就会 SIGKILL 掉父进程，而那些子节点会被 reparent 到
  # pid 1 继续活着。上一轮留下的坐标桥和 bt_navigator 于是和新一轮的同名节点并存：
  # `/map` 出现两个发布者（§13.2 第 2 项直接失败），lifecycle_manager 的
  # change_state 也会打到旧的那个上——症状是新的 planner_server 永远停在
  # unconfigured，bringup 日志里连一句报错都没有。
  local pidfile="$1" needle="$2" pid
  if owns_pidfile "$pidfile" "$needle"; then
    pid="$(<"$pidfile")"
    printf 'Stopping previous project-owned %s (pid %s, pgid %s) ...\n' "$needle" "$pid" "$pid" >&2
    kill -INT -- "-${pid}" 2>/dev/null || kill -INT "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 -- "-${pid}" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

topic_publisher_count() {
  local topic="$1"
  timeout 5 ros2 topic info "$topic" -v 2>/dev/null \
    | awk '/Publisher count:/ { print $3; exit }'
}

wait_for() {
  local label="$1" timeout_s="$2"
  shift 2
  local deadline=$((SECONDS + timeout_s))
  while (( SECONDS < deadline )); do
    if "$@"; then
      printf '[OK] %s\n' "$label"
      return 0
    fi
    sleep 0.5
  done
  printf '[FAIL] %s（等待 %ss 超时）\n' "$label" "$timeout_s" >&2
  return 1
}

# ---- 1. 感知 / 激光雷达 / Plain SLAM 必须在跑 ----------------------------- #
# 不在这里重复启动：既有脚本已经各自做了幂等检查与就绪判定，重新实现一遍
# 只会多出第二套真相。
missing=()
if [[ "$(topic_publisher_count /camera/front/image_raw)" != "1" ]]; then
  missing+=("/camera/front/image_raw（bash scripts/go2w/start_live_perception.sh）")
fi
# 默认必须与 Nav2 消费的那条话题逐字一致（config/nav2_params_humble.yaml 的
# obstacle_layer 与 collision_monitor.yaml 都是 /go2w/lidar/scan，由
# start_live_perception.sh 起的 lidar_preprocessor 发布）。写成 /scan 会让前置
# 检查永远失败——那条话题全仓没有发布者。
if [[ "$(topic_publisher_count "${GO2W_SCAN_TOPIC:-/go2w/lidar/scan}")" != "1" ]]; then
  missing+=("${GO2W_SCAN_TOPIC:-/go2w/lidar/scan}（bash scripts/go2w/start_live_perception.sh）")
fi
if [[ "$(topic_publisher_count /go2w/slam/aligned_scan)" != "1" ]]; then
  missing+=("/go2w/slam/aligned_scan（bash scripts/go2w/start_plain_slam_mapping.sh）")
fi
if [[ "${#missing[@]}" -gt 0 ]]; then
  printf '%s\n' 'ERROR: 以下上游话题没有唯一发布者，先启动它们：' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  printf '%s\n' '本脚本不代管感知/SLAM，避免和现场已有的启动流程打架。' >&2
  exit 2
fi

# ---- 2. Nav2 bringup ------------------------------------------------------ #
stop_owned "$nav2_pidfile" "robot_scene_nav2.launch.py"
if [[ "$ENABLE_MOTION" == 1 ]]; then
  execution_enabled=true
else
  execution_enabled=false
fi
printf 'Starting Nav2 bringup (execution_enabled:=%s) ...\n' "$execution_enabled"
setsid bash -c "exec ros2 launch robot_scene_nav_bringup robot_scene_nav2.launch.py execution_enabled:=${execution_enabled}" \
  >"${log_root}/nav2_bringup.log" 2>&1 &
printf '%s\n' "$!" >"$nav2_pidfile"

# ---- 3. 控制仲裁与 cmd_vel bridge ---------------------------------------- #
# 即便只规划也要起：它们自身 fail-closed，且「速度链只有一个预期发布者」
# 这条只规划证据要靠它们在图上出现才能核对。
stop_owned "$arbiter_pidfile" "go2w_control_arbiter"
stop_owned "$bridge_pidfile" "go2w_cmd_vel_bridge"
setsid bash -c 'exec ros2 run go2w_control_arbiter arbiter' \
  >"${log_root}/control_arbiter.log" 2>&1 &
printf '%s\n' "$!" >"$arbiter_pidfile"
if [[ "$ENABLE_MOTION" == 1 ]]; then
  setsid bash -c 'exec ros2 run go2w_cmd_vel_bridge bridge --ros-args -p execution_enabled:=true' \
    >"${log_root}/cmd_vel_bridge.log" 2>&1 &
else
  setsid bash -c 'exec ros2 run go2w_cmd_vel_bridge bridge' \
    >"${log_root}/cmd_vel_bridge.log" 2>&1 &
fi
printf '%s\n' "$!" >"$bridge_pidfile"

# ---- 4. 等齐就绪证据 ------------------------------------------------------ #
# 任何一项没到就不启动搜索：宁可让人看见「哪一项没到」，也不要让搜索跑在
# 半个链路上然后把失败推给下游。
ready_ok=1
wait_for "/map 有唯一发布者" 60 \
  bash -c '[[ "$(timeout 5 ros2 topic info /map -v 2>/dev/null | awk "/Publisher count:/ { print \$3; exit }")" == "1" ]]' || ready_ok=0
wait_for "/go2w/nav/localization_valid" 60 \
  bash -c 'timeout 5 ros2 topic list 2>/dev/null | grep -qx /go2w/nav/localization_valid' || ready_ok=0
# TF 连通必须在同一个 rclpy 进程里查，否则每个 ros2 CLI 调用都是一次新的发现。
wait_for "map -> odom_fused -> base_link TF 连通" 60 \
  /usr/bin/python3 -c '
import sys
try:
    import rclpy
    from rclpy.duration import Duration
    from rclpy.node import Node
    from tf2_ros import Buffer, TransformListener
except Exception:
    sys.exit(1)
rclpy.init()
node = Node("nav2_startup_tf_probe")
buffer = Buffer()
TransformListener(buffer, node)
ok = False
for _ in range(40):
    rclpy.spin_once(node, timeout_sec=0.1)
    try:
        buffer.lookup_transform("map", "base_link", rclpy.time.Time(), Duration(seconds=0.2))
        ok = True
        break
    except Exception:
        continue
node.destroy_node()
rclpy.shutdown()
sys.exit(0 if ok else 1)
' || ready_ok=0
# 必须逐字比较，不能 `grep -q active`：`ros2 lifecycle get` 在没起来时返回的是
# `inactive [2]`，而 grep 的**子串**匹配会把它当成通过。这个假绿曾经把一次真实的
# bringup 死锁（bt_navigator 卡在 configure，planner_server 停在 inactive）放了过去，
# 直到有人手工去看代价地图才发现整张图全 0。
wait_for "planner_server 生命周期 active" 60 \
  bash -c '[[ "$(timeout 5 ros2 lifecycle get /planner_server 2>/dev/null | awk "{ print \$1 }")" == "active" ]]' || ready_ok=0

if [[ "$ready_ok" != 1 ]]; then
  printf 'ERROR: 就绪证据不齐，未启动搜索。日志：%s/nav2_bringup.log\n' "${log_root}" >&2
  exit 3
fi

# ---- 5. WebUI + 搜索 worker（后端固定 nav2） ------------------------------ #
# 复用旧入口，避免维护第二套 WebUI 启动流程；它负责启 worker，而 worker 的
# 默认后端已经是 nav2（见 autonomous_search_worker.build_argv）。
web_args=()
if [[ "$ENABLE_MOTION" == 1 ]]; then
  web_args+=(--enable-autonomous-motion)
fi
printf 'Starting WebUI + search worker (backend=nav2) ...\n'
exec bash "${script_dir}/start_autonomous_search_web.sh" "${web_args[@]}"
