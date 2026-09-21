#!/usr/bin/env bash
# 只把整改计划涉及的文件同步到机器狗，并在远端保留定向备份。
#
#   bash scripts/go2w/deploy_plan_to_robot.sh --dry-run   # 只看会改哪些文件
#   bash scripts/go2w/deploy_plan_to_robot.sh             # 真正同步
#
# 覆盖两份计划：20260901 的「对象拓扑 / 固定世界三维建图」，以及
# docs/GO2W_Nav2自主搜索单链路改造计划.md 的「Nav2 单链路」。后者新增的
# `nav2_search_backend.py`、`navigation_gate.py`、`corridor_analyzer.py` 和
# 整个 `robot_scene_nav_bringup` 必须一起走，否则会装出一个自相矛盾的树——
# 例如 `backend_factory.py`（老清单里就有）会 `import nav2_search_backend`，
# 而那个模块不在老清单里，机器狗上生产搜索入口直接 ImportError。
#
# 不同步 build/ install/ outputs/ .venv/ data/，不动机器狗上的运动控制固件、
# 标定结果和历史运行产物。被覆盖的旧文件原样存进 .deploy_backup/<时间戳>/。
#
# 注意 rsync 不带 --delete：本地删掉的文件（如
# robot_scene_nav2_plan_only.launch.py）在机器狗上不会被删，需要人工清理。
set -euo pipefail

robot_host="${GO2W_ROBOT_HOST:-192.168.123.18}"
robot_user="${GO2W_ROBOT_USER:-unitree}"
robot_root="${GO2W_ROBOT_ROOT:-/home/unitree/robotscene}"
export SSHPASS="${GO2W_ROBOT_PASSWORD:-123}"

dry_run=0
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=1
elif [[ -n "${1:-}" ]]; then
  printf 'ERROR: unknown option: %s\n' "$1" >&2
  exit 2
fi

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${project_root}"

# 计划书涉及的文件（固定世界三维建图 + 对象拓扑 + 转向分段 + 搜索预算）。
files=(
  app/config.py
  app/detectors/siliconflow_vision_worker.py
  app/live_robot/autonomous_explorer.py
  app/live_robot/explorer_search_adapter.py
  app/live_robot/search_state_store.py
  app/live_robot/semantic_observer.py
  app/manual_web_demo/search_errors.py
  app/manual_web_demo/search_executor.py
  app/manual_web_demo/search_models.py
  app/manual_web_demo/search_routes.py
  app/manual_web_demo/search_session_service.py
  app/manual_web_demo/slam_map_snapshot.py
  app/manual_web_demo/web_server.py
  app/manual_web_demo/static/search_map.js
  app/manual_web_demo/static/search_ui.js
  app/manual_web_demo/static/slam_map_3d.js
  app/manual_web_demo/static/style.css
  app/manual_web_demo/templates/index.html
  app/navigation/backend_factory.py
  app/navigation/approach_controller.py
  app/navigation/candidate_goal_generator.py
  app/navigation/clearance_map.py
  app/navigation/decision_models.py
  app/navigation/exploration_config.py
  app/navigation/go2w_experimental_backend.py
  app/navigation/models.py
  app/navigation/recovery_manager.py
  app/navigation/robot_backend.py
  app/navigation/search_policy_config.py
  app/navigation/semantic_goal_localizer.py
  app/navigation/safety_models.py
  app/navigation/stuck_detector.py
  app/navigation/target_pose_generator.py
  app/navigation/topology_route_executor.py
  app/navigation/view_quality.py
  app/navigation/viewpoint_models.py
  app/navigation/viewpoint_planner.py
  app/perception/target_state.py
  app/prompts.py
  app/reasoning/embodiment_context.py
  app/reasoning/llm_situated_search_reasoner.py
  app/schemas.py
  app/spatial/geometric_relation_extractor.py
  app/spatial/place_graph.py
  app/spatial/pointcloud_web_codec.py
  app/spatial/semantic_navigation_graph.py
  app/video/video_navigation_topology_builder.py
  app/spatial/slam_web_map_state.py
  app/spatial/spatial_pose_validator.py
  configs/exploration/default.yaml
  configs/go2w/autonomous_search_web.yaml
  configs/go2w/cyclonedds_foxy_loopback.xml
  configs/go2w/cyclonedds_go2w.xml
  configs/go2w/rgbd_spatial_exploration.yaml
  configs/go2w/safety.yaml
  configs/go2w/search_policy.yaml
  pytest.ini
  ros2_ws/src/go2w_plain_slam_bridge/CMakeLists.txt
  ros2_ws/src/go2w_plain_slam_bridge/scripts/imu_fallback_adapter.py
  ros2_ws/src/go2w_plain_slam_bridge/scripts/imu_timestamp_aligner.py
  scripts/go2w/analyze_lio_motion_alignment.py
  scripts/go2w/analyze_search_run.py
  scripts/go2w/autonomous_search_worker.py
  scripts/go2w/check_go2w_ready.sh
  scripts/go2w/plain_slam_web_bridge.py
  scripts/go2w/plain_slam_web_cloud_relay.py
  scripts/go2w/record_plain_slam_debug_bag.sh
  scripts/go2w/reset_plain_slam_session.sh
  scripts/go2w/robot_camera_http_capture.py
  scripts/go2w/run_autonomous_loop.py
  scripts/go2w/run_semantic_exploration.py
  scripts/go2w/setup_environment.sh
  scripts/go2w/start_autonomous_search_web.sh
  scripts/go2w/start_plain_slam_mapping.sh
  scripts/go2w/start_semantic_exploration.sh
  scripts/go2w/start_vlm_daemon.sh
  scripts/go2w/stop_manual_web_demo.sh
  scripts/go2w/stop_plain_slam_mapping.sh
  tests/test_autonomous_explorer.py
  tests/test_autonomous_explorer_target_approach.py
  tests/test_clearance_map.py
  tests/test_frontier_viewpoint_candidates.py
  tests/test_go2w_experimental_backend.py
  tests/test_go2w_clearance_viewpoint_e2e.py
  tests/test_llm_embodiment_context.py
  tests/test_local_safety_snapshot.py
  tests/test_plain_slam_webui_3d.py
  tests/test_plain_slam_session_reset_path.py
  tests/test_regression_search_20260901_211756.py
  tests/test_search_state_store_spatial.py
  tests/test_search_exhaustion_vs_stuck.py
  tests/test_recovery_primitive_contract.py
  tests/test_search_session_service.py
  tests/test_semantic_goal_localizer_spatial_quality.py
  tests/test_spatial_pose_validator.py
  tests/test_stuck_detector.py
  tests/test_target_approach_controller.py
  tests/test_view_quality.py
  tests/test_viewpoint_planner.py
  tests/test_webui_object_topology_contract.js
  tests/test_webui_object_topology_contract.py
  GO2W_自主搜索_对象拓扑_固定世界三维建图_整改计划_20260901.md

  # --- docs/GO2W_Nav2自主搜索单链路改造计划.md（§11 新增与修改）-----------
  # 该计划 git diff c4412f4..HEAD 共 59 个文件；上面老清单只覆盖到 22 个，
  # 缺的 37 个（除本地已删除的 nav2_plan_only launch 外）全部列在这里。
  README.md
  docs/GO2W_AUTONOMOUS_SEARCH_WEBUI.md
  docs/GO2W_Nav2自主搜索单链路改造计划.md
  app/live_robot/experiment_readiness.py
  app/live_robot/navigation_gate.py
  app/navigation/corridor_analyzer.py
  app/navigation/local_goal_executor.py
  app/navigation/nav2_search_backend.py
  configs/go2w/control.yaml
  configs/go2w/high_level_experiment.yaml
  configs/go2w/navigation_gate.yaml
  ros2_ws/src/go2w_cmd_vel_bridge/go2w_cmd_vel_bridge/bridge_core.py
  ros2_ws/src/go2w_cmd_vel_bridge/go2w_cmd_vel_bridge/bridge_node.py
  ros2_ws/src/go2w_cmd_vel_bridge/test/test_bridge_core.py
  ros2_ws/src/robot_scene_nav_bringup/CMakeLists.txt
  ros2_ws/src/robot_scene_nav_bringup/package.xml
  ros2_ws/src/robot_scene_nav_bringup/config/collision_monitor.yaml
  ros2_ws/src/robot_scene_nav_bringup/config/nav2_params_humble.yaml
  ros2_ws/src/robot_scene_nav_bringup/config/velocity_smoother.yaml
  ros2_ws/src/robot_scene_nav_bringup/launch/collision_monitor.launch.py
  ros2_ws/src/robot_scene_nav_bringup/launch/robot_scene_nav2.launch.py
  ros2_ws/src/robot_scene_nav_bringup/scripts/plain_slam_nav_frame_bridge.py
  scripts/go2w/cmd_vel_gate.py
  scripts/go2w/start_nav2_autonomous_search.sh
  scripts/nav2_bridge_worker.py
  tests/test_autonomous_search_web_routes.py
  tests/test_autonomous_search_websocket.py
  tests/test_cmd_vel_gate_wiring.py
  tests/test_corridor_analyzer.py
  tests/test_experiment_readiness.py
  tests/test_live_exploration_graph.py
  tests/test_manual_web_demo_config.py
  tests/test_nav2_autonomous_search_contract.py
  tests/test_nav2_search_backend.py
  tests/test_plain_slam_nav_frame_bridge.py
  tests/test_search_session_recovery.py
  tests/test_search_state_store.py
)

missing=()
for path in "${files[@]}"; do
  [[ -e "${path}" ]] || missing+=("${path}")
done
if (( ${#missing[@]} )); then
  printf 'ERROR: 本地缺少这些文件，先确认分支正确：\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 2
fi

ssh_opts=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=6 -o LogLevel=ERROR)
remote() { sshpass -e ssh "${ssh_opts[@]}" "${robot_user}@${robot_host}" "$@"; }

if ! remote true 2>/dev/null; then
  printf 'ERROR: 无法登录 %s@%s（先确认网线/供电，宿主 %s 需要有 carrier）。\n' \
    "${robot_user}" "${robot_host}" "${GO2W_INTERFACE:-enp3s0}" >&2
  exit 2
fi

stamp="$(date +%Y%m%d_%H%M%S)"
backup_dir="${robot_root}/.deploy_backup/${stamp}"
rsync_flags=(-az --relative --backup --backup-dir="${backup_dir}"
             --itemize-changes)
if [[ "${dry_run}" == "1" ]]; then
  rsync_flags+=(--dry-run)
  printf '[dry-run] 只列出差异，不写机器狗\n'
fi

printf '同步 %s 个文件 -> %s@%s:%s\n' \
  "${#files[@]}" "${robot_user}" "${robot_host}" "${robot_root}"
rsync "${rsync_flags[@]}" -e "sshpass -e ssh ${ssh_opts[*]}" \
  "${files[@]}" "${robot_user}@${robot_host}:${robot_root}/"

if [[ "${dry_run}" == "1" ]]; then
  printf '[dry-run] 完成。去掉 --dry-run 才会真正同步。\n'
  exit 0
fi

printf '远端备份目录：%s\n' "${backup_dir}"
printf '远端语法自检（机器狗是 Python 3.8 + ROS Foxy）\n'
remote "cd ${robot_root} && python3 -m py_compile \
  app/navigation/approach_controller.py app/navigation/candidate_goal_generator.py \
  app/navigation/clearance_map.py app/navigation/decision_models.py \
  app/navigation/recovery_manager.py app/navigation/robot_backend.py \
  app/navigation/search_policy_config.py app/navigation/semantic_goal_localizer.py \
  app/navigation/safety_models.py app/navigation/stuck_detector.py \
  app/navigation/target_pose_generator.py app/navigation/view_quality.py \
  app/navigation/viewpoint_models.py app/navigation/viewpoint_planner.py \
  app/reasoning/embodiment_context.py app/reasoning/llm_situated_search_reasoner.py \
  app/spatial/slam_web_map_state.py app/spatial/pointcloud_web_codec.py \
  app/spatial/spatial_pose_validator.py app/manual_web_demo/web_server.py \
  app/manual_web_demo/search_routes.py app/manual_web_demo/search_errors.py \
  app/manual_web_demo/slam_map_snapshot.py app/live_robot/autonomous_explorer.py \
  app/navigation/nav2_search_backend.py app/navigation/corridor_analyzer.py \
  app/navigation/local_goal_executor.py app/live_robot/navigation_gate.py \
  app/live_robot/experiment_readiness.py scripts/go2w/cmd_vel_gate.py \
  scripts/nav2_bridge_worker.py \
  ros2_ws/src/robot_scene_nav_bringup/scripts/plain_slam_nav_frame_bridge.py \
  scripts/go2w/plain_slam_web_bridge.py scripts/go2w/plain_slam_web_cloud_relay.py \
  && bash -n scripts/go2w/start_nav2_autonomous_search.sh \
  && bash -n scripts/go2w/start_autonomous_search_web.sh \
  && bash -n scripts/go2w/start_plain_slam_mapping.sh \
  && bash -n scripts/go2w/setup_environment.sh \
  && bash -n scripts/go2w/reset_plain_slam_session.sh \
  && echo REMOTE_SYNTAX_OK"

printf '\n同步完成。下一步是计划书 §13.2「静止上电验证，只规划不运动」——\n'
printf '由现场人员执行，七项复核的完整命令见仓库 README.md 第 6.4 节。首条：\n'
printf '  ssh %s@%s\n' "${robot_user}" "${robot_host}"
printf '  cd %s && bash scripts/go2w/start_nav2_autonomous_search.sh   # 不带 --enable-motion\n' \
  "${robot_root}"
printf '\nconfigs/go2w/navigation_gate.yaml 的 nav2_allow_execute 在此之前必须保持 false；\n'
printf '§13.3 需要两人（一人监控、一人持急停），§13.4 之后才轮到 --enable-motion。\n'
printf '本节只同步代码，不启动任何东西，也不碰运动控制固件。\n'
