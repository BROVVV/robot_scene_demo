# robot_scene_demo 高层自主语义探索一次性实施计划书

> 版本：2026-08-17  
> 项目仓库：https://github.com/BROVVV/robot_scene_demo  
> 目标平台（当前实验）：Unitree Go2-W  
> 最终研究目标：可迁移到“底层定位、导航、避障、越野、传感器标定均已完成”的成熟机器狗上的高层自主语义探索系统  
> 当前阶段定义：**Operator-Supervised Autonomous Semantic Exploration Prototype（操作者监督下的自主语义探索实验原型）**

---

## 0. 给执行 AI 的最高优先级指令

如果你是一位拿到本计划书和 GitHub 链接的执行 AI，你的任务不是继续分析项目、写第二份计划，也不是只完成几个孤立模块，而是：

> **直接阅读当前仓库、复用已有代码、完成本计划全部软件改造、测试、文档与一键运行入口，使当前 Go2-W 能在操作者手持遥控器监督的条件下，执行连续的自主语义探索闭环。**

最终必须达到：

```text
自然语言目标
→ 实时观察
→ 目标/关系理解
→ 语义记忆更新
→ 自主选择下一探索目标/观察方向
→ 高层运动执行
→ 到达/动作完成
→ 再观察
→ Replan
→ ...
→ 目标视觉确认
→ TARGET_FOUND
→ STOP
```

执行时必须遵守以下原则。

### 0.1 不要重新从零设计已有系统

仓库已经存在并应优先复用：

```text
app/reasoning/semantic_navigation/
app/live_robot/
app/navigation/
app/planning/
app/memory/
app/video/
scripts/go2w/run_autonomous_loop.py
```

尤其现有：

```text
app/navigation/exploration_planner.py
app/navigation/candidate_goal_generator.py
app/navigation/navigation_planning_pipeline.py
app/navigation/models.py
app/navigation/nav2_*.py
app/live_robot/step_search_runner.py
app/live_robot/search_state_machine.py
app/live_robot/semantic_observer.py
app/live_robot/search_directive_adapter.py
app/reasoning/semantic_navigation/*
```

都必须先审计再复用。

**禁止因为本计划书提出了“Explorer / Exploration Graph / RobotBackend”等概念，就平行创建一套与现有模块职责重复的系统。**

如果现有模块能扩展，就扩展现有模块；只有现有结构无法合理承载时才新增文件。

---

### 0.2 当前本地主仓库优先于 GitHub

如果执行环境已经存在：

```text
/home/brov/robot/robot_scene_demo
```

则：

```text
当前本地 working tree
> 当前实时文件
> 最新本地报告/证据
> GitHub main
> 旧计划书/旧交接书
```

禁止为了和 GitHub 对齐而执行：

```bash
git reset --hard
git checkout -- .
git clean -fd
git clean -fdx
```

开始工作前必须保存：

```bash
cd /home/brov/robot/robot_scene_demo
git status --short
git diff --check
git diff --stat
git rev-parse HEAD
```

大量 dirty/untracked 文件可能就是前序阶段成果，不得破坏。

如果本地项目不存在，才从 GitHub clone 最新 `main`。

---

### 0.3 当前阶段不再以人工标定/特殊场景安全验收为 blocker

本阶段明确不要求用户执行以下事情：

```text
人工摆棋盘格
人工拿尺测相机/雷达外参
人工测轮径/轮距/4WS
人工摆障碍物到 front/right/rear/left
人工创造四周空旷环境
人工做 swept-envelope 验收
人工按指定轨迹走 LIO 标定路线
人工为 Pandar 做多场景几何标定
人工测保护框/雷达支架坐标
人工搭建 Nav2 产品级地图
```

这些工作不得阻塞本计划的高层自主探索。

---

### 0.4 允许做“全自动底层辅助能力”

只要满足以下三个条件，就可以实现：

1. 不需要用户额外操作机器狗；
2. 不要求特殊场地或特定摆放；
3. AI 能通过正常运行数据自动采集、计算、判断、降级和记录。

允许包括：

```text
自动 topic discovery
自动 TF discovery
自动 pose source discovery
自动 Action/service discovery
传感器健康检查
topic freshness / watchdog
自动重连
已有 LiDAR/IMU clock mapping
自动时间同步诊断
自动 odom 质量评估
运动 request-vs-observed correction learning
正常探索过程中的 opportunistic self-calibration
正常探索过程中的统计/参数估计
自动 session 恢复与日志
```

但这些能力：

> **除非高层闭环本身确实无法运行，否则不得成为实验自主搜索的硬 blocker。**

当自动标定不可观测时应输出：

```text
CALIBRATION_NOT_OBSERVABLE
```

并回退，而不是要求用户重新布置环境。

---

### 0.5 不删除现有安全系统；新增“实验模式”

现有项目的：

```text
stage2_readiness
rotation lease
dual lidar safety
navigation gate
fail-closed production policy
```

都必须保留。

本计划要求的是新增一个**明确、隔离、不可误认为 production 的实验运行 profile**。

推荐名称：

```text
operator_supervised_experiment
```

或：

```text
go2w_experimental
```

实验模式的含义是：

```text
有人现场
遥控器在手
允许高层算法连续决策
使用已有高层 /go2w/motion
不使用 LowCmd
不修改固件
不关闭厂商底层保护
不宣称 production-safe
```

绝不能把：

```text
navigation_gate.yaml
stage2_readiness.py
dual_lidar_safety.yaml
```

直接改成默认放行。

---

# 1. 本阶段最终目标

当前 Go2-W 的定位不再是“最终产品机器狗”。

它是：

> **高层具身智能算法实验载体。**

本阶段最终需要验证的不是：

```text
完美定位
完整 SLAM
产品级避障
精确 RGB-LiDAR metric 3D
产品级 Nav2
无人值守安全运行
```

而是：

> **高层语义自主探索是否能够形成真正的长周期闭环。**

---

## 1.1 必须跑通的 E2E 行为

例如输入：

```text
“寻找饮水机旁边的蓝色垃圾桶”
```

系统自动执行：

```text
START
↓
TargetProfile / GoalGraph
↓
OBSERVE
↓
LLM quick detection
↓
Observed SceneGraph
↓
GraphMatcher
↓
未找到目标
↓
Spatial / Semantic Memory 更新
↓
生成 Exploration Candidates
↓
SemanticNavigation + Exploration Planner 打分
↓
选择 Next Exploration Goal
↓
RobotBackend 执行动作
↓
等待动作结果
↓
OBSERVE
↓
Replan
↓
...
↓
发现 water dispenser
↓
anchor semantic relevance 上升
↓
优先检查其附近/相关视角
↓
发现 blue trash bin
↓
关系证据 near water dispenser
↓
verify
↓
TARGET_FOUND
↓
STOP
```

整个过程中用户不应该需要告诉机器人：

```text
现在左转
现在右转
现在向前
去看门口
去看饮水机
重新找一次
```

用户只负责：

```text
启动实验
现场监督
必要时遥控器中断
```

---

# 2. 当前项目基础：必须复用，不得推倒重来

根据当前仓库和最新交接，以下能力已经存在。

---

## 2.1 视觉与目标搜索

已有：

```text
RGB ROS2 bridge
CameraInfo
LLM quick detection
crop verify
tracking
SceneGraph
relation evidence
TargetProfile
evidence gate
Frame Bundle
ObservationMemoryStore
```

当前已经可以做到真实 2D：

```text
visual_confirmed
```

并可处理显式关系任务。

因此：

> **当前 Go2-W 实验阶段不要求先完成 metric 3D target localization。**

目标视觉确认即可作为搜索任务成功条件。

---

## 2.2 SemanticNavigation V1

已有：

```text
app/reasoning/semantic_navigation/
  models.py
  goal_graph_builder.py
  graph_matcher.py
  semantic_memory.py
  search_reasoner.py
  router.py
  auxiliary_hints.py
```

已有能力：

```text
GoalGraph
zero / partial / strong graph match
negative memory
sector penalty
context reasoning
exact / alias / lexical / attribute / relation matching
semantic next-view directive
legacy / semantic_navigation / hybrid routing
shadow / active
```

必须保留一条核心原则：

> **strong graph match 不是最终目标确认。最终确认仍由视觉/关系 evidence + verify 完成。**

---

## 2.3 当前真机状态机

已有：

```text
search_state_machine.py
step_planner.py
step_search_runner.py
search_directive_adapter.py
run_autonomous_loop.py
```

现有路径大致已有：

```text
search
→ candidate
→ approach
→ verify
→ target_reached
```

并且 SemanticNavigation 语义 reasoner 已经证明过：

```text
reasoner decision
→ 实际 Go2-W 转向
```

说明“高层决策→运动”的链已经能通。

下一阶段要解决的不是第一次动作，而是：

> **连续自主循环。**

---

## 2.4 已有 navigation/exploration 软件

当前仓库已经存在：

```text
app/navigation/candidate_goal_generator.py
app/navigation/exploration_planner.py
app/navigation/models.py
app/navigation/navigation_planning_pipeline.py
app/navigation/navigation_result_store.py
app/navigation/semantic_goal_localizer.py
app/navigation/target_pose_generator.py
app/navigation/video_navigation_map.py
app/navigation/nav2_adapter.py
app/navigation/nav2_gateway.py
...
```

其中 `app/navigation/exploration_planner.py` 已经有：

```text
information_gain
target_relevance
path_cost
frontier candidate
```

的初步实现。

因此执行 AI 必须：

> **把现有 video/offline exploration 能力收敛到 live autonomous exploration，而不是再造第三个 Planner。**

同时已有：

```text
app/planning/exploration_planner.py
```

承担的是 hypothesis / verification target helper。

不要和 `app/navigation/exploration_planner.py` 混为一谈。

---

# 3. 目标软件架构

最终应收敛为：

```text
┌───────────────────────────────────────────────┐
│               Natural Language Task           │
└──────────────────────┬────────────────────────┘
                       ↓
               TargetProfile / GoalGraph
                       ↓
              Live Perception / SceneGraph
                       ↓
                 Semantic Matcher
                       ↓
          Spatial + Semantic Exploration Memory
                       ↓
                Candidate Goal Generator
                       ↓
           SemanticNavigation Exploration Goal Selector
                       ↓
                AutonomousExplorer
                       ↓
                 RobotBackend API
                       ↓
       ┌───────────────┴────────────────┐
       │                                │
 Go2WExperimentalBackend        FutureRobotBackend
       │                                │
 relative motion / Action       global navigation API
       ↓                                ↓
 current experimental dog       future production dog
```

---

# 4. 平台解耦：RobotBackend

这是本计划第一核心改造。

---

## 4.1 目标

SemanticNavigation、SceneGraph、SemanticMemory、Exploration Planner 不允许直接依赖：

```text
Unitree
/go2w/motion
SportModeState
Pandar
rotation lease
Go2-W footprint
```

这些属于 backend。

未来换机器狗只需要实现新的 backend。

---

## 4.2 优先放置位置

优先评估现有 `app/navigation/` 是否适合承载。

推荐：

```text
app/navigation/robot_backend.py
app/navigation/backend_factory.py
app/navigation/go2w_experimental_backend.py
```

如果仓库当前结构已经有更合适的 adapter/backend 抽象，应复用现有结构，禁止为了匹配本文件名硬创建重复文件。

---

## 4.3 RobotCapabilities

至少需要：

```python
@dataclass(frozen=True)
class RobotCapabilities:
    supports_global_pose: bool
    supports_metric_navigation: bool
    supports_relative_translation: bool
    supports_relative_rotation: bool
    supports_heading_control: bool
    supports_navigation_cancel: bool
    supports_navigation_feedback: bool
    supports_platform_obstacle_avoidance: bool
```

---

## 4.4 PoseQuality

高层禁止伪造 metric pose。

必须明确：

```text
UNAVAILABLE
RELATIVE
METRIC
```

例如：

```python
class PoseQuality(str, Enum):
    UNAVAILABLE = "unavailable"
    RELATIVE = "relative"
    METRIC = "metric"
```

当前 Go2-W 可以使用：

```text
wheel/fused odom
```

作为实验性的 `RELATIVE` pose。

未来成熟机器狗可提供：

```text
map pose
```

作为 `METRIC`。

---

## 4.5 RobotBackend 最小接口

推荐语义：

```python
class RobotBackend(Protocol):

    def capabilities(self) -> RobotCapabilities:
        ...

    def get_pose(self) -> RobotPose | None:
        ...

    def execute_goal(self, goal: ExplorationGoal) -> NavigationHandle:
        ...

    def get_navigation_status(self, handle) -> NavigationResult:
        ...

    def cancel(self, handle=None) -> None:
        ...

    def stop(self) -> None:
        ...

    def health(self) -> BackendHealth:
        ...
```

不要让高层分别调用：

```text
turn_left()
forward()
/go2w/motion
```

这些在 Go2W backend 内部做适配。

---

# 5. 双模式探索：当前 Go2-W 与未来成熟机器狗共用一套大脑

这是本项目可迁移性的关键。

---

## 5.1 Local / Topological 模式

当前 Go2-W 没有可靠的长期 metric translation。

所以不要假装已经有：

```text
完整 map
准确 global x/y
Nav2-ready metric topology
```

当前实验 backend 使用：

> **Relative + Topological Exploration**

节点可以由：

```text
session observation id
relative pose
heading sector
动作历史
语义观察
```

组成。

例如：

```text
Node 12:
  pose_quality: relative
  relative_pose: [0.42, -0.08, 0.52]
  heading_sector: 1
  observed:
    - door
    - desk
  visited_count: 1
```

这已经足够做：

```text
避免立即重复
负证据
方向覆盖
语义 anchor 优先
拓扑式探索
```

---

## 5.2 Metric Navigation 模式

未来成熟机器狗 backend 若报告：

```text
supports_global_pose=true
supports_metric_navigation=true
```

则 ExplorationGoal 可以直接包含：

```text
x
y
yaw
frame=map
```

底层自行负责：

```text
SLAM
global planner
local planner
避障
轨迹跟踪
越野
```

高层不改变。

---

# 6. ExplorationGoal：统一高层动作语言

应定义一个平台无关的高层目标。

至少支持：

```text
REOBSERVE
ROTATE_VIEW
RELATIVE_MOVE
NAVIGATE_POSE
INSPECT_ANCHOR
REVISIT_NODE
STOP
```

建议结构：

```python
@dataclass
class ExplorationGoal:
    goal_id: str
    goal_type: str

    target_node_id: str | None
    position: tuple[float, float] | None
    yaw: float | None

    relative_dx: float | None
    relative_dy: float | None
    relative_dyaw: float | None

    semantic_anchor: str | None
    semantic_reason: str

    expected_information_gain: float
    semantic_relevance: float
    novelty_score: float
    estimated_cost: float

    provenance: dict
```

---

# 7. Spatial Semantic Exploration Memory

这是第二个核心改造。

---

## 7.1 不要废弃 ObservationMemoryStore

应复用：

```text
app/memory/observation_memory_store.py
```

现有长期 ObservationMemoryStore 继续负责历史观察。

新增/扩展的是：

> **当前 autonomous exploration session 的结构化空间/拓扑记忆。**

---

## 7.2 ObservationNode

推荐：

```python
@dataclass
class ObservationNode:
    node_id: str
    timestamp: float

    pose: RobotPose | None
    pose_quality: str
    heading: float | None
    heading_sector: int | None

    objects: list
    relations: list
    scene_graph: dict | None

    target_match_level: str
    target_score: float

    semantic_relevance: float
    information_gain: float

    visited_count: int
    negative_evidence_count: int

    navigation_fail_count: int
    reachable_state: str

    source_bundle_id: str | None
    provenance: dict
```

---

## 7.3 ExplorationEdge

至少：

```python
@dataclass
class ExplorationEdge:
    source_node_id: str
    target_node_id: str
    action_type: str
    requested_motion: dict
    observed_motion: dict
    navigation_result: str
    cost: float
```

---

## 7.4 节点状态

统一使用：

```text
UNSEEN
OBSERVED
VISITED
SEMANTIC_INTEREST
NEGATIVE
UNREACHABLE
TARGET_CANDIDATE
TARGET_CONFIRMED
```

---

## 7.5 存储

实验阶段优先简单、可审计：

```text
outputs/live_sessions/<session_id>.jsonl
outputs/live_runs/<session_id>/exploration_graph.json
outputs/live_runs/<session_id>/summary.json
```

不要无必要引入复杂数据库。

---

# 8. Exploration Graph

可以新增：

```text
app/navigation/exploration_graph.py
```

或者扩展现有：

```text
video_navigation_map.py
```

具体由代码审计决定。

目标是提供统一操作：

```python
add_observation(...)
connect_motion(...)
mark_visited(...)
mark_negative(...)
mark_unreachable(...)
nearest_nodes(...)
unvisited_nodes(...)
semantic_neighbors(...)
serialize(...)
```

当前 Go2-W 可构造“拓扑图”。

未来 metric backend 可把同一个图节点绑定真实 map pose。

---

# 9. Candidate Goal Generator 升级

必须优先扩展现有：

```text
app/navigation/candidate_goal_generator.py
```

当前它主要根据 candidate region / video trajectory 生成 re-observation goal。

需要扩展成 live candidate generator。

---

## 9.1 候选来源

至少包含：

### A. SemanticNavigation semantic directive

例如：

```text
inspect_anchor
look_left
look_right
reobserve_context
```

---

### B. 未访问 heading sector

例如把当前周围分为：

```text
12 × 30°
```

或可配置 sector。

当前 Go2-W relative 模式可用。

---

### C. 图中的 UNSEEN / SEMANTIC_INTEREST 节点

---

### D. 强语义 anchor 周围的 re-observation

例如：

```text
target = blue trash bin near water dispenser
```

看到：

```text
water dispenser
```

后应自动生成：

```text
INSPECT_ANCHOR
```

类型候选。

---

### E. last-known / target-lost

发现过候选但 verify 失败或 tracker 丢失时，生成：

```text
REVISIT_NODE
REOBSERVE
```

---

### F. fallback exploration

SemanticNavigation 没有高置信度方向时，仍应生成普通探索候选。

不能因为 semantic reasoner 没有建议就停止。

---

# 10. Exploration Planner 升级

优先扩展：

```text
app/navigation/exploration_planner.py
```

现有：

```text
information_gain
target_relevance
path_cost
```

继续保留，但需要真正 session-aware。

---

## 10.1 推荐评分

所有权重必须配置化。

初始可：

```text
score =
    0.35 * semantic_relevance
  + 0.25 * novelty
  + 0.20 * information_gain
  + 0.10 * frontier_bonus
  + 0.10 * continuity_bonus

  - 0.30 * visited_penalty
  - 0.25 * negative_evidence_penalty
  - 0.35 * navigation_failure_penalty
  - 0.15 * estimated_motion_cost
  - 0.20 * oscillation_penalty
```

注意：

> 数值只是默认策略，不是论文结论，必须写进 YAML 并可调。

---

## 10.2 semantic_relevance

来自：

```text
GoalGraph
GraphMatcher
explicit anchor
inferred context
SceneGraph relation
TargetProfile context_terms
SemanticNavigation reasoner provenance
```

显式 anchor 优先于弱 inferred context。

---

## 10.3 novelty

避免：

```text
左→右→左→右
同一个位置不断观察
```

需要基于：

```text
node visited_count
heading sector history
recent action sequence
scene similarity
```

产生 penalty。

---

## 10.4 negative evidence

已有 SemanticNavigation negative memory 必须接入 Planner。

如果某位置已经多次：

```text
target not found
relation absent
```

则降低再次访问优先级。

但 TTL 到期后允许恢复。

---

## 10.5 navigation failure

如果：

```text
goal A
```

连续失败：

```text
fail_count >= N
```

应：

```text
mark UNREACHABLE
```

并选下一个，不要无限 retry。

---

# 11. SemanticNavigation 从 Next-View 升级到 Next-Exploration-Goal

这是第三个核心改造。

---

## 11.1 保留现有 reasoner

不要重写：

```text
GoalGraphBuilder
GraphMatcher
SemanticSearchMemory
SearchReasoner
Router
```

而是在输出层增加：

```text
semantic directive
→ candidate exploration goal
```

的适配。

---

## 11.2 不再把 SemanticNavigation 只绑定 TURN

当前：

```text
directive
→ StepPlan
→ turn <=30°
```

应扩展成：

```text
directive
→ ExplorationIntent
→ CandidateGoalGenerator
→ ExplorationGoal
```

例如：

```text
inspect_anchor(water dispenser)
```

在：

### Go2-W relative 模式

可以被 backend/planner解释成：

```text
优先转向该 anchor 所在 sector
必要时做小幅 relative move
```

### Future metric 模式

可以变成：

```text
导航到 anchor 附近最佳 viewpoint
```

SemanticNavigation 自己不关心。

---

# 12. AutonomousExplorer

这是整个 E2E 系统的最高层 orchestrator。

推荐新增：

```text
app/live_robot/autonomous_explorer.py
```

如果 `step_search_runner.py` 经过审计后非常适合扩展，也可以把长期循环能力并入现有 runner，但必须避免让 `run_autonomous_loop.py` 继续无限膨胀。

---

## 12.1 状态

建议：

```text
BOOTSTRAP
OBSERVE
MATCH
VERIFY
UPDATE_MEMORY
PLAN
EXECUTE
WAIT_RESULT
RECOVER
TARGET_FOUND
SEARCH_EXHAUSTED
OPERATOR_STOP
FAILED
FINISHED
```

---

## 12.2 主循环

逻辑必须类似：

```python
while budget.remaining():

    observation = observer.observe()

    match = matcher.match(observation)

    if match.has_candidate:
        verification = verifier.verify(match)

        if verification.confirmed:
            backend.stop()
            return TARGET_FOUND

    memory.update(observation, match)

    candidates = candidate_generator.generate(
        observation=observation,
        memory=memory,
        graph=graph,
        semantic_navigation=semantic_reasoner,
        backend_capabilities=backend.capabilities(),
    )

    goal = planner.select(candidates, memory, graph)

    if goal is None:
        return SEARCH_EXHAUSTED

    handle = backend.execute_goal(goal)

    result = wait_and_monitor(handle)

    memory.record_navigation(goal, result)

    if result.failed:
        recover_and_replan()
```

---

# 13. Navigation Result → Recovery / Replan

必须统一状态：

```text
ACCEPTED
RUNNING
SUCCEEDED
FAILED
CANCELLED
TIMEOUT
OPERATOR_STOP
BACKEND_UNAVAILABLE
```

高层不能直接解析某个机器人厂商字符串。

---

## 13.1 Recovery policy

### SUCCEEDED

```text
→ OBSERVE
```

### FAILED

```text
mark fail
→ 降低 goal priority
→ PLAN
```

### TIMEOUT

```text
cancel
→ mark timeout
→ PLAN
```

### BACKEND_UNAVAILABLE

```text
有限次数 reconnect
→ 仍失败则 FAILED
```

### OPERATOR_STOP

立即：

```text
backend.stop()
session result = OPERATOR_STOP
```

不得自动继续。

---

# 14. Search Budget

新增统一配置。

建议：

```yaml
exploration:
  max_search_seconds: 600
  max_planning_cycles: 100
  max_motion_steps: 50
  max_replans: 100

  max_same_node_visits: 2
  max_navigation_failures_per_goal: 2
  max_consecutive_no_information_cycles: 8

  verify_attempts: 3
  negative_memory_ttl_seconds: 120
```

退出结果：

```text
TARGET_FOUND
TIMEOUT
SEARCH_EXHAUSTED
OPERATOR_STOP
BACKEND_FAILURE
PERCEPTION_FAILURE
MAX_STEPS_REACHED
```

---

# 15. 当前 Go2-W 实验 Backend

推荐：

```text
Go2WExperimentalBackend
```

---

## 15.1 必须使用高层控制接口

继续复用：

```text
/go2w/motion
/go2w/arm
/go2w/emergency_stop
```

以及现有 `unitree_go2w_control`。

禁止：

```text
/lowcmd
LowCmd
关节位置控制
关节速度控制
关节力矩控制
修改固件
关闭厂商安全保护
```

---

## 15.2 Go2-W backend 的动作映射

当前实验可支持：

```text
ROTATE_VIEW
RELATIVE_MOVE
STOP
```

如果 ExploreGoal 是 metric NAVIGATE_POSE，但 backend 不支持：

```text
supports_metric_navigation=false
```

则 Planner 不应生成该 goal。

---

## 15.3 Relative exploratory primitives

默认建议配置：

```yaml
go2w_experimental:
  max_turn_deg_per_action: 30
  forward_step_m: 0.20
  max_forward_step_m: 0.30
  allow_lateral: false

  relative_pose_source: auto
  odom_candidates:
    - /go2w/odom/fused
    - /go2w/odom/wheel
```

这些只是高层实验步幅，不等于产品控制参数。

---

# 16. Operator-Supervised Experiment Profile

新增配置，例如：

```text
configs/go2w/high_level_experiment.yaml
```

推荐：

```yaml
profile: operator_supervised_experiment

purpose:
  production_safe: false
  research_only: true
  operator_present: true

backend:
  type: go2w_experimental

high_level:
  semantic_reasoning: true
  reasoner: semantic_navigation
  continuous_exploration: true

manual_calibration_requirements:
  required: false

production_readiness:
  stage2_readiness_required: false
  pandar_extrinsic_required: false
  dual_lidar_validation_required: false
  four_direction_physical_evidence_required: false
  nav2_gate_required: false

automatic_checks:
  motion_action_available: true
  robot_mode_error_check: true
  pose_freshness_if_available: true
  camera_freshness: true
  emergency_stop_available: true
  stop_on_backend_error: true

limits:
  max_turn_deg: 30
  max_forward_step_m: 0.30
```

注意：

> 这是新增实验 profile，不是把 production gate 改成 false/true。

---

# 17. 自动健康检查

新增或扩展启动 health probe。

应该自动检查：

```text
camera publisher
camera freshness
Frame Bundle freshness
LLM API availability
pose topic candidates
motion Action
arm service
emergency stop service
SportModeState
existing perception processes
```

输出机器可读：

```json
{
  "ready": true,
  "degraded": ["metric_pose_unavailable"],
  "backend": "go2w_experimental",
  "capabilities": {
    "supports_global_pose": false,
    "supports_relative_translation": true,
    "supports_relative_rotation": true
  }
}
```

---

# 18. 自动 topic / pose discovery

当前项目已有固定 topic 仍可作为优先值，但 backend 应允许：

```text
configured
→ known candidates
→ ROS graph discovery
```

例如 pose 自动尝试：

```text
/go2w/odom/fused
/go2w/odom/wheel
/odom
/localization/pose
```

任何自动发现都必须在日志里记录最终选择。

不能静默猜测。

---

# 19. Opportunistic 底层优化（非 blocker）

这些属于“AI 能自己做就做”。

---

## 19.1 Clock diagnostics

保留已有：

```text
time bridge
clock mapping
Pandar clock tier
timestamp monotonicity
```

自动运行即可。

---

## 19.2 Motion correction learning

正常探索时记录：

```text
requested relative turn
observed odom turn

requested forward
observed odom displacement
```

积累：

```text
request vs observed
```

可以生成 session correction。

例如：

```json
{
  "rotation_scale": 1.034,
  "forward_scale": 0.92,
  "samples": 17,
  "confidence": "medium"
}
```

但必须：

```text
confidence low → 不应用
```

并且不要求用户专门做实验动作。

---

## 19.3 Opportunistic calibration

如果正常探索自然产生的数据足够，可尝试：

```text
camera-lidar
sensor temporal offset
relative transform refinement
```

但必须：

```text
not observable
→ 记录
→ 不阻塞
```

不得要求用户改场景。

---

# 20. Perception 与 Explorer 的接口

不要让 AutonomousExplorer 直接操作 detector 细节。

推荐统一：

```python
class LiveObservation:
    bundle_id: str
    timestamp: float
    image_ref: str | None

    detections: list
    scene_graph: dict
    target_match: dict

    pose: RobotPose | None
    sensor_health: dict

    provenance: dict
```

由现有：

```text
LiveSemanticObserver
FrameBundleReader
ObservedSceneGraphBuilder
TargetProfile
```

组合产生。

---

# 21. Target success 条件

当前 Go2-W 实验不要被 metric 3D 阻塞。

允许：

```text
2D visual confirmed
+
required relation evidence
+
verify PASS
```

直接输出：

```text
TARGET_FOUND
```

如果未来 backend/perception 有 metric target pose，则可额外：

```text
APPROACH_TARGET
```

但不是当前实验完成的必要条件。

---

# 22. Approach 的平台无关设计

如果目标已经确认：

### 当前 Go2-W

可配置：

```text
finish_on_visual_confirmation=true
```

直接 STOP + SUCCESS。

也可以实验性：

```text
bbox center alignment
small forward
verify
```

但不作为强制要求。

### 未来机器人

若有 metric target pose：

```text
APPROACH_TARGET(desired_distance=1.0m)
```

交给底层 navigation。

---

# 23. 一键运行入口

最终必须新增一个明确入口。

推荐：

```text
scripts/go2w/run_semantic_exploration.py
```

或将现有 `run_autonomous_loop.py` 拆出公共逻辑后提供新入口。

**不要继续把所有职责无限堆进 2500+ 行 runner。**

---

## 23.1 目标命令

至少实现：

```bash
/usr/bin/python3 scripts/go2w/run_semantic_exploration.py \
  --target "饮水机旁边的蓝色垃圾桶" \
  --backend go2w_experimental \
  --reasoner semantic_navigation \
  --operator-supervised-experiment \
  --max-seconds 600 \
  --max-motion-steps 50 \
  --output outputs/live_sessions/semantic_exploration_demo.jsonl
```

---

## 23.2 更理想的一键 launcher

推荐再提供：

```bash
bash scripts/go2w/start_semantic_exploration.sh \
  --target "饮水机旁边的蓝色垃圾桶"
```

launcher 自动完成：

```text
环境检查
ROS source
感知进程检查/启动
backend health
session id
自主探索 runner
日志目录
退出清理
```

如果外部 `unitree_go2w_control` 不可自动启动，则应明确报告：

```text
MOTION_BACKEND_UNAVAILABLE
```

不能挂死。

---

# 24. Runner 拆分原则

当前：

```text
scripts/go2w/run_autonomous_loop.py
```

已经非常大。

执行 AI 应优先：

```text
把 reusable core 移到 app/
CLI 只负责参数、构建对象、启动
```

推荐职责：

```text
app/live_robot/autonomous_explorer.py
  长期循环

app/navigation/robot_backend.py
  backend 协议

app/navigation/go2w_experimental_backend.py
  Go2-W 实现

app/navigation/exploration_planner.py
  candidate ranking

app/navigation/candidate_goal_generator.py
  candidate generation

app/navigation/exploration_graph.py
  graph/memory

scripts/go2w/run_semantic_exploration.py
  CLI
```

---

# 25. 现有 StepSearchRunner 的处理

不要删除。

它继续作为：

```text
legacy/local step search
```

和回归基线。

新的 AutonomousExplorer 可以在 Go2-W backend 内部复用：

```text
StepPlan
motion executor
odom verification
STOP
```

但不应该让高层 Planner 直接知道 StepPlan。

---

# 26. 与现有 Nav2 软件的关系

仓库已有大量：

```text
app/navigation/nav2_*.py
```

这些保留。

当前 Go2-W 本计划：

```text
不要求 Nav2 PASS
不要求 map
不要求 map->odom
不要求 Navigation Gate 解锁
```

未来成熟机器人：

```text
FutureRobotBackend
```

可以直接复用现有 Nav2 adapter/gateway，或者适配厂商 navigation API。

所以：

> **Nav2 是 backend option，不是 SemanticNavigation dependency。**

---

# 27. 与 Stage2 readiness 的关系

现有：

```text
app/live_robot/stage2_readiness.py
```

要求正式 Stage2 有完整 Pandar / rotation evidence。

不要删除。

实验模式另建 readiness：

推荐：

```text
ExperimentReadiness
```

只包含实际高层实验需要的自动条件，例如：

```text
camera_fresh
bundle_fresh
LLM_available
motion_action_available
mode_ok
emergency_stop_available
backend_healthy
```

不要修改正式 Stage2 语义。

---

# 28. Session Logging

每一次 autonomous exploration 必须能完全回放为什么这么做。

至少记录：

```text
session_start
target_profile
goal_graph

observation
scene_graph
graph_match
verification

memory_update
candidate_goals
candidate_scores
selected_goal

backend_command
navigation_status
observed_pose_delta

recovery
replan

target_found
abort
session_summary
```

每个 selected goal 必须带：

```text
reason
semantic_relevance
information_gain
novelty
penalties
provenance
```

这是科研项目非常重要的可解释性。

---

# 29. Session Summary

结束生成：

```json
{
  "result": "TARGET_FOUND",
  "target": "...",
  "duration_s": 243.2,
  "planning_cycles": 17,
  "motion_steps": 12,
  "observations": 19,
  "unique_nodes": 10,
  "replans": 5,
  "navigation_failures": 1,
  "verify_attempts": 2,
  "semantic_goal_selection_count": 8,
  "fallback_goal_selection_count": 4
}
```

---

# 30. 防振荡机制

必须实现。

检测：

```text
A → B → A → B
left → right → left → right
连续场景几乎相同
连续 N 次没有新 object/relation
```

策略：

```text
recent goal tabu
heading sector penalty
node revisit penalty
forced novelty candidate
```

不能让机器人无限摇头。

---

# 31. 信息增益

实验版本不需要复杂 entropy model。

至少可以用：

```text
新物体数
新关系数
新节点
新 heading sector
SceneGraph 差异
未访问状态
```

估计：

```text
information_gain
```

并写入日志。

---

# 32. Semantic anchor 行为

需要重点测试。

例如：

```text
target:
  blue trash bin
relation:
  near water dispenser
```

开始没看到 target。

后来观察：

```text
water dispenser
```

Planner 必须能够：

```text
提高 anchor 周边 candidate
```

而不是继续机械 scan。

这将是证明 SemanticNavigation 高层自主探索价值的重要演示。

---

# 33. Legacy fallback

必须保留。

如果：

```text
SemanticNavigation exception
低置信度
GoalGraph 无有效 candidate
LLM timeout
SceneGraph 缺失
```

则：

```text
fallback exploration
```

而不是直接终止。

可复用现有：

```text
legacy
hybrid
```

router。

---

# 34. 测试计划

执行 AI 不能只写代码不测试。

---

## 34.1 新增 unit tests

至少：

```text
tests/test_robot_backend.py
tests/test_go2w_experimental_backend.py
tests/test_exploration_graph.py
tests/test_live_candidate_goal_generator.py
tests/test_live_exploration_planner.py
tests/test_autonomous_explorer.py
tests/test_experiment_readiness.py
tests/test_exploration_recovery.py
tests/test_exploration_budget.py
```

文件名可以结合现有命名调整。

---

## 34.2 必测场景

### Scenario A：目标第一帧出现

```text
OBSERVE
→ verify
→ TARGET_FOUND
→ 0 motion
```

---

### Scenario B：目标不存在

必须：

```text
连续探索
→ budget exhausted
→ SEARCH_EXHAUSTED / TIMEOUT
```

不能无限循环。

---

### Scenario C：发现 semantic anchor

必须看到：

```text
anchor candidate score > unrelated candidate
```

---

### Scenario D：导航失败

```text
goal A fail
→ replan
→ goal B
```

---

### Scenario E：重复场景

```text
oscillation penalty
→ 新方向
```

---

### Scenario F：SemanticNavigation exception

```text
fallback
```

---

### Scenario G：operator stop

```text
cancel
STOP
finish=OPERATOR_STOP
```

---

### Scenario H：pose unavailable

当前 Go2-W 仍能：

```text
topological/heading exploration
```

而不是直接 crash。

---

### Scenario I：future metric backend mock

必须证明：

```text
same Explorer
```

可生成：

```text
NAVIGATE_POSE
```

而无需修改 SemanticNavigation。

---

# 35. Mock Backend

必须提供纯软件 Mock backend。

用于完整 E2E。

它应能模拟：

```text
pose
goal success
goal failure
timeout
target appears after N nodes
operator stop
```

这样 CI 不需要机器狗也能测试 AutonomousExplorer。

---

# 36. Replay 测试

复用已有真实 session：

```text
outputs/live_runs/*
outputs/live_sessions/*
```

对已有 Bundle / observation 做 replay。

要求：

```text
同一输入确定性
candidate provenance 可解释
不出现危险 forward hallucination
目标确认逻辑不回退
```

---

# 37. 回归测试

修改前记录当前测试基线。

修改后至少重跑：

```text
SemanticNavigation
SceneGraph
TargetProfile
semantic verifier
StepSearchRunner
motion bounds
live semantic observer
navigation
new autonomous explorer tests
```

同时：

```bash
git diff --check
```

必须 PASS。

不要因为历史某个真实 API/TLS test 卡住而把整个工作判定失败；应记录已知外部依赖测试。

---

# 38. Go2-W 真机实验验收

这里只要求操作者监督，不要求人工标定。

---

## 38.1 Trial 0：只观察

运行新 runner：

```text
backend=dry_run/mock
live perception=true
```

确认真实环境中至少：

```text
5+ planning cycles
candidate scoring
memory update
selected goal
```

---

## 38.2 Trial 1：只转向连续探索

允许：

```text
连续多次 autonomous rotate
```

不再限制：

```text
max-motion-steps=1
```

目标是验证：

```text
observe → plan → turn → observe → replan
```

至少连续 5 个 cycle。

---

## 38.3 Trial 2：转向 + 短步前进

实验 profile：

```text
turn <= 30°
forward <= 0.30 m / action
```

让机器人完成至少：

```text
10 autonomous planning cycles
```

操作者只拿遥控器，不给方向建议。

---

## 38.4 Trial 3：目标不存在

让系统自主探索直到：

```text
TIMEOUT
或
SEARCH_EXHAUSTED
```

确认：

```text
不会无限振荡
不会重复同节点
会使用 negative memory
```

---

## 38.5 Trial 4：普通目标

例如：

```text
绿色垃圾桶
灰色书包
```

目标一开始不在相机中心。

要求：

```text
自主重新观察
→ 发现
→ verify
→ TARGET_FOUND
```

---

## 38.6 Trial 5：关系目标

推荐最终演示：

```text
饮水机旁边的蓝色垃圾桶
```

要求日志能证明：

```text
GoalGraph relation
anchor observed
anchor relevance boost
goal priority change
relation evidence
visual verify
TARGET_FOUND
```

---

# 39. 当前阶段正式 PASS 定义

只有同时满足以下条件，才能写：

```text
HIGH_LEVEL_AUTONOMOUS_SEMANTIC_EXPLORATION = PASS
```

必须：

1. 自然语言 target 可直接启动；
2. perception 不需要人工逐步触发；
3. SemanticNavigation/Planner 自动给出连续 exploration goal；
4. RobotBackend 与 SemanticNavigation 解耦；
5. 当前 Go2-W backend 可执行连续高层动作；
6. 至少一次真实机器人完成 10+ autonomous planning cycle；
7. 用户期间没有提供“下一步往哪走”的指令；
8. visited/negative memory 真实影响后续规划；
9. navigation/action fail 可以 replan；
10. semantic anchor 能影响 goal priority；
11. target verify 可以结束任务；
12. operator stop 可以结束任务；
13. 产生完整 JSONL + summary；
14. Mock/Future backend 测试证明核心高层不绑定 Go2-W；
15. README 有一条可复制运行命令。

---

# 40. 不属于当前 PASS 的条件

以下全部不是本阶段 blocker：

```text
Pandar 正式 metric extrinsic
双 LiDAR production safety
四方向实体物理证据
rotation_clearance_valid=true
camera physical TF
RGB-LiDAR navigation-grade calibration
Point-LIO translation PASS
Go2-W map
map->odom
Go2-W Nav2 execute
Collision Monitor 产品验收
Velocity Smoother 产品验收
无人值守运行
```

这些不得被 AI 用作“无法继续高层自主探索”的理由。

---

# 41. 自动底层增强完成标准

如果执行 AI 有能力，可在不阻塞主线的前提下完成。

优先级：

```text
A. health + freshness
B. auto pose/topic/action discovery
C. watchdog/reconnect
D. motion request-observed statistics
E. opportunistic calibration
```

任何 E 类自动标定：

```text
失败/不可观测
```

都只能记为：

```text
OPTIONAL_DEGRADED
```

不能导致自主探索 unavailable。

---

# 42. 未来机器狗迁移验收

必须提供：

```text
MockMetricBackend
```

或者测试 backend，模拟成熟机器人。

证明同一 AutonomousExplorer 在不修改：

```text
GoalGraph
GraphMatcher
SemanticMemory
Exploration Planner
```

的情况下，可以：

```text
get_pose(map)
navigate_to_pose(map goal)
receive navigation status
replan
```

未来正式机器狗只需要实现：

```text
ProductionRobotBackend
```

---

# 43. 配置组织

推荐新增：

```text
configs/exploration/default.yaml
configs/go2w/high_level_experiment.yaml
```

如果项目已有更合适配置目录，可按现有风格。

核心配置分层：

```text
exploration policy
semantic weights
memory / TTL
budget
backend
Go2-W experimental limits
logging
```

不要把 Go2-W 参数混进 SemanticNavigation models。

---

# 44. CLI 参数

建议：

```text
--target
--backend
--reasoner
--operator-supervised-experiment

--max-seconds
--max-planning-cycles
--max-motion-steps

--finish-on-visual-confirmation

--output
--record-video

--dry-run
--replay
```

旧 CLI 保持兼容，不要破坏原 demo。

---

# 45. README 修改

最终 README 顶部增加一节：

```text
Operator-Supervised High-Level Semantic Exploration
```

明确：

```text
用途
不是 production safety mode
不要求 Pandar/Stage2 formal readiness
启动条件
一键命令
输出目录
停止方式
```

同时给未来 backend 接口说明。

---

# 46. 技术文档

新增：

```text
docs/HIGH_LEVEL_AUTONOMOUS_SEMANTIC_EXPLORATION.md
```

包含：

```text
架构
RobotBackend
Explorer state machine
Memory
Goal scoring
实验模式
future robot migration
```

---

# 47. 最终实施报告

执行完成后生成：

```text
reports/high_level_autonomous_exploration_handoff_<date>.md
```

必须写：

```text
修改文件
新增文件
架构变化
测试结果
真实机器狗运行结果
命令
当前已知限制
未来 ProductionRobotBackend 接法
```

---

# 48. 实施顺序

执行 AI 必须按依赖推进，但不要每完成一步停下来问用户。

---

## Phase 0：审计

完成：

```text
git status
git diff
README
latest handoff
app/live_robot
app/navigation
app/planning
app/reasoning/semantic_navigation
app/memory
run_autonomous_loop.py
tests
```

输出内部 implementation map。

不要只看旧计划书推测代码。

---

## Phase 1：平台接口

实现：

```text
RobotBackend
RobotCapabilities
NavigationResult
Go2WExperimentalBackend
MockBackend
backend factory
```

先测试。

---

## Phase 2：Memory / Graph

实现/扩展：

```text
ObservationNode
ExplorationEdge
ExplorationGraph
visited
negative
unreachable
serialization
```

先测试。

---

## Phase 3：Live Candidate Generator

扩展：

```text
candidate_goal_generator.py
```

接：

```text
SemanticNavigation directive
semantic anchor
unvisited sector
graph node
last-known
fallback
```

---

## Phase 4：Live Exploration Planner

扩展：

```text
exploration_planner.py
```

实现：

```text
semantic relevance
novelty
information gain
negative penalty
failure penalty
cost
oscillation penalty
```

---

## Phase 5：SemanticNavigation Goal Adapter

把：

```text
semantic directive
```

从单纯：

```text
StepPlan
```

扩展为：

```text
ExplorationIntent / ExplorationGoal
```

旧 StepPlan 兼容路径保留。

---

## Phase 6：AutonomousExplorer

实现真正长期循环。

接入：

```text
observer
matcher
verifier
memory
planner
backend
recovery
budget
logging
```

---

## Phase 7：Experiment Readiness + Health

新增非 production readiness。

实现：

```text
automatic only
```

不依赖人工标定证据。

---

## Phase 8：CLI / launcher

完成：

```text
run_semantic_exploration.py
start_semantic_exploration.sh
```

或等价统一入口。

---

## Phase 9：Offline E2E

Mock：

```text
FOUND
NOT FOUND
FAIL / REPLAN
TARGET LOST
OPERATOR STOP
```

全部通过。

---

## Phase 10：Real perception dry-run

真实 camera/Bundle/LLM：

```text
5+ cycles
```

只输出动作决策。

---

## Phase 11：Go2-W supervised E2E

操作者遥控器在手。

完成：

```text
turn-only
→ turn + short-forward
→ semantic target
→ relation target
```

---

## Phase 12：文档、回归、交接

更新：

```text
README
docs
reports
tests
```

最后：

```bash
git diff --check
```

并输出完整 status。

不要擅自提交，除非用户明确要求 commit/push。

---

# 49. 执行 AI 不得做的事情

不要：

```text
停在“建议下一步”
只写 TODO
只增加接口不接真机
重新实现已有 SemanticNavigation
重新造第四套 planner
删除 legacy 路径
把 experiment mode 改成 production default
把 candidate calibration 写成 validated
伪造 metric pose
要求用户摆标定场
要求用户四周清空来完成本计划
要求用户量尺寸
为了清 working tree reset/clean
直接 LowCmd
修改固件
关闭厂商保护
```

---

# 50. 执行 AI 遇到问题时的默认决策

## 代码结构冲突

优先：

```text
复用 > 扩展 > adapter > 新建
```

---

## 某底层校准不通过

如果高层仍可运行：

```text
degrade
记录
继续
```

---

## metric pose 不可靠

切：

```text
relative/topological mode
```

不要阻塞。

---

## SemanticNavigation 出错

```text
legacy/fallback candidate
```

---

## target metric 3D 不可用

```text
finish_on_visual_confirmation
```

---

## Pandar 不可用

如果当前实验高层不依赖：

```text
degraded
继续
```

---

## Nav2 不可用

Go2-W：

```text
relative backend
```

未来：

```text
metric backend
```

---

# 51. 推荐最终目录变化

以下只是目标，不要求机械照搬；先审计现有结构。

```text
app/
  live_robot/
    autonomous_explorer.py              # NEW preferred
    experiment_readiness.py             # NEW preferred

  navigation/
    robot_backend.py                    # NEW preferred
    backend_factory.py                  # NEW preferred
    go2w_experimental_backend.py        # NEW preferred
    exploration_graph.py                # NEW preferred

    candidate_goal_generator.py         # EXTEND
    exploration_planner.py              # EXTEND
    models.py                           # EXTEND

  reasoning/
    semantic_navigation/
      ...                               # KEEP / EXTEND adapter only

configs/
  exploration/
    default.yaml                        # NEW preferred
  go2w/
    high_level_experiment.yaml          # NEW preferred

scripts/
  go2w/
    run_semantic_exploration.py         # NEW preferred
    start_semantic_exploration.sh       # NEW preferred

tests/
  test_autonomous_explorer.py
  test_exploration_graph.py
  test_live_exploration_planner.py
  test_robot_backend.py
  test_go2w_experimental_backend.py
  test_experiment_readiness.py

docs/
  HIGH_LEVEL_AUTONOMOUS_SEMANTIC_EXPLORATION.md

reports/
  high_level_autonomous_exploration_handoff_<date>.md
```

---

# 52. 最终演示建议

最终 demo 不要只找一个无关系目标。

建议两套。

---

## Demo A：基础 autonomous search

```text
“寻找绿色垃圾桶”
```

展示：

```text
连续自主观察
visited memory
自主转向/短步
发现
verify
TARGET_FOUND
```

---

## Demo B：SemanticNavigation semantic search

```text
“寻找饮水机旁边的蓝色垃圾桶”
```

必须输出/展示：

```text
GoalGraph
SceneGraph
anchor water dispenser
graph match
semantic relevance
candidate goals
selected goal
memory
target relation
verify
TARGET_FOUND
```

这才真正证明：

> **不是随机 wander，而是语义引导自主探索。**

---

# 53. 最终验收命令目标

完成后 README 应能直接给出类似：

```bash
cd /home/brov/robot/robot_scene_demo

bash scripts/go2w/start_semantic_exploration.sh \
  --target "饮水机旁边的蓝色垃圾桶"
```

或：

```bash
/usr/bin/python3 scripts/go2w/run_semantic_exploration.py \
  --target "饮水机旁边的蓝色垃圾桶" \
  --backend go2w_experimental \
  --reasoner semantic_navigation \
  --operator-supervised-experiment \
  --finish-on-visual-confirmation \
  --max-seconds 600 \
  --max-motion-steps 50 \
  --record-video outputs/go2w_acceptance/high_level_semantic_exploration.mp4 \
  --output outputs/live_sessions/high_level_semantic_exploration.jsonl
```

用户启动后：

> **除紧急遥控中断外，不再进行人工干预。**

---

# 54. 计划完成后的项目定位

完成本计划后，不应再把项目描述为：

> “Go2-W 自主导航系统”。

而应描述为：

> **一个平台解耦的高层自主语义探索系统，在 Go2-W 上以 operator-supervised experimental backend 完成长周期 E2E 验证；未来可通过 RobotBackend 接入具备成熟定位、导航、避障和底盘控制能力的机器狗。**

核心研究资产应是：

```text
Target / Goal Understanding
SceneGraph
GoalGraph
SemanticNavigation
Spatial Semantic Memory
Exploration Graph
Candidate Goal Generation
Semantic Exploration Planner
AutonomousExplorer
Recovery / Replanning
RobotBackend abstraction
```

而不是：

```text
某一个 Go2-W 雷达参数
某一个轮径
某一个 Nav2 footprint
某一次手工外参
```

---

# 55. 最终 Definition of Done

执行 AI 在结束前逐项确认：

```text
[ ] 已审计当前 working tree，没有 reset/clean。
[ ] 已复用现有 app/navigation exploration 代码而不是重复造轮子。
[ ] 已建立 RobotBackend 平台抽象。
[ ] 已实现 Go2WExperimentalBackend。
[ ] 已实现 Mock/Future metric backend 测试路径。
[ ] 已实现 Exploration Graph / session spatial-semantic memory。
[ ] 已实现 live candidate goal generation。
[ ] 已让 SemanticNavigation semantic directive 能影响 exploration goal。
[ ] 已实现 visited/negative/failure/oscillation penalty。
[ ] 已实现 AutonomousExplorer 长周期循环。
[ ] 已实现 navigation result → recovery/replan。
[ ] 已实现 search budget 与终止原因。
[ ] 已实现 operator-supervised experiment profile。
[ ] 未删除正式 Stage2/dual-LiDAR/navigation fail-closed 逻辑。
[ ] 当前实验不再被人工 Pandar/四方向/相机标定阻塞。
[ ] 已实现自动 health/freshness 检查。
[ ] 自动可做的底层增强不要求用户摆场景。
[ ] 2D visual confirmation 可作为当前 Go2-W TARGET_FOUND。
[ ] 已有 offline mock E2E tests。
[ ] 已有 replay tests。
[ ] 已有真实 perception dry-run。
[ ] 已有真实 Go2-W 连续自主探索结果。
[ ] 至少一次真机实验有 10+ autonomous planning cycles。
[ ] 用户没有在实验过程中逐步指定运动方向。
[ ] 已验证普通目标搜索。
[ ] 已验证至少一个关系/anchor 语义搜索。
[ ] operator stop 能安全结束 session。
[ ] 生成 JSONL、exploration graph、summary、视频（若启用）。
[ ] README 给出一条可复制的一键运行命令。
[ ] docs/HIGH_LEVEL_AUTONOMOUS_SEMANTIC_EXPLORATION.md 已完成。
[ ] 最新 handoff/report 已完成。
[ ] 核心回归测试 PASS。
[ ] git diff --check PASS。
```

全部满足后，本阶段可以宣布：

```text
HIGH_LEVEL_AUTONOMOUS_SEMANTIC_EXPLORATION = PASS
GO2W_OPERATOR_SUPERVISED_E2E = PASS
PLATFORM_ABSTRACTION = PASS
FUTURE_ROBOT_BACKEND_READY = PASS
```

---

# 56. 给执行 AI 的最后一句话

不要把时间继续消耗在“如何让当前 Go2-W 成为一台底层完美的产品机器人”上。

这次实现的主线只有一条：

> **把仓库里已经存在的视觉、SceneGraph、SemanticNavigation、搜索状态机、导航/探索骨架真正收敛成一个平台解耦、能连续运行、能记忆、能重规划、能自主选择下一探索目标、能在当前 Go2-W 上由操作者监督跑通、未来能直接换 RobotBackend 的完整高层自主语义探索系统。**

只要某个底层问题：

```text
不需要人工操作
不需要特殊环境
AI 能自动完成
```

就可以顺手自动化。

只要某个底层问题需要：

```text
人工标定
人工摆场
人工量尺寸
人工执行特定采集动作
```

就不允许它阻塞本计划。

**最终以真实连续 E2E 闭环，而不是“代码文件已经存在”，作为完成标准。**
