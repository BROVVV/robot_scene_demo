# robot_scene_demo RGB-D × UniGoal V2 Spatial Exploration × PSG 一次性重构实施计划书

> 版本：2026-08-18  
> 主项目：https://github.com/BROVVV/robot_scene_demo  
> 参考项目：https://github.com/bagh2178/UniGoal  
> 当前真机：Unitree Go2-W  
> 新增主视觉传感器：Intel RealSense D435  
> 当前部署方式：D435 接在 Go2-W Jetson 上，由 `realsense-stream.service` 提供 RGB-D HTTP 服务  
> 当前阶段定义：**Operator-Supervised RGB-D Spatial Semantic Exploration Prototype**  
> 本计划目标：**一次性把当前“2D SceneGraph + heading next-view + 原地旋转式探索”升级为“同步 RGB-D + 3D Semantic SceneGraph + Spatial Map + Frontier + PSG Semantic Prior + UniGoal Long-Term Spatial Goal + Local Executor + PlaceGraph + WebUI”的真实空间自主搜索闭环。**

---

# 0. 给执行 AI 的最高优先级指令

如果你拿到：

1. 本计划书；
2. `https://github.com/BROVVV/robot_scene_demo`
3. `https://github.com/bagh2178/UniGoal`

你的任务不是再写一份建议、TODO、架构草图或对比报告。

你的任务是：

> **直接审计当前 `robot_scene_demo` 最新 working tree / GitHub main，复用已经部署好的真机搜索、WebUI、UniGoal、PSG、RobotBackend、RealSense 服务，在不推倒现有成果的前提下，把本计划从 RGB-D 数据入口一直做到真机连续空间搜索、WebUI 地图与最终验收。**

最终必须达到：

```text
用户在 WebUI 输入自然语言目标
        ↓
D435 同步 RGB + Depth
        ↓
目标检测 / VLM / Observed SceneGraph
        ↓
对象 Depth Localization
        ↓
3D Semantic SceneGraph
        ↓
RGB-D Spatial Mapping / Pose
        ↓
Free / Unknown / Occupied
        ↓
Frontier Extraction
        ↓
GoalGraph × Observed SceneGraph
        ↓
ZERO / PARTIAL / STRONG
        ↓
PSG Semantic Prior
        ↓
UniGoal V2 Long-Term Spatial Goal
        ↓
选择真实可达 Frontier / Anchor Region / Target Viewpoint
        ↓
Local Goal Executor
        ↓
RobotBackend
        ↓
Go2-W 转向 + 短步平移
        ↓
新的 RGB-D Observation
        ↓
更新 Spatial Map / PlaceGraph / Semantic Memory
        ↓
Replan
        ↓
TARGET_FOUND / SEARCH_EXHAUSTED / OPERATOR_STOP
```

最终行为不能再是：

```text
看不到目标
→ 转30°
→ 看
→ 再转30°
→ 原地转圈
```

最终地图不能再是：

```text
每个相机 Bundle 一个节点
→ 节点全部堆在原点
```

必须真正体现：

```text
机器人从哪里出发
→ 去过哪些 Place
→ 哪些方向/区域仍未知
→ 哪些 Frontier 被选过
→ 哪里出现语义 Anchor
→ 目标最终在哪里被确认
```

---

# 1. 不要重新定义项目研究目标

项目的研究核心是：

```text
Language Goal
→ Semantic Understanding
→ SceneGraph / GoalGraph
→ Predictive Scene Graph
→ Spatial Semantic Memory
→ Semantic Frontier Exploration
→ Long-Term Goal Planning
→ Replanning
```

不是：

```text
为 Go2-W 开发完整产品级底盘
重新开发成熟 SLAM
重新造 Nav2
做无人值守产品安全
```

当前 Go2-W 仍然只是：

> **高层具身智能算法实验载体。**

未来正式机器狗会提供：

```text
底层控制
定位
SLAM / Map
避障
导航
越野
传感器标定
```

因此新架构必须保持：

```text
High-Level Exploration Core
          │
          ▼
SpatialProvider
RobotBackend
          │
       platform
```

可替换。

---

# 2. 本计划人工工作边界

仍遵守用户已经明确的原则：

如果某项底层工作满足：

```text
AI 自己能完成
不需要用户搬机器狗
不需要用户摆棋盘/标定板
不要求四周空旷
不要求人工量尺寸
不要求人工执行专门轨迹
```

则允许实现。

例如：

```text
RGB-D 数据同步
RGB-D Odometry
RTAB-Map
自动 topic / health 检查
自动 camera intrinsic 获取
自动 depth scale 获取
自动地面平面估计
自动 RGB-D trajectory quality 评估
正常搜索过程中的 opportunistic extrinsic estimation
正常搜索过程中的 motion correction
```

如果某项工作必须用户：

```text
拿尺测 D435 外参
摆棋盘
摆特殊标靶
专门做标定轨迹
```

则：

```text
不得成为本计划 E2E 的硬 blocker
```

应提供：

```text
nominal / relative / degraded
```

模式继续工作。

---

# 3. 当前 D435 部署事实：直接复用

当前用户已经完成：

```text
Go2-W Jetson:
Ubuntu 20.04
aarch64
Python 3.8
pyrealsense2 2.55.1

D435:
640x480 @ 30fps
color
aligned depth
depth valid fraction ~93.4%
```

当前服务：

```text
/home/unitree/realsense_stream.py
systemd:
realsense-stream.service
```

HTTP 已有：

```text
/color
/depth
/depth_raw
/info.json
/health
/snapshot
/snap/
```

当前文件副本：

```text
outputs/realsense_d435/realsense_stream.py
outputs/realsense_d435/view_realsense.py
outputs/realsense_d435/README.md
```

执行 AI：

> **不要推倒该服务，不要把 D435 驱动重新迁回工作站。**

Go2-W Jetson 继续负责：

```text
RealSense device access
RGB-D alignment
streaming
health/reconnect
```

工作站负责：

```text
Perception
Spatial Mapping
UniGoal
PSG
Planner
WebUI
RobotBackend
```

---

# 4. 当前代码的关键缺口：必须先理解再修改

当前 `app/navigation/models.py` 的 `LiveObservation` 主要包含：

```text
bundle_id
timestamp
image_ref
detections
scene_graph
scene_objects
scene_relations
target_match
pose
heading_sector
sensor_health
```

当前没有正式：

```text
depth_ref
rgbd_frame_id
intrinsics
depth_scale
camera_xyz
map_xyz
spatial_quality
```

因此当前搜索本质仍是：

> **2D observation。**

---

# 5. 当前 ExplorationGoal 的问题

当前 high-level goal 包含：

```text
REOBSERVE
ROTATE_VIEW
RELATIVE_MOVE
NAVIGATE_POSE
INSPECT_ANCHOR
REVISIT_NODE
STOP
```

其中当前 Go2-W 真机路径主要实际使用：

```text
ROTATE_VIEW
INSPECT_ANCHOR
RELATIVE_MOVE
```

但是目前：

```text
ROTATE
```

与：

```text
FORWARD
```

被放在同一个探索候选池里竞争。

这会导致：

```text
“去哪里探索”
```

和：

```text
“怎么执行”
```

混为一层。

本计划必须拆开。

---

# 6. 当前 CandidateGoalGenerator 的问题

当前 live candidate 主要来自：

```text
UniGoal directive
未访问 heading sector
graph node
semantic anchor
last known
fallback
```

relative 模式下未访问 heading sector 会大量产生：

```text
ROTATE_VIEW
```

而 short forward：

```text
RELATIVE_MOVE 0.20m
```

只是另一个普通候选。

候选在进入最终 Planner 之前还会按：

```text
semantic_relevance + expected_information_gain
```

预排序并截断。

因此：

> **空间换位置本身没有成为一级规划目标。**

必须改。

---

# 7. 当前 UniGoal V1 的问题

当前 `app/reasoning/unigoal/search_reasoner.py` 的定位本质仍然是：

```text
next-view policy
```

主要行为：

```text
ZERO
→ explore unseen heading

PARTIAL
→ inspect anchor heading

STRONG
→ reobserve sector
```

也就是说它回答：

> “下一眼朝哪看？”

而不是：

> “下一个空间观察地点去哪？”

本计划要升级为：

# UniGoal V2 Spatial Exploration

---

# 8. 原版 UniGoal 应该借鉴什么

参考 `bagh2178/UniGoal`，应学习以下核心架构：

```text
RGB-D
↓
BEV Map
+
3D SceneGraph
↓
Graph Matching
↓
Long-Term Semantic Goal
↓
get_goal()
↓
Free/Unknown Frontier
↓
可达 Spatial Goal
↓
Local Navigation
```

原版主循环的关键思想是：

```text
BEV_map.mapping(rgbd)
graph.update_scenegraph()
graph.set_full_map(...)
graph.set_full_pose(...)
goal = graph.explore()
agent.step(goal)
```

它不是让 `Graph` 直接输出：

```text
turn_left
```

---

# 9. 原版 UniGoal 应该借鉴的三阶段逻辑

原版 `Graph.explore()` 大致根据 SceneGraph 与 GoalGraph overlap：

```text
较低/其他匹配
→ explore_subgraph

部分匹配
→ explore_remaining

高匹配特殊情况
→ reasonableness_correction
```

然后：

```text
goal = get_goal(goal)
```

把语义目标转换成真正 frontier goal。

本项目不要求机械复制 overlap 阈值和实现。

应保留现有：

```text
ZERO
PARTIAL
STRONG
```

语义，但升级为空间策略：

```text
ZERO
→ EXPLORE_FRONTIER

PARTIAL
→ INSPECT_ANCHOR_REGION

STRONG
→ APPROACH / VERIFY TARGET
```

---

# 10. 不要机械复制原版 UniGoal 的哪些东西

禁止因为参考 UniGoal 就直接搬：

```text
Habitat 环境
UniGoal_Agent
原版 FMMPlanner 整套控制
原版 simulator pose
原版 dataset glue
原版完整 BEV_Map
```

本项目已经有自己的：

```text
Go2-W
RobotBackend
WebUI
Perception
SceneGraph
GoalGraph
PSG
SearchSession
```

要借鉴：

> **空间规划层级和 Frontier 语义融合。**

不是复制整仓库。

---

# 11. 新总架构

最终收敛为：

```text
                         Natural Language Target
                                  │
                                  ▼
                             GoalGraph
                                  │
                                  │
D435 RGB ───────────────► Observed SceneGraph
D435 Depth ─────────────► Depth Object Localizer
                                  │
                                  ▼
                         3D Semantic SceneGraph
                                  │
                                  │
RGB-D Pose ─────┐                 │
                ▼                 │
           Spatial Map            │
      Free / Unknown / Occupied   │
                │                 │
                ▼                 │
          FrontierExtractor       │
                │                 │
                └──────┬──────────┘
                       ▼
               Graph Match State
              ZERO/PARTIAL/STRONG
                       │
                 ┌─────┴─────┐
                 ▼           ▼
                PSG      Negative Memory
                 │           │
                 └─────┬─────┘
                       ▼
             LongTermGoalSelector
                       │
                       ▼
              Spatial Exploration Goal
                       │
                       ▼
               LocalGoalExecutor
                       │
                       ▼
                  RobotBackend
                       │
                       ▼
                     Robot
                       │
                       └─────► next RGB-D observation
```

---

# 12. Phase 1：给 D435 服务增加“原子 RGB-D Frame”

这是第一优先级。

当前：

```text
/color
/depth_raw
```

是独立 HTTP 请求。

用于人看没问题，但算法端存在：

```text
RGB frame N
Depth frame N+1
```

的潜在时序错配。

本计划要求机器狗侧服务增加：

```text
atomic RGB-D frame
```

---

# 13. 推荐 API

增加：

```text
GET /rgbd/latest.json
```

返回：

```json
{
  "frame_id": 12345,
  "device_timestamp_ms": 123456.7,
  "host_timestamp": 1787000000.0,

  "color_url": "/rgbd/frame/12345/color.jpg",
  "depth_url": "/rgbd/frame/12345/depth.png",

  "depth_aligned_to_color": true,
  "depth_unit_m": 0.001,

  "width": 640,
  "height": 480,

  "intrinsics": {
    "fx": 0,
    "fy": 0,
    "cx": 0,
    "cy": 0
  }
}
```

然后：

```text
/rgbd/frame/<id>/color.jpg
/rgbd/frame/<id>/depth.png
```

必须永远来自同一个 RealSense `frameset`。

---

# 14. 服务端缓存

至少缓存最近：

```text
8~32
```

个 frame。

使用：

```text
frame_id
```

原子更新。

不要让 URL 读取期间缓存被覆盖。

---

# 15. Depth 格式

必须保留：

```text
16-bit raw depth
```

不要把 jet colormap 当算法输入。

算法 depth 单位统一成：

```text
meters
```

服务 metadata 明确：

```text
depth_unit_m
```

---

# 16. 时间戳

必须保存两个时间：

```text
device timestamp
host timestamp
```

当前机器狗 RTC/NTP 不可靠。

因此不能悄悄把 wall clock 当绝对可信。

第一版 RGB-D 内部同步只依赖：

```text
same frameset
```

长期时间融合另做质量等级。

---

# 17. D435Source

工作站新增统一数据源：

```python
class RGBDSource:
    def get_latest(self) -> RGBDFrame:
        ...
```

实现：

```text
RealSenseHTTPRGBDSource
```

---

# 18. RGBDFrame 数据结构

至少：

```python
@dataclass
class RGBDFrame:
    frame_id: str
    timestamp: float

    color_ref: str
    depth_ref: str

    width: int
    height: int

    fx: float
    fy: float
    cx: float
    cy: float

    depth_unit_m: float
    depth_aligned_to_color: bool

    device_timestamp_ms: float | None
    host_timestamp: float | None

    health: dict
    provenance: dict
```

---

# 19. RGB-D Bridge

工作站建议增加 ROS2 bridge：

```text
/go2w/d435/color/image_raw
/go2w/d435/depth/image_rect_raw
/go2w/d435/color/camera_info
/go2w/d435/rgbd_health
```

使用：

```text
ROS2 Humble /usr/bin/python3
```

如果应用环境与 ROS Python 冲突，保持项目已有双 Python 隔离。

---

# 20. Bridge 不是硬耦合

应用 Perception 层应允许：

```text
HTTP direct
```

或：

```text
ROS2 bridge
```

两种输入。

推荐生产实验路径：

```text
HTTP D435 service
→ workstation ROS bridge
→ RTAB-Map / spatial
```

视觉 LLM 仍可直接读 FrameBundle 文件。

---

# 21. 旧相机处理

D435 正式成为：

```text
primary_rgb_camera
```

旧 Go2-W RGB：

```text
fallback_rgb_camera
diagnostic
```

不要删除旧 camera bridge。

配置：

```yaml
perception:
  primary_camera: d435
  fallback_camera: go2w_builtin
```

---

# 22. FrameBundle V2

当前 FrameBundle 必须升级为：

```text
RGBD FrameBundle
```

至少记录：

```json
{
  "bundle_id": "...",

  "rgb": {
    "source": "d435",
    "image_ref": "..."
  },

  "depth": {
    "depth_ref": "...",
    "depth_unit_m": 0.001,
    "aligned_to_rgb": true
  },

  "camera_info": {
    "fx": 0,
    "fy": 0,
    "cx": 0,
    "cy": 0
  },

  "rgbd_frame_id": "...",

  "spatial": {
    "pose_quality": "...",
    "map_available": false
  }
}
```

---

# 23. 兼容旧 Bundle

旧录像/replay 不能全部坏掉。

如果 bundle 没有 depth：

```text
spatial_quality = RGB_ONLY
```

并保留旧 2D 路径。

---

# 24. Phase 2：Depth Object Localization

这是 D435 加入后最先能带来真实收益的能力。

新增：

```text
DepthObjectLocalizer
```

输入：

```text
bbox / mask
aligned depth
camera intrinsics
```

输出：

```text
depth_m
camera_xyz
bearing_deg
elevation_deg
spatial_confidence
```

---

# 25. 深度取样规则

禁止简单：

```text
bbox center pixel depth
```

推荐：

### 有 segmentation mask

```text
mask 内有效 depth
```

### 只有 bbox

使用：

```text
bbox 中心区域 40~60%
```

然后：

```text
filter zero
filter invalid
filter outside sensor range
MAD / percentile outlier reject
median depth
```

---

# 26. ObjectSpatialObservation

新增：

```python
@dataclass
class ObjectSpatialObservation:
    object_id: str | None
    label: str

    bbox: list[float] | None

    depth_m: float | None
    camera_xyz: tuple[float, float, float] | None

    bearing_deg: float | None
    elevation_deg: float | None

    map_xyz: tuple[float, float, float] | None

    spatial_quality: str
    confidence: float

    provenance: dict
```

---

# 27. Spatial Quality

统一：

```text
RGB_ONLY
CAMERA_LOCAL
RELATIVE_RGBD
METRIC_RGBD
```

定义：

### RGB_ONLY

只有旧 RGB。

### CAMERA_LOCAL

D435 深度有效：

```text
range/bearing/camera xyz
```

但无全局 pose。

### RELATIVE_RGBD

RGB-D odom / relative map 可用。

### METRIC_RGBD

稳定 map pose / occupancy 可用。

---

# 28. 立即淘汰粗 heading heuristic

当前类似：

```text
left → 固定角度
right → 固定角度
```

只能作为 RGB_ONLY fallback。

D435 下：

```text
bearing_deg
```

必须来自真实像素+内参+depth / ray geometry。

Anchor inspect / target approach 优先使用：

```text
bearing_deg
```

---

# 29. Phase 3：3D Semantic SceneGraph

Observed SceneGraph 仍然是：

> **事实图。**

不要混入 PSG 预测。

Object node 增加：

```text
camera_xyz
map_xyz
depth_m
bearing_deg
spatial_quality
observation_count
```

---

# 30. 3D Object 跨帧合并

如果空间质量为：

```text
RELATIVE_RGBD / METRIC_RGBD
```

允许按：

```text
label semantic similarity
+
3D spatial distance
+
appearance/tracking
```

跨帧 merge。

如果只有 CAMERA_LOCAL：

不要把不同机器人位置的 camera XYZ 直接当全局位置合并。

---

# 31. Semantic Object Map

新增独立：

```text
SemanticObjectMap
```

职责：

```text
Observed Object
Position
Relations
Seen from which Place
Confidence
Last seen
Negative evidence
```

不要让 `PlaceGraph` 自己承担所有 object graph 职责。

---

# 32. Phase 4：RGB-D Pose / Mapping

这是空间探索的基础。

本计划第一选择：

# RTAB-Map ROS2

工作站 Humble 上使用：

```text
rgbd_odometry
rtabmap_slam
```

消费：

```text
D435 RGB
D435 depth
CameraInfo
```

---

# 33. RTAB-Map 集成目标

需要至少获得：

```text
RGB-D odometry pose
robot/camera trajectory
occupancy/explored map
```

第一版不要求：

```text
完美 loop closure
导航级产品地图
```

只要足够支持：

```text
relative spatial exploration
frontier
Web map
```

即可。

---

# 34. SpatialProvider

不要让 UniGoal import RTAB-Map topic。

统一接口：

```python
class SpatialProvider(Protocol):

    def quality(self) -> SpatialQuality:
        ...

    def get_pose(self) -> SpatialPose | None:
        ...

    def get_map(self) -> SpatialMapSnapshot | None:
        ...

    def get_frontiers(self) -> list[FrontierCandidate]:
        ...

    def camera_point_to_spatial(
        self,
        xyz_camera,
    ):
        ...
```

---

# 35. SpatialProvider 实现

至少：

```text
CameraLocalSpatialProvider
RGBDRTABMapSpatialProvider
MockSpatialProvider
```

未来：

```text
ProductionRobotSpatialProvider
```

---

# 36. RTAB-Map 失败时的降级

RTAB-Map 不得成为整个搜索服务启动的唯一条件。

如果：

```text
rgbd odom LOST
```

则：

```text
METRIC/RELATIVE map mode → degraded
```

系统退到：

```text
CAMERA_LOCAL + RelativeFrontierProvider
```

继续实验。

---

# 37. Lightweight Depth BEV fallback

如果 RTAB-Map 实测在当前环境完全不可用，允许实现：

```text
LightweightDepthBEVMapper
```

输入：

```text
depth
relative pose/action integration
```

输出：

```text
local free
unknown
occupied
```

只用于实验空间 frontier。

不要宣称 SLAM。

---

# 38. Phase 5：真正 Frontier

这一步是解决“原地转圈”的核心。

以后：

```text
heading sector
```

不能再叫空间 frontier。

它只表示：

```text
Local View Coverage
```

真正 Frontier 来自：

```text
Free ↔ Unknown boundary
```

---

# 39. FrontierCandidate

定义：

```python
@dataclass
class FrontierCandidate:
    frontier_id: str

    position: tuple[float, float] | None
    frame: str

    bearing_deg: float | None
    distance_m: float | None

    size_score: float
    spatial_information_gain: float
    reachable: bool

    nearby_semantics: list[str]

    provenance: dict
```

---

# 40. MetricFrontierProvider

当有：

```text
Spatial Map
```

时：

1. 找 free/unknown 边界；
2. 连通域；
3. 过滤很小 frontier；
4. 过滤不可达；
5. 计算距离；
6. 输出候选。

不要直接复制原版 UniGoal FMM 代码。

可使用当前 map / path provider。

---

# 41. RelativeFrontierProvider

如果 map 不稳定，仍必须能移动。

输出：

```text
front-left
front
front-right
```

但注意这些表示：

> **新的空间观察地点**

不是：

> 只转头看方向。

结构：

```text
bearing
relocate_distance
```

例如：

```text
-30°, 0.25m
0°,   0.25m
+30°, 0.25m
```

---

# 42. Visual Semantic Frontier Hint

视觉还可以提取：

```text
doorway
corridor
open passage
room entrance
open area
```

作为：

```text
frontier semantic prior
```

不能直接变成 motion command。

---

# 43. Phase 6：PlaceGraph

当前每 Bundle 变成 node 的模式必须结束。

重新定义：

```text
Place
Observation
MovementEdge
```

---

# 44. PlaceNode

```python
@dataclass
class PlaceNode:
    place_id: str

    pose: SpatialPose | None
    pose_quality: str

    observation_ids: list[str]

    heading_coverage: dict

    observed_object_ids: list[str]

    semantic_interest: float

    visit_count: int
    negative_evidence: int

    target_candidate: bool
    target_confirmed: bool

    provenance: dict
```

---

# 45. Observation

每一次 Bundle：

```text
Observation
```

不是 map node。

记录：

```text
bundle
heading
objects
relations
RGBD frame
```

---

# 46. 什么时候创建新 Place

优先规则：

### 有 RGB-D pose

如果：

```text
距离最近 Place > place_merge_distance
```

创建新 Place。

推荐初始：

```text
0.20~0.35m
```

做成配置。

### 只有 action relative pose

成功平移观测距离：

```text
>= relocation_min_displacement
```

才创建新 Place。

---

# 47. 原地转动

机器人：

```text
0°
30°
60°
90°
```

地图：

```text
Place count = 1
Observation count = 4
```

只更新：

```text
Place.heading_coverage
```

---

# 48. 平移

机器人：

```text
forward 0.25m
```

观测到足够位移：

```text
new Place
```

创建：

```text
MovementEdge
```

---

# 49. MovementEdge

记录：

```text
from
to
requested goal
executed local actions
observed displacement
observed yaw
navigation result
```

---

# 50. Heading Coverage 改为 Place-local

禁止：

```text
session-global observed sectors
```

必须：

```text
Place A:
  sectors {0,1,2}

Place B:
  sectors {0}
```

走到新 Place 后所有方向重新具有观察价值。

---

# 51. Phase 7：信息增益重新定义

拆：

```text
view_coverage_gain
semantic_information_gain
spatial_information_gain
```

---

# 52. View Coverage Gain

例如：

```text
第一次看新的 heading sector
```

只说明：

```text
当前 Place 视野覆盖增加
```

不能无限 reset 全局 exploration stagnation。

---

# 53. Semantic Information Gain

包括：

```text
新 object
新 relation
新 anchor
target score 提升
PSG hypothesis 得到验证/反证
```

---

# 54. Spatial Information Gain

包括：

```text
新 Place
新 free area
新 frontier
frontier 被消耗
```

---

# 55. Stagnation

全局 stagnation 主要看：

```text
semantic gain
+
spatial gain
```

不是：

```text
新 heading
```

这样原地转一圈不会被系统误认为“探索很成功”。

---

# 56. Phase 8：Local Scan 与 Relocate 分层

Explorer 增加：

```text
LOCAL_SCAN
SELECT_LONG_TERM_GOAL
LOCAL_EXECUTE
```

而不是所有动作放一起打分。

---

# 57. LOCAL_SCAN

目的：

> 当前 Place 获取足够视觉证据。

允许：

```text
ROTATE_VIEW
REOBSERVE
```

限制：

```text
max_local_rotations
max_local_heading_sectors
max_local_scan_seconds
```

建议默认：

```yaml
local_scan:
  max_rotations: 3
  max_heading_sectors: 4
```

---

# 58. LOCAL_SCAN 饱和

满足任一：

```text
达到 rotation quota
没有 semantic gain
当前 relevant headings 已检查
```

则：

```text
不得继续普通 heading rotation
```

进入：

```text
SELECT_LONG_TERM_GOAL
```

---

# 59. SELECT_LONG_TERM_GOAL

候选不再是：

```text
左转
右转
前进
```

而是：

```text
Frontier
Anchor Region
Target Viewpoint
Revisit Place
```

---

# 60. LOCAL_EXECUTE

Long-Term Goal 确定后：

当前 Go2-W：

```text
计算目标 bearing
→ ROTATE
→ short FORWARD
→ STOP
→ reobserve
```

未来成熟机器人：

```text
NAVIGATE_POSE
```

---

# 61. RELATIVE_MOVE 降级

当前：

```text
RELATIVE_MOVE
```

不再作为一级探索 Candidate。

它变成：

> **LocalGoalExecutor primitive**

同理：

```text
ROTATE_VIEW
```

主要也是局部执行 primitive / local scan。

---

# 62. 新高层 Intent

增加：

```text
EXPLORE_FRONTIER
INSPECT_ANCHOR_REGION
APPROACH_TARGET
VERIFY_TARGET
REVISIT_PLACE
```

---

# 63. SpatialExplorationIntent

建议：

```python
@dataclass
class ExplorationIntent:
    intent_id: str
    intent_type: str

    target_frontier_id: str | None
    target_place_id: str | None
    target_region: dict | None
    target_object_id: str | None

    preferred_position: tuple[float,float] | None
    preferred_bearing_deg: float | None

    semantic_reason: str

    semantic_score: float
    psg_score: float
    spatial_gain: float
    travel_cost: float

    provenance: dict
```

---

# 64. Phase 9：UniGoal V2 Spatial Reasoner

保留现有：

```text
GoalGraphBuilder
GraphMatcher
SemanticMemory
router
```

新增：

```text
SpatialSearchReasoner
```

或升级现有 reasoner，保持 legacy next-view fallback。

---

# 65. ZERO Match

当前不再：

```text
持续找 unseen heading
```

流程：

```text
LOCAL_SCAN bounded
↓
仍 ZERO
↓
获取 Frontiers
↓
PSG semantic prior
↓
LongTermGoalSelector
↓
EXPLORE_FRONTIER
```

---

# 66. PARTIAL Match

例如：

```text
water dispenser found
trash bin missing
```

流程：

```text
Anchor 3D localization
↓
允许 1~2 次 local inspect
↓
仍 missing
↓
生成 AnchorSearchRegion
↓
从 region 周围找 Viewpoint/Frontier
↓
PSG + spatial ranking
↓
INSPECT_ANCHOR_REGION
```

---

# 67. AnchorSearchRegion

例如：

```python
@dataclass
class SemanticRegion:
    region_id: str
    anchor_object_id: str

    relation: str

    center: tuple[float,float] | None

    radius_min_m: float | None
    radius_max_m: float | None

    bearing_range_deg: tuple[float,float] | None

    confidence: float

    metric_claim: bool
    source: str
```

---

# 68. 不伪造语言关系精确坐标

`near` 只能变成：

```text
搜索区域
```

不能变成：

```text
目标必在 anchor 右侧 0.82m
```

除非有真实观测证据。

---

# 69. STRONG Match

流程：

```text
TARGET_HYPOTHESIS
↓
真实 RGB-D range/bearing
↓
APPROACH / REOBSERVE
↓
VERIFY
```

如果 verify fail：

```text
blacklist hypothesis
↓
lower PSG / region confidence
↓
replan
```

---

# 70. Phase 10：PSG 正式升级

当前 PSG 从：

```text
heading auxiliary hint
```

升级为：

# Semantic Prior Provider

---

# 71. PSG 的正确职责

PSG 回答：

> **哪些语义区域/Frontier 更值得探索？**

PSG 不回答：

```text
TURN_RIGHT
FORWARD_0.3
```

---

# 72. PSG 接口

```python
class SemanticPriorProvider(Protocol):

    def predict(
        self,
        goal_graph,
        observed_scene_graph,
        spatial_context,
        semantic_memory,
    ) -> SemanticPrior:
        ...
```

---

# 73. SemanticPrior

```python
@dataclass
class SemanticPrior:
    predicted_nodes: list
    predicted_relations: list

    anchor_hypotheses: list
    region_hypotheses: list

    frontier_scores: dict[str, float]

    confidence: float

    provenance: dict
```

---

# 74. ObservedGraph 与 PSG 严格分离

必须：

```text
ObservedSceneGraph
```

只放事实。

```text
PredictedSceneGraph
```

只放预测。

禁止：

```text
PSG 预测的 water dispenser
```

被写成：

```text
observed object
```

---

# 75. PSG Node 状态

可定义：

```text
PREDICTED
OBSERVED
SPATIALLY_ANCHORED
REJECTED
```

---

# 76. 空间绑定

例如 PSG：

```text
trash bin near water dispenser
```

真实观察到：

```text
water dispenser @ spatial position
```

则：

```text
PSG hypothesis
+
real anchor
↓
SemanticRegion
```

这就是 PSG 最核心的新作用。

---

# 77. PSG Frontier Score

Planner 正式增加：

```text
psg_semantic_prior
```

例如：

```text
FrontierScore =
    spatial_information_gain
  + goal_graph_relevance
  + psg_semantic_prior
  + novelty
  + anchor_region_affinity
  - travel_cost
  - visited_penalty
  - negative_evidence
  - blacklist_penalty
```

所有权重写配置。

---

# 78. PSG 动态权重

建议：

```text
ZERO
→ HIGH

PARTIAL
→ MEDIUM/HIGH

STRONG
→ LOW

VERIFY
→ ZERO
```

原则：

> 越缺真实证据越依赖预测；越接近确认越依赖传感器。

---

# 79. PSG Negative Evidence

每个 hypothesis 保存：

```text
searched_places
searched_viewpoints
negative_count
confidence
```

如果某 predicted region 已从多个 viewpoint 被观察：

```text
target absent
```

则降低 confidence。

低于阈值：

```text
BLACKLIST / REJECTED
```

---

# 80. PSG 不能覆盖真实负证据

如果：

```text
PSG = high
```

但：

```text
多个高可见性 viewpoint
+
真实 SceneGraph 都否定
```

必须信真实观测。

---

# 81. Phase 11：LongTermGoalSelector

新增真正的：

```text
LongTermGoalSelector
```

输入：

```text
match state
frontiers
PlaceGraph
SemanticObjectMap
PSG prior
negative memory
```

输出：

```text
ExplorationIntent
```

---

# 82. Candidate 类型

### ZERO

```text
EXPLORE_FRONTIER
```

### PARTIAL

```text
INSPECT_ANCHOR_REGION
EXPLORE_FRONTIER
```

### STRONG

```text
APPROACH_TARGET
VERIFY_TARGET
```

### recovery

```text
REVISIT_PLACE
EXPLORE_FRONTIER
```

---

# 83. CandidateGenerator 不再提前粗暴截断不同类别

禁止：

```text
12个 rotate candidates
→ 把 spatial candidates 全挤掉
```

使用：

```text
candidate quotas
```

或按类别后统一 Planner 排序。

---

# 84. LongTermGoal Score 可解释性

每个候选输出：

```text
spatial_gain
goal_graph_relevance
psg_prior
anchor_affinity
novelty
distance
visited_penalty
negative_penalty
blacklist
total
```

WebUI 可直接显示。

---

# 85. Phase 12：LocalGoalExecutor

职责：

> 把 Long-Term Goal 翻译成当前平台动作。

---

# 86. 当前 Go2-W 实现

`EXPLORE_FRONTIER`：

```text
frontier bearing
↓
rotate toward bearing
↓
forward 0.20~0.30m
↓
stop
↓
check observed displacement
↓
return
```

---

# 87. Anchor Region

```text
select viewpoint
↓
orient
↓
short move
↓
reobserve
```

---

# 88. Approach Target

D435 提供：

```text
target range
bearing
```

当前 Go2-W：

```text
rotate to target
↓
if range > desired
short forward
↓
reobserve
```

---

# 89. Future Robot

同一个 intent：

```text
EXPLORE_FRONTIER
```

直接：

```text
NAVIGATE_POSE
```

---

# 90. Web forward plumbing

必须确保：

```text
WebUI
→ SearchStartRequest
→ SearchSessionService
→ autonomous_search_worker
→ run_semantic_exploration
→ semantic forward enabled
```

规则简化成：

```text
enable_autonomous_motion=false
→ 无运动

enable_autonomous_motion=true
turn_only=true
→ rotation only

enable_autonomous_motion=true
turn_only=false
→ rotation + short forward
```

不要再让一个隐藏 `semantic_allow_forward` 造成 Web 能启动但永远不前进。

---

# 91. Phase 13：WebUI 地图重构

当前主地图不再显示 ObservationNode。

主地图显示：

```text
Spatial Map
PlaceGraph
Frontiers
Semantic Objects
Selected Long-Term Goal
```

---

# 92. WebUI Layer 1：Occupancy / Explored

如果 SpatialProvider 有 map：

背景显示：

```text
unknown
free
occupied
```

---

# 93. Layer 2：PlaceGraph

显示：

```text
P0 → P1 → P2
```

只有真实空间 relocation 才增加 Place。

---

# 94. Layer 3：Frontiers

显示：

```text
F1
F2
F3
```

并突出：

```text
selected frontier
```

---

# 95. Layer 4：Semantic Objects

至少显示：

```text
anchor
target candidate
target confirmed
```

例如：

```text
water dispenser
blue trash bin
```

---

# 96. Layer 5：PSG Region

Debug 模式可以半透明显示：

```text
PSG predicted semantic region
```

必须明确标：

```text
PREDICTED
```

不能和真实 object 混淆。

---

# 97. Current Place 面板

显示：

```text
Place ID
pose quality
observations
local heading coverage
objects
semantic gain
```

---

# 98. Planner 面板

显示：

```text
Match State
ZERO/PARTIAL/STRONG

Current Intent
EXPLORE_FRONTIER

Selected
F2

Reason

Scores
spatial
goal graph
PSG
novelty
cost
```

---

# 99. RGB-D 面板

WebUI 主相机改为：

```text
D435 RGB
```

可切：

```text
RGB
Depth
RGB+Depth
Last analyzed RGB-D
```

---

# 100. Object Card

例如：

```text
water dispenser

confidence 0.91
depth 2.13m
bearing +16.4°
spatial quality RELATIVE_RGBD
```

---

# 101. 地图质量标签

必须显示：

```text
CAMERA_LOCAL
RELATIVE_RGBD
METRIC_RGBD
```

如果只是相对地图：

明确：

```text
Relative RGB-D Exploration Map
```

不要写成：

```text
Validated metric map
```

---

# 102. SearchEvent 扩展

新增事件：

```text
RGBD_FRAME_UPDATED
SPATIAL_POSE_UPDATED
SPATIAL_MAP_UPDATED
FRONTIERS_UPDATED
PLACE_CREATED
PLACE_UPDATED
SEMANTIC_OBJECT_LOCALIZED
PSG_PRIOR_UPDATED
SEMANTIC_REGION_CREATED
LONG_TERM_GOAL_SELECTED
LOCAL_GOAL_PROGRESS
```

---

# 103. Event payload 必须 JSON-safe

Depth image 不走 WebSocket。

WebSocket 只传：

```text
metadata
object 3D
frontier
map revision
```

---

# 104. Map REST

建议：

```text
GET /api/search/spatial-map
GET /api/search/place-graph
GET /api/search/frontiers
GET /api/search/semantic-map
```

或统一：

```text
/api/search/map
```

返回分层 schema。

---

# 105. Phase 14：Spatial Memory

负证据粒度升级。

从：

```text
heading sector negative
```

升级：

```text
Place
Frontier
SemanticRegion
PSG Hypothesis
Object hypothesis
```

---

# 106. Frontier Memory

记录：

```text
visit_count
selected_count
fail_count
semantic_gain_after_visit
blacklisted
```

---

# 107. Place Negative Evidence

例如：

```text
Place P3
target not observed from sectors 1/2/3
```

影响 revisiting。

---

# 108. SemanticRegion Negative Evidence

例如：

```text
water_dispenser_05_near_region
```

已访问：

```text
V1 V2 V3
```

仍无目标：

```text
confidence down
```

---

# 109. Phase 15：自动 D435 安装参数估计

不作为 P0。

但允许机会式实现。

---

# 110. Camera mount nominal

配置：

```yaml
d435_mount:
  yaw_deg: 0
  status: nominal
```

禁止写：

```text
validated
```

除非有证据。

---

# 111. 自动 Ground Plane

利用 depth：

```text
RANSAC floor
```

估：

```text
camera height
roll
pitch
```

正常环境有地面时自动运行。

不可观测：

```text
GROUND_PLANE_NOT_OBSERVABLE
```

不阻塞。

---

# 112. Opportunistic SE(2) Extrinsic

正常探索积累：

```text
RGB-D trajectory delta
Robot odom/action delta
```

自动估计：

```text
camera ↔ base x/y/yaw
```

如果运动激励不足：

```text
confidence LOW
```

继续 nominal。

---

# 113. Phase 16：Search State Machine

最终状态建议：

```text
BOOTSTRAP
OBSERVE_RGBD
UPDATE_SPATIAL
UPDATE_SEMANTIC
GRAPH_MATCH
LOCAL_SCAN
SELECT_LONG_TERM_GOAL
LOCAL_EXECUTE
REOBSERVE
VERIFY
RECOVER
TARGET_FOUND
SEARCH_EXHAUSTED
PAUSED
OPERATOR_STOP
FAILED
```

---

# 114. 主循环伪代码

```python
while budget.remaining():

    rgbd = rgbd_source.get_latest()

    observation = perception.observe(rgbd)

    spatial.update(rgbd)

    objects_3d = localizer.localize(
        observation.scene_objects,
        rgbd
    )

    semantic_map.update(objects_3d, spatial.pose())

    match = graph_matcher.match(
        goal_graph,
        semantic_map.observed_scene_graph()
    )

    if match.has_target_candidate:
        if verifier.verify(...):
            backend.stop()
            return TARGET_FOUND

    if local_scan.needs_more_view(match):
        local_goal = local_scan.next_view(...)
        local_executor.execute(local_goal)
        continue

    frontiers = spatial.get_frontiers()

    psg_prior = psg.predict(
        goal_graph,
        observed_scene_graph,
        spatial_context,
        semantic_memory
    )

    intent = long_term_goal_selector.select(
        match=match,
        frontiers=frontiers,
        psg_prior=psg_prior,
        memory=memory,
    )

    result = local_executor.execute(intent)

    memory.update(intent, result)
```

---

# 115. Phase 17：配置

新增：

```text
configs/go2w/rgbd_spatial_exploration.yaml
```

建议：

```yaml
camera:
  primary: d435
  d435_base_url: http://192.168.123.18:8080
  require_atomic_rgbd: true

depth:
  min_m: 0.15
  max_m: 8.0
  bbox_inner_ratio: 0.5

spatial:
  preferred_provider: rtabmap
  fallback_provider: camera_local

place_graph:
  merge_distance_m: 0.25
  relocation_min_displacement_m: 0.10

local_scan:
  max_rotations: 3
  max_heading_sectors: 4

frontier:
  min_component_size: 10
  relative_step_m: 0.25

psg:
  enabled: true
  zero_match_weight: 1.0
  partial_match_weight: 0.7
  strong_match_weight: 0.2
  verify_weight: 0.0

exploration:
  max_search_seconds: 600
  max_planning_cycles: 100
  max_relocations: 50

backend:
  type: go2w_experimental
```

具体参数必须根据现有配置体系融合，不机械重复。

---

# 116. Phase 18：保留旧路径

必须保留：

```text
RGB-only
UniGoal V1 next-view
legacy search
manual WebUI
old replay
```

配置：

```text
search_mode:
  legacy
  unigoal_v1
  unigoal_v2_spatial
```

默认是否切 V2 由完成验收后决定。

---

# 117. 不删除旧 RGB-LiDAR 软件

旧：

```text
RGB-LiDAR overlay
fusion
```

保留：

```text
diagnostic
fallback research
```

但 D435 路径优先。

---

# 118. Phase 19：测试体系

必须先做纯软件测试。

---

# 119. RGB-D Source Tests

测试：

```text
atomic frame consistency
color/depth same frame id
intrinsics
16bit depth
depth unit
stale
camera reconnect
invalid frame
```

---

# 120. Depth Localizer Tests

合成：

```text
constant depth box
mixed wall/object box
invalid zero depth
outliers
mask
```

验证：

```text
depth median
bearing
camera xyz
confidence
```

---

# 121. 3D SceneGraph Tests

验证：

```text
RGB_ONLY fallback
CAMERA_LOCAL
RELATIVE pose transform
multi-frame merge
no false global merge
```

---

# 122. SpatialProvider Mock Tests

场景：

```text
small room
corridor
two frontiers
dead end
map unavailable
odom lost
```

---

# 123. Frontier Tests

验证：

```text
free/unknown boundary
small frontier removal
unreachable filter
distance
stable IDs
```

---

# 124. PlaceGraph Tests

必须：

### 原地旋转

```text
10 observations
1 Place
```

### 平移

```text
3 successful relocations
4 Places
3 edges
```

### Place-local heading

新 Place：

```text
heading coverage reset
```

---

# 125. Information Gain Tests

新 heading：

```text
view gain > 0
semantic gain = 0
spatial gain = 0
```

不能 reset global stagnation。

新 object：

```text
semantic gain > 0
```

新 Place：

```text
spatial gain > 0
```

---

# 126. UniGoal V2 ZERO Test

没有 goal related objects：

```text
bounded local scan
→ EXPLORE_FRONTIER
```

不能连续无限 ROTATE。

---

# 127. PARTIAL Test

anchor 发现：

```text
local inspect
→ anchor region
→ relocate viewpoint
```

---

# 128. STRONG Test

target candidate：

```text
approach/reobserve
→ verify
```

PSG 不能直接确认。

---

# 129. PSG Test

验证：

```text
PSG high → frontier ranking changes
```

但：

```text
frontier unreachable
```

不能因为 PSG 高而选择。

---

# 130. PSG Negative Test

重复搜索 predicted region：

```text
confidence decreases
```

最终 blacklist。

---

# 131. Planner Test

保证：

```text
Long-term intent
```

和：

```text
local motion primitive
```

分离。

---

# 132. LocalExecutor Test

Mock backend：

```text
EXPLORE_FRONTIER
→ rotate
→ forward
→ result
```

---

# 133. Web Tests

验证：

```text
RGB-D status
depth panel
frontiers
PlaceGraph
PSG region
long-term goal
map quality
```

---

# 134. Replay Tests

旧 RGB session：

```text
仍可 replay
```

新 RGB-D session：

```text
depth/spatial events
```

可 replay。

---

# 135. Phase 20：真机分阶段验收

不需要人工标定。

操作者拿遥控器监督。

---

# 136. Trial A：D435 原子 RGB-D

真机静止：

至少：

```text
300 frames
```

检查：

```text
frame IDs
depth valid
intrinsics
stale=0
```

---

# 137. Trial B：Depth Object

摆在当前自然环境里已有物体即可。

不要专门标定。

检测：

```text
chair
door
water dispenser
```

输出：

```text
range
bearing
camera xyz
```

跨多帧稳定。

---

# 138. Trial C：RGB-D Odometry

机器狗正常实验运动过程中采集。

不要求特殊轨迹。

判断：

```text
tracking uptime
pose continuity
rotation behavior
translation behavior
```

如果 RTAB-Map quality 不够：

退到 fallback。

---

# 139. Trial D：Map

正常房间搜索。

要求 WebUI：

```text
free / unknown
frontiers
robot trajectory
```

不再是 observation nodes 堆叠。

---

# 140. Trial E：目标不存在

至少：

```text
15~20 planning cycles
```

要求：

```text
>= 3 relocations
>= 4 Places
```

不能：

```text
连续原地转圈耗尽 budget
```

---

# 141. Rotation 限制验收

普通 ZERO search：

不允许：

```text
连续 > 3~4 次纯 rotation
```

除非：

```text
日志明确显示 anchor/target verification
```

---

# 142. Trial F：普通目标

例如：

```text
绿色垃圾桶
```

目标最初在视野外。

要求：

```text
LOCAL_SCAN
→ Frontier
→ Relocate
→ New Place
→ Target
→ Verify
```

---

# 143. Trial G：PSG 语义目标

推荐：

```text
饮水机旁边的蓝色垃圾桶
```

必须看到：

```text
GoalGraph
PSG Prediction
ZERO/PARTIAL
Frontiers
PSG frontier scores
anchor 3D
AnchorSearchRegion
Long-Term Goal
Relocation
Target verify
```

---

# 144. Trial H：PSG 错误预测

让 PSG 预测的一个区域没有目标。

要求：

```text
搜索若干 viewpoint
→ negative evidence
→ PSG hypothesis confidence down
→ blacklist
→ 选择其他 frontier
```

---

# 145. WebUI 最终显示

必须有：

```text
实时 D435 RGB
实时/可切 depth
当前 object + depth + bearing
GoalGraph
Observed SceneGraph
PSG
Match State
Frontiers
Selected Long-Term Goal
Reason
Spatial Map
PlaceGraph
Robot
Anchor
Target
Timeline
```

---

# 146. 真机 PASS 定义

以下同时满足才能宣布：

```text
RGBD_SPATIAL_EXPLORATION = PASS
```

1. D435 成为主 RGB；
2. RGB 与 depth 原子同步；
3. 视觉 object 有 depth/bearing；
4. 至少 CAMERA_LOCAL 可稳定运行；
5. RGB-D pose 或 fallback SpatialProvider 可运行；
6. 真正 Frontier 可产生；
7. heading sector 不再作为空间 frontier；
8. Observation 与 Place 分离；
9. 原地旋转不增加 Place；
10. 平移成功增加 Place；
11. UniGoal ZERO 会选 Frontier；
12. PARTIAL 会产生 Anchor Region；
13. STRONG 进入真实 verify；
14. PSG 可以影响 Frontier ranking；
15. PSG 不能覆盖真实 evidence；
16. PSG negative memory 有效；
17. RELATIVE_MOVE 已降为 local primitive；
18. Planner 选择的是 long-term spatial goal；
19. WebUI 地图有真实空间展开；
20. 目标视野外时机器人会换观察位置；
21. 至少 3 次 relocation；
22. 至少 4 个 Place；
23. 至少一次普通目标 TARGET_FOUND；
24. 至少一次关系目标 TARGET_FOUND；
25. 操作者没有逐步告诉机器人往哪走。

---

# 147. 推荐最终目录变化

以当前仓库实际代码为准，不机械重复已有模块。

建议方向：

```text
app/
  perception/
    rgbd_source.py
    realsense_http_rgbd_source.py
    depth_object_localizer.py

  spatial/
    models.py
    spatial_provider.py
    rtabmap_spatial_provider.py
    camera_local_spatial_provider.py
    frontier_extractor.py
    place_graph.py
    semantic_object_map.py

  reasoning/
    unigoal/
      spatial_reasoner.py
      semantic_prior_provider.py

  navigation/
    models.py
    long_term_goal_selector.py
    local_goal_executor.py
    candidate_goal_generator.py
    exploration_planner.py

  live_robot/
    autonomous_explorer.py
    semantic_observer.py
    search_event.py

  manual_web_demo/
    ...
    static/search_map.js
```

---

# 148. 推荐新增 ROS 部分

```text
scripts/go2w/realsense_rgbd_bridge.py
scripts/go2w/start_rgbd_spatial_stack.sh
scripts/go2w/validate_rgbd_spatial_stack.py
```

如果适合 ROS package：

```text
ros2_ws/src/go2w_rgbd_bridge/
```

优先按当前仓库 ROS 组织决定。

---

# 149. RTAB-Map 安装规则

执行 AI 应先检查：

```text
ros-humble-rtabmap-ros
```

是否已安装。

如机器有网络：

使用系统包。

如无网络：

不要破坏环境；允许使用已有缓存/离线包或轻量 fallback。

不要为了 RTAB-Map 卡住整个计划。

---

# 150. 一键启动最终目标

最终应该有类似：

```bash
cd /home/brov/robot/robot_scene_demo

bash scripts/go2w/start_rgbd_spatial_search_web.sh \
  --enable-autonomous-motion
```

然后打开：

```text
http://127.0.0.1:8765
```

输入：

```text
饮水机旁边的蓝色垃圾桶
```

即可。

---

# 151. WebUI 启动时自动健康检查

至少：

```text
D435 service
RGB-D atomic frame
Depth valid fraction
intrinsics
RGB-D bridge
SpatialProvider
RTAB-Map or fallback
Search worker
Motion backend
LLM
```

---

# 152. 降级矩阵

### D435 online + RTAB-Map good

```text
METRIC/RELATIVE RGBD spatial
```

### D435 online + RTAB-Map lost

```text
CAMERA_LOCAL
+
RelativeFrontierProvider
```

### D435 depth invalid

```text
RGB-only fallback
```

### PSG unavailable

```text
geometry frontier
+
GoalGraph observed semantics
```

### LLM transient fail

```text
fallback
```

系统不应因为一个可选模块 fail 全崩。

---

# 153. 不允许执行 AI 做的错误改法

禁止：

```text
只把 /color 换成旧 image source
然后宣布 RGB-D 集成完成
```

禁止：

```text
继续每 bundle 一个 map node
```

禁止：

```text
把 heading sector 改个名字叫 frontier
```

禁止：

```text
调高 forward score
然后宣布解决原地转圈
```

禁止：

```text
PSG 直接输出机器人动作
```

禁止：

```text
PSG predicted node 写进 ObservedSceneGraph
```

禁止：

```text
PSG strong prediction 直接 TARGET_FOUND
```

禁止：

```text
复制 UniGoal 的 Habitat/FMM/Agent 整套架构
```

禁止：

```text
把 RTAB-Map 当必须成功的唯一运行条件
```

禁止：

```text
把 camera-local xyz 伪装成 map xyz
```

禁止：

```text
未经证据把 D435→base 外参写 validated
```

---

# 154. Git 工作规则

如果本地已有：

```text
/home/brov/robot/robot_scene_demo
```

以当前 working tree 为最高优先。

开始：

```bash
git status --short
git diff --check
git diff --stat
git rev-parse HEAD
```

不得：

```bash
git reset --hard
git checkout -- .
git clean -fd
git clean -fdx
```

历史交接明确要求 dirty working tree 原地接续。

---

# 155. 代码复用优先级

始终：

```text
复用
>
扩展
>
Adapter
>
新建
```

不要因为本计划给了新类名就机械造重复系统。

---

# 156. 实施顺序总表

执行 AI 按以下顺序连续实施，不每阶段停下来问用户。

## Phase 0

审计：

```text
current main / working tree
D435 outputs
manual web
AutonomousExplorer
navigation
UniGoal
PSG
SearchEvent
RobotBackend
```

## Phase 1

D435 atomic RGB-D API。

## Phase 2

RGBDSource + FrameBundle V2。

## Phase 3

DepthObjectLocalizer。

## Phase 4

3D Semantic SceneGraph / SemanticObjectMap。

## Phase 5

RGB-D ROS Bridge。

## Phase 6

RTAB-Map SpatialProvider + fallback。

## Phase 7

FrontierExtractor。

## Phase 8

PlaceGraph。

## Phase 9

view/semantic/spatial gain 分层。

## Phase 10

LOCAL_SCAN / Long-Term Goal / LocalExecutor。

## Phase 11

UniGoal V2 Spatial ZERO/PARTIAL/STRONG。

## Phase 12

PSG SemanticPriorProvider。

## Phase 13

LongTermGoalSelector。

## Phase 14

Web forward plumbing。

## Phase 15

WebUI Spatial Map / Place / Frontier / PSG。

## Phase 16

Mock/replay tests。

## Phase 17

真 D435 observe-only。

## Phase 18

真 RGB-D map。

## Phase 19

真机 relocate search。

## Phase 20

普通目标。

## Phase 21

PSG relation target。

## Phase 22

回归、文档、handoff。

---

# 157. 测试不能只看“代码存在”

每个核心模块都需要：

```text
unit
integration
mock E2E
real sensor
real robot
```

至少一种对应证据。

---

# 158. 最终报告

生成：

```text
reports/go2w_rgbd_unigoal_v2_spatial_exploration_handoff_<date>.md
```

必须包含：

```text
Git status
架构变化
D435 integration
RGB-D synchronization
Object 3D
SpatialProvider
RTAB-Map结果
Fallback结果
Frontier
PlaceGraph
PSG
UniGoal V2
WebUI
测试
真机 session
Known limitations
Future production robot migration
```

---

# 159. README 更新

增加：

```text
RGB-D Spatial Semantic Exploration
```

包含：

```text
架构图
D435 service
启动命令
WebUI
地图模式
Spatial Quality
PSG
UniGoal V2
真机实验说明
```

---

# 160. 新技术文档

建议：

```text
docs/RGBD_UNIGOAL_V2_SPATIAL_EXPLORATION.md
```

详细说明：

```text
RGBDFrame
DepthObjectLocalizer
SpatialProvider
PlaceGraph
Frontier
PSG
LongTermGoalSelector
LocalGoalExecutor
Degraded modes
```

---

# 161. 对现有 WebUI 的最终目标

用户看到的不是：

```text
节点 1
节点 2
节点 3
```

全部重叠。

而是：

```text
Spatial Map

P0 ─── P1 ─── P2
              ▲
              Robot

F1 ○       ★ F2
              │
        water dispenser

PSG Region:
trash-bin-near-anchor

Selected:
F2

Reason:
PARTIAL match +
anchor spatially located +
PSG affinity +
unexplored frontier
```

---

# 162. 对自主搜索行为的最终目标

目标最开始视野外：

```text
Place 0
↓
RGB-D Observe
↓
Local Scan
↓
ZERO
↓
Frontier F2
↓
Relocate
↓
Place 1
↓
Observe
↓
Relocate
↓
Place 2
↓
water dispenser
↓
PARTIAL
↓
Anchor Region
↓
Relocate viewpoint
↓
Place 3
↓
blue trash bin
↓
STRONG
↓
Verify
↓
TARGET_FOUND
```

这才算完成。

---

# 163. 与未来机器狗的迁移

未来机器狗如果提供：

```text
map
pose
frontier/navigation
```

需要替换的主要是：

```text
SpatialProvider
RobotBackend
```

保留：

```text
GoalGraph
Observed SceneGraph
PSG
UniGoal V2
Semantic Memory
LongTermGoalSelector
PlaceGraph semantic layer
WebUI
```

---

# 164. 最终 Definition of Done

执行 AI 在结束前逐项检查：

```text
[ ] 已保护当前 working tree。
[ ] 已读取最新 GitHub/main 和本地现状。
[ ] 已复用现有 RealSense 服务。
[ ] D435 已成为主 RGB。
[ ] 旧 RGB 仍可 fallback。
[ ] 已实现 atomic RGB-D frame。
[ ] color/depth 同 frame_id。
[ ] 已实现 RGBDFrame。
[ ] FrameBundle 支持 RGB-D。
[ ] 旧 RGB-only replay 仍兼容。
[ ] 已实现 DepthObjectLocalizer。
[ ] Object 有 depth_m。
[ ] Object 有 bearing_deg。
[ ] Object 有 camera_xyz。
[ ] 没有把 camera xyz 伪装 map xyz。
[ ] 已实现 SpatialQuality。
[ ] 已升级 Observed 3D SceneGraph。
[ ] PSG 与 Observed SceneGraph 严格分离。
[ ] 已实现 SemanticObjectMap。
[ ] 已实现 RGB-D bridge 或等价同步接口。
[ ] 已尝试 RTAB-Map RGB-D odometry/map。
[ ] RTAB-Map 结果有 health/quality。
[ ] RTAB-Map 不可用时系统可降级。
[ ] 已实现 SpatialProvider。
[ ] 已实现真正 Frontier。
[ ] heading sector 只表示 Local View Coverage。
[ ] 已实现 RelativeFrontier fallback。
[ ] 已实现 PlaceGraph。
[ ] 每 Bundle 不再创建 Place。
[ ] 原地旋转 10 次仍只有 1 Place。
[ ] 成功平移才创建新 Place。
[ ] Heading coverage 是 Place-local。
[ ] 已拆 view gain / semantic gain / spatial gain。
[ ] 新 heading 不再无限 reset stagnation。
[ ] 已实现 bounded LOCAL_SCAN。
[ ] LOCAL_SCAN 饱和后必须进入空间 relocate。
[ ] 已实现 ExplorationIntent。
[ ] 已实现 EXPLORE_FRONTIER。
[ ] 已实现 INSPECT_ANCHOR_REGION。
[ ] 已实现 APPROACH_TARGET。
[ ] RELATIVE_MOVE 已降为 local primitive。
[ ] ROTATE_VIEW 不再作为长期空间探索目标。
[ ] 已实现 LongTermGoalSelector。
[ ] ZERO → Frontier。
[ ] PARTIAL → Anchor Region。
[ ] STRONG → Approach/Verify。
[ ] 已实现 PSG SemanticPriorProvider。
[ ] PSG 能给 Frontier 排名。
[ ] PSG 在 ZERO 权重高。
[ ] PSG 在 VERIFY 权重为 0。
[ ] PSG 不能覆盖视觉事实。
[ ] PSG 有 hypothesis negative memory。
[ ] 错误 PSG region 能被 blacklist。
[ ] 已修 Web autonomous forward plumbing。
[ ] WebUI 显示 D435 RGB。
[ ] WebUI 可显示 Depth。
[ ] WebUI 显示 Object depth/bearing。
[ ] WebUI 显示 Spatial Quality。
[ ] WebUI 显示 Occupancy/Spatial Map。
[ ] WebUI 显示 PlaceGraph。
[ ] WebUI 显示 Frontiers。
[ ] WebUI 显示 Selected Long-Term Goal。
[ ] WebUI 显示 PSG region/prior。
[ ] WebUI 显示 GoalGraph/ObservedGraph 分离。
[ ] WebUI 地图不再节点重叠。
[ ] Mock E2E PASS。
[ ] RGB-D source tests PASS。
[ ] Depth localization tests PASS。
[ ] Frontier tests PASS。
[ ] PlaceGraph tests PASS。
[ ] UniGoal V2 ZERO/PARTIAL/STRONG tests PASS。
[ ] PSG tests PASS。
[ ] Web tests PASS。
[ ] 真 D435 300+ frame RGB-D 测试 PASS。
[ ] 真 Object depth/bearing 测试 PASS。
[ ] 真 RGB-D pose/map 已测试。
[ ] 真机目标不存在测试有 >=3 relocation。
[ ] 真机搜索产生 >=4 Place。
[ ] 普通目标视野外测试 TARGET_FOUND。
[ ] 关系目标 PSG/Anchor 搜索 TARGET_FOUND。
[ ] 用户没有逐步告诉机器人往哪走。
[ ] README 已更新。
[ ] RGB-D Spatial 文档已更新。
[ ] 最新 handoff 已生成。
[ ] git diff --check PASS。
```

全部完成后才可以声明：

```text
D435_PRIMARY_PERCEPTION = PASS
RGBD_OBJECT_LOCALIZATION = PASS
SPATIAL_PROVIDER = PASS
TRUE_FRONTIER_EXPLORATION = PASS
PLACE_GRAPH = PASS
UNIGOAL_V2_SPATIAL = PASS
PSG_SEMANTIC_FRONTIER_PRIOR = PASS
RGBD_SPATIAL_WEBUI = PASS
GO2W_SPATIAL_AUTONOMOUS_SEARCH_E2E = PASS
```

---

# 165. 给执行 AI 的最后一句话

这次不要把任务理解为：

> “把旧 RGB 换成 D435”。

也不要理解为：

> “修复机器人只会原地转圈”。

真正的任务是：

> **利用 D435 提供的同步 RGB-D，把当前 `robot_scene_demo` 从 next-view semantic search 升级成 UniGoal-style spatial semantic exploration；利用 Spatial Map 产生真实 Frontier，利用 Observed 3D SceneGraph 提供事实，利用 PSG 提供可被负证据修正的语义空间先验，再由 UniGoal V2 选择长期空间目标，最后由 RobotBackend 执行局部运动。**

最终四层职责必须清楚：

```text
D435 / SpatialProvider
→ 哪里存在、哪里未知、哪里可以去

Observed 3D SceneGraph
→ 机器人真正看到了什么、在哪里

PSG
→ 根据目标和已观察事实，哪些未知区域更值得找

UniGoal V2
→ 综合事实、预测、Frontier、记忆，决定下一个长期空间目标

LocalGoalExecutor / RobotBackend
→ 怎么过去
```

最终验收标准不是“多了 depth 字段”，而是：

> **一个原本完全在视野外的目标，机器人能真正离开初始位置，在 RGB-D 空间地图上连续选择新的探索区域，PSG 能影响但不能支配语义 Frontier，地图能真实显示搜索轨迹，最后通过真实视觉/关系证据完成目标确认。**
