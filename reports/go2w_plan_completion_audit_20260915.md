# Go2W 计划书实现完成度审计 · 2026-09-15

> **取代** `reports/go2w_plan_completion_audit.md`（2026-08-06）——那份审的是
> 更早的"禁止移动"计划（相机 TF / RGB-LiDAR 外参 / Level 0 / Nav2），
> 与本文覆盖的 7 份文档**没有交集**，其 5 个 `BLOCKED` 结论已被后续工作整体推翻。

**审计范围**：根级 7 份计划/排查文档
**审计基线**：分支 `fix/go2w-autonomous-search-lio-topology-target`，HEAD `56574d4`
**方法**：三路并行，读全文 → 拆条目 → 用 `git log -S` / `git show` / `grep` / 读源码逐条核实，**不采信文档自述状态**
**审计局限**：为不改动任何文件，绝大部分未运行 pytest；测试判定基于"断言 vs 实际配置"的静态比对。标 ⚠ 的条目需真机复跑才能定论。

> **2026-09-15 当日晚间进展**（本文件部分条目已随之更新，凡改动处均标 ✅）：
> 拆掉 §五 定时炸弹、修好 §四 两条红灯、裁决并落地 §11.2 的三视图冲突（见「§二·补」）。
> **仍未动**：§一 的可观测层（收益最大，是 LIO 发散问题的前置条件）、
> §七 的 5 条阻塞、§二 的 13 项零实现。

---

## 一、最重要的发现：这不是一堆散缺口，是同一个病

四条看似无关的发现，其实是**同一个故障**：

| 现象 | 证据 |
|---|---|
| 计划定义的 **11 个事件，6 个全仓零命中，4 个只存在于解析分支**（`analyze_search_run.py:133/137/141`），无任何发射点 | 已复核 |
| 真机快照里 `mapping_health` / `mapping_session_id` / `capacity_limited` / `accepted_maps` / `motion_episode_id` / `lio_pose_valid` **全为 null** | 已复核 |
| `web_server.py:377` 的 `slam_map_not_drifting` 门**恒真**（`None != "DEGRADED_LIO_DRIFT"`），从未拦过任何东西 | 已复核 |
| Pandar 计划 **§17 运行时自动质量判断整节未实现**，其中 §17.1 的 **LIO 检查（pose NaN / 位移爆发 / z 发散 / quaternion 归一化）完全空白** | 已复核 |

**共同病因：可观测层与安全联锁被设计了，但从未接到生产写入路径上。**

- 状态机 `app/spatial/slam_web_map_state.py::snapshot()` **实现了全套字段**；
  真机跑的却是另一条瘦写端 `scripts/go2w/webui_map_snapshot_writer.py`（其注释自陈"§10 那些概念拿不到的一律不编"）。
- 离线分析器 `analyze_search_run.py` **实现了目标三态/地图健康/运动摘要的解析**；
  但系统发出的是另一套私有事件名，**解析器永远匹配不到**。

**后果**：`analyze_search_run.py` 的这几项指标在真实数据上恒为空，
而**"空"看起来和"没问题"一模一样** —— 验收工具本身是假绿的。

**这正是 `calibrate-before-trusting-a-negative` 那条记忆警告的情形，
只不过这次假绿的是验收工具自己。**

**它直接解释了 2026-09-15 的困境**：当天 LIO 发散 3 次，
而"地图为什么没冻"、"发散时各诊断字段是什么"这些问题，**靠现有数据根本查不出来** ——
因为那一层数据从来没被写出来过。

---

## 二、"计划书里写了但从没动过"（零实现）

按危害排序。这些是**完全空白**，不是做了一半。

| # | 计划项 | 出处 | 危害 |
|---|---|---|---|
| 1 | **11 个事件契约**（`imu_timestamp_health`、`lio_pose_validation`、`mapping_health_changed`、`place_resolution`、`place_identity_mismatch`、`target_state_changed`、`target_possible_verify`、`geometric_relations_generated`、`scan_rejected_lio_drift`、`live_session_reset`、`motion_observation_summary`） | 拓扑计划 §16.1 | 见 §一。**验收工具脱钩** |
| 2 | **§17 运行时自动质量判断 + 四级评级 + `quality.json`** | Pandar 计划 §17 | "不要求人工专门操控"的**验收机制本身**没做。全部质量判断至今手动跑 |
| 2b | 其中 **§17.1 的 LIO 检查**：pose NaN / 位移爆发 / z 发散 / quaternion 归一化 | 同上 | **当前最痛的盲区**——09-15 三次发散无一被自动发现 |
| 3 | **§7.3 运行期 `PLACE_IDENTITY_MISMATCH` 不变量 + 发现即停** | 拓扑计划 §7.3 | 计划里**唯一**防"Place 串号导致拓扑错乱"的运行期防线。现在只有事后离线比对，**错已发生才看得见** |
| 4 | **§15 的 12 个 `GO2W_*` 配置项** | 拓扑计划 §15 | 全仓 0 命中，参数硬编码在构造函数默认值里，且关系阈值与计划建议区间不一致 —— **现场想改 `near_distance_m` 必须改代码** |
| 5 | **§16 的 4 个事件**：`quick_vlm_retry`、`quick_vlm_failed`、`slam_scan_dropped`、`slam_scan_accumulated` | 详细修改计划书 §16 | **Quick VLM 失败重试**与**扫描帧被丢弃**这两个最常发生的路径，日志里查不到 |
| 6 | **§4.3 `scripts/go2w/validate_lio_time_extrinsics.py`** | 拓扑计划 §4.3 | 不存在。替代品是**未提交**的离线分析脚本，§8 门限判据链缺可复跑 CLI |
| 7 | **§30 点名的 7 个测试文件** | 拓扑计划 §30 | `test_place_identity_invariant` / `test_local_scan_place_stability` / `test_target_tri_state` / `test_target_motion_preemption` / `test_search_session_live_reset` / `test_plain_slam_web_bridge_gate` / `test_full_semantic_nonfatal` —— 全缺 |
| 8 | **§22.1 quick VLM 无 bbox → GroundingDINO high-recall retry** | RAM++ 计划 §22.1 | 兜底链在"VLM 说有但给不出框"这一步断了 |
| 9 | **canonical aliases**（`armchair→chair` / `cell phone→phone` / `television→tv`） | RAM++ 计划 §53 | 计划自己预判的重复物体场景无处置；`SemanticObjectMap` 属禁改文件，作者选择留着 |
| 10 | **`DYNAMIC_LABELS = {person, dog, cat}` 不入持久地图** | 轻量化计划 §26 | **人和宠物会被当成永久地图 landmark** |
| 11 | **轻量 §19/§20 六个配置项** | 轻量化计划 §19/§20 | 其中 `sam2_min_box_score` 缺席是真浪费：**SAM2 现在对 NMS 后每一个 box 都跑**，正是计划要优化掉的开销 |
| 12 | **`app/mapping/semantic_object_map.py` 轻量模块（150–250 行）+ `chair_NNN` ID** | 轻量化计划 §10/§24.1 | 实际用了 700 行旧模块（带 `weights/anchors/merge_history/negative_evidence`）和 `obj_NNN`。**"轻量化"这个计划名下的核心交付物没按计划交付** |
| 13 | **`tests/fixtures/ram_semantic/` 六类场景数据集 + §44.2 RAM worker 用例 + §44.9 用例** | RAM++ 计划 §43/§44.2/§44.9 | **`operation=tag` 这条新协议路径零测试覆盖**，只靠两个手工脚本验过 |

---

## 二·补：「§11.2 三视图」的裁决（2026-09-15 已落地）

审计初稿把这条写成"两份计划冲突，需人工裁决"。**核查后发现是三份文档，而且现实现
既不满足计划1 也不满足计划2 —— 它严格照的是第三份。**

| 文档 | 时间 | 主张 |
|---|---|---|
| 一键整改与验收计划 §11.2 | 2026-09-01 13:30 | 三视图 `[导航拓扑][物体拓扑][观测来源]`，**或至少给 toggle**；默认物体优先 |
| 对象拓扑整改计划 §7.3/§7.4 | 2026-09-01 21:53 | 只接受 `OBJECT`；不出现 `P1/P2/F01/F02`；不出现 `OBSERVED_FROM` |
| 重影与碎片化修复计划书 步骤 2.1/2.2 | 2026-09-03 17:20 | **反过来**：保留 PLACE 与 `OBSERVED_FROM`，前端"放开过滤" |

前两份同一天相隔 8 小时、**互相零引用**。第三份晚两天且逐行指定了实现——
`search_map.js` 的 `NON_OBJECT_RELATIONS` 与节点过滤**与计划3 写的 diff 一字不差**。

**关键：计划3 治的是"碎成孤岛"，代价是"星型又回来了"**（它实测界面裂成 3 块，大小 `[14,6,6]`），
而"星型"恰恰是前两份计划要治的病。**真机数据支持这个担心**：翻 09-01~09-02 全部
`webui_state.json`，物体间关系边**几乎恒为 0**（6 个 run 只有 1 个跑出 5 条 `left_of`，
含 39 个 OBJECT 的大 run 也是 0 条）。所以"用 PLACE 连成一张图"连接的其实是
"都在 P1 看到的"，把关系提取失败糊成了看起来正常的图——与 §一 的假绿同源。

**裁决（用户 2026-09-15 批准）：两视图 + 一个 toggle**，即计划2 §11.2 自己给的出路
（"**或至少提供 toggle**"）——计划1 描述的是其中一个 tab，计划3 描述的是另一个：

```
[物体拓扑] 默认  ← 计划1 §7.4 契约：只有 OBJECT，关系没提出来就显示孤立点（难看但诚实）
[导航拓扑]       ← 计划3 契约：PLACE 中转站 + OBSERVED_FROM，判据 T2 连通分量==1
```

**不做**第三个「观测来源」视图（§10.1 过度设计）。
落地：`search_map.js` 新增 `objectTopologyStrict()` + `topologyView` 状态；
`index.html` 加 toggle；`search_ui.js` 让物体列表/计数跟随当前视图
（顺带修掉"P1 被算成 N 个物体"）。契约测试 `test_webui_object_topology_contract.js/.py`
新增 case 5–8，**27 个拓扑/UI 测试 + 43 个搜索回归全绿**。

---

## 二·补二：上面那张"物体拓扑"图为什么一直是孤点（2026-09-15 晚已修 + 已对照验证）

§二·补 落地了两视图，但默认那个「物体拓扑」画的是**孤立点**——根因不在前端，在
`run_semantic_exploration.py:1512`：

```python
elif latest_semantic is not None and not quick_scene_objects:   # 旧
elif latest_semantic is not None:                                # 新
```

只有 full 路径带关系（quick 的 `scene_relations` 是 `:1466` 硬写死的 `[]`），而 full 是异步提交的、
**几乎总是晚一拍到达**（本轮实测 `age_ms` 5.5~8.2 s，且 `semantic_source_frame_id` 恒等于**上一轮**
的 bundle），装配 observation 时 frame 已经对不上；旧条件又额外要求"quick 什么都没看见"，
而 quick 每次都看见物体 ⇒ **该分支永不进入**，full 独有的关系被整批丢掉。

**A/B 对照（`--dry-run-motion`，零运动指令，唯一差异就是那一行）**：

| 批次 | 轮次 | `full_older_frame` 命中 | 携带 VLM 关系 |
|---|---|---|---|
| `search_20260915_165742_dr03`（修复版） | 12 | **6** | **23 条** |
| `search_20260915_170228_ctl`（换回旧码） | 12 | **0** | —— |

（两批目标都选不在场的"红色雨伞"以强制跑满轮次；第 1 轮恒为 `fresh_full`——启动延迟让 full 赢竞速，
所以**只看第 2 轮起**才有分辨力。）

范围：证明的是关系**不再在采纳门被丢弃**（观察事件 `scene_relations` 里的 VLM 条数）。
再往下进 WebUI 拓扑页那一段（`object_relation_store` → `search_state_store` 深拷贝进
`webui_state.json`）这条批次**验不到**——裸 worker 不产出该产物。

## 二·补三：又一条假绿 —— WebUI「开始搜索」当前是坏的

按下后 ~0.5 s `FAILED`，`exit_code 4` / `WORKER_DEPENDENCY_MISSING` /
`ModuleNotFoundError: No module named 'pydantic'`。真因是环境变量而非代码：
`~/.bashrc:136` 的 `PYTHONNOUSERSITE=1` 被 WebUI 继承、又被 `search_executor.py:103`
原样传给 worker，而 worker 的 `/usr/bin/python3` 的 pydantic **只在 `~/.local`**。

**而 `ready` 照样是 `true`**：`_resolve_worker_python()` 的探测只
`import rclpy, run_semantic_exploration`（模块层——缺 pydantic 也能过，实测 `PROBE_OK`），
`search_worker_available` 又只表示"进程活着"。**探测查的正是自己不缺的那部分依赖。**
这与 §一 那条"假绿同源"的病是同一类。

**已修**（2026-09-15 晚，`search_executor.py:109` 一行 `env.pop("PYTHONNOUSERSITE", None)`）。
验证不需要重启 WebUI：进程内用真实 `SubprocessSearchExecutor`、父环境塞回该变量做 A/B——
带修复走到 `LOAD_PIPELINE`/`START_EXPLORER`，去掉修复则 `FAILED` +
`No module named 'pydantic'`（逐字复现原故障）。回归测试见
`tests/test_search_worker_python.py::test_worker_env_drops_pythonnousersite`。
⚠ 运行中的 WebUI 载入的仍是旧模块，**下次重启才生效**。

（注意：探测本身仍然只查模块层依赖，没改——它放行与否不影响结果，兜底总会返回
`/usr/bin/python3`，改它属无用改动。）

---

## 三、部分实现 —— 缺的那块有实际风险

| 计划项 | 差哪一部分 |
|---|---|
| **Pandar §19/§21 归档自证** | `provenance.json` 缺计划点名的 `plain_slam commit` 与 `point timestamp mode`；且 `configs/go2w/plain_slam_lock.yaml` 记 `commit: 0c4b610f…` 而实际 HEAD 是 `65a135f2…`（patch 被 commit 进仓库后 `git apply --check` 判 "already applied"，lock **永不更新**）→ **锁定语义完全不可信** |
| **详细修改 §8.2 感知失败码** | `RGB_IMAGE_ERROR` / `FRAME_MISMATCH` 只存在于 `autonomous_explorer.py:240-241` 的 **docstring**，无任何 `raise` 使用 → 相机/深度抓取失败会落到 `UNKNOWN_PERCEPTION_ERROR`，**现场定位走弯路** |
| **§5 SpatialPoseValidator 接入** | 校验器本体完整、运行期已接入并降级，但**位姿被拒后只有降级、没有计划要求的停栈/冻结联锁闭环**（冻结实际由 `MotionEpisodeGate` 独立完成，两条路不共享事件） |
| **§8.5/§8.6 外参动态标定** | 实现**被证否**：`failed_gate_count: 2`、`extrinsic_upgrade_allowed: false`，两个纯旋转段 pslam Δxy = **0.53 m / 0.72 m**，远超 0.20 m 门限。yaw 的 90° 修复是走**"轴向约定推理"旁路**做的，不是计划规定的流程，**升级闸门至今判 false** |
| **§29/§18.3 运动中建图闭环** | 语义探索与建图**各自跑通，联动从未验证**（`HANDOVER_PLAIN_SLAM_INTEGRATION.md:188-193` 自认"未发任何运动指令"）。09-01 验收 C 的"通过"只证明了门控动作正确，"无重影"这一实质条件从未达标 —— 真因（IMU 时间轴脱钩）到 09-11 `f3b493e` 才定位 |
| **§14 WebUI 3D box** | `observation_count` 已写进 payload（`webui_map_snapshot_writer.py:306`）**但前端不渲染**；且不是计划说的 three.js `BoxGeometry`，是 2D canvas 投影线框 |
| **§11.2 三视图** | ~~两份计划互相冲突~~ → **2026-09-15 已裁决并落地**。详见下方「§11.2 的裁决」一节 |
| **§34 `docs/GITHUB_LATEST_RUNTIME.md`** | 内容停在 **2026-09-01**，计划要求的新提交号/部署版本/会话 id/外参状态/Place 数等均无 → **接手人按它读会拿到错误状态** |
| **§15 统一 Observation 数据模型** | 字段齐全，但计划要的 `ObservationBundle`/`FrameBundle` 嵌套契约未建，实现为扁平字段。功能等价，形式未达 |

### 验收整体缺失（跨两份计划，第一梯队）

RAM++ 计划 §46–§54、轻量化 §28–§34 的**核心量化指标没有一条被真正测过**：
target recall ≥90%、多视角位置 RMS ≤0.15 m、box 尺寸 RSD ≤25%、20 张 crop ≥18 正确、
60 秒 VLM 效率、RAM 60 秒频率。

作者自己在 `reports/RAM_PLUS_SEMANTIC_PERCEPTION_VALIDATION.md` 的"诚实记录"里也写了：
`FINAL: PASS` 只覆盖无机器人的 A0–A11 范围。

两条具体的：
- **§52 的 3D 语义地图验收用的是合成深度**（`verify_ram_semantic_chain.py::synth_depth`）—— 只能证明链路通
- **§21 的 VLM sweep 是同步阻塞**，最坏阻塞 65 秒，真机上很可能撞上搜索 stale 保护

---

## 四、已存在的红灯（不修会掩盖新问题）

**`tests/test_plain_slam_config_generation.py` 有两条必然失败的过期断言**（已复核）：

| 行 | 断言 | 实际 |
|---|---|---|
| `:82` | `pointcloud_topic == "/go2w/slam/pandar_points"` | `configs/go2w/plain_slam.yaml:36` = `pandar_points_buffered` |
| `:117` | `acc_scale == 1.0` | `runtime/go2w/plain_slam/lio_3d_params.yaml:2` = `0.954`（`740cd23` 改的） |

任何人跑测试都会撞上，**且会掩盖新引入的失败**。

> **✅ 2026-09-15 已修**：两条断言各自补上依据注释（缓冲话题的 89 ms IMU 迟到推导、
> `acc_scale` 的 10.2795 实测重力模长推导），当前 `5 passed`。
> 根因：`740cd23`（09-10）改了配置但**从未同步测试文件**；
> 该红灯自 09-08 起就已被文档记录（`docs/GO2W_SLAM重构_交接书_20260908.md:344`）。

---

## 五、⚠ 定时炸弹（单行，但会毁掉已验证的成果）

`scripts/go2w/calibrate_pandarxt16_extrinsics.py:215` 硬编码
`init_yaw_rad=math.radians(11.357)`。

**任何人重跑标定，都会把 2026-09-15 刚实测验证的 90.0°（偏角 −77.4° → +0.38°）
覆盖回那个已被证伪的错值。** 交接书 §6 已列为未决项，至今未修。

> **✅ 2026-09-15 已拆**：`init_yaw_rad` 改为 `math.radians(90.0)`，并注明
> yaw 搜索范围是 ±15°（`estimate_scene_transform` 默认参数），从 11.357 出发
> 只能覆盖 `[−3.6°, 26.4°]`，**永远搜不到 90°** —— 所以它比"定时炸弹"更糟，
> 是"怎么搜都不可能对"。已实测确认新搜索窗 `[75°, 105°]` 含 90°。

---

## 六、文档自身的错误

- **故障排查表 `:457` 与 `:1213`** 两次声称 `scripts/go2w/start_rgbd_spatial_stack.sh`
  "未找到该脚本" —— **事实错误，文件存在**（5319 字节，2026-08-20）。已复核。
- **`docs/go2w/plain_slam_pandarxt16.md`** 开头写 "Status: implemented per 计划书"，
  而 §17 实际未实现 → **文档状态标记不可信**。
- **`outputs/go2w_plan23_report.md`** 宣称"验收 C 通过"，同文件 `:121-123` 自认
  "LIO 平面位姿仍有明显平移漂移"。该结论已被推翻。

---

## 七、仍存在的阻塞（非"未实现"，是被外部条件卡住）

1. **LIO 发散无可靠早期预警** —— 09-15 三次（11:31 / 12:40 / 13:20）。
   唯一两次都命中的判据在 13:20 **只提前 0.4 秒**，从坏帧到 `v` 跑飞仅 1 秒，**人工来不及**。
   → 计划1 §12 验收 **E/F 在解决发散前不可能通过**。
2. **`slam_map_not_drifting` 门恒真** —— 见 §一。发散时这道"不许开自主搜索"的闸门不会拦。
3. **`odom_fused → base_link` TF 缺失** —— 计划书 §9.4 有意为之，非缺陷。
4. **D435 static TF 仍 `candidate_unconfirmed`** —— 08-31 至今未动。
5. **`/utlidar/imu` 时钟阶跃假设** —— 方法已验证可测（15.4 分钟仅漂 22.7 ms），
   但现有袋子 13:07:33 结束、发散点 13:12:54，**差 5.4 分钟没盖到**。
   → 已加 `imu_clock_watch.py` 记录器，**下一轮跑动期间必须开着**。

---

## 八、建议动手顺序

**第一步（半天，收益最大）：把可观测层接回去。**
先把 §16.1 的事件名与实际发射点对齐 —— **或在计划书里把事件表改成代码实际产出的事件族**，
二者选一，但必须一致。否则 §17 回放回归和 §20–§28 验收都建立在解析不到数据的基础上。

同时把 `webui_map_snapshot_writer.py` 补上 `mapping_health` 等字段
（或让手动模式也走 `slam_web_map_state` 那条写端）。
**这两件做完，`slam_map_not_drifting` 那道恒真的门会自动开始起作用。**

> 这一条同时是 LIO 发散问题的前置条件：**没有这层数据，发散事故永远查不清。**

**第二步（单行，立刻做）：修 `calibrate_pandarxt16_extrinsics.py:215` 的硬编码 11.357°。**
定时炸弹，成本一分钟。 → **✅ 2026-09-15 已完成**

**第三步：修 `test_plain_slam_config_generation.py:82/:117` 两条过期断言。**
恢复测试作为护栏的资格。 → **✅ 2026-09-15 已完成**

**第四步：决定要不要做 LIO 自动看门狗。**（需用户点头，属新功能）
理由：唯一可用的判据在 13:20 只给 0.4 秒，**人工反应不可能覆盖**。

**第五步（按需）：** §17.1 的 LIO 检查（pose NaN / 位移爆发 / z 发散）
可以并入第一步一起做，它和看门狗是同一份数据。

---

## 附：审计方法学备忘

- 三路审计中，**每一路独立得出了"`reports/go2w_plan_completion_audit.md` 已过时"的结论** ——
  该文件日期 2026-08-06，审的是另一批计划，应加"已被取代"横幅或归档。
- 两路审计**各自独立**发现"计划的对外契约与代码实际产出是两套不重合的名字" ——
  这种跨路复现是本次审计里置信度最高的信号。
- 本文中标注"已复核"的条目（§一全部、§四、§五、§六第一条、
  §二第 5 条的 4 个事件）由主会话**亲自 grep/sed 验证过**，不是转述子代理结论。
