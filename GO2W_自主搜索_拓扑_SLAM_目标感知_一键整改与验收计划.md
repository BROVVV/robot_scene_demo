# Go2-W 自主搜索：拓扑图、plain_slam 三维建图、目标感知与搜索策略一键整改及验收计划

> **用途**：把本文件直接交给代码 AI / Coding Agent。AI 应按本文从基线核对、代码修改、离线回归、ROS 构建、真机分级验收、日志归档到最终报告完整执行，不要只给建议，不要只改一个局部，也不要在没有测试证据的情况下宣称完成。
>
> **目标**：修复当前真机自主搜索中三个相互耦合的问题：
>
> 1. 拓扑图错误：搜索开始前残留旧 `P1/Fxx`；运行后物体全部以 `OBSERVED_FROM` 星型连接到 `P1`，缺少真实 OBJECT↔OBJECT 拓扑；原地旋转又错误创建 `P2/P3/...`。
> 2. 三维建图错误：静止点云正常，机器人旋转/运动后 `pslam_odom` 发散，aligned scan 像多份旋转后的相同场景叠加，地图严重重影。
> 3. 自主搜索错误：连续多次执行左转 `30°`；LOCAL_SCAN 每轮像重新从 step 1 开始；摄像头已经看到疑似目标时仍继续旋转；Full Semantic 异常还可能使搜索退出。
>
> **最终效果**：机器人能够按 `停稳 → 新鲜观测 → 目标抢占判断 → 语义/空间更新 → 规划 → 动作 → 停稳 → 再观测` 的闭环自主搜索；原地旋转不产生新 Place；真正位移才产生 `MOVED_TO`；OBJECT↔OBJECT 关系可稳定形成；三维地图在统一世界坐标中随探索逐步扩展，不再重复旋转叠图；看到疑似目标后先停住验证，而不是立即转走。

---

# 0. 基线、事实与硬约束

## 0.1 仓库与现场基线

执行前先核对，不允许凭本文中的版本号盲改：

```text
GitHub:
https://github.com/BROVVV/robot_scene_demo

用户给出的最新提交：
21a7bbb

同步分支：
main
feature/semantic-object-topology

机器狗现场源码快照：
github_artifacts/robot_project_snapshot_20260901/

快照来源：
192.168.123.18:/home/unitree/robotscene

现场快照 HEAD：
4eec3b7

最新异常自主搜索：
outputs/live_runs/search_20260901_122525_25291a89/

运行索引：
docs/GITHUB_LATEST_RUNTIME.md
```

**非常重要：**

`github_artifacts/robot_project_snapshot_20260901/` 是现场证据/快照，不应被默认当成唯一正式源码目录。

执行 AI 必须先查清：

1. 当前 Git 根目录的正式源码；
2. 启动脚本实际 import/执行的是哪一份文件；
3. 机器狗 `/home/unitree/robotscene` 上实际部署的是哪一份；
4. 根目录与 snapshot 中同名文件有哪些差异。

**禁止只修改 snapshot，然后误以为正式运行代码已经修改。**

---

## 0.2 本次日志已经证明的异常

最新异常会话：

```text
search_20260901_122525_25291a89
target = 白色垃圾桶
```

首轮 plain_slam pose 约：

```text
x = 1.044 m
y = -3.462 m
quality = METRIC_LIDAR
frame = pslam_odom
```

机器人主要执行原地约 `30°` 旋转，但后续 Place pose 已发散到：

```text
P7:
x ≈ 3605.418 m
y ≈ 3516.644 m

P8:
x ≈ 6229.680 m
y ≈ 7398.190 m

P10:
x ≈ 11475.867 m
y ≈ 20685.777 m
```

这不是正常漂移，而是 LIO 失效。

同一会话还出现：

```text
decision.current_place_id = P1
local_scan.provenance.place_id = P10
goal_id = local_scan_001
step = 1/3
turn = +30°
```

说明存在两套 Place 身份：

```text
导航图：仍认为 current=P1
空间 PlaceGraph：已经错误增长到 P10
```

导致：

```text
P1 step1
P2 step1
P3 step1
...
```

每次 LOCAL_SCAN 状态都重置，候选首项 `+30°` 反复胜出。

Quick VLM 当前 target gate 仍是严格二值：

```text
target_decision.is_present
AND target_objects 非空
AND bbox 合法
AND score >= threshold
```

最新会话没有成功产生目标命中；同时第 10 周期出现约 `65s` 的：

```text
FULL_SEMANTIC_ERROR
```

---

## 0.3 已确认的关键代码事实

执行 AI 必须自行再次打开并确认以下文件，不能只读本文：

```text
github_artifacts/robot_project_snapshot_20260901/
  app/spatial/place_graph.py
  app/spatial/plain_slam_spatial_provider.py
  app/spatial/object_relation_store.py
  app/spatial/semantic_entity_graph.py
  app/navigation/local_scan.py
  app/detectors/siliconflow_vision_worker.py
  app/manual_web_demo/search_session_service.py
  scripts/go2w/run_semantic_exploration.py
  scripts/go2w/plain_slam_web_bridge.py

ros2_ws/src/go2w_plain_slam_bridge/scripts/imu_fallback_adapter.py
```

当前已知关键点：

### IMU adapter

真实 IMU callback 中存在：

```python
aligned.header.stamp = node.get_clock().now().to_msg()
```

这会把**传感器采样时刻**替换成**消息到达 callback 的时刻**。

对 LIO/deskew 来说，这是高风险设计。

### PlaceGraph

接口其实已经有：

```python
observed_displacement_m: float | None = None
```

但当前自主搜索注册观察时没有传该参数。

当前 `_resolve_place()` 优先相信 metric `pose`：

```text
pose 非空
→ nearest place
→ distance 超 merge threshold
→ 新建 Place
```

所以错误的 pslam pose 会直接制造 `P2/P3/...`。

### Quick semantic

当前实时 Quick 场景路径仍会构造：

```python
"scene_relations": []
```

因此 ObjectRelationStore 虽然已经存在，实时 Quick 路径仍无法给它 OBJECT↔OBJECT 关系。

### WebUI 3D bridge

`aligned_scan` 被视为已经处于：

```text
pslam_odom
```

并直接累积。

当前 bridge 已有：

```text
frame gate
stationary gate
pose_jump gate
```

但是“stationary / pose_jump”主要仍依据 pslam 自己的 odom。  
当 pslam 自己发散时，这种自证式 gate 不够。

### WebUI session

`SearchSessionService._restore_latest_session()` 会恢复 newest session 的 state。  
历史 terminal session 与新的 live topology 需要彻底分离。

---

# 1. 执行 AI 的工作方式

## 1.1 必须真正修改代码

交付不接受：

```text
“建议修改……”
“可能是……”
“可以考虑……”
```

必须完成：

```text
读取
→ 定位
→ 修改
→ 写测试
→ 运行测试
→ 修失败测试
→ ROS 构建
→ 安全真机分级测试
→ 保存测试证据
→ 更新文档
→ 输出最终 diff/report
```

---

## 1.2 不允许大规模无关重构

本轮只围绕：

```text
LIO 时间/外参/质量门
Place identity
LOCAL_SCAN
目标感知抢占
OBJECT 拓扑
3D 累积
session reset
Full Semantic 隔离
```

禁止顺手重写整套 planner、WebUI 框架或 ROS 控制系统。

---

## 1.3 先找到“正式运行副本”

执行：

```bash
git status --short
git rev-parse HEAD
git branch --show-current
git log -1 --oneline

rg -n "class PlaceGraph" .
rg -n "class PlainSlamSpatialProvider" .
rg -n "quick_target_present" .
rg -n "class PlainSlamWebBridge" .
rg -n "imu_fallback_adapter" .
rg -n "_restore_latest_session" .
rg -n "select_local_scan_goal" .
```

然后检查启动链：

```bash
rg -n "run_semantic_exploration.py" scripts app runtime .
rg -n "plain_slam_web_bridge.py" scripts app runtime .
rg -n "go2w_plain_slam_bridge" scripts ros2_ws runtime .
```

记录：

```text
正式源码路径
机器狗部署路径
主机 WebUI 路径
ROS package 路径
snapshot 仅证据路径
```

写到：

```text
outputs/ai_fix_baseline_report.md
```

---

## 1.4 建独立修复分支

推荐：

```bash
git switch -c fix/go2w-autonomous-search-lio-topology-target
```

如果工作树不干净：

1. 不得覆盖用户已有修改；
2. 先保存 `git status` 和 `git diff`;
3. 不要擅自 reset/clean；
4. 只在确认无冲突后继续。

---

# 2. 总体目标架构

最终运行链必须变成：

```text
                 ┌──────────────────────┐
                 │  Wheel fused odom    │
                 │  运动事实/相对位移   │
                 └──────────┬───────────┘
                            │
                            ▼
                  Motion Evidence / Gate
                            │
                            │ consistency
                            ▼
Pandar + IMU ──► plain_slam/LIO ──► RAW PSLAM POSE
                            │
                            ▼
                 SpatialPoseValidator
                    │              │
                 VALID          REJECTED
                    │              │
                    ▼              └─► mapping_health=DEGRADED
          accepted pslam pose             保留 last-good
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
   PlaceGraph   Object map    3D accumulator
       │
       ▼
唯一 canonical Place ID
       │
       ├────► Navigation topology
       ├────► LOCAL_SCAN state
       └────► semantic observation provenance


RGB-D fresh frame
      │
      ▼
Quick VLM
      │
      ├─ ABSENT   ──► 可以规划
      ├─ POSSIBLE ──► 禁止运动 → 新帧/crop verify
      └─ PRESENT  ──► verify → TARGET_FOUND

Quick objects + RGB-D geometry
      │
      ▼
GeometricRelationExtractor
      │
      ▼
ObjectRelationStore
      │
      ▼
OBJECT ↔ OBJECT topology
```

---

# 3. P0：先修 IMU 时间戳，禁止 callback receive time 污染 LIO

## 3.1 根因目标

不要再做：

```python
aligned.header.stamp = node.get_clock().now().to_msg()
```

因为：

```text
真实采样：
t0  t1  t2  t3 ...

网络/DDS callback：
t0+e0  t1+e1  t2+e2  t3+e3
```

`e0/e1/e2/e3` 是不规则调度/网络 jitter。

deskew 需要的是：

```text
真实采样 Δt
```

而不是 callback jitter。

---

## 3.2 实现一个可测试的 Timestamp Aligner

推荐新增：

```text
ros2_ws/src/go2w_plain_slam_bridge/
  go2w_plain_slam_bridge/imu_timestamp_aligner.py
```

如果该 package 当前不是 Python package，则放入已有可 import 的模块路径，但必须保持纯 Python、可 pytest。

建议类型：

```python
@dataclass
class TimestampAlignmentResult:
    corrected_sec: float
    mode: str
    valid: bool
    raw_sensor_sec: float | None
    receive_ros_sec: float
    estimated_offset_sec: float | None
    reset_detected: bool
    reason: str
```

```python
class ImuTimestampAligner:
    def update(
        self,
        sensor_stamp_sec: float | None,
        receive_ros_sec: float,
    ) -> TimestampAlignmentResult:
        ...
```

---

## 3.3 时间戳策略

### 情况 A：原始 IMU stamp 已是 ROS 同一时钟域

如果：

```text
stamp 非 0
monotonic
与 ros_now 只差合理范围
```

直接：

```text
PASSTHROUGH_SENSOR_STAMP
```

不要改采样间隔。

### 情况 B：原始 stamp 有效，但来自设备 monotonic clock

计算：

```text
offset_i = receive_ros_time - sensor_time
```

使用滑动窗口 robust median / trimmed median 估计：

```text
t_corrected = t_sensor + offset
```

要求：

```text
corrected Δt ≈ sensor Δt
```

而不是：

```text
corrected Δt = callback receive Δt
```

offset 只允许慢速变化，不允许每包跟 callback jitter 跳。

推荐：

```text
window size = 21~51
offset update = median
max offset slew = 可配置
```

### 情况 C：stamp 为 0、倒退、重复、巨大跳变

不能悄悄继续以 host receive time 当成“高质量 IMU”。

状态应明确：

```text
IMU_STAMP_INVALID
```

如果只能使用 synthetic/static fallback：

```text
只允许 mapping-assist static 模式
机器人运动时必须让 mapping health degraded/invalid
```

---

## 3.4 修改 imu_fallback_adapter

目标状态建议：

```text
IMU_SOURCE_OK_SENSOR_STAMP
IMU_SOURCE_OK_OFFSET_ALIGNED
IMU_SOURCE_STAMP_RESET_RECOVERING
IMU_SOURCE_INVALID_STAMP
SYNTHETIC_STATIC
```

日志至少包含：

```text
raw_stamp
corrected_stamp
receive_time
offset
sample_dt
receive_dt
mode
```

不要每包刷屏，可每 1~5 秒汇总。

---

## 3.5 时间戳单元测试

新增：

```text
tests/test_imu_timestamp_aligner.py
```

至少覆盖：

### Test 1：sensor stamp 已正常

输入：

```text
sensor dt = 0.005s
receive jitter = ±0.020s
```

要求：

```text
corrected dt ≈ 0.005s
```

不能跟 jitter 一起跳。

### Test 2：设备时钟固定 offset

```text
sensor = 100.000, 100.005...
ROS = 1788... + jitter
```

要求 offset 对齐后仍保持 sensor Δt。

### Test 3：timestamp reset

模拟：

```text
100.200
100.205
0.001
0.006
```

必须：

```text
detect reset
重新初始化
不输出倒退 corrected time
```

### Test 4：zero stamp

必须进入明确 invalid/fallback，不得伪装成正常 sensor timing。

---

# 4. P0：核查 Pandar↔IMU 外参与 deskew，不允许“猜一个外参就继续”

## 4.1 先找到实际 LIO 配置

执行：

```bash
rg -n "extrinsic|imu|lidar|pandar|deskew|time_offset|time_sync" \
  configs ros2_ws scripts runtime github_artifacts/robot_project_snapshot_20260901
```

列出 plain_slam 真正加载的：

```text
LiDAR frame
IMU frame
base frame
R_li
t_li
time offset
deskew enable
IMU frequency
LiDAR timestamp source
```

输出到 baseline report。

---

## 4.2 禁止自动伪造“confirmed”标定

当前运行证据仍有：

```text
extrinsic_status = candidate_unconfirmed
```

执行 AI 不得因为需要让测试通过就把字符串改成 `confirmed`。

必须有数据证据。

---

## 4.3 新增 LIO 健康/标定检查脚本

推荐：

```text
scripts/go2w/validate_lio_time_extrinsics.py
```

至少支持：

```bash
python scripts/go2w/validate_lio_time_extrinsics.py \
  --duration 10 \
  --report outputs/lio_validation.json
```

输出：

```json
{
  "imu_stamp_monotonic": true,
  "imu_dt_p50_ms": 5.0,
  "imu_dt_p95_ms": 5.2,
  "receive_jitter_p95_ms": 14.0,
  "gravity_norm_mean": 9.81,
  "static_gyro_norm": 0.01,
  "loaded_extrinsic": {...},
  "extrinsic_status": "candidate_unconfirmed",
  "pslam_static_drift_m": 0.02
}
```

---

## 4.4 原地旋转方向一致性测试

用现有受安全限制的 motion action / acceptance harness。

不要绕开原有安全控制直接向底盘发危险命令。

比较：

```text
requested yaw
wheel observed yaw
IMU integrated yaw
pslam observed yaw
```

要求至少：

```text
sign 一致
量级一致
```

如果：

```text
wheel +30°
IMU -30°
```

优先检查坐标轴/外参，而不是调 planner。

---

## 4.5 如果时间同步修好后 LIO 仍旋转发散

按顺序排查：

```text
1. IMU axes / sign
2. LiDAR↔IMU rotation extrinsic
3. timestamp offset
4. point-level timestamp
5. deskew enable/implementation
6. LiDAR packet timing
7. IMU noise/bias parameters
8. LIO optimizer parameters
```

不能跳过前 5 项直接“调 ICP 权重”。

---

# 5. P0：新增 SpatialPoseValidator，错误 LIO 不得继续标 METRIC_LIDAR

## 5.1 新模块

推荐新增：

```text
app/spatial/spatial_pose_validator.py
```

结构：

```python
@dataclass
class MotionEvidence:
    command_type: str
    requested_turn_deg: float
    requested_forward_m: float
    wheel_delta_xy_m: float | None
    wheel_delta_yaw_deg: float | None
    wheel_linear_speed_mps: float | None
    wheel_yaw_rate_dps: float | None
    motion_completed_at: float | None

@dataclass
class PoseValidationResult:
    accepted: bool
    health: str
    reason_code: str
    raw_pose: SpatialPose | None
    accepted_pose: SpatialPose | None
    pslam_delta_xy_m: float | None
    pslam_delta_yaw_deg: float | None
    wheel_delta_xy_m: float | None
```

状态：

```text
HEALTHY
DEGRADED
REJECTED
RECOVERING
UNAVAILABLE
```

---

## 5.2 必须实现的规则

所有阈值放 settings/config，不要散落 magic number。

建议初值：

```text
rotation wheel max translation:
0.08 m

rotation pslam max translation:
0.20 m

absolute pslam instantaneous speed reject:
2.0 m/s

stationary pslam drift warning:
0.10~0.15 m

recovery consecutive valid samples:
3~5
```

核心规则：

```python
if motion_is_rotation_only:
    if wheel_delta_xy_m <= rotation_wheel_translation_max:
        if pslam_delta_xy_m > rotation_pslam_translation_max:
            reject("ROTATION_TRANSLATION_DRIFT")
```

```python
if pslam_instant_speed_mps > max_pslam_speed:
    reject("PSLAM_POSE_JUMP")
```

```python
if timestamp_stale_or_non_monotonic:
    reject("PSLAM_TIMESTAMP_INVALID")
```

---

## 5.3 质量传播

当前：

```text
收到 /go2w/slam/odom_base
→ quality=METRIC_LIDAR
```

必须改为：

```text
RAW pose
→ validator
→ accepted pose
```

只有：

```text
validator accepted + fresh
```

才可以：

```text
quality = METRIC_LIDAR
```

被拒绝时：

```text
quality = DEGRADED_LIO
或 pose 不对外作为 metric world pose
```

**不得把错误坐标继续传给：**

```text
PlaceGraph
SemanticObjectMap.map_xyz
Frontier metric position
3D permanent accumulator
```

---

## 5.4 保留 raw pose 做诊断

不要简单丢掉原始 LIO 数据。

snapshot/event 里同时记录：

```json
{
  "raw_pslam_pose": {...},
  "accepted_pslam_pose": {...},
  "pose_validation": {
    "accepted": false,
    "reason": "ROTATION_TRANSLATION_DRIFT"
  }
}
```

这样后续还能继续调 LIO。

---

## 5.5 恢复机制

一次 reject 后不要立刻在下一样本恢复成 HEALTHY。

要求：

```text
连续 N 个 valid sample
且运动事实一致
→ RECOVERING
→ HEALTHY
```

在 `DEGRADED/RECOVERING` 期间：

```text
可显示 latest raw scan
不能写永久 world map
不能用 raw pose 创建新 Place
```

---

# 6. P0：PlaceGraph 必须以“真实位移证据”阻止原地旋转创建新 Place

## 6.1 runner 必须传 observed_displacement_m

找到：

```python
place_graph.register_observation(...)
```

改成类似：

```python
place_id, created = place_graph.register_observation(
    observation_id=observation.bundle_id,
    heading_sector=...,
    objects=observation.object_labels,
    rgbd_frame_id=observation.rgbd_frame_id,
    pose=validated_spatial_pose,
    observed_displacement_m=wheel_observed_displacement_m,
    timestamp=observation.timestamp,
    target_candidate=observation.target_present,
)
```

`wheel_observed_displacement_m` 必须来自：

```text
/go2w/odom/fused
```

或现有 backend 的真实 motion result。

**它是“有没有平移”的证据，不是拿 wheel x/y 替代 pslam world x/y。**

---

## 6.2 修改 `_resolve_place()` 优先级

当前问题：

```text
只要 pose 有值
→ 先看 pslam metric distance
→ pslam 漂了就创建新 Place
```

改成：

```text
第一优先：motion evidence
第二优先：validated pslam pose
第三优先：无 metric pose 时的 relative fallback
```

建议逻辑：

```python
if observed_displacement_m is not None:
    if observed_displacement_m < relocation_min_displacement_m:
        # 已知没有发生足够平移
        return current_place, False, False

if validated_pose is not None:
    # 才做 global nearest / revisit / new place
    ...

if observed_displacement_m is not None:
    if observed_displacement_m >= relocation_min_displacement_m:
        # relative-only fallback
        create_new_place_without_metric_pose()
```

更严格可增加：

```text
如果 action_type == ROTATE_VIEW / turn-only
→ 无论 raw pslam 说走了多远
→ 只要 wheel translation 很小，Place 必须保持
```

---

## 6.3 relocation threshold

当前 `0.10 m` 太敏感。

建议先改成配置项，默认：

```text
0.45~0.60 m
```

推荐起点：

```text
0.50 m
```

`merge_distance_m` 可先保留：

```text
0.35 m
```

后续根据真实走廊测试再调。

---

## 6.4 新 Place 的 hysteresis

避免 0.49/0.51m 附近抖动。

推荐：

```text
create threshold = 0.50m
stay/remerge threshold = 0.35m
```

或者要求：

```text
forward displacement >= threshold
AND
连续 2 次 observation 支持已经离开当前 Place
```

但不要为了 hysteresis 增加数秒控制延迟。

---

## 6.5 PlaceObservation provenance 增强

每次记录：

```json
{
  "place_resolution": {
    "previous_place_id": "P1",
    "resolved_place_id": "P1",
    "created": false,
    "reason": "ROTATION_ONLY_NO_RELOCATION",
    "wheel_displacement_m": 0.012,
    "raw_pslam_displacement_m": 3000.0,
    "pslam_pose_accepted": false
  }
}
```

---

# 7. P0：系统只能有一个 canonical current Place ID

## 7.1 当前必须消灭的状态

绝对不能再出现：

```text
decision.current_place_id = P1
local_scan.provenance.place_id = P10
```

---

## 7.2 Source of truth

一次 observation 完成后：

```text
place_graph.register_observation()
→ resolved place_id
```

这个 `place_id` 成为当前 cycle 唯一 canonical Place。

所有下游必须使用它：

```text
SemanticEntityGraph.current_place_id
SemanticNavigationGraph current place
planner current_place_id
frontier parent place
LOCAL_SCAN state key
decision record
motion target place
WebUI current place
```

---

## 7.3 添加 runtime invariant

开发/测试模式：

```python
assert decision.current_place_id == local_scan_place_id
```

真机正式模式不要因为 assertion 让机器人异常运动，改成：

```text
记录 PLACE_IDENTITY_MISMATCH
停止自主动作
session 进入安全 degraded/error
```

绝不能在 mismatch 下继续规划旋转。

---

## 7.4 回归测试

新增：

```text
tests/test_place_identity_invariant.py
```

输入模拟：

```text
5 次 turn-only
raw pslam 每次错误跳 10m
wheel displacement ≈ 0
```

要求：

```text
Place count = 1
current_place_id = P1
LOCAL_SCAN key = P1
decision current_place = P1
```

---

# 8. P1：LOCAL_SCAN 修成稳定的“同 Place 有限扫描”，不再重复 +30° step1

## 8.1 不要先粗暴改候选顺序

已有候选：

```text
+30
-30
+60
-60
```

已有：

```text
same direction penalty
recent sector penalty
quota
```

当前主要问题是 Place 每次变，状态重置。

因此顺序：

```text
先修 Place
再验证 local_scan
最后才调整 scoring
```

---

## 8.2 状态必须绑定 canonical Place

正确：

```text
P1:
step 1/3
step 2/3
step 3/3
quota exhausted
```

禁止：

```text
P1 step1
P2 step1
P3 step1
```

---

## 8.3 增加全局死循环保险

即使以后 PlaceGraph 再异常，也增加 secondary guard：

```text
same_direction_consecutive_count >= 2
AND no_new_information
→ 禁止再次同方向 +30°
```

“新信息”至少包括：

```text
新 heading sector
新 persistent object
新 confirmed object relation
新 frontier
target state 从 ABSENT→POSSIBLE
target confidence 显著提升
真实新 Place
```

---

## 8.4 reached sector 必须用实际 yaw

动作后：

```text
requested +30°
```

不能直接认为：

```text
reached +30°
```

必须读实际 wheel odom yaw：

```text
sector_before
requested_dyaw
observed_dyaw
sector_reached
```

更新 heading coverage。

---

## 8.5 LOCAL_SCAN 单测

新增/扩展：

```text
tests/test_local_scan.py
tests/test_local_scan_place_stability.py
```

测试：

```text
initial yaw = 0
same P1
max_local_rotations = 3
无新目标
```

要求：

```text
不能输出 +30,+30,+30,+30,+30
最多 3 个 local scan action
step 不能反复回 1
quota 后退出 LOCAL_SCAN
```

---

# 9. P1：目标检测改成 ABSENT / POSSIBLE / PRESENT 三态，并加入运动抢占

## 9.1 为什么必须三态

目标：

```text
白色垃圾桶
```

视觉可能清楚看到：

```text
“垃圾桶”
```

但因为：

```text
颜色光照
遮挡
边缘裁切
bbox 不稳定
```

暂时不能确认“白色”。

此时不应该：

```text
found=false
→ 直接左转
```

应该：

```text
POSSIBLE
→ 停止
→ 再观察/裁剪验证
```

---

## 9.2 新统一结构

推荐：

```python
class TargetState(str, Enum):
    ABSENT = "absent"
    POSSIBLE = "possible"
    PRESENT = "present"
```

Quick 输出：

```json
{
  "target_state": "possible",
  "target_decision": {
    "target_text": "白色垃圾桶",
    "state": "possible",
    "confidence": 0.58,
    "reason_zh": "识别到垃圾桶，但颜色受光照影响暂不能确认"
  },
  "target_candidates": [
    {
      "frame_object_id": "target_candidate_001",
      "name_zh": "垃圾桶",
      "bbox_2d": [0.02, 0.30, 0.20, 0.90],
      "confidence": 0.65,
      "uncertain_attributes": ["白色"]
    }
  ],
  "scene_objects_light": [...]
}
```

保留向后兼容字段：

```text
target_decision.is_present = state == PRESENT
```

但所有新控制逻辑改用 `state`。

---

## 9.3 修改 Quick prompt

明确告诉 VLM：

```text
如果类别匹配目标，但颜色/属性/状态因遮挡、边缘、光照不确定，
必须返回 possible，不要返回 absent。

例如：
目标=白色垃圾桶
看到垃圾桶，但颜色无法可靠判断
→ possible
```

同时控制输出体积，不能让 Quick 变成 Full Semantic。

---

## 9.4 bbox 异常不能直接把“视觉候选存在”抹掉

当前如果：

```text
found=true
但 bbox normalize 失败
```

会导致 target_objects 为空，然后 gate false。

应区分：

```text
semantic presence
spatial grounding validity
```

例如：

```text
state=POSSIBLE
reason=BBOX_INVALID_REQUIRES_REOBSERVE
```

而不是直接 ABSENT。

---

## 9.5 运动抢占状态机

主循环在任何规划动作前：

```text
if target_state == PRESENT:
    stop
    verify
    if confirmed:
        TARGET_FOUND
    else:
        reobserve

if target_state == POSSIBLE:
    stop
    禁止创建 motion command
    等待 settle
    抓取“运动完成后的新帧”
    crop verify / fresh-frame verify
    最多 N 次
    最终转 PRESENT 或 ABSENT

if target_state == ABSENT:
    允许 planner 规划
```

**任何 POSSIBLE 未决期间 motion_steps 必须保持不变。**

---

## 9.6 不要用固定 sleep 冒充停稳

新增/复用 settle detector：

```text
wheel linear speed < threshold
wheel yaw rate < threshold
持续 0.6~1.0s
且 frame.capture_timestamp > motion_completed_timestamp
```

建议默认：

```text
settle = 0.8s
```

然后才取 fresh RGB-D。

---

## 9.7 POSSIBLE 多帧策略

建议：

```text
attempt 1：
full frame

attempt 2：
200~400ms 后 fresh frame

若有 bbox：
crop 放大验证 + full-frame context
```

最多 2~3 次。

防止因为 POSSIBLE 永久卡住。

---

## 9.8 目标感知测试

新增：

```text
tests/test_target_tri_state.py
tests/test_target_motion_preemption.py
```

案例：

### Case A

```text
目标=白色垃圾桶
模型输出 category=垃圾桶
颜色 uncertain
```

要求：

```text
POSSIBLE
motion command = None
```

### Case B

```text
POSSIBLE → verify PRESENT
```

要求：

```text
TARGET_FOUND
motion_steps 无增加
```

### Case C

```text
POSSIBLE → 连续验证排除
```

要求：

```text
ABSENT
然后才能恢复 planner
```

### Case D

```text
found=true 但 bbox invalid
```

要求：

```text
不应立即 ABSENT
应至少进入 POSSIBLE/reobserve
```

---

# 10. P1：Quick 路径直接生成轻量 OBJECT↔OBJECT 几何关系

## 10.1 新增模块

推荐：

```text
app/spatial/geometric_relation_extractor.py
```

输入用当前已经有的数据：

```text
frame_object_id
bbox_2d
depth_m
camera_xyz
bearing_deg
confidence
```

输出：

```python
{
    "subject_id": "semantic_obj_003",
    "object_id": "semantic_obj_005",
    "relation": "left_of",
    "confidence": 0.82,
    "description_zh": "...",
    "provenance": {
        "source": "rgbd_geometric_relation",
        "frame_id": "..."
    }
}
```

必须使用 frame object identity，不能用 label 当 identity。

---

## 10.2 可安全本地推导的关系

### LEFT_OF / RIGHT_OF

同帧 bbox center：

```text
Δcx > threshold
```

建议 normalized center separation：

```text
> 0.10~0.15
```

### ABOVE / BELOW

bbox vertical center：

```text
Δcy > threshold
```

注意图像 y 向下。

### IN_FRONT_OF / BEHIND

两物体都有可靠 depth：

```text
abs(depth_a - depth_b) > 0.30~0.40m
```

### NEAR

两物体都有可靠 `camera_xyz`：

```text
3D euclidean distance < configurable threshold
```

推荐初始：

```text
0.8m
```

根据室内尺度后续调。

---

## 10.3 不要过度推理

以下关系不要只凭 2D bbox 就强推：

```text
ON
INSIDE
CONTAINS
ATTACHED_TO
BLOCKS
```

这些继续主要来自 Full Semantic，除非有可靠几何规则。

---

## 10.4 关系融合

每帧：

```text
quick geometric relations
+
current-frame Full Semantic relations（如果完成）
```

先 normalize/dedupe：

```text
(subject frame id, relation, object frame id)
```

再进入已有：

```text
ObjectRelationStore.sync()
```

由 persistent association 做：

```text
frame_object_id
→ persistent obj_xxx
```

不能跳过 identity bridge。

---

## 10.5 provenance

ObjectRelationStore 中每条 relation 至少标：

```text
source:
rgbd_geometric_relation
full_semantic_vlm
merged_evidence
```

后续 UI 可区分。

---

## 10.6 关系确认

保留现有“多次观测确认”思想：

```text
第 1 次：TENTATIVE
第 2 次同向证据：CONFIRMED
```

对于 view-relative：

```text
LEFT_OF / RIGHT_OF / FRONT / BEHIND
```

不要把跨大视角变化的矛盾关系永久当结构事实。

可继续通过 `relation_scope=VIEW_RELATIVE` 管理。

---

# 11. P1：重构 WebUI 拓扑展示，让“真实拓扑”而不是 OBSERVED_FROM 星型图成为主视图

## 11.1 三种边必须分层

### Navigation topology

```text
P1 --MOVED_TO--> P2
P2 --MOVED_TO--> P3
```

### Object topology

```text
chair --LEFT_OF--> desk
monitor --ABOVE/ON--> desk
bin --NEAR--> cabinet
```

### Observation provenance

```text
P1 --OBSERVED_FROM--> obj_x
```

`OBSERVED_FROM` 不是物体之间的拓扑关系。

---

## 11.2 WebUI 视图建议

提供：

```text
[导航拓扑]
[物体拓扑]
[观测来源]
```

或者至少提供 toggle。

默认“语义拓扑”应优先展示：

```text
OBJECT↔OBJECT relation
```

`OBSERVED_FROM`：

```text
默认隐藏
或细线/低透明
```

不能再让一个 `P1` 中心节点把整个图变成星型。

---

## 11.3 Object topology 必须显示状态

节点：

```text
TENTATIVE
CONFIRMED
STALE
```

边：

```text
relation
confidence
observation_count
source
```

例如：

```text
obj_014 垃圾桶
CONFIRMED ×3

obj_014 --NEAR(0.84, ×2)--> obj_004 柜子
```

---

# 12. P1：新搜索 session 必须拥有干净的 live topology

## 12.1 修 `_restore_latest_session()`

历史 session 可以恢复供历史查看。

但是当 newest session 已 terminal：

```text
TARGET_FOUND
FAILED
MAX_STEPS_REACHED
CANCELLED
```

WebUI 主 live state 不应该假装它还是当前搜索。

应变成：

```text
Live:
IDLE
empty live graph

History:
保留 last archived session
```

---

## 12.2 新 session start 原子 reset

开始搜索时 reset：

```text
live topology
live frontier projection
current canonical Place
local_scan_states
negative evidence scoped to session
temporary target candidates
object relation store scoped to session
3D display accumulator reset marker
```

注意：

如果系统设计上部分长期语义 memory 本来应该跨 session 保留，必须与：

```text
live current-session graph
```

分开，不能直接投影进 live UI。

---

## 12.3 第一帧之前

正确：

```text
Live graph = empty
```

第一帧真实 observation 后才创建：

```text
P1
```

Frontier 也必须基于当前真实 heading coverage 再生成。

---

## 12.4 WebUI session 测试

新增：

```text
tests/test_search_session_live_reset.py
```

场景：

```text
archive 中有 old terminal:
P1, F01, F02...
```

新 WebUI process 启动：

```text
history 可找到旧 session
live graph 必须空
```

点击 start：

```text
仍先空
第一 observation 后才 P1
```

---

# 13. P0/P1：加强 3D accumulator，不能再用坏 LIO 污染永久地图

## 13.1 保留已有 frame contract

继续要求：

```text
aligned_scan.header.frame_id == pslam_odom
```

否则：

```text
必须 timestamped TF transform
或 drop
```

禁止 relabel sensor frame。

---

## 13.2 不能只用 pslam 自己判断 pslam 是否正常

当前 bridge 主要订阅：

```text
/go2w/slam/odom_base
```

建议增加只读订阅：

```text
/go2w/odom/fused
```

只用于 consistency gate。

强调：

```text
wheel odom 不参与 pslam world coordinate transform
只作为“机器人是否真的平移/旋转”的独立运动证据
```

---

## 13.3 最好接入 authoritative motion state

先 `rg` 找现有：

```text
motion_active
/go2w/motion action state
motion completed event
```

优先用现有 authoritative state。

若没有，再用 wheel twist 推断。

---

## 13.4 Rotation-only drift gate

每个动作窗口记录：

```text
wheel pose before/after
pslam pose before/after
```

如果：

```text
requested forward = 0
wheel Δxy < 0.08m
pslam Δxy > 0.20m
```

立即：

```text
mapping_health = DEGRADED_LIO_DRIFT
lio_pose_valid = false
```

然后：

```text
latest scan 可以作为 debug
禁止 permanent voxel update
禁止 map_3d 替换 last-good cloud
```

---

## 13.5 map_3d 也要经过 health gate

当前 `_on_map()` 可能清空 cloud 再用 upstream global map 替换。

必须保证：

```text
mapping health DEGRADED
→ 不让可疑 map_3d 清掉 last-good cloud
```

保留：

```text
last good map
```

WebUI 明确显示：

```text
Mapping degraded: LIO drift detected
Last-good map frozen
```

---

## 13.6 snapshot 增加字段

例如：

```json
{
  "mapping_health": "DEGRADED_LIO_DRIFT",
  "lio_pose_valid": false,
  "wheel_delta_xy_m": 0.012,
  "pslam_delta_xy_m": 8.34,
  "pslam_delta_yaw_deg": 30.1,
  "last_good_pose": {...},
  "received_scans": 100,
  "accumulated_scans": 23,
  "dropped_reason_counts": {
    "motion_active": 20,
    "lio_drift_rejected": 57
  }
}
```

---

## 13.7 地图恢复策略

默认：

```text
出现 LIO drift
→ freeze permanent cloud
→ 继续 health monitor
```

连续 N 个 valid samples 后：

```text
RECOVERING
```

是否继续在原 map 上增量融合，要保守。

如果 world transform 已经明显跳变：

```text
不要把新坐标直接接到 old cloud
```

更安全：

```text
要求 mapping session reset
```

并在 UI/日志明确提示。

---

# 14. P1：Full Semantic 必须成为非致命后台增强器

## 14.1 原则

实时控制依赖：

```text
Quick target perception
```

Full Semantic 只负责：

```text
更多 objects
复杂 relations
scene summary
memory enrichment
```

因此：

```text
FULL_SEMANTIC_ERROR
```

不能结束自主搜索。

---

## 14.2 manager 处理

后台：

```python
try:
    ...
except FullSemanticError as exc:
    record_error()
    keep_latest_quick_semantic()
    continue_search()
```

不能让 future/thread exception 在 poll 时重新抛到主循环杀死 worker。

---

## 14.3 限制 Full Semantic 输出

Prompt/schema 限制：

```text
max objects
max relations
max descriptions
max output tokens
```

尽量使用 provider 支持的：

```text
JSON mode
structured output
schema
```

防止 65 秒后 JSON 被截断。

---

## 14.4 测试

新增：

```text
tests/test_full_semantic_nonfatal.py
```

注入：

```text
Full Semantic timeout
JSON truncation
parse error
```

要求：

```text
session 继续
Quick 仍可规划
semantic health=DEGRADED
不出现 WORKER_INTERRUPTED
```

---

# 15. 配置项统一化

不要把阈值散落代码。

建议加入现有 settings 系统，例如：

```text
GO2W_LIO_ROTATION_WHEEL_MAX_TRANSLATION_M=0.08
GO2W_LIO_ROTATION_PSLAM_MAX_TRANSLATION_M=0.20
GO2W_LIO_MAX_POSE_SPEED_MPS=2.0
GO2W_LIO_RECOVERY_VALID_SAMPLES=4

GO2W_PLACE_MERGE_DISTANCE_M=0.35
GO2W_PLACE_RELOCATION_MIN_M=0.50

GO2W_PERCEPTION_SETTLE_SECONDS=0.8
GO2W_TARGET_POSSIBLE_VERIFY_ATTEMPTS=2
GO2W_TARGET_POSSIBLE_REOBSERVE_DELAY_SECONDS=0.3

GO2W_RELATION_LEFT_RIGHT_MIN_NORM=0.12
GO2W_RELATION_DEPTH_ORDER_MIN_M=0.35
GO2W_RELATION_NEAR_MAX_M=0.80
```

必须：

```text
有默认值
有范围校验
有 README/.env.example 注释
启动日志打印关键生效值
```

---

# 16. 日志与可观测性必须一起改

## 16.1 新事件

> **2026-09-16 对齐**：本节原是一张纯名单，11 个名字里有 7 个**全仓零发射点**、
> 4 个只在离线解析器 `scripts/go2w/analyze_search_run.py` 里被接受过，且
> git 历史里从未有过生产者。真正在真机上跑着的是**另一批名字**，而验收工具
> **早就同时接受两套**（见下表的"消费端"列）。所以缺的不是代码，是这张表和
> 现实之间的对齐 —— 改代码去凑一个从没被任何消费者依赖过的名字，只会让 4 个
> 有真机实证的名字丢掉历史可比性。下表是逐条核对后的现状。

| 计划书名字 | 实际状态 | 发射点 / 消费端 |
|---|---|---|
| `lio_pose_validation` | **改名**：实际事件叫 `spatial_pose_validation` | 发射 `run_semantic_exploration.py:1823`；消费 `analyze_search_run.py:148`（两个名字都收） |
| `geometric_relations_generated` | **改名**：实际叫 `geometric_relations_extracted` | 发射 `run_semantic_exploration.py:1704` |
| `target_state_changed` | **不是独立事件**：它是 `observation` 事件里的一个字段 | `autonomous_explorer.py` 的 `self._emit("observation", ..., target_state=...)`；`analyze_search_run.py:144` 把 `observation` 与这个名字并列收，取的是同一个 `target_state` 字段 |
| `target_possible_verify` | **拆成两个**：`target_possible_reobserve` + `target_possible_resolved_absent` | `autonomous_explorer.py:757` / `:774`；`analyze_search_run.py:144` 也收这个旧名 |
| `mapping_health_changed` | ✅ **本次接上** | 发射 `run_semantic_exploration.py:1873`，仅在 health 变化时发；消费 `analyze_search_run.py:148` |
| `place_identity_mismatch` | ✅ **本次接上** | 发射 `autonomous_explorer.py:1101`；消费 `analyze_search_run.py:133` |
| `scan_rejected_lio_drift` | ✅ **本次接上** | 发射 `run_semantic_exploration.py:1887`，**只在 `reason_code == "LIO_DRIFT_DURING_ROTATION"` 时发** |
| `place_resolution` | **缺定义** | 全仓零命中。本文档只给了名字没给语义，且没有可对应的现成状态机；在下一次改这一节之前不要实现它，否则没人知道该断什么言 |
| `live_session_reset` | **阻塞**（不是"还没做"） | 唯一有意义的落点是 SLAM 会话边界（`ros2_ws/src/plain_slam_ros2`），本次为不动正在跑的 LIO 栈而未解除禁改；替代仪表 `mapping_session_id` 已由 `webui_map_snapshot_writer.py` 写入快照，`scripts/go2w/reset_plain_slam_session.sh` 的第 [2/6] 步可以读它 |
| `imu_timestamp_health` | **不接**，且说明原因 | 唯一检测器 `artifacts/.../imu_clock_watch.py` 是离线工具，其自陈数据 15.4 分钟仅漂 22.7 ms、且数据段差 5.4 分钟没盖到发散点。**没有校准过的阈值就不写进门** —— 接了就是又一个假绿灯 |

**两个健康词表不要合并**（这一节最容易踩的坑）：

| | 数据源 | 取值 | 写在哪 |
|---|---|---|---|
| `mapping_health`（就绪闸门） | `[GO2W-DIAG]` 的 `iter`/`fix_t` | `HEALTHY` / `DEGRADED_LIO_DRIFT` / 不写 | `webui_map_snapshot_writer.py` 写快照，`web_server.py:377` 读 |
| `mapping_health_changed`（事件流） | `SpatialPoseValidator` 的轮式-激光一致性判定 | `HEALTHY` / `DEGRADED` / `RECOVERING` / `REJECTED` | `run_semantic_exploration.py` 发事件 |

两者名字像、含义不同、数据源不同。**不要为了整齐把词表统一**：闸门问的是
"LIO 的激光修正是不是停了"，事件问的是"这一帧的位姿与轮式里程计一致吗"。

---

## 16.2 每次 motion 后统一记录

```json
{
  "event": "motion_observation_summary",
  "requested": {
    "turn_deg": 30.0,
    "forward_m": 0.0
  },
  "wheel": {
    "delta_xy_m": 0.013,
    "delta_yaw_deg": 30.4
  },
  "pslam": {
    "delta_xy_m": 2.4,
    "delta_yaw_deg": 29.8,
    "accepted": false,
    "reason": "ROTATION_TRANSLATION_DRIFT"
  }
}
```

这是以后排查最重要的数据之一。

---

## 16.3 WebUI 状态栏

至少显示：

```text
Quick target:
ABSENT / POSSIBLE / PRESENT

Quick VLM:
OK / RETRY / ERROR

Full Semantic:
FRESH / PENDING / DEGRADED / ERROR

LIO:
HEALTHY / DEGRADED / RECOVERING

IMU timing:
SENSOR_STAMP / OFFSET_ALIGNED / INVALID

Current canonical Place:
P1

Wheel motion:
Δxy / Δyaw

PSLAM:
raw Δxy / accepted/rejected

3D map:
LIVE / FROZEN_LAST_GOOD
```

---

# 17. 必须新增“最新异常日志离线回放回归”

## 17.1 不要只写人工构造 unit test

最新失败：

```text
outputs/live_runs/search_20260901_122525_25291a89/events.jsonl
```

就是极好的 regression fixture。

---

## 17.2 新分析/回放工具

推荐：

```text
scripts/go2w/analyze_search_run.py
```

用法：

```bash
python scripts/go2w/analyze_search_run.py \
  outputs/live_runs/search_20260901_122525_25291a89/events.jsonl \
  --output outputs/replay_analysis_122525.json
```

统计：

```text
pslam max radius
pslam per-cycle delta
wheel per-cycle displacement
Place count
local_scan step sequence
turn direction sequence
current_place mismatches
target state sequence
Full Semantic failures
mapping health decisions
```

---

## 17.3 用历史故障做自动断言

建立：

```text
tests/test_regression_search_20260901_122525.py
```

可以从日志提取精简 fixture，避免测试加载 148MB 全部日志。

必须验证修复后的逻辑面对同类输入会：

```text
raw pslam 发散
→ validator reject

rotation only
→ Place 保持 P1

Place 不变
→ LOCAL_SCAN step 连续递增

坏 pslam
→ 3D accumulator reject

navigation current place
== local scan current place
```

---

# 18. 离线测试矩阵

在任何真机自主运动前，必须全部通过。

## 18.1 语法/静态

```bash
python -m compileall app scripts ros2_ws/src/go2w_plain_slam_bridge
```

如果项目有：

```text
ruff
mypy
```

按现有 CI 使用，不要擅自引入大量新 linter。

---

## 18.2 定向 pytest

至少：

```bash
python -m pytest -q \
  tests/test_imu_timestamp_aligner.py \
  tests/test_spatial_pose_validator.py \
  tests/test_place_graph.py \
  tests/test_place_identity_invariant.py \
  tests/test_local_scan.py \
  tests/test_local_scan_place_stability.py \
  tests/test_target_tri_state.py \
  tests/test_target_motion_preemption.py \
  tests/test_geometric_relation_extractor.py \
  tests/test_object_relation_store.py \
  tests/test_search_session_live_reset.py \
  tests/test_plain_slam_web_bridge_gate.py \
  tests/test_full_semantic_nonfatal.py \
  tests/test_regression_search_20260901_122525.py
```

若现有文件名不同，则在实际测试目录中合并，但功能覆盖不能减少。

---

## 18.3 全量测试

```bash
python -m pytest tests/ -q
```

区分：

```text
代码失败
环境依赖 skip
ROS/GPU unavailable
```

不能把真正 failed 说成“环境原因”跳过。

---

# 19. ROS2 构建与无运动健康检查

## 19.1 构建

按当前 workspace：

```bash
cd ros2_ws
colcon build --symlink-install
source install/setup.bash
```

若需要只构建：

```bash
colcon build --symlink-install --packages-select go2w_plain_slam_bridge
```

返回仓库根目录。

---

## 19.2 无运动健康检查

机器狗不动，先：

```bash
bash scripts/go2w/check_go2w_ready.sh --json
```

再检查：

```text
IMU topic frequency
IMU corrected stamp monotonic
Pandar frequency
/go2w/slam/odom_base frequency
/go2w/odom/fused frequency
aligned_scan frame_id
TF tree
```

保存报告。

---

# 20. 真机验收必须分级，不允许直接跑 10 分钟自主搜索

> 全程操作员持遥控器/急停；环境清空；先小步验证。  
> 任一级失败，停止进入下一等级。

---

# 21. 验收 A：静止 LIO / timestamp

## 操作

机器人完全静止：

```text
30~60 秒
```

记录：

```text
IMU
Pandar
pslam odom
wheel odom
aligned scan
mapping snapshot
```

## 通过条件

建议：

```text
IMU corrected stamp 单调
IMU dt p95 合理稳定
无 callback-jitter 型大跳

pslam 60s XY drift:
目标 < 0.10m
最多允许 < 0.20m

mapping_health = HEALTHY
```

若静止就发散，禁止继续旋转测试。

---

# 22. 验收 B：3 × 30° 原地旋转

先只做：

```text
+30°
停稳
+30°
停稳
+30°
停稳
```

每次保存：

```text
wheel before/after
pslam before/after
IMU yaw
aligned scan
WebUI screenshot
```

## 通过条件

每次：

```text
wheel Δxy < 0.08m

pslam Δxy:
目标 < 0.10m
硬上限 < 0.20m

observed yaw:
约 30°，方向正确

Place count = 1
current_place = P1
```

3D：

```text
同一墙/桌/柱不能出现明显三份旋转重影
```

LOCAL_SCAN：

```text
同 Place step 正常推进
不能每次 step1
```

失败立即停，不进入 360°。

---

# 23. 验收 C：360° 原地旋转闭环

只有 B 通过才执行。

建议：

```text
12 × 30°
每次停稳并采 fresh frame
```

通过条件：

```text
Place count = 1

wheel net Δxy < 0.10~0.15m

pslam end-to-start XY:
目标 < 0.15m
最大可接受 < 0.25m

yaw 闭环误差:
建议 < 10°

3D 场景结构闭合
不出现 12 份扇形副本
```

如果：

```text
wheel 不动
pslam >0.5m
```

直接判 LIO 专项未通过，不继续自主导航。

---

# 24. 验收 D：真实平移后 Place 创建

找安全直线空旷区域。

动作：

```text
P1 静止观察
向前约 0.6~1.0m
停稳
重新观察
```

通过：

```text
只创建一个 P2
P1 --MOVED_TO--> P2
```

然后在 P2 原地转：

```text
Place 仍是 P2
```

3D：

```text
新视角点云在同一 world 中补充地图
不是复制一份场景
```

---

# 25. 验收 E：OBJECT topology

场景摆 3~5 个稳定物体，例如：

```text
椅子
柜子
纸箱
垃圾桶
桌子
```

同一 Place 从至少两个视角观察。

要求：

```text
objects 有 persistent obj_xxx identity
```

产生至少部分：

```text
LEFT_OF / RIGHT_OF
NEAR
IN_FRONT_OF / BEHIND
ABOVE / BELOW
```

至少重复两次的稳定 relation 应：

```text
TENTATIVE → CONFIRMED
```

WebUI 默认不能只剩：

```text
P1 --OBSERVED_FROM--> 所有物体
```

应该可以直接看到：

```text
OBJECT↔OBJECT graph
```

---

# 26. 验收 F：目标 POSSIBLE 抢占

目标：

```text
白色垃圾桶
```

把目标放在：

```text
画面边缘
部分遮挡
颜色有轻微光照变化
```

预期：

```text
Quick = POSSIBLE
→ motion command 不生成
→ robot 保持停止
→ fresh frame/crop verify
→ PRESENT
→ TARGET_FOUND
```

关键断言：

```text
POSSIBLE unresolved 期间：
motion_steps 不增加
```

---

# 27. 验收 G：Full Semantic 故障注入

主动模拟一次：

```text
timeout
或 truncated JSON
```

必须：

```text
Full Semantic = DEGRADED/ERROR
Quick 仍正常
自主搜索继续
机器人不发生异常动作
session 不因为它变 WORKER_INTERRUPTED
```

---

# 28. 验收 H：短周期完整自主搜索

只有 A~G 全通过才运行。

建议先：

```text
max_local_rotations = 3
max_motion_steps = 5
max_planning_cycles = 10~15
operator supervised
```

目标使用现场可验证目标。

要求：

```text
1. 搜索前 live graph 空
2. 第一 observation 后才 P1
3. 原地旋转不创建 P2
4. LOCAL_SCAN 不无限同方向
5. 真位移后才 P2
6. MOVED_TO 正确
7. OBJECT relations 出现
8. 3D map 持续补全
9. mapping health 无异常
10. POSSIBLE 目标先停再 verify
11. Full Semantic 失败不杀主循环
12. 最终 TARGET_FOUND 或预算结束，不能 unclassified crash
```

---

# 29. 真机硬停止条件

任一出现，停止自主实验：

```text
IMU corrected timestamp 倒退

TF 出现 base_link 多父关系

rotation-only：
wheel Δxy < 0.08m
但 pslam Δxy > 0.50m

pslam pose 突然出现米级/千米级跳变

mapping_health = DEGRADED
但 permanent map 仍继续写

POSSIBLE target 未解决却执行 motion

decision.current_place_id != local_scan.place_id

机器人动作与 command 符号明显相反

急停/动作服务/odom 状态异常
```

不得为了跑完验收而继续。

---

# 30. 建议新增文件清单

执行 AI 可根据实际结构调整，但功能不要减少。

```text
app/spatial/spatial_pose_validator.py
app/spatial/geometric_relation_extractor.py

ros2_ws/src/go2w_plain_slam_bridge/.../imu_timestamp_aligner.py

scripts/go2w/validate_lio_time_extrinsics.py
scripts/go2w/analyze_search_run.py

tests/test_imu_timestamp_aligner.py
tests/test_spatial_pose_validator.py
tests/test_place_identity_invariant.py
tests/test_local_scan_place_stability.py
tests/test_target_tri_state.py
tests/test_target_motion_preemption.py
tests/test_geometric_relation_extractor.py
tests/test_search_session_live_reset.py
tests/test_plain_slam_web_bridge_gate.py
tests/test_full_semantic_nonfatal.py
tests/test_regression_search_20260901_122525.py
```

---

# 31. 必须重点修改的已有文件

根据现场 snapshot 已确认：

```text
app/spatial/place_graph.py
app/spatial/plain_slam_spatial_provider.py
app/spatial/object_relation_store.py
app/spatial/semantic_entity_graph.py

app/navigation/local_scan.py

app/detectors/siliconflow_vision_worker.py

app/manual_web_demo/search_session_service.py

scripts/go2w/run_semantic_exploration.py
scripts/go2w/plain_slam_web_bridge.py

ros2_ws/src/go2w_plain_slam_bridge/scripts/imu_fallback_adapter.py
```

另外执行 AI 必须通过 `rg` 找 root canonical 对应文件并修改正式运行副本。

---

# 32. 不要做的错误修法

## 32.1 不要只把 LOCAL_SCAN 候选改成先右转

错误：

```text
+30 重复
→ 把顺序改成 -30
```

这只会变成连续右转。

---

## 32.2 不要只把 Place merge_distance 调成几千米

这会掩盖 LIO 爆炸，不是修复。

---

## 32.3 不要拿 wheel odom x/y 硬塞到 pslam_odom

wheel odom 只做运动一致性证据。

世界坐标仍必须来自 valid pslam/SLAM。

---

## 32.4 不要在 WebUI 对错点云“逆旋转补偿”

aligned scan 本身如果世界位姿错，前端画图补偿无法恢复真实地图。

---

## 32.5 不要用更大的 voxel size 隐藏重影

voxel 只能降采样，不能修复 pose。

---

## 32.6 不要把 VLM 等待时间简单加长

问题不是“AI 思考不够久”。

正确是：

```text
fresh frame
tri-state target
motion preemption
verify
```

---

## 32.7 不要让 Full Semantic 阻塞实时控制

Full Semantic 应后台增强。

---

## 32.8 不要伪造外参 confirmed

必须通过数据验收。

---

# 33. 最终交付物

执行 AI 修改完成后必须生成：

```text
outputs/go2w_autonomous_search_fix_report.md
```

报告必须包含：

## 33.1 基线

```text
原 HEAD
工作分支
机器狗现场版本
修改的正式 runtime 路径
```

## 33.2 根因

至少分别写：

```text
LIO
Place
LOCAL_SCAN
target gate
object topology
WebUI session
Full Semantic
```

## 33.3 修改文件

逐文件：

```text
path
修改内容
为什么
对应测试
```

## 33.4 测试结果

```text
pytest
compileall
colcon
静止
3×30°
360°
平移 Place
OBJECT topology
target possible
Full Semantic injection
完整短搜索
```

每一项：

```text
PASS / FAIL / NOT RUN
证据文件
关键指标
```

## 33.5 SLAM 指标表

示例：

| Test | wheel Δxy | pslam Δxy | yaw | Place count | Mapping |
|---|---:|---:|---:|---:|---|
| static 60s | ... | ... | ... | 1 | PASS |
| turn 30 #1 | ... | ... | ... | 1 | PASS |
| turn 30 #2 | ... | ... | ... | 1 | PASS |
| 360 loop | ... | ... | ... | 1 | PASS |

---

# 34. 更新项目运行索引

验收后更新：

```text
docs/GITHUB_LATEST_RUNTIME.md
```

写入：

```text
新 commit
部署版本
最新 session id
LIO timestamp mode
extrinsic status
rotation drift 指标
Place count
3D map result
target preemption result
仍未解决风险
```

不要把旧异常记录删掉；标记为 historical regression evidence。

---

# 35. Git 交付

在所有离线测试通过后：

```bash
git diff --check
git status --short
```

建议至少分成逻辑清晰的 commits：

```text
fix: preserve and align imu sensor timestamps
fix: reject inconsistent pslam poses
fix: stabilize place identity and local scan
feat: add tri-state target preemption
feat: infer rgbd object relations
fix: isolate live topology and mapping health
test: add autonomous search regression suite
docs: record go2w acceptance results
```

如果用户没有明确要求 push：

```text
不要擅自 force push
不要改写历史
```

---

# 36. AI 最终回复格式

完成后不要只说“已完成”。

必须返回：

```text
1. 修改了什么
2. 根因是否被实测证实
3. 哪些测试 PASS
4. 哪些测试因为硬件/权限没跑
5. 真机旋转后的 pslam drift 实测数值
6. Place count 实测
7. LOCAL_SCAN 序列
8. OBJECT relation 数量
9. target POSSIBLE 抢占是否成功
10. 3D WebUI 是否还有扇形重影
11. commit id
12. 报告/日志路径
13. 剩余风险
```

---

# 37. Definition of Done

只有下面全部满足，才可以称本轮“完成”：

```text
[ ] 真实 IMU 不再无条件 stamp=callback now
[ ] timestamp jitter 单测通过
[ ] Pandar/IMU 外参与 deskew 已核查并记录状态

[ ] raw pslam pose 有 validator
[ ] 错误 pslam 不再标为可用 METRIC_LIDAR
[ ] rotation-only LIO drift 可被自动拒绝

[ ] run_semantic_exploration 传 observed_displacement_m
[ ] 原地旋转不创建新 Place
[ ] 真平移可以创建新 Place
[ ] planner / local scan / WebUI 使用同一个 current Place

[ ] LOCAL_SCAN 不再每轮 local_scan_001 / step1
[ ] 同方向死循环保护存在

[ ] target gate 为 ABSENT/POSSIBLE/PRESENT
[ ] POSSIBLE 时禁止运动
[ ] fresh frame/crop verify 已接入

[ ] Quick RGB-D 路径能生成至少基础 OBJECT↔OBJECT relation
[ ] relation 使用 frame_object_id→persistent_object_id identity
[ ] WebUI 可以突出 Object topology
[ ] OBSERVED_FROM 不再淹没关系图

[ ] WebUI 重启不会把旧 terminal topology 当 live graph
[ ] 新 session 第一 observation 前 live graph 为空

[ ] 3D accumulator 有 wheel-vs-pslam consistency gate
[ ] DEGRADED LIO 时 last-good map 冻结
[ ] 坏 map_3d 不会覆盖 good cloud

[ ] Full Semantic ERROR 非致命
[ ] 最新异常会话有自动 regression

[ ] 定向 pytest 全通过
[ ] 全量 pytest 无新增失败
[ ] ROS colcon build 通过

[ ] 静止验收通过
[ ] 3×30° 验收通过
[ ] 360° 验收通过
[ ] Place 平移验收通过
[ ] Object topology 验收通过
[ ] Target preemption 验收通过
[ ] Full Semantic injection 验收通过
[ ] 短周期完整自主搜索通过

[ ] outputs/go2w_autonomous_search_fix_report.md 已生成
[ ] docs/GITHUB_LATEST_RUNTIME.md 已更新
```

---

# 38. 修复后的预期行为示例

## 38.1 搜索刚开始

```text
Live topology:
(empty)
```

第一帧：

```text
P1
```

语义对象：

```text
obj_001 椅子
obj_002 柜子
obj_003 垃圾桶
```

Object topology：

```text
obj_001 --LEFT_OF--> obj_002
obj_003 --NEAR--> obj_002
```

---

## 38.2 第一次原地 +30°

```text
wheel Δxy = 0.01m
wheel Δyaw = +30.2°

pslam Δxy = 0.04m
pslam accepted

Place = P1
LOCAL_SCAN step = 1/3
```

---

## 38.3 第二次扫描

```text
Place = P1
LOCAL_SCAN step = 2/3
```

候选根据 coverage/info gain 选择，不再重置到 step1。

---

## 38.4 如果 pslam 再异常

例如：

```text
wheel Δxy = 0.01m
pslam Δxy = 3.4m
```

系统：

```text
LIO REJECTED
reason=ROTATION_TRANSLATION_DRIFT

Place 仍 P1
semantic map 不使用错误 world pose
3D permanent cloud freeze
WebUI 显示 DEGRADED_LIO_DRIFT
```

而不是：

```text
创建 P2
把 scan 累积到错误位置
```

---

## 38.5 看到疑似白色垃圾桶

```text
Quick:
POSSIBLE
```

系统：

```text
STOP
settle
fresh frame
crop verify
```

确认：

```text
PRESENT
TARGET_FOUND
```

期间绝不继续左转。

---

## 38.6 真正向前探索

机器人向前约 0.8m：

```text
wheel displacement supports relocation
validated pslam position supports relocation
```

创建：

```text
P1 --MOVED_TO--> P2
```

新扫描继续写入同一个 `pslam_odom` world：

```text
地图逐步扩展
不是复制/旋转旧场景
```

---

# 39. 最后原则

这次不能把三个现象分别做表面修补。

必须按因果链修：

```text
IMU timing / extrinsic / deskew
          ↓
valid LIO pose
          ↓
SpatialPoseValidator
          ↓
稳定 Place identity
          ↓
稳定 LOCAL_SCAN
          ↓
正确 navigation topology
```

同时并行修：

```text
Quick tri-state target
          ↓
POSSIBLE motion preemption
          ↓
不会把已经看到的目标转出画面
```

以及：

```text
Quick objects + RGB-D geometry
          ↓
ObjectRelationStore
          ↓
真正 OBJECT↔OBJECT topology
```

最后由：

```text
mapping health gate
```

保证任何未来 LIO 瞬时异常都不会再次污染：

```text
Place
Object world position
3D permanent map
自主规划
```

**优先保证世界模型的输入是可信的，再让 planner 变聪明。**
