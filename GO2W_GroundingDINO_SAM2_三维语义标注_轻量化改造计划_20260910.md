# GO2W GroundingDINO + SAM2 三维语义标注轻量化改造计划

> **目标仓库**：`BROVVV/robot_scene_demo-go2w-deploy`  
> **目标分支**：`main`  
> **计划日期**：2026-09-10  
> **用途**：本文件直接交给 Coding Agent / AI 执行。  
>
> **本次前提**：当前三维建图已经稳定，**禁止替换现有 SLAM、禁止引入 LIO-SAM/FAST-LIO、禁止重构现有三维地图主链路**。  
>
> **本次唯一目标**：在现有稳定三维地图基础上，把视觉感知链改成：
>
> `GroundingDINO 快速找物体 → SAM2 精确分割 → D435 Depth 求 3D 位置和 3D Box → 投影到现有 map 坐标 → 简单实例融合去重 → 大模型只在必要时判断“是不是用户要找的目标”`
>
> 要求机器狗移动时可以持续更新三维地图和语义物体，不因为大模型推理而频繁停下来。

---

# 0. Coding Agent 必须先理解的当前代码事实

执行修改前，先读取当前本地代码；如果本地代码比 GitHub `main` 更新，以本地为准。

根据当前 `main`，项目已经具备以下基础，**必须复用，禁止重复造轮子**：

1. `app/detectors/grounded_sam_subprocess.py`
   - 已有 `GroundedSAMSubprocessDetector`
   - 已经会构造 GroundingDINO 动态 prompt
   - 已经会调用外部 Grounded-SAM-2 Python 环境
   - 已经有 `DetectorRuntimeError`

2. `app/detectors/grounded_sam_worker.py`
   - 已经运行 GroundingDINO
   - 已经支持 SAM2
   - 已经做 NMS
   - 已经输出 bbox、label、score
   - **当前 SAM2 只输出 `mask_area_ratio`，没有把真实 mask 返回到主进程**
   - **当前 worker 每次调用都会重新加载 GroundingDINO 模型**
   - **当前 SAM2 每次使用时也重新 build model**
   - 这是当前最明显的实时效率浪费。

3. `app/detectors/base.py`
   - `DetectedObject` 已有：
     - `label`
     - `bbox_2d`
     - `score`
     - `mask_area_ratio`
     - `source`
   - 当前没有真实 mask 数据。

4. `app/perception/grounding_prompt_planner.py`
   - 已经可以由大模型把自然语言任务扩展成 GroundingDINO prompt。
   - 这个能力保留。
   - **只允许任务开始时或确实需要 retry 时调用，不允许每帧调用。**

5. `app/perception/depth_object_localizer.py`
   - 已经有 `DepthObjectLocalizer`
   - 已经能输出：
     - `depth_m`
     - `camera_xyz`
     - `bearing_deg`
     - `map_xyz`
     - `spatial_quality`
   - 已经强调必须使用同一 RGB-D 帧，不能 old bbox + current depth。
   - **当前主要按 bbox 内部区域采 depth，还没有使用 SAM2 mask。**

6. `app/slam/slam_bridge.py`
   - 当前已有：
     - `robot_pose_map(timestamp)`
     - `robot_pose_map_reprojected(timestamp)`
     - `camera_pose_map(timestamp)`
     - `camera_point_to_map(xyz_camera, timestamp)`
   - 已经支持按图像拍摄时刻投影。
   - 已经考虑 map→odom 更新后的历史语义重投影。
   - **本次直接复用，禁止重写 SLAMBridge。**

7. `app/mapping/map_quality_evaluator.py`
   - 已经有：
     - 点云 surface thickness
     - endpoint error
     - object spread
     - duplicate metrics
   - **本次只扩展必要的 semantic box / performance 指标，不新建第二个 evaluator。**

8. 当前 tests 已经包括：
   - `test_grounded_sam_runtime.py`
   - `test_grounded_sam_prompt_integration.py`
   - `test_grounding_prompt_planner.py`
   - `test_depth_object_localizer.py`
   - `test_depth_object_localizer_identity.py`
   - `test_live_persistent_id_mapping.py`
   - `test_map_quality_evaluator.py`
   - `test_async_semantic_observer.py`
   - `test_autonomous_explorer_target_approach.py`
   - 等大量现有测试。
   - **优先扩展这些测试，不要为了每个小函数创建一堆新测试文件。**

---

# 1. 本次明确禁止的事情

这是强约束。

## 1.1 禁止改三维建图算法

不允许：

- 换 LIO-SAM
- 换 FAST-LIO
- 引入视觉 SLAM
- 修改 plain_slam 主算法
- 重新做 pose graph
- 重新设计 map/odom/base_link
- 重新做全局点云累计

当前三维地图已经稳定，本次只把它当作稳定世界坐标和几何底图。

---

## 1.2 禁止过度编程

不允许为了本功能新增：

- 通用插件框架
- detector registry
- semantic backend interface hierarchy
- 多层 repository/service/controller
- event sourcing
- dependency injection framework
- 通用 RPC 框架
- protobuf
- Redis
- 数据库
- 消息队列
- Kafka
- 新的微服务体系
- 复杂 scene graph
- CLIP embedding database
- 向量数据库

如果一个普通 Python class / dict / dataclass 能完成，就用普通实现。

---

## 1.3 禁止工业化编程

这个是机器狗单机实验项目，不是互联网 SaaS。

不要实现：

- 多租户
- 权限系统
- 自动横向扩缩容
- 多 GPU 调度器
- worker pool
- Kubernetes
- distributed inference
- generic model serving platform
- metrics server
- tracing system
-复杂 health-check supervisor

GroundingDINO + SAM2 只需要一个常驻本地子进程即可。

---

## 1.4 禁止防御性编程堆叠

不需要：

```text
retry -> retry -> fallback -> fallback -> retry daemon -> restart supervisor
```

新 Grounded-SAM 常驻进程：

- 正常运行就运行；
- worker 真崩溃就明确报错；
- 不新增 watchdog/restart manager；
- 不做无限 retry；
- 不做静默吞异常。

已有项目中的正常错误处理不用删除，但本次不要继续增加复杂恢复层。

---

# 2. 改造后的最简架构

最终只要形成下面这条链：

```text
用户任务：
“找蓝色垃圾桶”
        │
        ▼
现有任务理解 / LLM
        │
        ▼
GroundingPromptPlanner
        │
        │ 一次生成 prompt
        ▼
"blue trash can . trash bin . blue bin"
        │
        ▼
────────────────────────────────────────
机器狗持续运动
────────────────────────────────────────
        │
D435 RGB frame
        │
        ▼
GroundingDINO
        │
        ├── bbox
        ├── label
        └── score
        │
        ▼
SAM2
        │
        └── exact mask
        │
        ▼
D435 aligned depth
        │
        ▼
DepthObjectLocalizer
        │
        ├── depth
        ├── camera_xyz
        ├── mask 3D points
        └── 3D extent
        │
        ▼
SLAMBridge.camera_point_to_map(timestamp)
        │
        ▼
map_xyz + map 3D box
        │
        ▼
SemanticObjectMap
        │
        ├── 去重
        ├── 位置融合
        └── 稳定 object_id
        │
        ├──────────► 3D 地图画 Box + Label
        │
        └──────────► 如果可能是目标
                          │
                          ▼
                      LLM/VLM verify
                          │
                  yes ────┴──── no
                  │              │
          现有 target approach   继续探索
```

关键思想：

> **SLAM 负责地图；GroundingDINO + SAM2 负责“地图里有什么”；大模型负责“这个是不是我要找的目标”。**

---

# 3. 第一项修改：GroundingDINO/SAM2 模型常驻

这是性能提升最大的修改。

当前 `grounded_sam_subprocess.py` 每次 detect 启动 worker，worker 内部再：

```python
load_model(...)
build_sam2(...)
```

这会让大量时间花在“重复加载模型”，而不是推理。

## 3.1 不新建服务框架

直接修改现有：

```text
app/detectors/grounded_sam_worker.py
app/detectors/grounded_sam_subprocess.py
```

只做一个简单常驻 subprocess。

---

## 3.2 `grounded_sam_worker.py`

增加一个非常简单的 `--serve` 模式。

启动时一次：

```python
grounding_model = load_model(...)
sam2_model = build_sam2(...)
sam2_predictor = SAM2ImagePredictor(sam2_model)
```

之后：

```text
stdin 每行一个 JSON request
stdout 每行一个 JSON response
```

request：

```json
{
  "image": "/tmp/frame_0001.jpg",
  "text_prompt": "chair . trash can . blue bin",
  "box_threshold": 0.35,
  "text_threshold": 0.25,
  "max_objects": 20
}
```

response：

```json
{
  "objects": [...]
}
```

不实现：

- socket server
- HTTP server
- grpc
- worker pool

只用 `subprocess.Popen(..., stdin=PIPE, stdout=PIPE)`。

---

## 3.3 `GroundedSAMSubprocessDetector`

改成：

第一次 detect：

```text
ensure_worker()
```

后续每次：

```text
向同一个 process 写一行 JSON
读取一行 JSON
```

整个自主搜索 session 中：

```text
GroundingDINO load = 1 次
SAM2 load = 1 次
```

而不是每帧一次。

---

## 3.4 生命周期

只需要：

```python
start
detect
close
```

程序退出时 terminate 子进程。

不要加复杂进程管理。

---

# 4. 第二项修改：SAM2 不再只返回 `mask_area_ratio`

当前 SAM2 已经真正算出了 mask，但 worker 最后只保留：

```text
mask_area_ratio
```

这浪费了 SAM2 最有价值的信息。

本次必须让真实 mask 进入后续 Depth 定位。

---

# 5. 最轻量的 mask 传递方式

不要保存一堆 PNG 文件，也不要引入新的 image server。

给 `DetectedObject` 增加：

```python
mask_rle: list[list[int]] | None = None
mask_size: tuple[int, int] | None = None
```

其中 `mask_rle` 使用项目自己最简单的 row-run 编码即可。

例如 mask 一行：

```text
000111100
```

保存：

```text
[row_index, start_x, length]
```

所有前景连续段组成：

```python
[
    [120, 245, 38],
    [121, 243, 42],
    ...
]
```

就够了。

不要：
- COCO schema 全套
- pycocotools dependency
- polygon simplify library

实现两个小函数：

```python
encode_mask_runs(mask) -> list[list[int]]
decode_mask_runs(runs, height, width) -> np.ndarray
```

可以直接放在：

```text
app/detectors/grounded_sam_worker.py
```

和需要 decode 的地方。

如果避免代码重复，可放一个很小的：

```text
app/detectors/mask_utils.py
```

仅包含 encode/decode，不要继续抽象。

---

# 6. SAM2 输出内容

`grounded_sam_worker.py` 每个 object 改成：

```json
{
  "label": "chair",
  "score": 0.88,
  "bbox_2d": [0.2, 0.3, 0.6, 0.9],
  "mask_area_ratio": 0.12,
  "mask_size": [480, 640],
  "mask_rle": [...]
}
```

同时使用 SAM2 mask 重新计算一个 tight bbox。

也就是说：

```text
GroundingDINO bbox
       ↓
SAM2 mask
       ↓
mask 的 min/max x/y
       ↓
更精确的 bbox_2d
```

最终 `bbox_2d` 使用 SAM2 refined bbox。

---

# 7. 第三项修改：DepthObjectLocalizer 使用 SAM2 mask

当前：

```text
bbox
↓
取 bbox 内中央区域 depth
↓
估计 object depth
```

存在一个问题：

例如椅子 bbox：

```text
┌─────────────────┐
│ 背景墙           │
│      椅子        │
│      椅子        │
│ 地板             │
└─────────────────┘
```

bbox 里面有大量背景。

SAM2 mask 可以直接告诉系统：

```text
哪些 pixel 真正属于椅子
```

---

## 7.1 修改 `DepthObjectLocalizer.localize`

逻辑：

```python
if object has valid SAM2 mask:
    depth_samples = depth[mask]
else:
    depth_samples = existing_bbox_sampling
```

保持当前 bbox 逻辑作为没有 SAM2 mask 时的正常兼容路径。

不要新增第二个 localizer。

---

## 7.2 Mask depth 清洗

保持简单：

```text
mask pixels
↓
过滤 depth=0 / NaN
↓
过滤 min_depth/max_depth
↓
median
```

当前已经有 MAD / dominant surface 逻辑的地方继续复用。

不要新增复杂 clustering。

---

# 8. 第四项修改：从 mask + depth 直接生成 3D Box

这一步就是你想要的“像 SuperMap 一样在地图上给物体画 box”。

不用做复杂 oriented 3D bounding box。

第一版只做：

> **axis-aligned 3D bounding box (AABB)**

已经够好看、够用。

---

## 8.1 从 mask 随机/均匀采样深度点

不需要把每个像素都算成 3D。

例如：

```yaml
semantic_mapping:
  max_mask_depth_points: 500
```

SAM2 mask 内最多均匀取 500 个有效 depth pixel。

通过 D435 intrinsics：

```text
(u, v, depth)
↓
camera_xyz
```

得到：

```text
N x 3 camera points
```

---

## 8.2 去掉明显 depth outlier

保持轻量：

```text
以 object median depth 为中心
仅保留 ±0.30m 或 MAD 合格的点
```

不使用 DBSCAN。

---

## 8.3 Camera-frame box

计算：

```python
min_xyz = points.min(axis=0)
max_xyz = points.max(axis=0)

center = (min_xyz + max_xyz) / 2
size = max_xyz - min_xyz
```

为了避免少量离群值影响，推荐实际使用：

```python
p05 = percentile(points, 5)
p95 = percentile(points, 95)
```

然后：

```python
center = (p05 + p95) / 2
size = p95 - p05
```

这比 min/max 稳。

---

# 9. 第五项修改：把 3D Box 投影到现有 SLAM 地图

**不要改 SLAM。**

直接使用当前已有：

```python
SLAMBridge.camera_point_to_map(
    xyz_camera,
    timestamp=image_timestamp
)
```

对：

```text
box center
以及 box 所需 corner/extent
```

进行 map transform。

---

## 9.1 新增到 `ObjectSpatialObservation`

扩展现有 observation：

```python
box_center_camera: tuple[float, float, float] | None
box_size_camera: tuple[float, float, float] | None

box_center_map: tuple[float, float, float] | None
box_size_map: tuple[float, float, float] | None
```

如果 map 目前只有 yaw 平面 transform：

```text
box size 直接保留 XYZ extent
box center 做 map transform
```

第一版不用做 oriented box rotation。

---

# 10. 第六项修改：新增一个极简单的 `SemanticObjectMap`

当前 `app/mapping/` 只有质量 evaluator。

新增：

```text
app/mapping/semantic_object_map.py
```

只写一个 class。

---

## 10.1 数据结构

```python
@dataclass
class SemanticMapObject:
    object_id: str
    label: str

    position_map: tuple[float, float, float]
    box_size: tuple[float, float, float] | None

    confidence: float
    observations: int

    first_seen: float
    last_seen: float

    source_prompt_term: str | None = None
```

不要加：
- embedding
- relations
- room
- scene graph
- database model
- versioning

---

## 10.2 内部存储

就：

```python
self.objects: list[SemanticMapObject]
```

够了。

不需要数据库。

---

# 11. 同一物体去重

每个新的 3D detection 到达后：

先查：

```python
same_label_objects
```

然后计算：

```python
distance_3d
```

初始配置：

```yaml
semantic_mapping:
  merge_distance_m: 0.45
```

如果：

```text
label 相同
AND
distance <= 0.45m
```

就更新旧 object。

否则创建新 object。

---

## 11.1 两个相邻同类物体不能错误合并

加一个很简单的辅助条件即可：

如果当前 live tracker 已经能提供不同 persistent/track id，并且两个 id 同时出现在同一帧：

```text
禁止把这两个 object merge
```

优先复用现有：

```text
VideoObjectTracker
persistent id mapping
```

不要重新写 tracking。

---

## 11.2 更新位置

不要存每帧一个新 instance。

已有 object：

```python
new_position = 0.7 * old_position + 0.3 * observation_position
```

box：

```python
new_size = 0.7 * old_size + 0.3 * observed_size
```

confidence：

```python
max(old_confidence, new_confidence)
```

observations：

```python
+= 1
```

就够了。

禁止设计 Kalman filter / factor graph。

---

# 12. 物体必须先有可靠 3D 坐标再进入地图

下面这些 detection：

```text
RGB-only
depth 无效
没有 timestamp pose
map transform 不存在
```

继续可以作为当前视觉 detection 使用。

但是：

> **不要写入 persistent SemanticObjectMap。**

只有：

```text
spatial_quality 可用于 map
AND
map_xyz != None
```

才进入三维语义地图。

这样可以避免地图上漂着假的 box。

---

# 13. 第七项修改：利用现有 SLAM reproject 支持地图校正

现有 `SLAMBridge` 已经有：

```python
robot_pose_map_reprojected(timestamp)
reproject_camera_point_to_map(...)
```

因此 SemanticObjectMap 不需要复杂设计。

每个 object 额外保留最近若干有效 observation：

```python
camera_xyz
timestamp
```

建议最多：

```text
10 个
```

如果现有 SLAM 出现全局 map correction：

调用现有 reproject：

```text
camera_xyz + original timestamp
↓
reproject_camera_point_to_map
↓
新的 map_xyz
```

然后重新平均 object position。

只做这一层即可。

不要做 semantic pose graph。

---

# 14. 第八项修改：3D 地图上显示 Box + Label

复用当前 WebUI 的三维地图。

执行 AI 先 grep：

```text
/go2w/slam/map_3d
pslam_map
three.js
PointCloud
map_3d
map3d
```

找到当前 3D viewer 的实际后端接口和前端渲染代码。

---

## 14.1 后端只需要增加一个轻量 endpoint/payload

如果已有 `/api/...map...`：

直接在现有 payload 中增加：

```json
"semantic_objects": [
  {
    "id": "chair_001",
    "label": "chair",
    "label_zh": "椅子",
    "center": [2.3, 1.4, 0.6],
    "size": [0.55, 0.55, 0.9],
    "confidence": 0.91,
    "observations": 14
  }
]
```

不要创建新的 WebSocket protocol。

---

## 14.2 前端

现有 Three.js / 3D viewer：

每个 semantic object 只画：

```text
BoxGeometry
+
Edges/Line
+
文字 label
```

类似：

```text
┌───────────────┐
│ chair 0.91    │
│               │
│    3D BOX     │
└───────────────┘
```

不做：
- mesh reconstruction
- 纹理
- 3D object model
- fancy animation

---

# 15. 第九项修改：重新分配 GroundingDINO / SAM2 / 大模型职责

这是本次核心。

---

## 15.1 GroundingDINO

负责：

```text
当前画面里有哪些可能相关的物体？
```

运行频率高。

---

## 15.2 SAM2

负责：

```text
这个 bbox 里，哪些 pixel 真正属于物体？
```

用于：

- 精确 depth；
- 3D center；
- 3D box。

---

## 15.3 LLM / VLM

只负责：

```text
这个视觉候选究竟是不是用户要求的目标？
```

例如用户任务：

```text
寻找饮水机旁边的蓝色垃圾桶
```

GroundingDINO 可提供：

```text
blue trash can
water dispenser
trash bin
```

SAM2 提供 mask。

三维 map 提供：

```text
trash_bin_002 position
water_dispenser_001 position
```

然后大模型再判断：

```text
颜色是否符合
是否确实是目标物体
关系是否符合
```

---

# 16. 禁止大模型每帧看整图

新正常循环不允许：

```text
每一帧
→ full image
→ VLM
→ scene analysis
```

改成：

```text
每帧/关键帧
→ GroundingDINO + SAM2

只有出现“可能是目标”的 candidate
→ crop candidate
→ VLM/LLM verify
```

这样机器人就不用等大模型。

---

# 17. 复用现有 `GroundingPromptPlanner`

这个模块不要删。

但调用时机明确：

```text
任务开始
↓
LLM 理解用户任务
↓
GroundingPromptPlanner.build()
↓
得到 prompt
↓
整次搜索复用
```

例如：

```text
用户：
“寻找饮水机旁边的蓝色垃圾桶”

prompt plan:
blue trash can
trash bin
blue bin
water dispenser
```

后面不用每一帧重新问 LLM。

---

# 18. Retry 的轻量规则

GroundingDINO 连续几个关键观察点都没有候选时：

允许使用现有：

```python
GroundingPromptPlanner.build_retry(...)
```

一次。

不要无限扩 prompt。

建议：

```yaml
grounded_sam:
  max_prompt_retries_per_task: 1
```

如果已有逻辑能表达，不新增配置。

---

# 19. 感知执行频率

无需追求 30 FPS。

SLAM 三维地图本来就独立实时运行。

语义层第一版目标：

```text
2~5 Hz
```

就完全足够。

建议：

```yaml
grounded_sam:
  semantic_fps: 3.0
```

如果现在是 event-driven stable observation，则沿用现有 `AsyncSemanticObserver`，只保证：

> **语义推理不能阻塞 SLAM 和运动线程。**

不要新建复杂 task queue。

---

# 20. SAM2 优化策略

SAM2 不必给所有低分 bbox 做 mask。

例如：

```yaml
grounded_sam:
  sam2_min_box_score: 0.35
```

只有通过 GroundingDINO 基础门槛的 bbox 才跑 SAM2。

当前已有 NMS，继续复用。

---

# 21. 目标候选的最终大模型验证

复用现有 crop verifier / target verifier。

输入尽量小：

```text
candidate crop
+
任务文本
+
GroundingDINO label/score
+
必要的关系信息
```

不要把：
- 整张 3D 点云
- 所有历史帧
- 全地图

都传给大模型。

返回只需要：

```json
{
  "is_target": true,
  "confidence": 0.94,
  "reason": "..."
}
```

如果现有 verifier schema 已经够用，就完全不要改 schema。

---

# 22. 与现有自主搜索的接法

本次不要重构 `AutonomousExplorer`。

只改变视觉事实的来源。

正常探索：

```text
GroundedSAM
↓
scene_objects
↓
DepthObjectLocalizer
↓
SemanticObjectMap
↓
现有 exploration
```

目标 candidate：

```text
GroundedSAM candidate
↓
现有 target verifier
↓
confirmed
↓
现有 TARGET_CONFIRMED / target approach
```

前一轮已经做好的：

- clearance navigation
- recovery
- target approach

都不要重写。

---

# 23. 需要修改的文件清单

## 必改

### `app/detectors/grounded_sam_worker.py`

修改：

1. 模型加载移到 worker startup；
2. 添加简单 `--serve` JSONL loop；
3. SAM2 返回实际 mask RLE；
4. SAM2 mask refine bbox；
5. 不再只返回 `mask_area_ratio`。

---

### `app/detectors/grounded_sam_subprocess.py`

修改：

1. 从“每次 run 一个 subprocess”改成“session 内一个 Popen”；
2. 向常驻 worker 发 JSON request；
3. 解析 `mask_rle/mask_size`；
4. close 时退出 worker；
5. 不增加复杂 watchdog。

---

### `app/detectors/base.py`

给 `DetectedObject` 增加：

```python
mask_rle
mask_size
```

只加这两个字段即可。

---

### `app/perception/depth_object_localizer.py`

修改：

1. 优先使用 SAM2 mask 取 depth；
2. 保留 bbox fallback；
3. 从 mask depth points 计算：
   - object center
   - object size
4. 输出 map center + simple 3D box；
5. 继续使用同帧 depth 和 image timestamp。

---

### `app/live_robot/semantic_observer.py`
或真正负责 detector→scene_objects 转换的当前代码位置。

修改：

1. 保留 mask 信息直到 DepthObjectLocalizer；
2. 给每帧 detection 保留稳定 `frame_object_id`；
3. 不因为 map 语义融合改变当前帧 detector identity。

---

### `app/slam/slam_bridge.py`

**原则上不重写。**

只在现有 API 不足时补一个很小 helper：

```python
camera_box_to_map(...)
```

如果普通 `camera_point_to_map` 已经够用，就不要修改。

---

### `app/mapping/semantic_object_map.py`

**新增唯一主要新模块。**

负责：

```text
add/update observation
3D distance merge
stable object id
EMA position/size
reproject
to_dict
```

控制在一个文件内。

---

### 当前 3D WebUI 后端/前端文件

执行 AI 通过 grep 定位，不预设文件名。

只加入：

```text
semantic_objects payload
3D box render
label render
```

不改 3D point map 逻辑。

---

## 可小改

### `app/mapping/map_quality_evaluator.py`

增加：

- object box center spread；
- box size spread；
- semantic instance count；
- performance metrics 聚合（若有需要）。

不要重写。

---

### `app/config.py`

只新增确实需要的少量参数。

建议最多：

```text
grounded_sam_persistent_worker
grounded_sam_semantic_fps
grounded_sam_sam2_min_score
semantic_map_merge_distance_m
semantic_map_min_confirmations
semantic_map_max_mask_depth_points
```

如果现有字段已经表达同义含义，复用旧字段。

不要加入 30 个新配置。

---

# 24. SemanticObjectMap 具体逻辑

建议代码控制在约 150~250 行。

接口：

```python
class SemanticObjectMap:
    def update(self, observation) -> SemanticMapObject:
        ...

    def reproject(self, slam_bridge) -> None:
        ...

    def to_list(self) -> list[dict]:
        ...
```

内部：

```python
objects = []
next_id = 1
```

---

## 24.1 新对象 ID

例如：

```text
chair_001
chair_002
trash_can_001
```

创建新物体时生成。

一旦创建，不因为位置更新改 ID。

---

## 24.2 Merge

```python
same_label = ...
nearest = ...

if distance <= merge_distance:
    update(nearest)
else:
    create()
```

如果同一帧 tracker 明确表明两个不同 instance：

```text
不 merge
```

---

## 24.3 Confirmation

建议：

```yaml
semantic_map_min_confirmations: 2
```

一次 detection：

```text
candidate
```

第二次位置一致：

```text
confirmed
```

3D viewer 默认画 confirmed。

目标验证本身不一定需要等待 map confirmed；target candidate 可以立即送 verifier。

---

# 25. 3D Box 稳定化

每次 SAM2 mask 得到一个 box size。

不要让 box 每帧剧烈跳。

使用和 position 一样的 EMA：

```python
alpha = 0.3

fused_size =
    (1-alpha) * previous_size
    + alpha * observed_size
```

不要上滤波库。

---

# 26. 动态人物的处理

第一版不要做动态地图追踪。

简单规则：

```text
person
dog
cat
```

默认：

```text
不写入 persistent semantic map
```

仍然可以显示当前检测。

配置一个简单 set：

```python
DYNAMIC_LABELS = {"person", "dog", "cat"}
```

不要做运动分类算法。

如果你的任务需要找人，可 target verifier 仍可以找，但不把人当永久地图 landmark。

---

# 27. 地图语义层和几何层严格分离

一定保持：

```text
existing 3D SLAM map
```

完全不因为视觉检测被修改。

SemanticObjectMap 只是 overlay：

```text
map point cloud
+
semantic box
```

不要：

```text
把 SAM2 pixel 重新融合进 SLAM point cloud
```

本次没有必要。

---

# 28. 性能验收：必须验证“真的变快”

执行 AI 必须在修改前后跑同一组图片，例如 20 张。

记录：

```text
cold_start_ms
warm_inference_median_ms
warm_inference_p95_ms
worker_spawn_count
grounding_model_load_count
sam2_model_load_count
```

---

## 28.1 必须通过

一个搜索 session：

```text
worker_spawn_count = 1
grounding_model_load_count = 1
sam2_model_load_count = 1
```

20 帧不能 load 20 次。

---

## 28.2 性能目标

以旧版同硬件测得 baseline 为准。

要求：

```text
修改后 warm median latency
<= 旧版 median latency 的 60%
```

即至少约 40% 改善。

如果旧版耗时主要来自 model load，实际应该改善更多。

---

## 28.3 不要求

不强行要求 30 FPS。

只要求：

```text
semantic pipeline >= 2 Hz
```

或能够达到配置的 event-driven 更新节奏，而且：

```text
机器人运动/SLAM线程不被等待
```

---

# 29. LLM 调用次数验收

录制一个 100 帧、没有目标候选的 session。

要求：

```text
任务开始：
GroundingPromptPlanner LLM call <= 1

普通 100 帧：
target VLM verify calls = 0
```

出现一个稳定 candidate：

```text
只在 candidate 出现时 verify
```

不得：

```text
100 帧 -> 100 次 VLM
```

---

# 30. SAM2 mask 对 depth 的有效性测试

构造测试图：

```text
bbox 内：
30% 是目标
70% 是背景墙
```

假设：

```text
目标 depth = 1.5m
背景 = 3.5m
```

要求：

bbox-only 可能产生偏差。

SAM2-mask localizer 必须得到：

```text
object depth ≈ 1.5m
```

验收误差：

```text
<= 0.10m
```

在合成/fixture 数据上。

---

# 31. 三维物体位置稳定性验收

同一个静态 object：

从至少 5 个不同 viewpoint 观测。

记录所有：

```text
map_xyz
```

使用现有 `map_quality_evaluator.object_spread()`。

要求：

```text
centroid_rms_error <= 0.15m
centroid_p95_error <= 0.25m
```

如果不通过：

优先修：

```text
mask depth
RGB-depth 同帧
timestamp
camera/map transform
```

不要首先调大 merge radius。

---

# 32. 3D Box 稳定性验收

同一个物体 5~10 次观测。

记录 box：

```text
width
depth
height
```

要求：

```text
box center RMS <= 0.15m
```

并要求三个 dimension：

```text
相对标准差 <= 25%
```

极细/软物体可以豁免 size 指标，但 chair/bin/box 类测试物必须满足。

---

# 33. 去重验收

## Case A：一个椅子

机器狗从 10 个位置看到。

假设：

```text
detections = 30
```

最终：

```text
confirmed chair instances = 1
```

必须：

```text
false_split = 0
```

---

## Case B：两把椅子

两把真实椅子间距 > 1m。

从不同位置观察。

最终：

```text
confirmed chair instances = 2
```

必须：

```text
false_merge = 0
```

---

# 34. 大模型目标判定验收

准备：

```text
10 张真实目标 crop
10 张相似但不是目标的 distractor crop
```

例如目标：

```text
蓝色垃圾桶
```

distractor：

```text
黑色垃圾桶
蓝色箱子
蓝色椅子
普通垃圾桶
```

GroundingDINO 可以把它们都视为候选。

最终 VLM verifier：

建议：

```text
20 个样本正确 >= 18
```

也就是：

```text
>=90%
```

如果数据太少，不宣称统计泛化能力，只作为本项目验收集。

---

# 35. 三维地图本身的回归验收

虽然不改 SLAM，但修改后必须证明没有破坏现有稳定地图。

继续运行当前已有：

```text
map_quality_evaluator
```

以及现有地图验证流程。

必须满足：

```text
修改前后 map quality 指标不恶化
```

至少检查：

```text
surface_thickness_p95
endpoint error
```

如果项目已有固定阈值，使用现有阈值，不重新发明一套。

---

# 36. 实时联调场景

最终真机至少跑：

## 场景 1：边走边建图

机器人连续走，不人为停顿。

要求：

- 3D map 持续更新；
- GroundingDINO/SAM2 在后台运行；
- 不因为语义推理让运动主循环明显停顿；
- WebUI box 随观测逐渐出现。

---

## 场景 2：一个物体多角度

放一个垃圾桶。

绕它不同方向看到至少 5 次。

要求：

```text
地图一直只有 trash_can_001
```

位置逐渐稳定。

---

## 场景 3：两个相同物体

放两个椅子。

要求：

```text
chair_001
chair_002
```

不能合成一个。

---

## 场景 4：目标搜索

任务：

```text
“寻找蓝色垃圾桶”
```

正常表现：

```text
LLM 一次生成 Grounding prompts
↓
GroundedSAM 持续扫描
↓
发现候选
↓
SAM2 mask + Depth 3D
↓
地图出现 box
↓
VLM verify
↓
confirmed
↓
现有 approach
```

---

# 37. 测试文件修改计划

优先扩展现有测试。

---

## `tests/test_grounded_sam_runtime.py`

新增：

- persistent worker；
- 连续调用不重复模型加载；
- mask RLE round-trip；
- SAM2 refined bbox。

---

## `tests/test_grounded_sam_prompt_integration.py`

验证：

- prompt task 级缓存；
- detection 连续运行不重新调用 prompt LLM。

---

## `tests/test_depth_object_localizer.py`

新增：

- mask-depth sampling；
- bbox background contamination；
- 3D box center；
- 3D box size；
- mask 缺失时 bbox fallback。

---

## `tests/test_depth_object_localizer_identity.py`

确保：

```text
SAM2 mask / spatial result
```

仍通过 frame_object_id 精确对应，不把两个同类物体串位。

---

## `tests/test_live_persistent_id_mapping.py`

扩展：

- 10/30 次同物体观测 -> 1 persistent object；
- 两个同类物体 -> 2 object。

---

## `tests/test_map_quality_evaluator.py`

增加：

- semantic box center spread；
- duplicate count；
- false merge / false split。

---

## `tests/test_async_semantic_observer.py`

验证：

- GroundedSAM 耗时不会阻塞 observation loop；
- stale result 不错误覆盖新帧。

复用现有 async 结构，不新建 scheduler。

---

## 新增最多一个测试文件

允许新增：

```text
tests/test_semantic_object_map.py
```

集中测试：

- create
- merge
- no merge
- EMA
- confirmation
- reproject
- serialization

不要拆成十个测试文件。

---

# 38. 测试命令

Coding Agent 修改前先跑 baseline：

```bash
python -m pytest -q \
  tests/test_grounded_sam_runtime.py \
  tests/test_grounded_sam_prompt_integration.py \
  tests/test_grounding_prompt_planner.py \
  tests/test_depth_object_localizer.py \
  tests/test_depth_object_localizer_identity.py \
  tests/test_live_persistent_id_mapping.py \
  tests/test_map_quality_evaluator.py \
  tests/test_async_semantic_observer.py
```

修改后重新跑。

然后：

```bash
python -m pytest -q tests/test_semantic_object_map.py
```

如果新增该文件。

最后：

```bash
python -m pytest -q
```

并：

```bash
python -m compileall app tests
```

---

# 39. 性能测试脚本

允许新增一个简单脚本：

```text
scripts/benchmark_grounded_sam.py
```

它只做：

```text
启动 detector
↓
warmup 2 张
↓
连续跑 20 张
↓
打印 latency
↓
打印 worker/model load count
```

输出：

```json
{
  "frames": 20,
  "worker_spawn_count": 1,
  "grounding_model_load_count": 1,
  "sam2_model_load_count": 1,
  "median_ms": 320,
  "p95_ms": 410
}
```

不要引入 benchmark framework。

---

# 40. 最终验收表

Coding Agent 完成后必须逐项检查。

## GroundedSAM

- [ ] GroundingDINO session 内只加载一次
- [ ] SAM2 session 内只加载一次
- [ ] 不再每帧 spawn worker
- [ ] SAM2 输出真实 mask
- [ ] mask 能进入主程序
- [ ] SAM2 refined bbox 生效

## RGB-D

- [ ] DepthObjectLocalizer 优先 mask depth
- [ ] 无 mask 时 bbox fallback
- [ ] 使用同帧 depth
- [ ] 使用图像 timestamp
- [ ] 输出 camera_xyz
- [ ] 输出 map_xyz
- [ ] 输出 simple 3D box

## Semantic Map

- [ ] 新增轻量 SemanticObjectMap
- [ ] 只有有效 3D object 才写地图
- [ ] 一个静态物体多次观测不会不断创建新 ID
- [ ] 两个同类物体不会错误合并
- [ ] position 使用简单 EMA
- [ ] box size 使用简单 EMA
- [ ] SLAM map correction 时可 reproject

## WebUI

- [ ] 原有 3D point map 完全保持
- [ ] 地图可画 object 3D box
- [ ] box 有 label
- [ ] label 有 confidence / observation count
- [ ] 不新建复杂 UI framework

## LLM

- [ ] Grounding prompt 任务级生成
- [ ] 不每帧调用大模型
- [ ] 没有 candidate 时不调用 target verifier
- [ ] candidate 出现后才调用 VLM/LLM
- [ ] target confirmed 后继续使用现有 approach

## 性能

- [ ] worker spawn = 1/session
- [ ] GroundingDINO load = 1/session
- [ ] SAM2 load = 1/session
- [ ] warm latency 比旧版下降至少约 40%
- [ ] semantic pipeline 至少约 2Hz，或达到现有事件驱动节奏
- [ ] SLAM/运动线程不等待 VLM

## 质量

- [ ] object center RMS <= 0.15m
- [ ] object center P95 <= 0.25m
- [ ] 单物体 30 detections -> 1 confirmed instance
- [ ] 两个同类测试物 -> 2 confirmed instances
- [ ] false_split = 0
- [ ] false_merge = 0
- [ ] 3D box size 相对标准差 <= 25%
- [ ] 目标/干扰 crop 验收集正确率 >= 90%

## Regression

- [ ] 原 SLAM 三维地图没有被改
- [ ] map quality 不恶化
- [ ] targeted pytest PASS
- [ ] full pytest PASS 或仅保留明确 baseline unrelated failure
- [ ] compileall PASS

---

# 41. 失败后的修改顺序

如果测试失败，Coding Agent 按原因修，不乱加机制。

## GroundedSAM 仍然慢

先查：

```text
是不是仍在每帧 load_model
是不是仍在每帧 build_sam2
是不是图片磁盘 IO 占主要时间
SAM2 是否对低分 bbox 也运行
```

不要先上多进程。

---

## Object depth 漂

先查：

```text
mask 是否正确
RGB-depth 是否同帧
depth scale
SAM2 mask 是否包含背景
```

不要先调 merge distance。

---

## Object map_xyz 漂

先查：

```text
image_timestamp
SLAMBridge timestamp pose
camera extrinsic
```

不要用大 merge radius 掩盖。

---

## 同一物体重复

如果观测点本身聚得很好：

```text
修 SemanticObjectMap merge
```

如果观测点散得很开：

```text
先修定位
```

---

## 两个同类被合并

减小：

```text
merge_distance
```

并利用现有 tracker simultaneous IDs。

不要加 ReID 神经网络。

---

## VLM 太慢

确认：

```text
是不是只对 candidate crop 调用
```

不要换整个架构。

---

# 42. 本次最终文件变化应该很克制

理想情况：

### 新增

```text
app/mapping/semantic_object_map.py
scripts/benchmark_grounded_sam.py
tests/test_semantic_object_map.py
```

### 修改

```text
app/detectors/base.py
app/detectors/grounded_sam_worker.py
app/detectors/grounded_sam_subprocess.py
app/perception/depth_object_localizer.py
app/live_robot/semantic_observer.py
app/config.py
app/mapping/map_quality_evaluator.py
当前 3D WebUI 对应后端/前端文件
现有相关 tests
```

如果 Coding Agent 最后新增了：

```text
20 个新 Python 模块
5 个 abstraction layer
3 个新 server
```

说明方案偏离了本计划。

---

# 43. 最终完成后的实际效果

机器狗开始搜索：

```text
三维 SLAM 地图：
一直实时建立，不停
```

视觉线程：

```text
GroundingDINO:
chair
table
trash can
monitor
...
```

SAM2：

```text
准确分出每个物体区域
```

D435：

```text
把 mask 变成真实 3D 位置和尺寸
```

地图：

```text
══════════════════════════════════

       ┌───────────┐
       │ chair_001 │
       │   3D Box  │
       └───────────┘

                      ┌──────────────┐
                      │ trash_can_001│
                      │    3D Box    │
                      └──────────────┘

══════════════════════════════════
```

机器人继续移动时：

```text
chair_001
```

不会变成：

```text
chair_001
chair_002
chair_003
chair_004
...
```

用户目标如果是：

```text
“蓝色垃圾桶”
```

GroundingDINO + SAM2 负责：

```text
发现并定位垃圾桶候选
```

大模型只负责：

```text
这个是不是用户真正要找的蓝色垃圾桶？
```

如果确认：

```text
进入现有 target approach
```

而不是让大模型一直承担实时视觉扫描。

---

# 44. 给 Coding Agent 的最终执行提示词

把下面这段连同本计划一起交给 Coding Agent：

```text
你正在修改 BROVVV/robot_scene_demo-go2w-deploy 当前本地 main 工作区。

当前三维建图已经稳定，本任务严禁替换或重构 SLAM。
不要引入 LIO-SAM、FAST-LIO、视觉 SLAM，也不要重新设计 map/odom/base_link。

严格执行《GO2W GroundingDINO + SAM2 三维语义标注轻量化改造计划》。

本次目标是：
1. 把现有 GroundedSAM 从“每次 subprocess 推理都重新加载模型”改成 session 内模型常驻；
2. 让 SAM2 的真实 mask 被返回，而不是只保留 mask_area_ratio；
3. 让现有 DepthObjectLocalizer 使用 SAM2 mask + D435 depth 得到更准确的 object 3D center 和简单 AABB 3D box；
4. 使用现有 SLAMBridge 按图像 timestamp 把物体投影到稳定 map 坐标；
5. 新增一个轻量 SemanticObjectMap，用同 label + 3D distance + 现有 tracker identity 做简单去重和 EMA 融合；
6. 在现有三维地图 WebUI 上画 3D box + label；
7. GroundingDINO/SAM2 负责高频视觉发现，大模型只在出现可能目标 candidate 时做最终目标验证；
8. 不允许大模型每帧分析完整图像；
9. 保留现有 autonomous search / clearance / recovery / target approach，不重构这些模块。

禁止过度编程：
- 不建微服务；
- 不建数据库；
- 不建通用 plugin/registry/framework；
- 不建 worker pool；
- 不加复杂 watchdog；
- 不加 ReID/CLIP/vector DB；
- 不加 scene graph 新系统；
- 不添加无关 abstraction。
能在当前文件里直接改就直接改。
新增主要业务模块最多一个：app/mapping/semantic_object_map.py。

禁止防御性编程堆叠：
- 不做无限 retry；
- 不做 restart supervisor；
- worker 错误直接清晰抛出。

实现前先跑现有 GroundedSAM、Depth、semantic observer、map evaluator tests 作为 baseline。

实现后必须验证：
- GroundingDINO load_count = 1/session；
- SAM2 load_count = 1/session；
- worker_spawn_count = 1/session；
- warm median inference latency 比旧实现至少下降约 40%；
- SAM2 mask depth 合成测试误差 <= 0.10m；
- 同一静态物体多视角 map center RMS <= 0.15m，P95 <= 0.25m；
- 同一物体 30 次 detection 最终只有 1 个 confirmed semantic instance；
- 两个相距 >1m 的同类真实物体最终仍是 2 个 instances；
- false_split = 0；
- false_merge = 0；
- 3D box size 相对标准差 <= 25%；
- 目标/distractor crop 验收集至少 18/20 判断正确；
- 原有 SLAM map quality 指标不恶化；
- targeted pytest、full pytest、compileall 通过。

如果上述任一项未通过，继续定位并修改对应模块，直到通过；不要通过调大 merge radius、降低检测门槛或增加复杂框架掩盖根因。
```

---

# 45. 最终原则

本次只做一件简单而有效的事情：

> **保留已经稳定的三维 SLAM，把原本需要大模型频繁做的视觉扫描工作下放给 GroundingDINO + SAM2；利用 D435 把分割结果直接变成三维 object box，挂到现有地图；大模型退到低频的“最终目标确认”角色。**

最终分工固定为：

```text
现有 SLAM
= 建地图、给坐标

GroundingDINO
= 找物体

SAM2
= 分物体

D435 Depth
= 算距离、3D center、3D box

SemanticObjectMap
= 简单去重和位置融合

LLM/VLM
= 判断这个候选是不是用户真正要找的目标
```

不再增加其它体系。
