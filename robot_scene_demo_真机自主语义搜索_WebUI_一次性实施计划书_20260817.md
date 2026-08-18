# robot_scene_demo 真机自主语义搜索 WebUI 一次性实施计划书

> 版本：2026-08-17  
> 项目仓库：https://github.com/BROVVV/robot_scene_demo  
> 当前实验平台：Unitree Go2-W  
> Web 技术路线：复用当前 `app/manual_web_demo/` 的 FastAPI + 原生 HTML/CSS/JS + MJPEG + WebSocket + ROS Worker IPC  
> 高层搜索路线：复用/接入项目现有视觉、SceneGraph、UniGoal、搜索状态机、Exploration Planner / Navigation Map，并与未来 `RobotBackend` 保持平台解耦  
> 本计划目标：**把当前项目升级成一个能通过浏览器输入目标、启动真实机器狗自主搜索，并实时显示相机、当前物体、目标证据、下一步决策、搜索轨迹和实时探索地图的完整真机 Web 控制台。**

---

# 0. 给执行 AI 的最高优先级任务

如果你是一位同时拿到：

1. 本计划书；
2. GitHub 仓库 `https://github.com/BROVVV/robot_scene_demo`

的执行 AI，你的任务不是重新给用户写一份分析报告，也不是只搭一个前端原型。

你的任务是：

> **直接审计当前仓库，在不推倒已有 Manual Web Demo、UniGoal、真机搜索、navigation/exploration 代码的前提下，把“真机自主语义搜索 WebUI”从后端到前端完整实现、接线、测试、文档化，并提供一条可直接启动的命令。**

最终用户必须能做到：

```text
打开浏览器
↓
输入：寻找“饮水机旁边的蓝色垃圾桶”
↓
点击“开始搜索”
↓
机器狗开始自主探索
↓
页面持续实时显示：
  - 相机画面
  - 当前观察到的物体
  - 当前目标匹配/关系证据
  - 当前搜索阶段
  - 下一步高层探索指令
  - 为什么选择这一步
  - Candidate goal 排名/分数
  - 当前机器人/搜索节点
  - 实时 Exploration Graph / 拓扑地图
  - 已访问位置
  - Semantic-interest 节点
  - action / replan timeline
↓
目标确认
↓
TARGET_FOUND
↓
停止机器人并保存 session
```

执行过程中：

> **不要每实现一个小模块就停下来问用户。**

先自行审计、复用、实现、测试。

只有出现真正无法通过代码/当前仓库解决、且会导致整个项目不可继续的问题，才在最终报告中明确列出。

---

# 1. 当前项目的定位

本阶段不是要把 Go2-W 做成生产级自主机器人。

当前项目定位为：

> **Operator-Supervised Autonomous Semantic Exploration Prototype**

也就是：

```text
用户：
  输入目标
  点击开始
  现场拿遥控器监督
  必要时人工中断

系统：
  感知
  目标理解
  语义推理
  记忆
  探索规划
  真机动作
  再观察
  Replan
  目标确认
```

未来会迁移到另一台已经具备：

```text
SLAM
定位
底层导航
避障
越野
相机标定
LiDAR 标定
底盘控制
```

的成熟机器狗。

因此 WebUI 和高层自主搜索必须：

> **平台无关。**

当前 Go2-W 只是一个 experimental backend。

---

# 2. 本计划必须遵循的人工工作边界

本计划不得因为以下事情未完成而停止 WebUI/高层搜索开发：

```text
Pandar 人工外参标定
人工摆棋盘
人工量尺寸
人工测轮距/轮径
人工摆四方向障碍物
要求四周空旷
人工按特定路线做 SLAM/LIO 标定
人工重建当前 Go2-W 产品级 Nav2
人工完成产品级 Collision Monitor
```

如果某些底层功能：

```text
不需要用户操作机器狗
不要求特殊场地
AI 可自动采集/判断
```

可以自动完成，例如：

```text
topic discovery
TF discovery
pose topic discovery
camera health
Frame Bundle freshness
自动重连
ROS worker 重启
LLM 状态
Action availability
session 日志
正常搜索过程中的 motion correction statistics
```

但不能让可选自动标定成为 WebUI 启动硬 blocker。

---

# 3. 当前仓库已经有什么：必须先复用

执行 AI 修改前必须完整审计当前仓库。

特别是：

```text
README.md

app/manual_web_demo/
app/live_robot/
app/reasoning/unigoal/
app/navigation/
app/planning/
app/memory/
app/video/

scripts/go2w/
tests/

docs/GO2W_MANUAL_WASD_WEB_DEMO.md
docs/UNIGOAL_SEMANTIC_SEARCH_INTEGRATION.md
```

---

# 4. 当前 Manual Web Demo 是本次 WebUI 的基础，不准重写一套

当前仓库已经有：

```text
app/manual_web_demo/
  __init__.py
  config.py
  manual_drive_controller.py
  models.py
  ros_worker_client.py
  scene_object_analyzer.py
  web_server.py
  static/
  templates/
```

已有能力包括：

```text
FastAPI
HTML/CSS/JS
/api/camera.mjpeg
/api/status
/api/objects
/api/llm/enable
/api/llm/disable
/api/control/enable
/api/control/disable
/api/estop
/ws/control

ROS worker subprocess
JSONL/stdin/stdout IPC
thread-safe queue
WebSocket broadcaster
latest.jpg -> MJPEG
WASD+QE
SceneObjectAnalyzer
```

所以本计划的原则是：

> **升级现有 `app/manual_web_demo`，把它从 Manual WASD Demo 升级成“Manual + Autonomous Search Web Console”。**

不得另开：

```text
第二套 FastAPI server
第二个 camera MJPEG server
第二套 ROS worker
第二份独立 LLM scene analyzer 作为自主搜索真值
```

---

# 5. 当前 Manual Demo 和自主搜索必须并存

推荐最终页面三个主视图：

```text
[ 自主搜索 ] [ 手动控制 ] [ 系统状态 ]
```

其中：

## 自主搜索

本计划重点。

## 手动控制

继续保留当前 WASD+QE。

用于：

```text
调试
人工接管
快速实验
```

## 系统状态

显示：

```text
Camera
Frame Bundle
LLM
ROS Worker
Motion backend
Pose source
Search worker
WebSocket
```

不要删除现有 Manual Demo。

---

# 6. UI 不是“大脑”

这是架构上最重要的原则。

禁止：

```text
浏览器 JS
→ 自己决定 TURN_LEFT
→ 直接调用 ROS
```

禁止：

```text
WebUI
→ 单独调用 LLM
→ 得到目标
→ 直接控制机器人
```

正确结构：

```text
┌───────────────────────────────────────────────┐
│                    Browser                     │
│                                               │
│ Target input / Camera / Objects / Map / Logs  │
└───────────────────────┬───────────────────────┘
                        │
                  HTTP + WebSocket
                        │
┌───────────────────────▼───────────────────────┐
│           Existing FastAPI Web Process        │
│                                               │
│ app/manual_web_demo/web_server.py             │
│ + Autonomous Search Web integration           │
│                                               │
│ SearchSessionService                          │
│ SearchStateStore                              │
│ SearchEventBroadcaster                        │
└───────────────────────┬───────────────────────┘
                        │
                Search command / events
                        │
┌───────────────────────▼───────────────────────┐
│             Autonomous Search Core            │
│                                               │
│ Observation                                   │
│ TargetProfile / GoalGraph                     │
│ SceneGraph                                    │
│ UniGoal                                       │
│ Semantic Memory                               │
│ Exploration Planner                           │
│ AutonomousExplorer                            │
└───────────────────────┬───────────────────────┘
                        │
                 ExplorationGoal
                        │
┌───────────────────────▼───────────────────────┐
│                 RobotBackend                  │
│                                               │
│ Go2WExperimentalBackend                       │
│ FutureProductionRobotBackend                  │
└───────────────────────┬───────────────────────┘
                        │
                 ROS / platform API
                        │
                      Robot
```

WebUI 只负责：

```text
发任务
发暂停
发恢复
发停止
展示
```

搜索决策必须来自真实搜索后端。

---

# 7. 目标 WebUI 最终页面布局

建议桌面端：

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Autonomous Semantic Search                        Camera● Robot● LLM●   │
│                                                                         │
│ 寻找目标：[ 饮水机旁边的蓝色垃圾桶                    ] [开始搜索]     │
│                                              [暂停] [继续] [停止] [急停]│
├────────────────────────────────────┬────────────────────────────────────┤
│                                    │ SEARCH STATUS                      │
│                                    │                                    │
│           REAL-TIME CAMERA         │ Target                             │
│                                    │ 蓝色垃圾桶 near 饮水机             │
│       RGB / optional overlay       │                                    │
│                                    │ Phase: PLAN                        │
│                                    │ Cycle: 17                          │
│                                    │ Elapsed: 02:43                     │
│                                    │                                    │
│ FPS: 10.2    Age: 80 ms            │ Match: PARTIAL                     │
│                                    │ Anchor: water dispenser ✓          │
│                                    │ Target: not found                  │
├────────────────────────────────────┼────────────────────────────────────┤
│ CURRENT OBSERVATION                │ NEXT DECISION                      │
│                                    │                                    │
│ chair               ×3             │ INSPECT_ANCHOR                     │
│ water dispenser     ×1             │ ↻ inspect right-front sector       │
│ door                ×1             │                                    │
│ desk                ×2             │ Reason:                            │
│                                    │ Found explicit anchor...           │
│ Related evidence:                  │                                    │
│ water dispenser ✓                  │ semantic      0.91                  │
│ blue trash bin ✕                   │ info_gain     0.72                  │
│ near relation ?                    │ novelty       0.64                  │
├────────────────────────────────────┼────────────────────────────────────┤
│          EXPLORATION MAP           │ EVENT TIMELINE                     │
│                                    │                                    │
│       ○──────●──────★              │ 17:31:11 OBSERVE                   │
│              ▲ Robot               │ 17:31:14 OBJECTS_UPDATED           │
│              │                     │ 17:31:15 ANCHOR_FOUND              │
│              ○                     │ 17:31:16 GOAL_SELECTED             │
│                                    │ 17:31:17 ACTION_STARTED            │
│ ● visited                          │ ...                                │
│ ○ unseen                           │                                    │
│ ★ semantic interest               │                                    │
│ ✕ unreachable                     │                                    │
└────────────────────────────────────┴────────────────────────────────────┘
```

---

# 8. 页面必须响应式

至少适配：

```text
1920×1080
1440×900
1366×768
```

移动端不是当前重点，但布局不能完全不可用。

桌面优先。

---

# 9. 顶部任务区

必须实现：

```text
目标输入框
开始搜索
暂停
继续
停止
急停
```

目标输入框支持中文自然语言，例如：

```text
绿色垃圾桶
灰色书包
门旁边的灭火器
饮水机旁边的蓝色垃圾桶
```

---

# 10. Start 请求语义

新增：

```http
POST /api/search/start
```

Body：

```json
{
  "target": "饮水机旁边的蓝色垃圾桶",
  "reasoner": "unigoal",
  "backend": "go2w_experimental",
  "finish_on_visual_confirmation": true
}
```

返回必须立即：

```json
{
  "ok": true,
  "session_id": "search_20260817_174105",
  "status": "STARTING"
}
```

绝不能让 HTTP 请求等待整个搜索完成。

---

# 11. Search 必须异步

禁止：

```python
@app.post("/api/search/start")
def start():
    autonomous_search.run()
```

如果 `.run()` 是 10 分钟 blocking loop，FastAPI 会被卡住。

必须：

```text
POST /search/start
→ 建立 SearchSession
→ 创建独立 search task / worker
→ 立即返回
→ 搜索状态通过 WebSocket 持续推送
```

---

# 12. Search Worker 的进程/线程策略

项目存在：

```text
Conda Python
ROS2 /usr/bin/python3
```

环境隔离。

因此优先推荐：

```text
FastAPI Web Process
  Conda / application environment

Autonomous Search Process
  根据搜索链实际依赖选择合适环境

ROS Worker
  /usr/bin/python3 + ROS2 Humble
```

不要为了简化，把：

```text
rclpy
Conda models
FastAPI
```

全部强塞进一个 Python process。

---

# 13. 推荐的数据流

最终：

```text
Camera ROS
  │
  ▼
ROS Worker
  │
  ├──────── latest.jpg ──────────────► MJPEG /api/camera.mjpeg
  │
  └──────── sensor/pose status
                │
                ▼
        AutonomousExplorer
                │
       ┌────────┼──────────┐
       ▼        ▼          ▼
 Observation  Memory      Planner
                           │
                           ▼
                    ExplorationGoal
                           │
                           ▼
                     RobotBackend
                           │
                           ▼
                         Robot
                           │
                       result/pose
                           │
                           ▼
                   ExplorationGraph
                           │
                           ▼
                    SearchEventBus
                           │
                        WebSocket
                           │
                           ▼
                         Browser
```

---

# 14. SearchEvent：本次集成最核心的数据协议

必须建立统一事件模型。

推荐：

```text
app/live_robot/search_event.py
```

或仓库审计后选择更适合位置。

---

# 15. SearchEvent 数据结构

推荐：

```python
@dataclass
class SearchEvent:
    event_id: int
    session_id: str
    timestamp: float
    event_type: str
    cycle: int | None
    payload: dict[str, Any]
```

还可包括：

```text
schema_version
source
```

例如：

```json
{
  "schema_version": "search_event_v1",
  "event_id": 127,
  "session_id": "search_20260817_174105",
  "timestamp": 1786969265.82,
  "event_type": "GOAL_SELECTED",
  "cycle": 17,
  "payload": {}
}
```

---

# 16. 必须支持的 SearchEvent 类型

至少：

```text
SESSION_CREATED
SESSION_STARTED
SEARCH_STATE_CHANGED
OBSERVATION_STARTED
OBSERVATION_UPDATED
OBJECTS_UPDATED
SCENE_GRAPH_UPDATED
TARGET_PROFILE_READY
GOAL_GRAPH_READY
TARGET_MATCH_UPDATED
TARGET_CANDIDATE
VERIFICATION_STARTED
VERIFICATION_FINISHED
TARGET_CONFIRMED
MEMORY_UPDATED
MAP_UPDATED
CANDIDATES_GENERATED
GOAL_SELECTED
ACTION_STARTED
ACTION_PROGRESS
ACTION_FINISHED
REPLAN
PAUSED
RESUMED
OPERATOR_STOP
ERROR
SEARCH_FINISHED
```

---

# 17. 所有事件必须可序列化

禁止 event payload 中直接放：

```text
numpy array
PIL.Image
ROS message instance
OpenCV Mat
Python class object
```

必须转成 JSON-safe：

```text
str
number
bool
list
dict
null
```

图像走 MJPEG，不塞 WebSocket。

---

# 18. SearchEventBus

新增或实现等价：

```text
SearchEventBus
```

职责：

```text
publish(event)
subscribe(callback)
unsubscribe(callback)
recent_events()
```

WebUI、JSONL logger、session recorder 可以同时订阅。

---

# 19. EventBus 不得和 FastAPI 耦死

错误：

```text
AutonomousExplorer
→ websocket.send_json()
```

正确：

```text
AutonomousExplorer
→ SearchEventBus.publish()
```

然后：

```text
WebSocket adapter
JSONL logger
tests
replay
```

分别消费。

这样 CLI 模式仍然可运行。

---

# 20. SearchStateStore

Web 页面刷新以后，必须能恢复。

不能只依赖 WebSocket 增量。

新增：

```text
SearchStateStore
```

维护当前 session 最新快照：

```text
session
target
phase
cycle
current observation
objects
scene graph summary
target evidence
current goal
navigation status
map revision
health
timeline
```

---

# 21. `/api/search/state`

新增：

```http
GET /api/search/state
```

返回：

```json
{
  "session_id": "search_20260817_174105",
  "status": "RUNNING",
  "target": "饮水机旁边的蓝色垃圾桶",
  "phase": "PLAN",
  "cycle": 17,
  "elapsed_seconds": 163.2,
  "observation": {
    "bundle_id": "bundle_...",
    "timestamp": 1786969265.1,
    "objects": []
  },
  "target_match": {
    "level": "partial",
    "target_confirmed": false,
    "explicit_anchor_found": true,
    "anchor_labels": ["water dispenser"]
  },
  "selected_goal": {},
  "robot": {
    "motion_status": "IDLE",
    "pose_quality": "relative"
  },
  "map_revision": 37
}
```

用途：

```text
首次打开页面
F5
WebSocket 重连
网络瞬断
```

---

# 22. Search WebSocket

新增：

```text
/ws/search
```

不要强行把所有自主搜索事件塞进现有：

```text
/ws/control
```

手动控制和搜索 telemetry 分离更清楚。

---

# 23. `/ws/search` 连接行为

连接成功：

1. accept；
2. 发送完整当前 snapshot；
3. 从下一事件开始发送增量 SearchEvent；
4. 定期 heartbeat；
5. 重连时不丢当前状态。

示例：

```json
{
  "type": "snapshot",
  "state": {}
}
```

之后：

```json
{
  "type": "event",
  "event": {
    "event_type": "GOAL_SELECTED"
  }
}
```

---

# 24. WebSocket heartbeat

前端和后端至少：

```text
10~20 秒 heartbeat
```

浏览器要显示：

```text
LIVE
RECONNECTING
OFFLINE
```

不要网络断掉后 UI 看起来仍像实时。

---

# 25. Camera：必须复用当前 MJPEG

现有：

```text
GET /api/camera.mjpeg
```

继续使用。

前端：

```html
<img src="/api/camera.mjpeg">
```

不要：

```text
WebSocket base64 video
每帧 POST
第二套视频编码服务
```

---

# 26. Camera overlay

第一版可继续显示 raw RGB。

第二步建议增加 optional overlay。

可支持：

```text
bbox
label
target candidate
anchor
current command
cycle
```

推荐优先浏览器 Canvas overlay：

```text
<img MJPEG>
+
<canvas overlay>
```

这样无需重新编码视频。

---

# 27. 当前物体列表的数据源

当前 Manual Demo 有：

```text
SceneObjectAnalyzer
```

它可以继续保留在“手动控制/场景概览”模式。

但是：

> **自主搜索页面的“当前看到了什么”必须优先来自 AutonomousExplorer 真正使用的 observation / SceneGraph。**

否则会出现：

```text
UI 独立分析：看见垃圾桶
Explorer：没看见垃圾桶
```

这会让整个系统不可解释。

---

# 28. UI 物体数据应该分三层

## 当前帧

```text
water dispenser   0.91
chair              0.85
door               0.74
desk               0.71
```

## Session 累积

```text
chair              12 observations
desk                8
door                3
water dispenser     1
```

## 与目标有关的语义证据

```text
Target
blue trash bin         NOT FOUND

Explicit anchor
water dispenser        FOUND

Relation
near                    PENDING
```

---

# 29. `/api/search/objects`

推荐新增：

```http
GET /api/search/objects
```

返回：

```json
{
  "current": [],
  "session_seen": [],
  "target_evidence": {}
}
```

---

# 30. SceneGraph UI

第一版不要求画完整图。

先显示：

```text
Objects
Attributes
Relations
```

后续可增加折叠 Graph View。

---

# 31. GoalGraph UI

Debug Drawer 显示：

```text
Target
Required relations
Explicit anchors
Context
```

方便展示 UniGoal。

---

# 32. “下一步指令”必须显示三层

第一层：

```text
INSPECT_ANCHOR
```

第二层：

```text
↻ 向右观察约 30°
```

第三层：

```text
已发现目标描述中的显式锚点“饮水机”，
但尚未找到蓝色垃圾桶。
右前区域尚未充分观察，
因此优先检查饮水机周边视角。
```

---

# 33. Candidate score UI

必须显示：

```text
semantic relevance
information gain
novelty
visited penalty
negative evidence penalty
motion/navigation cost
total
```

---

# 34. Candidate ranking Event

`CANDIDATES_GENERATED` payload：

```json
{
  "candidates": [
    {
      "goal_id": "goal_017_01",
      "goal_type": "INSPECT_ANCHOR",
      "label": "检查饮水机右侧",
      "score": 0.82,
      "components": {
        "semantic_relevance": 0.91,
        "information_gain": 0.72,
        "novelty": 0.64,
        "visited_penalty": 0.08,
        "cost": 0.11
      },
      "reason": "...",
      "selected": true
    }
  ]
}
```

---

# 35. AutonomousExplorer 必须成为 SearchEvent 生产者

至少在：

```text
observe start
observation ready
objects updated
match ready
memory updated
candidates ready
goal selected
action start
action finish
verification
finish
```

emit。

---

# 36. 不要让 WebUI 直接绑 `run_autonomous_loop.py` 的内部变量

当前脚本是 CLI runner。

WebUI 不允许：

```text
import script
读 global
```

Reusable core 必须下沉到：

```text
app/
```

CLI 只负责参数和启动。

---

# 37. SearchSessionService

推荐职责：

```text
start_search()
pause_search()
resume_search()
stop_search()
current_session()
state_snapshot()
subscribe_events()
```

---

# 38. SearchSession 状态机

```text
IDLE
STARTING
RUNNING
PAUSING
PAUSED
STOPPING
TARGET_FOUND
SEARCH_EXHAUSTED
FAILED
OPERATOR_STOP
FINISHED
```

---

# 39. Start 防重复

已有 RUNNING session 时：

```text
POST /api/search/start
```

不得启动第二个。

返回 409 或结构化错误。

---

# 40. Pause 语义

```text
停止生成新 goal
取消/停止当前运动
保留 memory
保留 ExplorationGraph
状态 PAUSED
```

Resume：

```text
重新 OBSERVE
→ REPLAN
```

不恢复旧指令。

---

# 41. Stop 语义

```text
cancel current goal
RobotBackend.stop()
session result=OPERATOR_STOP
flush log
summary
SEARCH_FINISHED
```

---

# 42. 急停

现有：

```text
POST /api/estop
```

必须一直可见。

点击急停后：

```text
机器人停止
SearchSession 同时进入 ESTOP/OPERATOR_STOP
```

不能随后自动发新动作。

---

# 43. Manual / Autonomous 控制权互斥

建立：

```text
ControlOwner
```

状态：

```text
NONE
MANUAL
AUTONOMOUS
ESTOP
```

Autonomous RUNNING 时：

```text
WASD disabled
```

手动接管前：

```text
pause/stop autonomous
stop robot
再 enable manual
```

---

# 44. 实时地图：当前显示 Semantic Exploration Graph

当前 Go2-W 不要求有正式 metric occupancy map。

所以当前 WebUI 必须显示：

> **Topological / Relative Semantic Exploration Map**

而不是伪造 SLAM。

---

# 45. 地图必须复用现有 navigation 思想

优先复用：

```text
app/navigation/video_navigation_map.py
app/navigation/exploration_planner.py
```

当前它们已经有：

```text
nodes
edges
pose
objects
relative/metric status
information_gain
target_relevance
path_cost
```

扩展成 live graph。

---

# 46. ExplorationGraph schema

```json
{
  "schema_version": "live_exploration_graph_v1",
  "revision": 37,
  "map_mode": "topological",
  "current_node_id": "node_009",
  "robot": {
    "pose_quality": "relative",
    "x": 1.1,
    "y": 0.3,
    "yaw": 0.52
  },
  "nodes": [],
  "edges": []
}
```

---

# 47. Node schema

```json
{
  "node_id": "node_009",
  "x": 1.1,
  "y": 0.3,
  "yaw": 0.52,
  "pose_quality": "relative",
  "state": "SEMANTIC_INTEREST",
  "visited_count": 1,
  "objects": [
    "water dispenser",
    "door"
  ],
  "target_match_level": "partial",
  "semantic_relevance": 0.91,
  "information_gain": 0.72,
  "timestamp": 1786969265.1
}
```

---

# 48. Node 状态

```text
UNSEEN
OBSERVED
VISITED
SEMANTIC_INTEREST
NEGATIVE
UNREACHABLE
TARGET_CANDIDATE
TARGET_CONFIRMED
CURRENT
```

---

# 49. Edge schema

```json
{
  "edge_id": "edge_008_009",
  "from": "node_008",
  "to": "node_009",
  "action_type": "ROTATE_VIEW",
  "distance": 0.0,
  "delta_yaw": 0.52,
  "navigation_result": "SUCCEEDED",
  "traversable": true
}
```

---

# 50. map_mode 双模式

从第一版支持：

```text
topological
metric
```

当前：

```text
topological / relative
```

未来成熟机器狗：

```text
metric
```

WebUI 不重写。

---

# 51. Map API

```http
GET /api/search/map
```

初始化和重连用完整 snapshot。

实时通过：

```text
MAP_UPDATED
```

---

# 52. 前端地图必须 SVG/Canvas，不用 matplotlib

推荐：

```text
SVG
```

支持：

```text
node
edge
robot heading
selected goal
hover
click
auto fit
```

---

# 53. 地图视觉语义

```text
CURRENT            ▲
VISITED            ●
UNSEEN             ○
SEMANTIC_INTEREST  ★
NEGATIVE           ◌
UNREACHABLE        ✕
TARGET_CANDIDATE   ◎
TARGET_CONFIRMED   ✓
```

---

# 54. pose 不可靠时

允许：

```text
display_x
display_y
layout_only=true
```

只用于 SVG。

禁止把 UI layout 坐标作为机器人导航坐标。

---

# 55. Timeline

实时展示 SearchEvent。

浏览器保留最近：

```text
200~500
```

完整日志在 JSONL。

---

# 56. Session 持久化

每次搜索：

```text
outputs/live_runs/<session_id>/
  events.jsonl
  summary.json
  exploration_graph.json
  target_profile.json
  goal_graph.json
  final_state.json
  optional_recording.mp4
```

---

# 57. Session Summary

至少：

```json
{
  "session_id": "...",
  "result": "TARGET_FOUND",
  "target": "...",
  "duration_seconds": 243.2,
  "planning_cycles": 17,
  "actions": 12,
  "observations": 19,
  "unique_nodes": 10,
  "replans": 5,
  "semantic_anchor_hits": 1,
  "target_verify_attempts": 2,
  "finish_reason": "visual_confirmation"
}
```

---

# 58. 顶部状态灯

```text
Camera
Search
Robot
LLM
WebSocket
```

状态：

```text
green
yellow
red
gray
```

---

# 59. Search readiness

不要用正式 Stage2 12/12 作为本实验 WebUI 的 hard blocker。

建立：

```text
ExperimentSearchReadiness
```

自动检查：

```text
camera fresh
Frame Bundle fresh
LLM available
search backend available
robot mode/error readable
motion backend available（需要运动时）
stop/estop available
```

人工标定项不得阻塞。

---

# 60. Dry-run

WebUI 必须支持：

```text
真实 camera
真实 LLM
真实 SceneGraph
真实 UniGoal
真实 Planner
不执行动作
```

---

# 61. Mock

必须支持无 ROS 的：

```text
Mock Search Backend
```

用于 CI 和前端开发。

至少场景：

```text
target_first_frame
target_after_5_nodes
anchor_then_target
search_exhausted
navigation_failure
operator_stop
```

---

# 62. 前端技术

继续：

```text
HTML
CSS
plain JavaScript
SVG
WebSocket
```

第一版不要引入 React/Vue。

---

# 63. 前端状态

推荐：

```javascript
const appState = {
  search: {},
  observation: {},
  objects: {},
  targetMatch: {},
  selectedGoal: null,
  candidates: [],
  map: {},
  events: [],
  health: {}
};
```

统一：

```text
applySearchEvent(event)
```

更新状态。

---

# 64. 页面初始化

```text
GET /api/status
GET /api/search/state
GET /api/search/map
connect /ws/search
```

---

# 65. WebSocket 重连

```text
1s
2s
5s
```

重连成功重新取 snapshot。

---

# 66. 按钮状态

IDLE：

```text
Start enabled
```

RUNNING：

```text
Pause / Stop enabled
```

PAUSED：

```text
Resume / Stop enabled
```

FINISHED：

```text
Start enabled
```

---

# 67. LLM latency UI

相机继续实时。

LLM 分析期间显示：

```text
Analyzing...
last analyzed frame age
```

不要让 UI 看起来卡死。

---

# 68. Camera 与 analyzed frame 区分

建议：

```text
LIVE CAMERA
LAST ANALYZED FRAME
```

后者可显示 bbox。

避免旧 bbox 套在新视频上。

---

# 69. Robot action 状态

显示：

```text
PLANNED
EXECUTING
SUCCEEDED
FAILED
```

失败后：

```text
REPLAN
```

可见。

---

# 70. Search result

TARGET_FOUND：

```text
✓ TARGET FOUND
```

醒目 banner。

NOT FOUND：

显示具体：

```text
SEARCH_EXHAUSTED
TIMEOUT
MAX_STEPS
```

---

# 71. Error 分类

```text
PERCEPTION_ERROR
LLM_ERROR
SEARCH_ERROR
BACKEND_ERROR
ROS_WORKER_ERROR
MOTION_ERROR
WEBSOCKET_ERROR
```

一次可恢复错误不能让整个 UI 崩溃。

---

# 72. Future RobotBackend

WebUI 不写死：

```text
/go2w/motion
/go2w/odom/fused
```

自主搜索状态来自通用 backend。

当前：

```text
Go2WExperimentalBackend
```

未来：

```text
ProductionRobotBackend
```

---

# 73. 当前 Manual SceneObjectAnalyzer

保留在 Manual Tab。

Autonomous Tab：

```text
objects source = explorer
```

可在 Debug 区显示 analyzer 参考结果。

---

# 74. 三个 Tab

## Autonomous Search

```text
目标
相机
物体
目标证据
下一步
地图
timeline
```

## Manual Control

保留现有 WASD+QE。

## System

```text
camera
ROS worker
motion
pose
LLM
search
outputs
```

---

# 75. URL

继续：

```text
http://127.0.0.1:8765
```

不要另开第二个 server/port。

---

# 76. 启动脚本

推荐新增：

```text
scripts/go2w/start_autonomous_search_web.sh
scripts/go2w/stop_autonomous_search_web.sh
```

底层复用同一个 FastAPI server。

---

# 77. 推荐最终命令

```bash
cd /home/brov/robot/robot_scene_demo

bash scripts/go2w/start_autonomous_search_web.sh   --enable-autonomous-motion
```

然后浏览器输入目标。

---

# 78. launcher

自动检查：

```text
environment
FastAPI
runtime dir
existing process
ROS worker
camera
motion backend
```

不随意 kill 其他 ROS/用户进程。

---

# 79. 默认行为

建议默认 Web 可以只读启动。

真机运动需：

```text
--enable-autonomous-motion
```

这只是实验启动开关，不是人工标定。

---

# 80. Browser disconnect

Autonomous Search 不应无脑继承 manual deadman。

推荐配置：

```yaml
stop_search_on_last_browser_disconnect: false
```

因为 autonomous task 是后端任务。

但 Web server/process 退出时必须：

```text
stop owned search
stop backend
flush logs
```

---

# 81. Process ownership

只停止项目拥有的进程。

禁止：

```text
pkill python
pkill ros2
```

---

# 82. ROS worker crash

UI 红灯。

Search：

```text
BACKEND_UNAVAILABLE
```

停止发新动作。

---

# 83. Search worker crash

状态：

```text
FAILED
```

RobotBackend stop。

---

# 84. 并发

显式保护：

```text
SearchStateStore
SearchEventBus
session lifecycle
ROS Worker commands
```

---

# 85. FastAPI event loop

禁止 blocking LLM/runner 直接跑在 async route。

使用：

```text
thread/process/task
```

---

# 86. Search IPC

如果 search 独立 process：

推荐：

```text
JSONL stdin/stdout
```

或：

```text
multiprocessing queue
```

保持项目现有 IPC 风格。

---

# 87. 不轮询 JSONL 做实时 UI

实时：

```text
IPC/EventBus → WebSocket
```

日志：

```text
JSONL
```

两者职责分离。

---

# 88. Debug View

可展开：

```text
GoalGraph
SceneGraph
Candidates
Selected provenance
Memory penalty
Raw state
```

---

# 89. 简洁/调试切换

```text
[ 简洁 ] [ 调试 ]
```

默认简洁。

---

# 90. Node detail

点击地图节点：

```text
Node ID
Time
Pose quality
Objects
Relations
Target match
Semantic relevance
Visited
Negative evidence
Navigation failures
```

---

# 91. 安全的数据展示原则

UI 不自行决定：

```text
target found
visited
unreachable
selected goal
```

必须后端给出。

---

# 92. Planner score 后端输出

至少：

```text
semantic_relevance
information_gain
novelty
frontier_bonus
visited_penalty
negative_penalty
navigation_failure_penalty
motion_cost
total
```

前端不反算。

---

# 93. Goal reason

后端提供：

```text
human-readable reason
+
structured provenance
```

两者都保存。

---

# 94. relation evidence

关系目标 UI 必须能显示：

```text
blue trash bin --near--> water dispenser
```

状态：

```text
pending
confirmed
rejected
```

---

# 95. Target confirmation

不能把：

```text
strong graph match
```

直接展示为 confirmed。

沿用现有 verify/evidence gate。

---

# 96. Session 原子状态

snapshot 读写用 lock/copy，避免半更新。

---

# 97. Unit Tests

至少新增/扩展：

```text
tests/test_search_event.py
tests/test_search_state_store.py
tests/test_search_session_service.py
tests/test_autonomous_search_web_routes.py
tests/test_autonomous_search_websocket.py
tests/test_control_ownership.py
tests/test_live_exploration_graph.py
```

---

# 98. API Tests

必须：

```text
start
duplicate start
pause
resume
stop
state
map
objects
history
```

---

# 99. WebSocket Tests

```text
initial snapshot
event delivery
ordering
disconnect
reconnect
```

---

# 100. Ownership Tests

```text
manual active → autonomous start blocked
autonomous running → manual control blocked
estop overrides all
```

---

# 101. Crash Tests

```text
search worker crash
ROS worker crash
LLM timeout
camera stale
```

UI/后端都不能挂死。

---

# 102. Search Logic Mock Tests

## Target first frame

```text
TARGET_FOUND
0 motion
```

## Anchor case

```text
anchor found
semantic score rises
goal changes
target appears
confirmed
```

## Exhausted

```text
graph grows
budget ends
```

## Navigation failure

```text
goal A fail
REPLAN
goal B
```

## Pause/Resume

fresh observe after resume。

## Operator stop

summary written。

---

# 103. FastAPI TestClient

使用现有 Python 测试体系。

---

# 104. 真机验收 A：相机

Web 启动：

```text
camera live
status live
```

不运动。

---

# 105. 真机验收 B：Search dry-run

真实：

```text
camera
LLM
SceneGraph
UniGoal
Planner
```

动作 dry-run。

至少 5 cycles。

---

# 106. 真机验收 C：turn-only

操作者手持遥控器。

Web 输入目标后：

```text
observe
plan
turn
observe
replan
```

至少 5 cycles。

用户不告诉它方向。

---

# 107. 真机验收 D：turn + short-forward

至少：

```text
10+ autonomous planning cycles
```

Web map 实时增长。

---

# 108. 真机验收 E：目标不存在

直到：

```text
SEARCH_EXHAUSTED/TIMEOUT
```

页面一直可操作。

---

# 109. 真机验收 F：普通目标

例如：

```text
绿色垃圾桶
```

最终：

```text
TARGET_FOUND
```

---

# 110. 真机验收 G：关系目标

推荐：

```text
饮水机旁边的蓝色垃圾桶
```

必须在 UI/日志看到：

```text
GoalGraph
anchor observed
semantic relevance increase
goal priority change
relation evidence
verify
TARGET_FOUND
```

---

# 111. README

增加：

```text
Go2-W Autonomous Semantic Search WebUI
```

包含：

```text
一键启动
URL
Start/Pause/Stop/Estop
实验模式
输出目录
```

---

# 112. 新文档

新增：

```text
docs/GO2W_AUTONOMOUS_SEARCH_WEBUI.md
```

内容：

```text
architecture
process model
API
WebSocket
SearchEvent
map schema
session lifecycle
manual/autonomous ownership
mock
real robot
troubleshooting
future backend
```

---

# 113. Handoff

生成：

```text
reports/go2w_autonomous_search_webui_handoff_<date>.md
```

---

# 114. Git 工作规则

开始：

```bash
git status --short
git diff --check
git diff --stat
git rev-parse HEAD
```

禁止：

```text
git reset --hard
git clean -fd
git checkout -- .
```

---

# 115. 实施顺序

## Phase 0

审计全部相关代码，不停止。

## Phase 1

```text
SearchEvent
SearchEventBus
SearchStateStore
```

## Phase 2

instrument AutonomousExplorer / 当前真机 search core。

## Phase 3

```text
SearchSessionService
ControlOwner
```

## Phase 4

```text
/api/search/*
/ws/search
```

## Phase 5

Autonomous Tab 第一版：

```text
target
camera
objects
next goal
timeline
```

## Phase 6

live ExplorationGraph。

## Phase 7

SVG map。

## Phase 8

Planner/GoalGraph/SceneGraph debug。

## Phase 9

Pause/Resume/Manual ownership。

## Phase 10

Mock E2E。

## Phase 11

真实相机 dry-run。

## Phase 12

真机 supervised E2E。

## Phase 13

文档、回归、handoff。

---

# 116. 推荐目录变化

先审计，按职责实现，不机械重复已有文件。

```text
app/
  manual_web_demo/
    web_server.py
    config.py
    models.py

    search_routes.py
    search_session_service.py
    search_state_store.py
    search_models.py

    templates/
      index.html

    static/
      app.js
      style.css
      search_ui.js
      search_map.js

  live_robot/
    search_event.py
    search_event_bus.py
    autonomous_explorer.py

  navigation/
    exploration_planner.py
    video_navigation_map.py
    exploration_graph.py

scripts/
  go2w/
    start_autonomous_search_web.sh
    stop_autonomous_search_web.sh

tests/
  test_search_event.py
  test_search_state_store.py
  test_search_session_service.py
  test_autonomous_search_web_routes.py
  test_autonomous_search_websocket.py
  test_control_ownership.py
  test_live_exploration_graph.py

docs/
  GO2W_AUTONOMOUS_SEARCH_WEBUI.md

reports/
  go2w_autonomous_search_webui_handoff_<date>.md
```

---

# 117. 配置

推荐：

```text
configs/go2w/autonomous_search_web.yaml
```

示例：

```yaml
web:
  host: 127.0.0.1
  port: 8765
  search_event_buffer: 500

search:
  reasoner: unigoal
  backend: go2w_experimental
  finish_on_visual_confirmation: true

  max_search_seconds: 600
  max_planning_cycles: 100
  max_motion_steps: 50

map:
  mode: auto

supervision:
  operator_supervised: true
```

---

# 118. 网络

默认：

```text
127.0.0.1
```

不要默认把机器人控制 API 暴露到局域网。

---

# 119. 用户输入

限制：

```text
1~500 字符
```

前端使用：

```text
textContent
```

防 XSS。

---

# 120. Event ordering

每个：

```text
event_id
```

单调增加。

Map：

```text
revision
```

单调增加。

前端忽略旧事件。

---

# 121. 当前项目不需要的 Web 功能

暂时不做：

```text
账号系统
云部署
公网
多机器人
3D WebGL
完整 RViz
ROS bag Web viewer
前端模型推理
```

---

# 122. 最终 WebUI Definition of Done

执行 AI 完成前逐项确认：

```text
[ ] 已审计当前 working tree，没有 reset/clean。
[ ] 已复用 app/manual_web_demo FastAPI。
[ ] 现有 Manual WASD Demo 未被破坏。
[ ] 已建立 SearchEvent。
[ ] 已建立 SearchEventBus。
[ ] 已建立 SearchStateStore。
[ ] 已建立 SearchSessionService。
[ ] 已完成 ControlOwner。
[ ] 已完成 Start。
[ ] 已完成 Pause。
[ ] 已完成 Resume。
[ ] 已完成 Stop。
[ ] Estop 与 SearchSession 联动。
[ ] 已完成 /ws/search。
[ ] 已完成 /api/search/state。
[ ] 已完成 /api/search/map。
[ ] 已完成 /api/search/objects。
[ ] 已有 history 基础能力。
[ ] 相机继续复用 /api/camera.mjpeg。
[ ] Autonomous objects 来自真实 Explorer observation。
[ ] UI 显示当前物体。
[ ] UI 显示 session 累计物体。
[ ] UI 显示 target/anchor/relation evidence。
[ ] UI 显示 search phase。
[ ] UI 显示 next intent。
[ ] UI 显示 robot action。
[ ] UI 显示 decision reason。
[ ] UI 显示 candidate ranking。
[ ] 已复用/建立 live ExplorationGraph。
[ ] 地图实时更新。
[ ] 地图支持 topological/relative。
[ ] 不伪造 metric map。
[ ] current robot/current node 可见。
[ ] visited 可见。
[ ] semantic interest 可见。
[ ] negative 可见。
[ ] target candidate/confirmed 可见。
[ ] timeline 可见。
[ ] WebSocket 可重连。
[ ] 页面刷新可恢复 session。
[ ] Manual/Autonomous 不抢控制权。
[ ] Web shutdown 会停止自己拥有的 search/motion。
[ ] events.jsonl 自动保存。
[ ] summary.json 自动生成。
[ ] Mock Web E2E 全通过。
[ ] target first-frame test 通过。
[ ] anchor semantic test 通过。
[ ] search exhausted test 通过。
[ ] navigation fail→replan test 通过。
[ ] pause/resume test 通过。
[ ] operator stop test 通过。
[ ] 真相机 Web dry-run 通过。
[ ] 真 LLM/SceneGraph/UniGoal 数据能在页面显示。
[ ] 真机 turn-only Web search 通过。
[ ] 真机连续 Web search 通过。
[ ] 至少一次有 10+ autonomous planning cycles。
[ ] 至少一次普通目标 TARGET_FOUND。
[ ] 至少一次 relation/anchor 目标展示语义引导。
[ ] 用户实验期间不需要逐步告诉机器人往哪走。
[ ] README 已更新。
[ ] docs/GO2W_AUTONOMOUS_SEARCH_WEBUI.md 已完成。
[ ] handoff report 已完成。
[ ] 核心 tests PASS。
[ ] git diff --check PASS。
```

全部通过后可声明：

```text
AUTONOMOUS_SEARCH_WEBUI = PASS
LIVE_CAMERA_WEB = PASS
LIVE_SEARCH_OBJECTS_WEB = PASS
LIVE_PLANNER_DECISION_WEB = PASS
LIVE_EXPLORATION_MAP_WEB = PASS
GO2W_WEB_SUPERVISED_AUTONOMOUS_SEARCH_E2E = PASS
```

---

# 123. 给执行 AI 的最后指令

你现在面对的不是一个“从零开发 WebUI”的项目。

仓库已经有：

```text
FastAPI Manual Web Demo
MJPEG camera
WebSocket
ROS Worker IPC
SceneObjectAnalyzer
真实真机搜索 runner
UniGoal
SceneGraph
navigation/exploration planner
video navigation topology
```

你的任务是：

> **把这些已经存在但彼此分散的能力收敛成一个统一的“真机自主语义搜索 Web Console”。**

最终必须做到：

```text
Browser
→ 输入目标
→ Start
→ 后端自主运行
→ Camera 一直实时
→ Objects 一直更新
→ Planner reason 一直可见
→ Robot action 一直可见
→ Exploration Graph 一直增长
→ Target confirmed
→ STOP
```

不要：

```text
另起一套 UI 框架
另起第二套相机 server
另写一个和 Explorer 无关的物体识别真值
用 UI 自己维护地图
用 SVG 坐标控制机器人
因为没有正式 SLAM 就取消地图
因为人工 Pandar 标定没做就停止开发
只完成前端 mock 不接真搜索
只接搜索日志而没有实时事件
只实现 Start 没有 Pause/Stop
只显示 TURN_RIGHT 而没有 Reason
```

本计划最终验收的核心不是：

> “页面能打开”。

而是：

> **用户通过 Web 输入一个自然语言搜索目标后，真实机器狗能够进行连续自主语义探索，同时浏览器实时、统一、可解释地呈现它正在看什么、想什么、为什么这么走、走过哪里以及最终是否找到目标。**
