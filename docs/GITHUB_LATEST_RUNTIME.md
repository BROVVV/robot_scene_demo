# Go2-W 项目 GitHub 运行上下文

更新时间：2026-09-01（Asia/Shanghai）

## 项目边界

## 2026-09-01 20:51 接管复核

- 清理主机遗留的第二个 `plain_slam_web_cloud_relay`；`/go2w/slam/web_scan` 当前仅 1 个
  publisher，远端 3D bridge 仍正常接收。
- 最新真实 WebUI 会话 `search_20260901_205129_fdb39af8` 回放为 `TARGET_FOUND`，目标状态
  `PRESENT`，Full Semantic 错误 0，Place 仅 `P1`，未发送物理运动 goal。
- 机器狗端地图连续复核为 `HEALTHY`，`pslam_odom`、`lio_pose_valid=true`、
  `motion_odom_seen=true`、`dropped_scans=0`；当前点数约 5209–5212。
- 启动自检返回码 0；`lidar_fresh`、`spool_bundle` 仍是明确降级项。`rotation_clearance_valid`
  为 false，故 `motion_authorized/safety_authorized` 继续为 false。

## 2026-09-01 整改后现场状态（最新）

- 整改代码已部署到机器狗 `/home/unitree/robotscene`；覆盖前的 20 个远端源码文件已保存到
  `/home/unitree/robotscene/.codex_backups/20260901/`。
- 机器狗端 `go2w_plain_slam_bridge` 已在 ROS 2 Foxy 环境中构建成功；新增 IMU 时间对齐器、
  三态目标门控、空间位姿校验、几何关系提取和独立运动里程计门控均已随正式启动链路部署。
- 当前现场服务：WebUI 已在机器狗 `0.0.0.0:8765` 监听；D435 HTTP capture、VLM daemon、
  plain_slam display relay 和 3D Web bridge 均在线。浏览器地址为
  `http://192.168.123.18:8765/`，三维地图接口为 `/api/slam/map3d`。
- 当前只读验收实测：相机 `1280x720` 且 fresh；VLM `Qwen/Qwen3-VL-8B-Instruct`
  返回 `status=ok`；地图 `available=true`、`mapping_health=HEALTHY`、
  `frame_id=pslam_odom`、`point_count≈5820`、`motion_odom_seen=true`、
  `lio_pose_valid=true`、`stationary=true`。跨主机显示使用紧凑 relay topic
  `/go2w/slam/web_scan`，不改变运动控制坐标链。
- 真实 WebUI 后端只读干跑 `search_20260901_173622_fa9e0a38` 经真实相机/VLM
  返回 `TARGET_FOUND`（办公椅），`planning_cycles=0`、`motion_steps=0`，没有发送运动 goal；
  worker 正常退出并完成归档。此前 `search_20260901_165344_61d612fa` 也曾通过同一链路。
- 当前安全状态：WebUI owner 为 `NONE`、命令为 `stop`、`motion_in_flight=false`，正式自主
  运动保持关闭。机器狗本机已有且仅有 1 个 `/go2w/motion` action server，
  `/go2w/arm`、`/go2w/emergency_stop` 在线，readiness 为 `ready=true`。
- 真机运动验收仍需在清空场地、确认 `/go2w/odom/fused` 唯一发布者、确认机器人周围安全且
  遥控器可立即急停后，由现场操作员显式授权；在此之前不发送真实运动 goal。

- 本仓库对应机器狗项目目录：`/home/unitree/robotscene`。
- 主机工作区：`/home/mxt/robotscene`。
- 机器狗地址：`192.168.123.18`。
- WebUI：`http://192.168.123.18:8765`（运行在机器狗系统；mapping-assist 的传感器链仍在
  主机 `192.168.123.99`，通过 DDS + display-only relay 提供三维显示）。
- 机器狗端 D435 HTTP `/health`：最近检查 HTTP 200。
- 机器狗端 SiliconFlow VLM daemon：在线。
- 为避免主机工作树与机器狗现场工作树混淆，机器狗源码另存于
  `github_artifacts/robot_project_snapshot_20260901/`：来源是机器狗的
  `/home/unitree/robotscene`，同步时现场 HEAD 为 `4eec3b7`，当时工作树有 459 个修改项。
  该快照包含 1,870 个源码/配置/文档文件，未包含机器狗 `.git` 元数据、`.env`、虚拟环境、
  第三方依赖、ROS 编译产物和运行日志。

## 整改前历史运行状态（归档）

- plain_slam mapping-assist：运行中，独立 session 启动。
- 3D 显示数据源：`/go2w/slam/map_3d` + `/go2w/slam/aligned_scan`。
- 显示/累积坐标系：`pslam_odom`；display-only bridge 不发布运动控制数据。
- 最新快照：`available=true`、`fresh=true`、`stationary=true`，`frame_id=pslam_odom`，
  `target_map_frame=pslam_odom`。
- 动作服务可用；最近任务结束后为 `DISABLED/stop`，无动作在途。
- 手动 turn 按钮因正式四向 rotation-clearance 尚未标定而保持安全禁用；操作员监督实验
  通道只允许已确认的 ≤30° 原地转向。

## 最近验收证据

- A：静止语义通过；真实 D435/VLM 产生物体和 frontier 拓扑。
- B：`outputs/live_sessions/acceptance_b_20260901_114550_turns.jsonl`，3 个 30°
  原地转向成功，导航失败为 0。
- C：`outputs/live_sessions/acceptance_c_20260901_120316_turns.jsonl`，两步成功；
  运动期 `motion_active` 丢帧，停稳后 voxel 继续累积，错误帧被拒绝。
- D：一次 Quick VLM timeout 注入后 retry 恢复，无运动伪成功。
- E/WebUI：
  - `search_20260901_120501_96392a00`：自主搜索权限开启、turn-only、1 个受限原地转向，
    结果 `MAX_STEPS_REACHED`（预算结束，非错误）。
  - `search_20260901_121219_0a3965d1`：通过 WebUI 首帧确认“垃圾桶”，结果
    `TARGET_FOUND`，`motion_steps=0`。

## 最新追加运行（需后续 AI 优先关注）

会话 `search_20260901_122525_25291a89`（目标“白色垃圾桶”）是在上述验收之后产生的
最新运行记录，不应与已完成的受限验收混为一谈：

- 前 9 个原地扫描动作均成功，观测 yaw 约 29.64°–31.53°；第 5 周期虽然观测到
  30.79°，但动作结果被记录为失败，随后按恢复策略继续。
- 第 10 周期出现 `FULL_SEMANTIC_ERROR`，最终会话以 `FAILED/WORKER_INTERRUPTED`
  结束；这是需要后续排查的最新异常，不代表机器狗未停机。
- 最终安全状态：`motion_in_flight=false`、命令为 `stop`、动作状态 `DISABLED`，
  相机和 mapping 仍在线。

## 给后续 AI 的读取顺序

1. 先读 `GO2W_真机自主搜索_详细修改计划书.md` 和 `docs/GO2W_真机验收_交接书.md`。
2. 再读 `outputs/go2w_plan23_report.md` 获取验收结论和未决风险。
3. 搜索事件读取 `outputs/live_runs/<session_id>/events.jsonl`、`webui_state.json`、
   `summary.json`。
4. mapping 运行日志读取 `github_artifacts/robot_runtime_logs/` 及
   `outputs/autonomous_search/logs/`。
5. 如果需要判断机器狗现场代码，以
   `github_artifacts/robot_project_snapshot_20260901/` 为准；仓库根目录代码是主机项目工作树
   的提交快照，两者不要按同一个 Git HEAD 推断。

## 上传边界

- 未上传 `.env`、API token、密码、虚拟环境、模型权重、第三方仓库、ROS build/install
  产物、相机 JPEG/PNG 缓存和运行 PID 文件。
- 机器狗运行日志已脱离第三方依赖目录集中归档；代码和配置保留为可阅读源码。
- 仍需人工标定：D435 外参 `candidate_unconfirmed`；原地旋转后的 LIO 平面漂移需按
  IMU 外参、时间同步和 deskew 专项继续分析。
