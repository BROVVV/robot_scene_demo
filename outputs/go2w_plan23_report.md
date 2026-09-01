# GO2W 真机自主搜索修复 — 最终报告（§23 格式）

> 状态：**代码与主机联调完成，机器人副本已同步；A–E 真机/WebUI 受限验收已完成**。
> 当前 WebUI 已启动并启用自主搜索入口，plain_slam 3D 实时快照持续更新。

---

## 【实际修改基线】

```text
branch: feature/semantic-object-topology
before commit: 81cda75（排查时基线）
after commit: 工作区未提交（保留用户现有 WIP；主机与机器人副本已按文件 hash 同步）
```

修改文件清单（按计划书 §21 模块）：

| 模块 | 文件 |
| --- | --- |
| P0-A 语义状态 | `app/live_robot/semantic_observer.py`、`app/live_robot/async_semantic_observer.py`、`app/navigation/models.py`、`app/config.py`、`configs/exploration/default.yaml` |
| P0-A2 轻量场景 | `app/detectors/siliconflow_vision_worker.py`（Quick prompt + scene_objects_light） |
| P0-B heading 解耦 | `app/live_robot/autonomous_explorer.py`、`scripts/go2w/run_semantic_exploration.py` |
| P0-C local scan | 新增 `app/navigation/local_scan.py`、`scripts/go2w/run_semantic_exploration.py` |
| P1-A 感知 retry | `app/live_robot/autonomous_explorer.py`、`scripts/go2w/run_autonomous_loop.py` |
| P1-B 坐标契约 | `app/spatial/models.py`、`app/spatial/plain_slam_spatial_provider.py`、`scripts/go2w/run_semantic_exploration.py` |
| P1-C 3D 累积 | `scripts/go2w/plain_slam_web_bridge.py`、`scripts/go2w/start_autonomous_search_web.sh`、`app/manual_web_demo/search_session_service.py` |
| P2-A D435 契约 | `app/perception/rgbd_source.py`、`app/perception/realsense_http_rgbd_source.py`、`scripts/go2w/realsense_rgbd_bridge.py` |
| P2-B frontier ID | `app/spatial/semantic_navigation_graph.py`、`app/live_robot/autonomous_explorer.py` |
| P2-C 日志/健康状态 | `app/live_robot/search_event.py`、`explorer_search_adapter.py`、`search_state_store.py`、`app/manual_web_demo/templates/index.html`、`static/search_ui.js`、`static/search_map.js`、`static/style.css` |
| 测试 | 新增 `tests/test_semantic_freshness.py`、`test_local_scan.py`、`test_explorer_heading_and_retry.py`、`test_plain_slam_frame_contract.py`、`test_frontier_unique_ids.py`、`test_rgbd_timestamp_contract.py` |

本次真机联调另外修复/确认：Humble/Foxy ROS 环境自适配、`SportModeState.imu_state.rpy`
里程计桥、VLM daemon 独立 session 与 `.env` 加载、plain_slam map/odom 回调排空、3D
 静止门控、WebUI readiness 误报、WebUI 重启清理、`sport_odom_bridge` 合法单一里程计预检，
以及 CLI 的 ActionClient/异步语义清理顺序；本轮还修复 plain_slam 重力初始化完成时
`imu_odom_state` 未同步的问题，并将 mapping 后台启动改为独立 session，避免脚本返回后
映射树被父 shell 带死。

---

## 【问题1：拓扑图没有真实物体】

- 修改点：
  1. `AsyncSemanticObservationManager`：首帧 Full Semantic 也提交后台 single-flight（不再同步阻塞 75s）；超时/失败/降级空结果**绝不**进入 `latest_success`，显式分类 `semantic_timeout/semantic_error/semantic_discarded`；
  2. `analyze_semantic`（run_semantic_exploration）超时/失败改抛 `PerceptionFailure(FULL_SEMANTIC_TIMEOUT/FULL_SEMANTIC_ERROR)`，不再返回“空场景成功”payload；
  3. Quick VLM 一次调用同时输出 `scene_objects_light`（≤10 个显著物体，独立于 target_objects），作为 Full Semantic 不可用时的当前帧轻量语义（`fresh_quick_scene`）；
  4. 语义优先级：当前帧 Full 成功 > 当前帧 scene_objects_light > 当前帧无语义（`pending/unavailable`），旧语义只做短窗口“历史记忆”并标记 `stale`。
- 为什么能解决：timeout 不再伪装成空场景，拓扑在 Full Semantic 挂掉时仍由 Quick 轻量物体建 OBJECT 节点。
- 测试：`tests/test_semantic_freshness.py`（timeout 不缓存、首帧后台、轻量 fallback 元信息）。

## 【问题2：三维建图重影】

- 修改点：
  1. `PlainSlamSpatialProvider`：`set_pose`/`camera_point_to_spatial`/`get_frontiers` 增加 frame 契约，`frame_id != pslam_odom` 抛 `SpatialFrameMismatch`，绝不静默混算 wheel odom 与 pslam 地图；
  2. `run_semantic_exploration.py`：空间位姿一律取 `provider.get_pose()`（pslam_odom）；wheel odom 只用于运动/当前 yaw；plain_slam 位姿不可用时降级 `NO_GLOBAL_SPATIAL_POSE`，不拿 wheel pose 冒充；
  3. `plain_slam_web_bridge.py`：aligned_scan 必须 `frame_id == pslam_odom` 才累积（否则 drop + 原因统计）；运动门控（|yaw rate|≤0.03 rad/s 且速度≤0.02 m/s 连续稳定 ≥0.5s 才永久累积）；pose jump 坏帧丢弃；map origin 跳变/`--reset-marker`/启动时清空旧累积；snapshot 输出 scan 诊断（received/accumulated/dropped/dropped_reason/stationary/pose）。
- 统一后的 map frame：`pslam_odom`；aligned_scan frame：`pslam_odom`（校验后累积）。
- 运动期 scan gate：运动中只显示最新帧，不写入永久 voxel map。
- 最小旋转实验：**已完成（验收 C）**；两次 l30 均成功（观测 30.29°、30.02°），
  运动期 `motion_active=129` 帧被丢弃，停稳后累积体素从 3442 增至 6724；另有
  5 次 `map_frame_mismatch` 被丢弃，未污染累积图。
- 测试：`tests/test_plain_slam_frame_contract.py`。

## 【问题3：连续左转 30°】

- heading sector 来源：`navigation_heading_sector` 永远由当前 capture pose 计算（explorer 强制覆盖），与语义 sector 完全解耦（`semantic_heading_sector` 仅描述“从哪个方向看到”）。
- coverage 更新逻辑：`place_graph.register_observation`、`entity_graph.sync_from_observation`、`observed_sectors` 全部改用 navigation sector。
- local_scan quota：新增 `LocalScanState`（steps/last_direction/same_direction_count），`max_local_rotations` 真正限制每个 Place 的旋转次数（纯函数 `select_local_scan_goal`）。
- 左右选择逻辑：候选 ±30/±60 对称；同向重复惩罚 -0.6；同向连续 ≥2 且无新信息硬禁止 -2.0；最近 sector 惩罚；同分优先反向。
- 测试：`tests/test_local_scan.py`（quota/不固定左/同向惩罚/死循环保护）、`tests/test_explorer_heading_and_retry.py`（§17.4 最重要的 coverage 回归：语义恒 0 时导航 coverage 仍 0,1,2,…）。

## 【PERCEPTION_FAILURE】

- 错误分类：`PerceptionFailure(code, recoverable, detail, last_success_age_s)`；代码含 QUICK_VLM_TIMEOUT / FULL_SEMANTIC_TIMEOUT / RGBD_TIMEOUT / FRAME_STALE / VLM_PARSE_ERROR / TF_UNAVAILABLE / UNKNOWN_PERCEPTION_ERROR 等。
- retry 次数：默认 `max_perception_retries=2`（原始 1 次 + retry 2 次），backoff 1s/2s；**无论之前是否已有成功 observation**，只要 recoverable 就重试。
- 单次 timeout 注入：`GO2W_QUICK_TIMEOUT_INJECT_ONCE=1`（验收 D 用，默认关闭）。
- 最终失败 cause：`session_finish` 带 cause/attempts/last_success_age_s/recoverable/error_detail；`SessionResult.summary.perception_failure` 可查；WebUI 失败横幅显示 cause/attempts。
- 测试：`tests/test_explorer_heading_and_retry.py`（§17.7/§17.8）。

## 【Frame Binding】

- current frame：每轮捕获的 RGB-D 帧（`camera_frame_selected`）。
- semantic source frame：`SemanticObservation.semantic_source_frame_id`（Quick=当前帧；Full=请求帧）。
- depth frame：`resolve_depth_frame()` 只允许 source frame 的深度（当前帧或 60 帧有界 RGB-D 缓存）；找不到 → `SEMANTIC_2D_ONLY` + `semantic_depth_frame_mismatch` 日志。
- mismatch 行为：绝不用 current depth 顶替旧帧语义。
- 测试：`tests/test_semantic_freshness.py::test_resolve_depth_frame_only_uses_matching_frame`。

## 【自动化测试】

- 命令：`python -m pytest -q tests/test_semantic_freshness.py tests/test_local_scan.py tests/test_explorer_heading_and_retry.py tests/test_plain_slam_frame_contract.py tests/test_frontier_unique_ids.py tests/test_rgbd_timestamp_contract.py`（详见 runbook）
- 计划书核心 6 组：**23 passed**。
- 语义/异步 observer、explorer、WebUI、plain_slam 等扩展组：**51 passed**。
- 本轮修改后的 readiness/provider/WebUI/estop 回归：**24 passed**。
- `python -m py_compile`、shell `bash -n` 均通过。

## 【真机验收】

- A 静止语义：**通过（无运动）**。真实 D435/VLM/readiness；3 个观测周期产生 11 个
  唯一物体、11 个 frontier、23 个拓扑节点，目标不在当前视野后按规划周期结束。
- B 30° coverage：**通过**。`acceptance_b_20260901_114550_turns.jsonl`：3 步
  `success=true`，实际 yaw 约 30°/步，`navigation_failures=0`。
- C 3D rotation：**通过验收门控**。`acceptance_c_20260901_120316_turns.jsonl`：2 步
  成功；WebUI snapshot 为 `frame_id=pslam_odom`、`target_map_frame=pslam_odom`、
  `motion_active=129`、`stationary=true`，停稳后体素继续增长。
- D timeout retry：**通过（无运动）**。一次 `GO2W_QUICK_TIMEOUT_INJECT_ONCE=1`
  产生结构化 `QUICK_VLM_TIMEOUT` retry，随后恢复 2 次正常观测，10 个物体、22 个
  拓扑节点；无空场景伪成功、无运动指令。
- E full search：**通过受限 WebUI 真机闭环**。会话
  `search_20260901_120501_96392a00` 在 `turn_only=true`、`max_motion_steps=1` 下完成
  1 个规划周期和 1 个 l30，结果 `MAX_STEPS_REACHED`（预算终止，非错误）；随后会话
  `search_20260901_121219_0a3965d1` 在自主运动权限开启状态下首帧确认“垃圾桶”，结果
  `TARGET_FOUND`、`motion_steps=0`。

- WebUI 端到端（无运动）：**通过**。从 `/api/search/start` 下发“蓝色垃圾桶”，完成
  `SESSION_CREATED → TASK_UNDERSTANDING → OBSERVATION_UPDATED → TARGET_CONFIRMED →
  SEARCH_FINISHED`，结果 `TARGET_FOUND`，`motion_steps=0`。

## 【仍未解决/需要人工标定】

1. D435 外参仍为 `candidate_unconfirmed`：可发布候选外参用于实验，但 readiness 标 `UNCALIBRATED`，禁止标高质量 GLOBAL（计划书 §11.3）。
2. plain_slam/LIO 本体漂移：C 已证明坐标契约和运动门控生效，但原地转向后 LIO 平面
   位姿仍出现明显平移漂移；若现场视觉上仍见几何扇形重影，应按交接书 §6.6 采 rosbag
   做 IMU 外参/time sync/deskew 专项，不能通过修改 planner 掩盖。
3. wheel odom TF（odom_fused→base_link）未发布：按计划书 §9.4 不建议无脑开 TF，真机 TF tree 待梳理。

---

## 当前运行状态与剩余人工项

- WebUI：`http://127.0.0.1:8765`，当前已用现场确认的安全环境启动自主搜索权限；
  手动控制默认仍为 `DISABLED`，任务结束后动作状态为 `stop`。
- 3D WebUI 数据源：`/go2w/slam/map_3d + /go2w/slam/aligned_scan`，显示坐标系为
  `pslam_odom`；桥为 display-only，不发布 ROS、不触碰运动权威 odom。
- readiness：`ready=true`；相机、VLM、ROS worker、Action、急停服务、机器人模式均在线；
  最终检查 `rotation_clearance_valid=false`，因此手动方向按钮仍保持相应安全禁用，
  只有操作员监督实验通道允许已确认的 ≤30° 原地转向。
- 本次运动验收已由现场操作者确认：平稳放置、至少 2 m 无障碍、遥控急停在手；最终
  启动命令为 `GO2W_AREA_CLEARED=I_HAVE_CLEARED_THE_AREA bash scripts/go2w/start_autonomous_search_web.sh --enable-autonomous-motion --with-plain-slam`。

## 阻塞说明（历史模板信息）

本节原为交接时的历史阻塞说明；本轮已具备命令执行、SSH、WebUI 和真机联调条件，
不再适用。B/C/E 已在操作员监督和受限运动预算下完成。
