# GO2W RAM++ + GroundingDINO + SAM2 + VLM 双通路语义感知详细实施计划

> **项目**：`BROVVV/robot_scene_demo`  
> **目标分支**：`fix/go2w-autonomous-search-lio-topology-target`  
> **计划日期**：2026-09-11  
> **用途**：直接交给 Coding Agent / AI 执行。  
>
> **本计划只改视觉语义感知链。**  
> 当前三维建图已经稳定，**不允许更换或重构 SLAM，不允许引入 LIO-SAM / FAST-LIO，不允许重写现有三维地图链路。**
>
> 最终目标是：
>
> 1. RAM++ 低频自动发现当前画面里的大量常见物体类别；
> 2. GroundingDINO 使用“RAM++ 场景词 + 当前任务强制目标词 + 上下文物体词”定位物体；
> 3. SAM2 继续负责 mask；
> 4. 现有 D435 Depth + `DepthObjectLocalizer` 继续负责三维物体定位和 3D Box；
> 5. 现有 `SemanticObjectMap` 继续负责多帧融合、持久 object ID 和去重；
> 6. VLM 不再承担每帧场景枚举，但保留：
>    - 复杂属性判断；
>    - 关系判断；
>    - 稀有目标复核；
>    - GroundingDINO/RAM++ 长时间无候选时的低频整图漏检兜底；
> 7. “黄色椅子”“挂着书包的椅子”“特殊设备盒”等复杂目标，不能因为 RAM++ 没识别到完整语义就被漏掉。

---

# 0. 给 Coding Agent 的硬性执行规则

在修改代码前先完整阅读本计划和被点名的现有文件。

必须遵守：

1. **以当前本地工作区为真实基线。**
   - 用户可能有尚未提交的修改。
   - 开始前记录：
     ```bash
     git status
     git diff --stat
     git branch --show-current
     git rev-parse HEAD
     ```
   - 不覆盖、不回滚与本计划无关的本地修改。

2. **禁止改当前已经稳定的三维建图。**
   以下模块原则上只读：
   ```text
   plain_slam_ros2/
   go2w_plain_slam_bridge/
   app/spatial/slam_web_map_state.py
   app/slam/slam_bridge.py
   app/spatial/plain_slam_spatial_provider.py
   ```
   除非只是为了读取现有输出做极小接口接线，否则不要修改。

3. **禁止重写当前 Grounded-SAM 基础设施。**
   当前分支已经有：
   - persistent `Popen` worker；
   - `--serve` JSONL 模式；
   - GroundingDINO 一次加载；
   - SAM2 一次加载；
   - `mask_rle`；
   - D435 mask depth；
   - 3D box；
   - timestamp-aware map transform。

   **不要重复实现这些能力。**

4. **禁止新建第二套 Semantic Map。**
   必须继续使用：
   ```text
   app/spatial/semantic_object_map.py
   ```

5. **禁止工业化过度设计。**
   不要引入：
   - gRPC；
   - Redis；
   - Kafka；
   - Celery；
   - 新 HTTP inference service；
   - 新数据库；
   - worker pool；
   - 多级 scheduler；
   - heartbeat/watchdog；
   - 复杂 retry framework；
   - 新的 world model 框架。

6. **禁止防御性编程泛滥。**
   - 不为理论上每种异常写一层 fallback；
   - 不加大量 try/except；
   - 不增加复杂状态机；
   - 使用现有错误处理风格；
   - RAM++ 不可用时明确报错或按配置关闭，不要偷偷切换到十种方案。

7. **新增文件越少越好。**
   推荐最多新增：
   ```text
   app/detectors/ram_plus.py
   ```
   如果直接在现有 Grounded-SAM worker 中实现更清楚，也可以不新增该文件。

8. **修改后必须自己跑测试。**
   不能只给代码不验证。

---

# 1. 当前代码已经完成的能力：不要重复造轮子

Coding Agent 必须先确认以下事实。

---

## 1.1 Grounded-SAM 已经是常驻 worker

现有：

```text
app/detectors/grounded_sam_subprocess.py
```

已经维护：

```python
self._worker: subprocess.Popen | None
```

并通过：

```python
_ensure_worker()
```

只在 worker 不存在时：

```python
subprocess.Popen(...)
```

启动：

```text
grounded_sam_worker.py --serve
```

因此：

> **本次绝对不要再写“把 subprocess.run 改成 Popen”的代码。已经改好了。**

---

## 1.2 GroundingDINO / SAM2 已经只加载一次

现有：

```text
app/detectors/grounded_sam_worker.py
```

已经明确支持：

```text
--serve
```

启动时：

```python
grounding_model = load_model(...)
sam2_predictor = _build_sam2_predictor(...)
```

之后循环读 stdin JSON request。

所以：

> **本次只需要把 RAM++ 也加载到这个长期存活的视觉 worker 生命周期里，或者做一个同样简单的常驻 RAM++ wrapper。**

优先方案见第 5 节。

---

## 1.3 SAM2 mask 已经真正进入三维定位链

当前：

```python
DetectedObject
```

已经有：

```python
mask_area_ratio
mask_rle
mask_size
```

`grounded_sam_worker.py` 已经输出：

```text
mask_rle
mask_size
refined bbox
```

---

## 1.4 `DepthObjectLocalizer` 已经支持 mask depth 和 3D Box

现有：

```text
app/perception/depth_object_localizer.py
```

已经：

- 解码 `mask_rle`；
- 从 SAM2 mask 内抽取 D435 depth；
- 过滤背景深度；
- 得到 mask foreground 3D points；
- 输出：
  ```text
  camera_xyz
  map_xyz
  box_center_camera
  box_size_camera
  box_center_map
  box_size_map
  ```
- 使用 p05/p95 等方式估计简单 3D extent。

所以：

> **不要重新写 SAM2→Depth→3D Box。**

---

## 1.5 GroundingDINO 已经支持 dynamic terms

现有：

```python
GroundedSAMSubprocessDetector.detect_with_dynamic_terms(
    image_path,
    target_text,
    dynamic_terms,
)
```

而 `_build_prompts()` 已经会合并：

```text
TargetProfile.detector_terms()
+
dynamic_terms
+
TargetProfile.context_labels_en
```

因此 RAM++ 最适合的接入方式就是：

```text
RAM++ tags
→ dynamic_terms
→ 现有 detect_with_dynamic_terms()
```

不需要新写 Grounding prompt pipeline。

---

## 1.6 TargetProfile 已经支持复杂目标

现有：

```text
app/reasoning/target_profile.py
```

已经有：

```text
primary_labels_en
aliases_en
attributes
colors
materials
affordances
relation_constraints
context_labels_en
negative_terms
possible_confusions
```

而：

```python
detector_terms()
```

已经会把：

```text
yellow + chair
```

组合成：

```text
yellow chair
```

同时仍保留：

```text
chair
```

因此：

> **“黄色椅子”无需新做任务解析器。**

---

## 1.7 CropVerifier 已经能检查属性与关系

现有：

```text
app/detectors/crop_verifier.py
```

已经把：

```text
目标描述
核心实体
英文词
属性
颜色
空间关系
混淆物
上下文
```

全部给 VLM。

输出已经有：

```text
target_match_score
category_score
attribute_score
context_score
observed_attributes
missing_required_attributes
```

所以：

> **VLM 复杂目标复核能力已经存在，本次只需要调整“什么时候调用”和“给它什么候选上下文”。**

---

## 1.8 AsyncSemanticObserver 已经会丢弃过期帧

现有：

```text
app/live_robot/async_semantic_observer.py
```

已经：

```text
inference 正忙
→ 新 frame 进来
→ 只保留最新 pending frame
```

不要新建 queue/scheduler。

---

# 2. 本次真正要解决的问题

现在系统有两个感知目标，但不能混为一条链。

---

## 2.1 目标 A：给三维地图尽可能多地贴普通物体标签

例如：

```text
chair
table
monitor
keyboard
plant
backpack
printer
trash can
lamp
cabinet
...
```

这件事要求：

```text
低成本
自动发现
不需要用户提前给 vocabulary
```

由：

```text
RAM++
→ GroundingDINO
→ SAM2
→ D435
→ SemanticObjectMap
```

负责。

---

## 2.2 目标 B：用户指定目标绝对不能只依赖 RAM++

例如：

```text
黄色椅子
挂着书包的椅子
带两个红色接口的设备盒
饮水机旁边的蓝色垃圾桶
```

这件事必须独立于 RAM++。

目标通路：

```text
用户任务
→ 现有 TargetProfileResolver
→ TargetProfile.detector_terms()
→ 强制送进 GroundingDINO
→ 候选 crop
→ VLM CropVerifier
```

因此：

> **RAM++ 漏掉目标类别，不代表目标搜索失败。**

---

# 3. 最终架构：双通路，不是单链

实现后必须是：

```text
                         用户任务
                            │
                            ▼
                 现有 TargetProfileResolver
                            │
              ┌─────────────┼─────────────┐
              │             │             │
          主类别词        属性词       上下文物体
          chair          yellow        backpack
              │             │             │
              └─────────────┬─────────────┘
                            │
                    mandatory target terms
                            │
                            │
RGB ─────────────► RAM++    │
                   │        │
                   ▼        │
            scene tags      │
                   │        │
                   └───┬────┘
                       ▼
             final Grounding terms
                       │
                       ▼
                GroundingDINO
                       │
                      boxes
                       │
                       ▼
                     SAM2
                       │
                     masks
                       │
                       ▼
                  D435 Depth
                       │
                       ▼
                3D objects / boxes
                       │
                       ▼
              SemanticObjectMap
                       │
                       ▼
                  3D Web Map

                      同时

             target-like candidates
                       │
                       ▼
               existing CropVerifier
                       │
                 attributes / relation
                       │
                       ▼
                    target?

如果长期没有 target candidate：
                       │
                       ▼
              low-frequency full-frame
                   VLM target sweep
```

---

# 4. 三类词必须明确分开

不要把所有 prompt term 混成同一种优先级。

---

## 4.1 `target_mandatory_terms`

来源：

```python
TargetProfile.detector_terms()
```

以及必要的：

```python
TargetProfile.context_labels_en
GroundingPromptPlan.direct_terms_en
GroundingPromptPlan.proxy_object_terms_en
GroundingPromptPlan.context_anchor_terms_en
```

这些词：

> **RAM++ 无论有没有输出，都必须进入 GroundingDINO。**

例如：

用户：

```text
找黄色椅子
```

mandatory：

```text
chair
yellow chair
seat
```

用户：

```text
找挂着书包的椅子
```

mandatory：

```text
chair
seat
backpack
bag
```

---

## 4.2 `ram_scene_terms`

来源：

```text
RAM++
```

用于建立丰富场景语义地图。

例如：

```text
monitor
keyboard
lamp
plant
picture frame
backpack
chair
table
printer
```

这些词：

- 可以被 RAM++ 漏掉；
- 不影响 target mandatory；
- 每隔一段时间更新。

---

## 4.3 `base_terms`

现有项目的固定 vocabulary 不必删除。

但不要把一大堆固定词永远塞进去。

只保留现有逻辑作为：

```text
RAM++ disabled / first frame before RAM tags ready
```

或少量高价值基础对象。

不要重新维护巨大 ontology。

---

# 5. RAM++ 的最小接入方式

推荐方案：

> **把 RAM++ 加到现有 `grounded_sam_worker.py --serve` 进程中，和 GroundingDINO/SAM2 共用一个长期 worker。**

原因：

- 当前 worker 已经是 persistent；
- 已有 stdin/stdout JSONL；
- 已经在 GPU perception 环境；
- 不需要增加第二个 daemon；
- 不需要新增 IPC；
- 最少代码。

---

## 5.1 新依赖

在现有 `full` profile 中增加：

```text
recognize-anything
RAM++ checkpoint
```

优先使用官方：

```text
xinyu1205/recognize-anything
```

模型：

```text
ram_plus_swin_large_14m.pth
```

不要训练。

---

## 5.2 bootstrap 修改

检查：

```text
scripts/bootstrap_fresh_machine.sh
scripts/verify_fresh_deployment.sh
```

现有 `--profile=full` 已经安装：

```text
Grounded-SAM-2
GroundingDINO
SAM2
```

在 **full profile** 里再增加：

```text
clone/install recognize-anything
download/check RAM++ checkpoint
```

不要影响：

```text
core
go2w
```

profile。

---

## 5.3 不把模型权重提交到 Git

只保存：

```text
路径配置
下载逻辑
校验逻辑
```

权重继续本地。

---

# 6. `grounded_sam_worker.py` 的具体修改

---

## 6.1 新增参数

建议只加：

```text
--ram-root
--ram-checkpoint
--disable-ram-plus
```

不要再加十几个参数。

RAM++ image size 使用：

```text
384
```

直接常量或一个现有配置字段即可。

---

## 6.2 worker 启动时加载 RAM++

伪代码：

```python
ram_model = None
ram_transform = None

if not args.disable_ram_plus:
    ram_model, ram_transform = _build_ram_plus(
        args.ram_root,
        args.ram_checkpoint,
        device,
    )
```

使用官方 API：

```python
from ram.models import ram_plus
from ram import inference_ram, get_transform

transform = get_transform(image_size=384)

model = ram_plus(
    pretrained=checkpoint,
    image_size=384,
    vit="swin_l",
)
model.eval()
model = model.to(device)
```

---

## 6.3 不要每帧重新加载

硬性测试：

```text
worker 生命周期：
RAM++ model init = 1
GroundingDINO init = 1
SAM2 init = 1
```

---

# 7. 扩展现有 JSONL worker protocol

不要建立新 protocol 文件。

当前 detect request：

```json
{
  "image": "...",
  "text_prompt": "...",
  "box_threshold": 0.12,
  "text_threshold": 0.10,
  "max_objects": 20
}
```

增加一个可选字段：

```json
{
  "operation": "detect"
}
```

默认缺省仍视为：

```text
detect
```

保持旧测试兼容。

---

## 7.1 RAM request

新增：

```json
{
  "operation": "tag",
  "image": "/path/frame.jpg"
}
```

返回：

```json
{
  "tags_en": [
    "chair",
    "table",
    "monitor",
    "backpack",
    "plant"
  ],
  "tags_zh": [
    "椅子",
    "桌子",
    "显示器",
    "书包",
    "植物"
  ]
}
```

不要返回 embedding。

不要存 feature vector。

---

# 8. RAM++ 输出必须做轻量过滤

RAM++ 会输出：

```text
chair
lamp
gray
green
living room
sit
wood floor
...
```

不是每个 tag 都应该进入 GroundingDINO。

---

## 8.1 不新增 NLP 模型

只做简单规则。

可以放在：

```text
app/detectors/vocabulary.py
```

新增：

```python
filter_ram_scene_terms(tags: list[str], max_terms: int) -> list[str]
```

---

## 8.2 小型 stoplist

删除明显不是独立物体的词：

```text
indoor
outdoor
room
home
background
image
photo

sit
stand
lay
hang

gray
green
yellow
black
white
red
blue

wood
metal
plastic
```

注意：

> 颜色不能从 TargetProfile 中删，只删除 **RAM scene tags 中单独出现的纯属性词**。

---

## 8.3 不要删除这些

即使不在现有 vocabulary，也保留：

```text
1~4 个英文单词
不是 stoplist
```

因为 RAM++ 的意义就是扩大开放物体覆盖。

例如：

```text
picture frame
wall lamp
fire cabinet
network switch
tool box
```

不要因为“不在 COMMON_OBJECT_VOCAB”就删除。

否则又退回封闭词表。

---

## 8.4 去重

统一：

```text
lowercase
strip
空白归一
完全字符串去重
```

即可。

不要做 embedding similarity。

---

# 9. `GroundedSAMSubprocessDetector` 增加一个方法

在：

```text
app/detectors/grounded_sam_subprocess.py
```

增加：

```python
def discover_scene_terms(self, image_path: str) -> list[str]:
    ...
```

逻辑：

```text
ensure worker
→ operation=tag
→ tags_en
→ filter_ram_scene_terms()
→ return
```

不要新增新的 detector class hierarchy。

---

# 10. RAM++ 不能每帧跑

RAM++ 的职责是：

> “当前视野里大概出现了哪些类别？”

不是 tracking。

第一版推荐：

```text
每 1.5~2.0 秒最多跑一次
```

即：

```text
0.5~0.7 Hz
```

如果实测 GPU 余量充足，可以调到：

```text
1 Hz
```

但不要每 camera frame 运行。

---

# 11. 缓存 RAM scene tags

不要创建 scheduler。

直接在现有搜索/感知 orchestrator 保存：

```python
self._cached_ram_terms: list[str] = []
self._last_ram_tag_time: float = 0.0
```

位置优先选择：

```text
app/live_robot/live_search_pipeline.py
```

如果实际 detector 调用发生在其他现有函数，就在那个最小作用域保存。

---

## 11.1 刷新逻辑

```python
if now - self._last_ram_tag_time >= ram_scene_tag_interval_seconds:
    self._cached_ram_terms = detector.discover_scene_terms(image_path)
    self._last_ram_tag_time = now
```

否则：

```text
复用 cached terms
```

不要建后台 RAM queue。

---

# 12. Grounding prompt 合并规则

现有 `GroundedSAMSubprocessDetector._build_prompts()` 已经会自动加入 TargetProfile。

因此调用时只需要：

```python
detector.detect_with_dynamic_terms(
    image_path,
    target_text,
    dynamic_terms=self._cached_ram_terms,
)
```

不要在外面再拼一次 `target_profile.detector_terms()`，避免重复。

---

## 12.1 目标词优先级必须高于 RAM tags

如果 `grounding_prompt_max_terms` 有上限：

排序必须：

```text
1. target direct terms
2. target context/proxy terms
3. RAM scene terms
4. static baseline terms
```

**绝不能因为 RAM++ 返回很多 tag，把用户目标词截掉。**

---

# 13. 修改 `GroundingPromptPlanner` 时只做最小调整

现有：

```text
app/perception/grounding_prompt_planner.py
```

已经负责任务相关 prompt。

如果其 `max_terms` 截断发生在 RAM terms merge 之前：

```text
不改。
```

如果 RAM terms merge 后总 prompt 会超过合理长度：

只在：

```text
grounded_sam_subprocess.py
```

最终 terms 组装处做：

```text
mandatory target terms 永不截断
RAM terms 截到 N 个
```

不要重写 `GroundingPromptPlanner`。

---

# 14. “黄色椅子”完整执行逻辑

必须通过该测试。

用户：

```text
找黄色椅子
```

现有 TargetProfile 应得到类似：

```text
primary = chair
colors = yellow
detector_terms:
  chair
  yellow chair
```

假设 RAM++ 当前只输出：

```text
table
monitor
plant
```

最终 GroundingDINO prompt 仍必须包含：

```text
chair
yellow chair
table
monitor
plant
```

GroundingDINO 检出：

```text
chair_1
chair_2
```

然后：

```text
CropVerifier
```

判断：

```text
chair_1:
attribute_score=0.2
is_target=false

chair_2:
attribute_score=0.95
is_target=true
```

RAM++ 是否知道“黄色椅子”不影响目标搜索。

---

# 15. “挂着书包的椅子”完整执行逻辑

必须通过该测试。

用户：

```text
找挂着书包的椅子
```

TargetProfile：

```text
base:
  chair

context:
  backpack

relation:
  backpack hanging on chair
```

最终 GroundingDINO 必须至少搜索：

```text
chair
backpack
```

---

## 15.1 不要求 GroundingDINO 判断 relation

GroundingDINO 只负责：

```text
chair bbox
backpack bbox
```

---

## 15.2 先做非常便宜的几何 context

对于每个 chair candidate：

检查同帧 backpack：

```text
bbox overlap
或
center distance
或
expanded chair bbox 是否包含 backpack center
```

只需简单 2D 几何。

---

## 15.3 VLM crop 必须包含关系上下文

当前 `crop_verify_expand_ratio` 已存在。

对于 relation target：

不要只裁 chair 紧边界。

使用：

```text
chair bbox 与附近 backpack bbox 的 union
```

再加少量 margin。

把 context 传给现有：

```python
CropVerifier.verify_crop(..., context=...)
```

context：

```json
{
  "nearby_objects": [
    {
      "label": "backpack",
      "bbox": [...]
    }
  ],
  "required_relations": [
    "backpack hanging on chair"
  ]
}
```

不要新建 relation reasoner。

---

# 16. 普通场景物体不要进 VLM

对于：

```text
chair
table
monitor
lamp
plant
```

只要不是当前 target candidate：

```text
RAM++
→ GroundingDINO
→ SAM2
→ Depth
→ SemanticObjectMap
```

结束。

不要调用 VLM。

---

# 17. 当前 full-scene VLM scene enumeration 要降级

现有：

```text
semantic_observer.py
```

逻辑中，如果 quick target 判断没有 object list：

```text
会 fall through 到 full-scene VLM
去列出所有可见物体
```

加入 RAM++ 后，Grounded-SAM 模式应改为：

```text
RAM++ + GroundingDINO 的 scene_objects
=
普通场景语义来源
```

因此：

> **Grounded-SAM + RAM++ 模式下，不再为了“给地图列普通物体”调用 full-scene VLM。**

---

# 18. 但是 VLM 不能彻底放到最后

为了防止：

```text
RAM++ 漏掉稀有目标
+
GroundingDINO 目标词仍没产生 candidate
```

保留一个：

> **低频 full-frame target sweep**

注意：

它不是：

```text
“请列出画面所有东西”
```

而是：

```text
“当前整张图里是否存在符合 TargetProfile 的目标，
包括稀有类别、颜色、属性、关系？
如果可能存在，指出候选区域和可见证据。”
```

---

# 19. 复用现有 VLM daemon，不新增服务

现有：

```text
siliconflow_vision_protocol.py
```

已经有：

```text
MODE_QUICK
MODE_SEMANTIC
MODE_VERIFY
```

本次不要再加一个 daemon。

优先复用：

```text
MODE_QUICK
```

配：

```text
target_profile.prompt_context()
+
额外 target-sweep 指令
```

如果现有 quick 模式无法返回可用候选区域：

才在同一个 protocol 中增加：

```text
MODE_TARGET_SWEEP
```

但优先避免。

---

# 20. VLM 漏检兜底触发条件

保持简单。

满足任一即可：

```text
1. 连续 4 次 GroundingDINO 都没有 target-like candidate
且
2. 距离上一次 full-frame target sweep >= 10 秒
```

或：

```text
TargetProfile.requires_scene_confirmation == True
且
当前是新的明显视角
```

第一版建议：

```yaml
vlm_target_sweep_interval_seconds: 10.0
vlm_target_sweep_empty_cycles: 4
```

只需两个参数。

---

# 21. VLM sweep 不应该阻塞普通语义地图

继续使用现有异步设施。

```text
RAM/GDINO/SAM2 scene mapping
继续

VLM target sweep
后台低频
```

如果当前 quick VLM 已经在实时链路中：

直接复用已有调用，不新建线程。

---

# 22. VLM sweep 发现疑似稀有目标后怎么办

VLM 不直接宣布任务完成。

它只产生：

```text
target_sweep_candidate
```

例如：

```json
{
  "is_present": true,
  "confidence": 0.72,
  "candidate_description": "桌面右侧黑色设备盒",
  "bbox_2d": [0.62, 0.31, 0.84, 0.68],
  "evidence": [
    "黑色矩形盒体",
    "可见红色接口"
  ]
}
```

然后：

```text
bbox
→ 现有 crop verification
或
→ SAM2 box prompt
→ D435 depth
→ SemanticObjectMap
```

---

## 22.1 如果 quick VLM 没有 bbox 输出

不要大改 protocol。

可先：

```text
VLM sweep 只返回“可能存在”
↓
触发当前帧 GroundingDINO high-recall retry
并把 target/profile terms 再搜索一次
```

若仍无 candidate：

保持：

```text
possible but unlocalized
```

不能伪造 3D object。

---

# 23. RAM++ 与 VLM 的职责边界

最终必须严格：

| 能力 | RAM++ | GroundingDINO | SAM2 | VLM |
|---|---:|---:|---:|---:|
| 自动发现常见场景类别 | ✅ 主力 | ❌ | ❌ | 低频兜底 |
| 找 bbox | ❌ | ✅ | ❌ | 只做稀有候选 |
| 精确 mask | ❌ | ❌ | ✅ | ❌ |
| 普通 3D 地图标签 | 提供词 | ✅ | ✅ | ❌ |
| 判断黄色/材质/状态 | 不作为最终判断 | 候选级 | ❌ | ✅ |
| 判断“挂在/旁边/属于” | ❌ | 只找组成物体 | ❌ | ✅ |
| 稀有目标漏检兜底 | ❌ | 目标词强制搜索 | ❌ | ✅ |
| 最终目标确认 | ❌ | ❌ | ❌ | ✅ |

---

# 24. SemanticObjectMap 不重写

当前现有：

```text
app/spatial/semantic_object_map.py
```

已经有：

- persistent object IDs；
- observation_count；
- one-to-one association；
- reproject observations；
- multi-view merge。

本次：

> **只验证 RAM++ 增加大量 scene objects 后，去重是否仍然正常。**

如果正常：

```text
不改。
```

只有确实出现：

```text
chair_001
chair_002
chair_003
都是同一张椅子
```

且 map_xyz 本身稳定时：

才小幅调整现有 association threshold。

---

# 25. RAM++ tag 不能直接变成地图 object

关键原则：

```text
RAM++ tag = “画面里可能有这个类别”
```

不是：

```text
SemanticObject
```

必须经过：

```text
RAM tag
→ GroundingDINO bbox
→ SAM2 mask
→ D435 depth
→ valid 3D localization
→ SemanticObjectMap
```

RAM++ 输出：

```text
monitor
```

但 GroundingDINO 找不到 monitor bbox：

```text
不创建 monitor object。
```

---

# 26. 普通物体标签质量

GroundingDINO 返回：

```text
source_prompt_term
```

继续使用。

对于 RAM++ term：

```text
label = GroundingDINO matched label
source_prompt_term = RAM scene term
```

不要让 RAM++ 中文标签直接覆盖 map canonical ID。

---

# 27. 不做复杂 label normalization

只用现有：

```text
label_zh()
category_for_label()
COMMON_OBJECT_VOCAB
```

以及 lowercase cleanup。

不要新增：

- WordNet；
- sentence embedding；
- LLM label merge；
- ontology graph。

---

# 28. 配置新增项：严格控制数量

在：

```text
app/config.py
.env.example
```

只增加必要字段。

建议：

```text
ENABLE_RAM_PLUS=true
RAM_PLUS_ROOT=<recognize-anything path>
RAM_PLUS_CHECKPOINT=<ram_plus_swin_large_14m.pth>
RAM_SCENE_TAG_INTERVAL_SECONDS=2.0
RAM_SCENE_TAG_MAX_TERMS=24

VLM_TARGET_SWEEP_INTERVAL_SECONDS=10.0
VLM_TARGET_SWEEP_EMPTY_CYCLES=4
```

共 7 个。

不要再做几十个 threshold。

RAM++ 使用官方默认 tagging threshold。

---

# 29. `app/config.py` 具体修改

增加 defaults：

```python
DEFAULT_ENABLE_RAM_PLUS = True
DEFAULT_RAM_PLUS_ROOT = ...
DEFAULT_RAM_PLUS_CHECKPOINT = ...
DEFAULT_RAM_SCENE_TAG_INTERVAL_SECONDS = 2.0
DEFAULT_RAM_SCENE_TAG_MAX_TERMS = 24

DEFAULT_VLM_TARGET_SWEEP_INTERVAL_SECONDS = 10.0
DEFAULT_VLM_TARGET_SWEEP_EMPTY_CYCLES = 4
```

加入 `Settings`。

加入 env 解析。

不要新增独立 config class。

---

# 30. `grounded_sam_worker.py` 修改清单

必须：

- [ ] 新增 RAM++ args；
- [ ] startup 加载 RAM++；
- [ ] serve 支持 `operation=tag`；
- [ ] 旧 detect request 不受影响；
- [ ] RAM inference 返回英文/中文 tag；
- [ ] 模型只加载一次；
- [ ] 不改变现有 GroundingDINO/SAM2 detect 结果 schema。

不要动：

```text
mask_rle
SAM2 refine box
NMS
现有 detection threshold
```

除非测试证明有冲突。

---

# 31. `grounded_sam_subprocess.py` 修改清单

必须：

- [ ] `discover_scene_terms(image_path)`；
- [ ] 使用同一个 `_worker`；
- [ ] operation=tag；
- [ ] filter RAM terms；
- [ ] 继续 `detect_with_dynamic_terms()`；
- [ ] target profile 的词仍然自动进入 prompt；
- [ ] target terms 不被 RAM terms 挤掉。

不要：

- [ ] 新启 RAM subprocess；
- [ ] 新增第二个 worker 管理器。

---

# 32. `vocabulary.py` 修改清单

只加：

```python
RAM_SCENE_TAG_STOPLIST = {...}

def filter_ram_scene_terms(
    terms: list[str],
    max_terms: int = 24,
) -> list[str]:
    ...
```

规则：

```text
normalize
drop empty
drop pure attribute/action/scene terms
keep unknown object nouns/phrases
dedupe
truncate
```

不要做 POS tagging。

---

# 33. `live_search_pipeline.py` 修改清单

找到当前 Grounded-SAM inference 入口。

增加：

```python
_cached_ram_terms
_last_ram_tag_time
_target_empty_candidate_cycles
_last_vlm_target_sweep_time
```

这是足够的全部状态。

不要新建 class。

---

## 33.1 每个语义 inference cycle

伪代码：

```python
if ram_plus_enabled and ram_refresh_due:
    self._cached_ram_terms = detector.discover_scene_terms(image_path)
    self._last_ram_tag_time = now

detections = detector.detect_with_dynamic_terms(
    image_path,
    target_text,
    dynamic_terms=self._cached_ram_terms,
)
```

后面继续走现有：

```text
SAM2
DepthObjectLocalizer
Semantic graph
SemanticObjectMap
```

---

# 34. candidate classification

当前检测结果需要分：

```text
scene object
target-like object
context object
```

不要新增新 schema。

可以通过现有：

```text
label
source_prompt_term
TargetProfile.direct_terms()
TargetProfile.context_terms()
```

判断。

---

## 34.1 普通 scene object

直接进入地图。

---

## 34.2 direct target candidate

进入：

```text
CropVerifier
```

---

## 34.3 context object

例如：

```text
backpack
```

对于：

```text
挂着书包的椅子
```

它既：

```text
进入 SemanticObjectMap
```

又：

```text
提供给 chair candidate 的 VLM context
```

---

# 35. relation candidate 的简单几何组装

只需要新增一个小 helper。

推荐放：

```text
live_search_pipeline.py
```

而不是新建 relation module。

例如：

```python
def nearby_context_objects(
    candidate,
    detections,
    target_profile,
) -> list[DetectedObject]:
    ...
```

判断：

```text
same frame
label matches context terms
expanded bbox overlap / center near
```

---

# 36. CropVerifier 修改原则

优先不改它的 JSON schema。

现有已经能判断：

```text
attribute_score
context_score
```

可能只需：

- 给 relation target 扩大 crop；
- 把 nearby objects 放入 context；
- prompt 增加一句：
  ```text
  若目标包含关系约束，必须同时看到主体和关联物体证据，
  不能只看到其中一个就判定 true。
  ```

这是小改。

---

# 37. VLM target sweep 的实现位置

优先复用：

```text
app/detectors/siliconflow_vision_protocol.py
app/detectors/siliconflow_vision_worker.py
```

现有：

```text
MODE_QUICK
```

已经是低 token target 判断。

先尝试：

```text
MODE_QUICK
+
extra_instructions
```

完成。

不要先创建 `MODE_TARGET_SWEEP`。

---

# 38. target sweep prompt

可以直接使用：

```text
TargetProfile.prompt_context()
```

再追加：

```text
这是漏检兜底检查，不需要列出全部场景物体。
只回答当前整图是否存在符合目标画像的可见候选。
尤其检查 GroundingDINO 可能漏掉的稀有类别、颜色、部件、状态和物体关系。
不确定则 is_present=false 或降低 confidence。
```

---

# 39. 什么时候触发 VLM sweep

在 `live_search_pipeline.py` 使用：

```python
if not target_like_candidates:
    self._target_empty_candidate_cycles += 1
else:
    self._target_empty_candidate_cycles = 0
```

当：

```python
empty_cycles >= configured_limit
and now - last_sweep >= configured_interval
```

触发一次。

---

# 40. VLM sweep 命中后的动作

优先使用现有 quick payload 的：

```text
matched_indices
scene_objects
target_decision
```

如果 payload 已有 bbox object：

```text
把 bbox 作为 VLM-generated target candidate
→ crop verifier / depth localization
```

如果没有 bbox：

```text
只触发 GroundingDINO high-recall target retry
```

不要把没有空间定位的 VLM 文本直接写成 3D map object。

---

# 41. `semantic_observer.py` 修改

当前 quick target absence 没 object list 时会：

```text
fall through
→ full scene analysis
→ VLM 枚举所有可见 object
```

在：

```text
Grounded-SAM + RAM++ enabled
```

时改成：

> **不要因为 scene object list 为空而自动让 VLM 全场景枚举。**

普通 object map 的职责已转给：

```text
RAM++ → GroundingDINO → SAM2
```

VLM 只走 target sweep。

---

# 42. 语义地图 coverage 和目标 recall 必须分开测试

这次不能只看“识别多少 object”。

必须两个指标：

```text
scene semantic coverage
target recall
```

---

# 43. 测试数据集

准备最少 6 类静态图片/录制帧。

可以放：

```text
tests/fixtures/ram_semantic/
```

如果仓库不适合提交图片，就用本地 `data/eval` 并写脚本。

---

## Scene A：普通办公室

真实有：

```text
chair
table
monitor
keyboard
mouse
plant
backpack
cup
```

目标：

```text
RAM++ 至少给出大部分物体词
GroundingDINO 能定位主要实例
```

---

## Scene B：RAM++ 属性混杂

RAM++ 可能输出：

```text
chair
gray
green
room
sit
wood floor
lamp
```

过滤后：

```text
chair
lamp
...
```

属性/动作不能污染 DINO prompt 太严重。

---

## Scene C：黄色椅子

RAM++ 故意 mock 为：

```text
table
monitor
plant
```

**不包含 chair。**

TargetProfile：

```text
yellow chair
```

要求：

```text
最终 Grounding prompt 仍包含 chair / yellow chair
```

并能走到 VLM attribute verify。

---

## Scene D：挂书包的椅子

图中：

```text
chair A：没有书包
chair B：挂着 backpack
```

要求：

```text
GroundingDINO 找到 chair + backpack
VLM 最终只确认 chair B
```

---

## Scene E：稀有设备

RAM++ mock 不包含目标。

GroundingDINO 第一轮也无 candidate。

连续空 candidate 达阈值：

```text
VLM full-frame target sweep 触发
```

必须证明 VLM 旁路存在。

---

## Scene F：普通搜索，无目标

持续 30~60 秒。

要求：

```text
大量普通 objects 进入地图
VLM target sweep 调用低频
VLM 不每帧调用
```

---

# 44. 单元测试清单

---

## 44.1 RAM tag filtering

新增：

```text
tests/test_ram_scene_terms.py
```

输入：

```text
armchair
gray
green
living room
sit
wood floor
picture frame
lamp
backpack
```

要求：

```text
保留：
armchair
picture frame
lamp
backpack

删除：
gray
green
living room
sit
wood floor
```

不要要求 NLP 完美。

---

## 44.2 RAM worker operation

扩展：

```text
tests/test_grounded_sam_persistent_worker.py
```

或新增：

```text
tests/test_grounded_sam_ram_operation.py
```

fake worker 连续：

```text
tag
detect
tag
detect
```

要求：

```text
Popen 仍然只有 1 个
```

---

## 44.3 Target mandatory terms independent of RAM

新增：

```text
tests/test_grounding_prompt_ram_target_merge.py
```

RAM：

```text
table
monitor
plant
```

目标：

```text
黄色椅子
```

最终 prompt 必须含：

```text
chair
yellow chair
```

---

## 44.4 RAM terms 被截断不能影响 target

RAM 产生 100 个 terms。

`max_terms` 限制后：

目标词仍全部存在。

---

## 44.5 Yellow chair VLM verify

mock：

```text
2 chair candidates
```

VLM：

```text
chair A attribute false
chair B attribute true
```

最终只 B 为目标。

---

## 44.6 Relation target

mock：

```text
chair A
chair B
backpack near chair B
```

要求传给 chair B verifier 的 context 包含：

```text
backpack
required relation
```

chair A 不应该有该 context。

---

## 44.7 VLM target sweep trigger

连续：

```text
4 cycles no target candidate
```

时间达到 interval：

```text
VLM quick target sweep call = 1
```

下一周期：

```text
不能马上再次 call
```

---

## 44.8 有 candidate 时不 sweep

只要正常 Grounding target candidate 存在：

```text
empty counter reset
```

---

## 44.9 RAM ordinary objects 不进 VLM

RAM+DINO 返回：

```text
chair
table
monitor
plant
```

目标：

```text
fire extinguisher
```

VLM crop verify：

```text
0
```

普通 object 仍然进入 semantic map。

---

# 45. 必须跑的现有测试

不要猜文件名，先：

```bash
find tests -type f | grep -E \
'grounded|sam|depth|semantic|target_profile|crop|vision|live_search|prompt'
```

至少覆盖：

```text
GroundedSAMSubprocessDetector
grounded_sam_worker
DepthObjectLocalizer
TargetProfile
GroundingPromptPlanner
CropVerifier
SemanticObjectMap
AsyncSemanticObserver
LiveSemanticObserver
LiveSearchPipeline
VLM protocol
```

最后：

```bash
python -m pytest -q
```

---

# 46. 真实模型离线验证

不能只 mock。

准备 20~30 张室内图片。

每张人工记录主要物体。

运行：

```text
RAM++
→ DINO
→ SAM2
```

输出：

```text
ram_tags.json
detections.json
```

统计：

```text
RAM tag recall
DINO localized-object recall
```

---

# 47. 场景语义覆盖验收

不要要求“识别所有东西”。

MVP 目标：

对普通室内明显可见、尺寸合理的主要物体：

```text
scene object category recall >= 70%
```

建议按图片级：

```text
人工标 8 个主要物体
识别/定位 >= 6 个
```

重点是比固定 vocabulary 更丰富。

---

# 48. Target recall 验收必须更高

因为有：

```text
TargetProfile mandatory terms
+
GroundingDINO
+
VLM sweep
```

对测试目标集：

```text
黄色椅子
挂书包的椅子
蓝色垃圾桶
灭火器
打印机
特殊设备盒
```

目标：

```text
target candidate recall >= 90%
```

这里：

```text
candidate recall
```

不是最终确认准确率。

---

# 49. 属性/关系最终确认验收

对复杂目标：

```text
黄色椅子
挂书包的椅子
桌上的红色水杯
饮水机旁边垃圾桶
```

至少设计正负样本。

要求：

```text
VLM 不应只因为 base category 正确就确认。
```

例如：

```text
蓝椅子 ≠ 黄色椅子
普通椅子 ≠ 挂书包椅子
```

---

# 50. VLM 调用效率验收

运行 60 秒正常场景。

统计：

```text
semantic frames
RAM calls
GroundingDINO calls
crop verify calls
full-frame VLM target sweep calls
full-scene enumeration calls
```

目标：

```text
full-scene VLM enumeration calls = 0
```

普通无目标情况下：

```text
VLM full-frame target sweep
<= 6 次 / 60 秒
```

因为 interval 默认 10 秒。

如果普通 scene object 没有目标属性需求：

```text
crop verify = 0
```

---

# 51. RAM++ 调用频率验收

默认：

```text
RAM interval = 2 sec
```

60 秒：

理论最多：

```text
约 30 次
```

不应该等于 camera frame count。

---

# 52. 3D Semantic Map 验收

RAM++ 新发现的普通对象：

```text
lamp
picture frame
backpack
printer
```

经过：

```text
DINO + SAM2 + D435
```

后：

必须能够：

```text
进入 SemanticObjectMap
获得 map_xyz
持续 observation merge
```

---

# 53. 同一物体不能因为 RAM tags 刷新而重复创建

同一 chair：

```text
RAM frame1: chair
RAM frame2: armchair
RAM frame3: chair
```

如果现有 `SemanticObjectMap` 能正常 merge：

保持。

若由于 label synonym 导致：

```text
chair_001
armchair_001
```

不要引入 embedding。

只在：

```text
vocabulary.py
```

增加极少数已知 canonical aliases：

```text
armchair -> chair
cell phone -> phone
television -> tv
```

只对实测重复进行补充。

禁止做通用 ontology。

---

# 54. 性能验收

RAM++ 会增加 GPU 负载，所以必须测。

记录：

```text
RAM inference median ms
GroundingDINO median ms
SAM2 median ms
overall semantic cycle ms
GPU memory before/after RAM load
```

最低要求：

```text
机器狗导航主循环不被阻塞
AsyncSemanticObserver 仍正常 coalesce
```

场景语义不要求实时 30 FPS。

建议目标：

```text
RAM 0.5 Hz
Grounded-SAM semantic updates 1~3 Hz
```

---

# 55. 显存要求的处理原则

不要写复杂自动显存降级。

如果 RAM++ + GroundingDINO + SAM2 同时加载确实 OOM：

Coding Agent：

1. 记录实际 GPU / VRAM；
2. 先确认没有重复模型实例；
3. 确认一个 session 只有一个 worker；
4. 若确实装不下，再将 RAM++ 放到**一个单独常驻进程**。

只有发生真实 OOM 才允许做这个调整。

不要预先做双架构。

---

# 56. 如果必须分 RAM worker

仅 OOM/依赖冲突时：

最多新增：

```text
app/detectors/ram_plus_worker.py
```

使用同样：

```text
stdin/stdout JSONL
persistent Popen
```

不创建网络服务。

但默认计划仍是：

```text
same Grounded-SAM worker
```

---

# 57. 部署位置

RAM++ 应跟：

```text
GroundingDINO
SAM2
```

运行在同一台具备 NVIDIA GPU 的现有视觉计算环境。

不要放到算力弱的机器狗 Python 环境，除非当前 Grounded-SAM 本来就在那里且实测满足性能。

---

# 58. README / 文档改动

只更新必要部分。

说明新的视觉职责：

```text
RAM++:
自动场景 tags

GroundingDINO:
开放词汇定位

SAM2:
实例 mask

D435:
3D localization

VLM:
复杂属性/关系与漏检兜底
```

以及：

```text
RAM++ 不决定任务目标是否存在
```

这句话必须写清楚。

---

# 59. 最终文件修改表

| 文件 | 修改内容 |
|---|---|
| `app/detectors/grounded_sam_worker.py` | 加载 RAM++；JSONL `operation=tag`；保持 detect 不变 |
| `app/detectors/grounded_sam_subprocess.py` | 新增 `discover_scene_terms()`；RAM tags 作为 dynamic terms |
| `app/detectors/vocabulary.py` | RAM tag 轻量过滤、少量 canonical aliases |
| `app/config.py` | 增加 RAM 与 VLM sweep 的少量配置 |
| `.env.example` | 增加对应配置示例 |
| `app/live_robot/live_search_pipeline.py` | 缓存 RAM tags、刷新间隔、empty candidate counter、低频 VLM sweep |
| `app/live_robot/semantic_observer.py` | RAM 模式不再靠 full-scene VLM 枚举普通 object |
| `app/detectors/crop_verifier.py` | 关系目标 prompt/context 小幅增强，如现有能力足够则不改 |
| `scripts/bootstrap_fresh_machine.sh` | full profile 安装 RAM++ repo/依赖/权重 |
| `scripts/verify_fresh_deployment.sh` | full profile 检查 RAM++ 安装和 checkpoint |
| README / 对应部署文档 | 简要说明视觉双通路 |
| tests | 增加本文列出的 targeted tests |

---

# 60. 明确不应该修改的核心模块

除非因为字段传递缺失做极小修复，否则不要动：

```text
app/perception/depth_object_localizer.py
app/spatial/semantic_object_map.py
app/slam/slam_bridge.py
app/spatial/slam_web_map_state.py
plain_slam_ros2/
go2w_plain_slam_bridge/
navigation/
```

原因：

这些能力当前已经存在并且不是本次问题源头。

---

# 61. 实施顺序

Coding Agent 必须按以下顺序。

### Phase 0：Baseline

```bash
git status
git diff --stat
pytest targeted existing tests
```

---

### Phase 1：RAM++ runtime

完成：

```text
bootstrap
worker load
operation=tag
discover_scene_terms
tag filter
```

先测试 RAM 单独工作。

---

### Phase 2：RAM → GroundingDINO

把：

```text
cached RAM scene terms
```

接到现有：

```python
detect_with_dynamic_terms()
```

验证 target mandatory 不会消失。

---

### Phase 3：普通场景 Semantic Map

验证：

```text
RAM tag
→ DINO bbox
→ SAM2 mask
→ existing DepthObjectLocalizer
→ existing SemanticObjectMap
```

完整通过。

---

### Phase 4：复杂目标 VLM verify

测试：

```text
黄色椅子
挂书包的椅子
```

---

### Phase 5：VLM target sweep

加低频漏检旁路。

---

### Phase 6：去掉 Grounded-SAM 模式下普通 full-scene VLM 枚举

确保地图仍由 RAM pipeline 构建。

---

### Phase 7：完整回归

```bash
python -m compileall app tests
python -m pytest -q
```

---

# 62. Coding Agent 自检逻辑

出现问题时只能修对应层。

```text
RAM 漏普通物体
→ 看 RAM tags / filtering

RAM 有 tag 但无 bbox
→ 看 GroundingDINO

有 bbox 但 3D 不对
→ 看现有 SAM2 mask / Depth / timestamp

3D 正确但重复 ID
→ 看现有 SemanticObjectMap association

黄色椅子误判
→ 看 CropVerifier attribute

书包关系误判
→ 看 relation crop/context

稀有目标全漏
→ 看 target mandatory terms + VLM sweep
```

禁止一个问题就重构整个 pipeline。

---

# 63. 最终验证报告

Coding Agent 完成后生成：

```text
reports/RAM_PLUS_SEMANTIC_PERCEPTION_VALIDATION.md
```

必须写真实结果。

格式：

```text
Baseline branch:
Baseline HEAD:
Local dirty files preserved:

RAM++:
  model loaded:
  worker load count:
  median inference ms:

Scene dataset:
  annotated major categories:
  RAM discovered:
  DINO localized:
  scene category recall:

Target tests:
  yellow chair:
  backpack-on-chair:
  rare object:
  target candidate recall:

VLM:
  60s frames:
  crop verify calls:
  full-frame sweep calls:
  full-scene enumeration calls:

Semantic map:
  persistent objects:
  duplicate objects:
  same-class separate objects test:

Regression:
  pytest passed:
  pytest failed:
  compileall:

FINAL:
  PASS / FAIL
```

---

# 64. 硬验收标准

全部满足才能写 PASS。

## RAM++

- [ ] 预训练 RAM++ 成功加载
- [ ] 一个 worker 生命周期只加载一次
- [ ] 每约 2 秒最多刷新一次 scene tags
- [ ] RAM tag filter 不只允许旧 vocabulary
- [ ] 普通 scene object coverage 相比固定词表明显增加

## GroundingDINO + SAM2

- [ ] RAM scene terms 进入 dynamic terms
- [ ] target mandatory terms 无论 RAM 是否输出都保留
- [ ] SAM2 现有 mask pipeline 未被破坏
- [ ] D435 现有 3D localization 未被破坏

## 复杂目标

- [ ] RAM 不含 chair 时，“黄色椅子”仍能搜索 chair
- [ ] 颜色由 VLM 最终复核
- [ ] “挂着书包的椅子”同时搜索 chair + backpack
- [ ] relation target 的 crop/context 包含关联物体
- [ ] 只看到普通 chair 不能确认 relation target

## 稀有目标

- [ ] RAM++ 漏词不会直接宣告目标不存在
- [ ] target profile 强制词独立运行
- [ ] 连续无 candidate 后低频 VLM target sweep 会触发
- [ ] VLM sweep 不会每帧调用
- [ ] 无 bbox/空间证据时不能伪造 3D object

## 场景地图

- [ ] 普通物体无需 VLM 即可进入 3D SemanticObjectMap
- [ ] RAM++ tag 自己不能直接创建 3D object
- [ ] 同一物体多帧仍复用现有 persistent object
- [ ] 三维地图/SLAM 行为没有被修改

## 效率

- [ ] full-scene VLM ordinary object enumeration = 0
- [ ] VLM 只用于 candidate verify / low-frequency target sweep
- [ ] 60 秒内 full-frame VLM sweep 不超过 interval 所允许次数
- [ ] AsyncSemanticObserver 主链不阻塞导航

## 回归

- [ ]现有 Grounded-SAM tests 通过
- [ ]现有 SAM2/depth tests 通过
- [ ] TargetProfile tests 通过
- [ ] CropVerifier tests 通过
- [ ] SemanticObjectMap tests 通过
- [ ] LiveSearch tests 通过
- [ ] SLAM/map tests 通过
- [ ] `python -m pytest -q` 无本次引入的新失败

---

# 65. 给 Coding Agent 的最终执行提示词

把下面文本和本计划书一起交给 Coding Agent：

```text
你正在修改 BROVVV/robot_scene_demo 的
fix/go2w-autonomous-search-lio-topology-target 分支。

严格按《GO2W RAM++ + GroundingDINO + SAM2 + VLM 双通路语义感知详细实施计划》执行。

重要：当前三维建图已经稳定，本次禁止换 SLAM、禁止改 plain_slam、禁止引入 LIO-SAM/FAST-LIO、禁止重写三维地图。

先完整阅读当前代码再修改。当前分支已经具备：
- Grounded-SAM persistent Popen worker；
- grounded_sam_worker.py --serve；
- GroundingDINO/SAM2 一次加载；
- DetectedObject.mask_rle/mask_size；
- DepthObjectLocalizer 的 SAM2 mask depth 与 3D box；
- TargetProfile 的颜色/属性/关系/context；
- GroundingPromptPlanner；
- CropVerifier；
- AsyncSemanticObserver；
- SemanticObjectMap 的 persistent ID / association / reprojection。

因此绝对不要重复实现这些东西。

本次真正要做的是：
1. 在现有 Grounded-SAM persistent worker 中加载 RAM++；
2. 增加 JSONL operation=tag；
3. 新增 discover_scene_terms(image_path)；
4. RAM++ 每约 2 秒自动发现场景 tags；
5. RAM tags 通过现有 detect_with_dynamic_terms 进入 GroundingDINO；
6. TargetProfile.detector_terms/context terms 永远优先，不能依赖 RAM++；
7. 普通物体由 RAM++→GroundingDINO→SAM2→Depth→SemanticObjectMap 建图；
8. VLM 不再每帧列出所有物体；
9. 复杂属性/关系目标继续由 CropVerifier 判断；
10. 连续多次没有 target candidate 时，每约 10 秒最多做一次 full-frame VLM target-only sweep，用于稀有目标漏检兜底。

关键例子必须通过：
A. RAM++ 没输出 chair，但任务是“黄色椅子”：
   Grounding prompt 仍必须有 chair/yellow chair，最后 VLM 判断颜色。
B. “挂着书包的椅子”：
   DINO 搜 chair + backpack，VLM 必须看包含二者的 context 后才确认关系。
C. 稀有设备：
   RAM++ 和第一轮 DINO 都没找到时，低频 full-frame VLM target sweep 必须触发，但不能每帧调用。
D. 普通 chair/table/monitor/plant：
   不得调用 full-scene VLM，直接进入现有 SemanticObjectMap。

禁止过度编程：
- 不建 gRPC/HTTP 服务；
- 不建第二套 semantic map；
- 不建数据库；
- 不建 worker pool；
- 不建复杂 scheduler；
- 不引入 embedding ontology；
- 不重写现有 association；
- 不增加复杂 fallback；
- 不修改导航/SLAM。

修改前记录 git status/diff，保留用户本地未提交代码。

修改后必须：
- 跑新增 targeted tests；
- 跑现有相关 tests；
- python -m compileall app tests；
- python -m pytest -q；
- 做真实 RAM++ 室内图片测试；
- 测黄色椅子、挂书包椅子、稀有物体旁路；
- 输出 reports/RAM_PLUS_SEMANTIC_PERCEPTION_VALIDATION.md。

有测试失败就修对应层并重跑，不要靠扩展架构解决局部问题。
```

---

# 66. 最终成功后的行为

正常场景：

```text
机器狗移动
↓
稳定 SLAM 正常建 3D 地图（完全不改）
↓
RGB 最新帧
↓
RAM++ 低频：
chair / table / monitor / plant / backpack
↓
GroundingDINO：
给这些物体定位 bbox
同时强制搜索用户目标词
↓
SAM2：
mask
↓
D435：
3D box
↓
SemanticObjectMap：
多帧融合 / 去重
↓
3D map：
显示普通物体语义
```

复杂任务：

```text
“找挂着黑色书包的黄色椅子”
↓
TargetProfile:
chair + yellow + backpack + hanging relation
↓
GroundingDINO:
chair + backpack
↓
几何找相邻候选
↓
VLM CropVerifier:
黄色？
黑色书包？
真的是挂在椅子上？
↓
确认 / 拒绝
```

稀有目标：

```text
RAM++ 没发现
↓
没关系
↓
TargetProfile 强制 GroundingDINO 搜索
↓
仍连续无 candidate
↓
10 秒级低频 full-frame VLM target sweep
↓
发现疑似区域
↓
重新 localization / crop verification
```

这就是本次改造的最终目标：

> **RAM++ 负责扩大日常场景覆盖；GroundingDINO/SAM2 负责快速定位分割；现有 Depth/SLAM/SemanticObjectMap 负责三维世界落点；VLM 不做每帧主检测，但仍保留复杂语义判断和稀有目标漏检兜底。**
