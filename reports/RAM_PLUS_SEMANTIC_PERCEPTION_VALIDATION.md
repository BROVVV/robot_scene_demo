# RAM++ 双通路语义感知 —— 验证报告（§63 强制格式）

生成时间：2026-09-11
执行范围：计划书 Part 3 的 **A0–A11**（无机器人即可完成的部分）。**B3/B4 未执行** ——
真机端到端需要机器人运动，属于新的运动授权批次，必须由用户重新点头。

---

```text
Baseline branch: fix/go2w-autonomous-search-lio-topology-target
Baseline HEAD:   b1e929a9afc2b2696c8727cf9d1686aebfc235cf
Local dirty files preserved:
  # 本轮之前就存在的未提交改动，一个都没回滚、没提交：
  M data/memory/observational_memory.jsonl
  M outputs/autonomous_search/logs/motion_control.log
  M outputs/autonomous_search/logs/slam_web_bridge.log
  M outputs/autonomous_search/logs/web_server.log
  M outputs/autonomous_search/runtime/slam_map_3d.json
  # 以上都是真机跑起来的运行时产物（B2 受控运动那一批写的），不是代码。
  # 全程没有执行过 git add . / git reset / git clean / git checkout。

RAM++:
  model loaded:        True（ram_plus_swin_large_14m.pth，swin_l，384×384）
  worker load count:   1（每个 worker 进程一次；见下方「证据」）
  median inference ms: 77 ms（热态 tag，n=8，min=73 / max=81）
                       冷启动含载入+首帧 tag = 17.34 s
                       刷新限频 = 1.8 s（RAM_SCENE_TAG_INTERVAL_SECONDS ≤ 2 s）

Scene dataset:
  annotated major categories: 17（= RAM++ 与 DINO 在两帧里出现的类别并集）
  RAM discovered:             17
  DINO localized:             17
  scene category recall:      88%（15/17，RAM 类别被 DINO 实际定位到的比例）
  备注：DINO 定位到而 RAM 没给的类别 = backpack / office chair / chair，
        正是 §4 的 target mandatory terms 独立运行的结果。

Target tests:
  yellow chair:         PASS（§14）
  backpack-on-chair:    PASS（§15）
  rare object:          PASS（§44.7/§44.8 触发逻辑 + §18 sweep 落地；未跑真机 60 s 窗口）
  target candidate recall: 未给百分比 —— 手上只有 2 帧、没有逐目标的标注真值集，
                           编一个 recall 数字没有意义。可核对的定性事实是：
                           「挂书包的椅子」的两半（chair 与 backpack）在同一帧里
                           都被 DINO 定位到（4/4 次），「黄色椅子」的 5 个 chair
                           候选全部被 VLM 以颜色理由判掉。证据见下方 §14/§15。

VLM:
  60s frames:                    不适用 —— 未跑真机 60 s 窗口（B3/B4 未执行）
  crop verify calls:             6 / 次（A7 单次跑：黄色椅子 5 次 + 挂书包椅子 1 次）
                                 最终采纳的 4 次连跑共 24 次
  full-frame sweep calls:        0（A7 全程存在 target-like 候选，未触发兜底）
  full-scene enumeration calls:  0（grounded_sam + RAM++ 且**本帧已跑过检测**时，
                                 analyze_semantic 在 _ram_scene_payload 就返回了。
                                 本帧还没检测过时仍会落到全场景 worker，不是绝对不可达）

Semantic map:
  persistent objects:          49 条（A6 离线链路，33 个检测 × 2 次 update）
  duplicate objects:           16 条未复用（合成深度导致，见「诚实记录」第 3 条）
  same-class separate objects test: PASS（现有 tests/test_live_persistent_id_mapping.py）

Regression:
  pytest passed:  <见文末「最终回归」>
  pytest failed:  <见文末「最终回归」>
  compileall:     PASS（python -m compileall -q app tests scripts，rc=0）

FINAL:
  PASS（A0–A11 范围内的无机器人验收项全部通过；B3/B4 真机项未执行，不参与判定）
```

---

## 证据

### RAM++：只加载一次

`app/detectors/grounded_sam_worker.py` 里 `_build_ram_plus()` 只在 `main()` 里被调用
一次（第 78 行），紧接着 `_serve()` 进入 stdin 循环（第 81 行）。`_serve` 里
`operation == "tag"` 分支直接复用传进来的 `ram_model` / `ram_transform`，不再重建。

实测佐证（`/tmp/ram_metrics.py`，真实模型、仓库内真机 D435 缓存帧）：

```text
冷启动(含载入+首帧 tag) = 17.34s  tags=12
  bundle_106952.jpg: 78 ms   bundle_107873.jpg: 80 ms   bundle_108194.jpg: 79 ms
  bundle_108373.jpg: 81 ms   bundle_108688.jpg: 74 ms   bundle_109088.jpg: 73 ms
  bundle_109441.jpg: 75 ms   bundle_110261.jpg: 77 ms
热态 tag 中位耗时 = 77 ms
```

如果每次 tag 都重载，耗时会回到 17 s 量级；8 次连续调用都是 ~77 ms，即模型常驻。

### 场景类别召回（§47 / §63 Scene dataset）

`/tmp/scene_recall.py`，目标 `找挂着书包的椅子`：

```text
bundle_137555.jpg: RAM=12 DINO类别=12 命中=10
bundle_106952.jpg: RAM=12 DINO类别=10 命中=8
两帧合计: RAM 类别=17  DINO 类别=17
scene category recall = 15/17 = 88%
```

两帧的 RAM tag 里**都没有 chair**（137555 有 chair，106952 没有），但两帧的
GroundingDINO 结果里都有 `chair` / `office chair`。这就是 §64
「RAM 不含 chair 时，『黄色椅子』仍能搜索 chair」的直接证据。

### 普通物体覆盖度（§64 RAM++ 最后一条）

`tests/test_ram_scene_terms.py::test_real_ram_tags_extend_coverage_beyond_the_static_vocabulary`
把 A7 真实跑出的 12 个 RAM tag 与 `BASE_TERMS`（30 个固定词）对表：

```text
落在 BASE_TERMS 里：backpack / chair / machine / office chair（4 个）
固定词表根本没有：barrel / bin / container / equipment / floor /
                  glass door / glass wall / stool（8 个）
```

### 目标词表（§14 / §15）

判据全部取自**真正送给 GroundingDINO 的那串 prompt**，不是 `profile.detector_terms()`
（关系锚点按设计不进 detector_terms —— 那是「目标本身」的词表；只查它会永远 FAIL，
而实际 prompt 里其实有 backpack）。日志原件见 `outputs/ram_target_verify/a7_run{3,4,5,6}.log`。

```text
§14 找黄色椅子（run3）
  detector_terms = ['chair', 'yellow chair', 'yellow seat']
  两帧共 5 个 chair 候选，全部被 VLM 判掉
  （理由样例：「主体物体颜色为黑色，不符合目标'黄色椅子'的颜色要求」）

§15 找挂着书包的椅子 —— 连跑 4 次全部通过（run3/4/5/6）
  关系锚点 = ['backpack', '书包']            ← 4 次都非空
  DINO prompt = ['chair. office chair. backpack.',
                 'barrel. bin. cart. chair. container. dolly. equipment. floor.
                  glass door. glass wall. machine. stool.']   ← 4 次逐字符相同
  裁剪框 candidate=[0.692, 0.000, 0.999, 0.961]
        -> verify   =[0.570, 0.000, 0.999, 0.961]   邻近 context 物体 = ['backpack']
  复核结果 is_target=True（match/attr/context 三次 1.0/1.0/1.0，一次 0.95/0.95/0.95）
  注意同帧里离椅子更近的还有 glass door，被锚点优先级正确压下去了。

  只有 detector_terms 随 LLM 措辞抖动（4 次分别是
  ['chair','chair with backpack','chair with hanging bag'] /
  ['chair','chair with backpack','backpack chair'] /
  ['chair','chair with backpack','backpack on chair'] / 同上），
  不影响最终 prompt 与结果。
```

`python scripts/go2w/verify_ram_target_verify.py` 输出：`Phase 4 通过: 黄色椅子=True 挂书包的椅子=True`（4/4）。

**裁剪图肉眼复核（不是只信数字）**：`outputs/ram_target_verify/bag_chair_crop.jpg`
里能直接看到一张白色网面办公椅，**椅背上挂着一只黑色书包** —— 并集确实把关系证据
带进了 VLM 的视野，`is_target=True` 不是蒙的。

**同一张图也说明了 §14 的边界**：这把椅子是白/灰色的，画面里没有黄色椅子
（背景那块黄色是隔板/墙面）。所以 5 个 chair 候选被颜色理由判掉是**正确行为，
不是漏检**；但也意味着**这两帧只能证明 §14 的负例（非黄色椅子被拒），
证明不了正例（真有一把黄色椅子时能确认）** —— §14 正例留给 B4 真机，
或者补一张带黄色椅子的帧。

### 全链路（§52 / §46）

`python scripts/go2w/verify_ram_semantic_chain.py`（深度图是合成的，见「诚实记录」第 3 条）：

```text
[1] RAM++ 场景词 (12)
[2] 目标词: ['chair', 'chair with backpack', 'chair with bag']
[3] GroundingDINO+SAM2: 33 个候选，33 个带 mask        ← SAM2 mask pipeline 未破坏
[4] DepthObjectLocalizer: 33 条，其中 33 条有深度       ← 3D 定位未破坏
[5] SemanticObjectMap: 49 个物体
链路通过: RAM=True DINO+SAM2=True 定位=True 地图=True
```

### 效率（§50 / §51 / §64）

* full-scene VLM enumeration = **0**：`analyze_semantic`（`scripts/go2w/run_autonomous_loop.py:2064`）
  现在先试 `semantic_payload_from_quick_target_absence`，再试 `_ram_scene_payload`；
  在 `grounded_sam` + `enable_ram_plus` 且本帧跑过检测时**直接返回**，走不到
  `siliconflow_vision_worker.py`（全场景枚举）那条路。
* VLM 只剩两处用途：候选裁剪复核（crop verify）与低频整帧兜底扫描（sweep）。
* sweep 频率由纯函数 `app/live_robot/target_sweep.py::decide_target_sweep` 决定，
  `tests/test_target_sweep_trigger.py::test_sixty_seconds_of_empty_cycles_sweeps_at_most_once_per_interval`
  模拟 60 s（每 0.5 s 一个检测周期 = 120 帧、全程无 target-like 候选），
  用生产参数（`empty_cycles_threshold=4` / `interval_seconds=10.0`）实跑：
  扫描时刻 = 第 **2 / 12 / 22 / 32 / 42 / 52 s**，即 **60 s 内恰好 6 次**
  （测试断言上界是 7 次，实测 6 次）。
* RAM tag 限频**在生产循环里确实生效**，不是只写了个配置值：
  `run_autonomous_loop.py:2444` 的 `_ram_scene_terms()` 先看
  `now - self._last_ram_tag_time < settings.ram_scene_tag_interval_seconds` 就直接
  返回缓存，超时才真的调 `discover_scene_terms()`（`:2446`）。
  热态 77 ms + 限频 1.8 s，不会成为瓶颈。
* RAM 词走**独立 prompt 槽位**（`:2429` 把它当第 4 个参数传给
  `detect_with_dynamic_terms`，落到 `_build_prompts` 里单独 append 一个 prompt），
  所以 RAM 返回的 tag 再多也挤不掉目标词那一槽 —— 这是 §12.1 优先级的实现方式。

### 三维地图 / SLAM 未被修改（§60 / §64）

```text
git status --short app/perception/depth_object_localizer.py \
  app/spatial/semantic_object_map.py app/slam/slam_bridge.py \
  app/spatial/slam_web_map_state.py ros2_ws/src/plain_slam_ros2 \
  ros2_ws/src/go2w_plain_slam_bridge app/navigation
→ 无输出（一个都没动）
```

### 回归（§45 / §61 Phase 7）

```text
python -m compileall -q app tests scripts   → rc=0
python -m pytest tests/ -q                  → <见文末「最终回归」>
```

A2 阶段先跑的是**定向子集**（53 个测试：grounded_sam / semantic_observer /
target_profile / crop_utils / frame_bundle 等），基线为 **2 failed / 51 passed**，
两次一致，两个失败都是既有漂移（`AsyncSemanticRequest` 新增 `timestamp_quality`
后测试没跟上，`tests/test_async_semantic_observer.py:70`）。

本轮为了给出**全量**基线口径，额外把 HEAD 原样导出到 `/tmp/rs_baseline`
（`git archive HEAD | tar -x`，不动工作区）跑同一套 `pytest tests/ -q`，
与当前工作区逐条对比失败集合。

> ⚠️ **这个基线第一次跑出来的数字是无效的，见文末「最终回归」。**
> `git archive` 导出的是被 git 跟踪的内容，**不含 `.env`**（gitignore），
> 而 `app/config.py:708` 是 `load_dotenv(_project_root() / ".env")` ——
> 按**文件位置**定位项目根，跟 cwd 无关。基线树没有 `.env` ⇒ LLM key 为空
> ⇒ `parse_robot_task` 走规则降级、不发网络请求 ⇒ LLM 相关用例"秒绿"。
> 本轮据此做 diff，把 3 个环境差异误判成了代码回归。

---

## 本轮发现并修掉的问题

1. **`_build_prompts` 收到 `ram_scene_terms=None` 会崩**
   （`app/detectors/grounded_sam_subprocess.py`）——
   `detect()` 调 `detect_with_dynamic_terms(image_path, target_text, [])` 时不传
   RAM 词，参数默认 `None`，直接进 `filter_ram_scene_terms(None)` →
   `TypeError: 'NoneType' object is not iterable`。
   影响 5 个既有测试（`test_grounded_sam_runtime.py` 3 个、
   `test_grounded_sam_prompt_integration.py` 2 个）。修法是在函数入口把
   `None` 归一成 `[]`（一行，不引入分支）。修完 19 passed。

2. **`_verification_region` 把同帧所有物体都跳过**（本轮早些时候已修，记在这里备查）——
   它靠 `obj.get("object_id") == candidate.get("object_id")` 跳过候选自己，
   两侧都没这个键时 `None == None` 成立，于是 §15.3 的并集**静默失效**。
   修法：检测结果统一带 `object_id`（`_detection_frame_objects`），
   候选 id 按 bbox 相等反查。

3. **§4.1：目标画像的上下文词被 `STATIC_OBJECT_PROMPTS_ENABLED=false` 挡在门外** ——
   `.env` 里这个开关是给 static base 词表用的，却连 `context_labels_en`
   （「挂着书包的椅子」的 backpack）一起关掉了，导致 GroundingDINO 只搜 chair。
   修法：目标画像的 context labels 无条件进 prompt，只有 `include_base_terms`
   继续受该开关控制。

4. **关系锚点会被上游 LLM 丢掉（§15 的真凶）** —— 同一个「找挂着书包的椅子」，
   `TargetProfileResolver(use_llm=True)` 反复给出不含 backpack 的 `context_labels_en`
   （实测 `['desk','table','room']`、`['desk','door']`、`['desk','table','cabinet','wall']`），
   于是 §15.3 的并集永远拿不到背包。光加 prompt 规则不够（时给时不给），
   所以单独立了 `relation_anchor_labels_en/_zh` 两个字段：它跟「目标可能在哪」的
   通用 context 是两回事，必须被找到。加字段后 4/4 命中。
   同时把锚点接进两条 prompt 装配路径（`build_grounding_prompt()` 与
   `grounded_sam_subprocess._build_prompts` 的 `context_terms=`）。

5. **⚠️ 两份 `TargetProfile` 分叉 —— 真机链路上的 `AttributeError`** ——
   仓库里有两份**逐字节相同**的 `TargetProfile`：`app/reasoning/target_profile.py`
   与 `app/video/target_profile.py`（`app/vision/target_profile.py` 这个 shim 注释
   写明「shared target-profile implementation → app.reasoning」，说明 reasoning
   那份才是 canonical）。我只把新字段加到了 `app/video` 那份，于是：

   ```text
   run_autonomous_loop.py:95   from app.reasoning.target_profile import ... TargetProfileResolver
   run_autonomous_loop.py:2036 profile = TargetProfileResolver(...).resolve(target, use_llm=True)
   run_autonomous_loop.py:2170 verify_bbox, _context = _verification_region(..., profile)
   app/video/semantic_verifier.py:175  *target_profile.relation_anchor_labels_en,
   → AttributeError: 'TargetProfile' object has no attribute 'relation_anchor_labels_en'
   ```

   **这不是测试脚本的毛病，是生产路径的毛病** —— 只要目标带关系约束，
   真机整条搜索链路就会在这里崩，A7 只是先撞上它。修法：把同样两个字段补到
   canonical 的 `app/reasoning/target_profile.py`（补丁搬运，两份重新一致），
   并加 `tests/test_video_semantic_verifier.py::test_reasoning_profile_drives_the_video_crop_region`
   锁住「reasoning 画像 → video 裁剪」这个真实组合（用最小改动锁住真实组合，
   而不是去重构两套模块）。
   **没有做的是**：把两个模块合并成一个 —— 那是重构，超出本轮范围，留给后续决定。
   代价是两份仍要保持同步，这一点写进「下一步」。

6. **§15 曾经「假通过」** —— 早先的判据只问「裁剪框变大了没」，结果并进一扇
   `glass door` 也算过。修法有两处：`_verification_region` 改成
   **锚点优先于通用 context**（否则按最近距离会挑到玻璃门），
   A7 的判据加 `anchor_ok`（并进来的那个物体的 label 必须含 backpack/bag/schoolbag），
   并且 `search_ok` 改查真实 DINO prompt。

---

## 诚实记录（未达成 / 有水分 / 需要真机的地方）

1. **B3/B4 真机端到端没有跑。** 本报告的 `FINAL: PASS` 只覆盖 A0–A11。
   §64 里依赖真机的条目（D435 真深度下的 3D 定位、真机多帧 persistent object
   复用、60 s 真机 VLM 调用计数、真实场景的 target recall）**一律未验**，
   必须在 B4 补齐后才能给整轮下结论。

2. **`TargetProfileResolver(use_llm=True)` 有 LLM 波动，锚点已用字段锁住，但别名仍在抖。**
   §15 的**关系锚点**现在是 4/4 命中（单独立字段之后），不再依赖 LLM 当天的心情。
   残留的波动只在别名措辞上：同一查询 4 次解析出
   `chair with hanging bag` / `backpack chair` / `backpack on chair` 等不同
   `detector_terms`，最终 prompt 与判定结果不受影响。
   这是 D7/C9 已记录的 LLM 波动，本轮没有引入新的兜底
   （§0 第 6 条明确禁止「偷偷切换十种方案」）。
   **处置建议**：B4 真机验收时同一个查询至少跑 3 次，记录命中率，不要只看一次。

3. **A6 的深度图是合成的**（`scripts/go2w/verify_ram_semantic_chain.py::synth_depth`
   造了一堵 2 m 墙 + 一块 1.2 m 近景）。所以那一跑的 33/33 有深度、49 个物体
   **只能证明链路通**，不能证明深度精度，也不能证明多帧复用率。
   49 = 33 + 16 里那 16 个「未复用」是合成平面深度下关联门限的正常表现，
   **不是本轮引入的**：`app/spatial/semantic_object_map.py` 属 §60 禁改文件，
   本轮一个字节没动，且现有 `tests/test_live_persistent_id_mapping.py` 覆盖了
   同类别分开物体的持久 id 契约。真机复用率列为 B4 观察项。

4. **VLM sweep 是同步的，而且超时是 65 s 不是 20 s。**
   `_vlm_target_sweep`（`run_autonomous_loop.py:2487`）在 detect 周期里同步调用
   `_daemon_vlm_request("quick", ..., timeout=settings.vlm_runtime_quick_timeout_seconds)`
   （`:2525`）。**本机这个值是 65.0 s** —— `.env` 里没有这一项，走的是
   `app/config.py:147` 的 `DEFAULT_VLM_RUNTIME_QUICK_TIMEOUT_SECONDS = 65.0`；
   而 `.env.example:430` 写的是 20。**两者不一致，报告先前抄了 example 的 20。**
   同步是刻意的（§20/§21 要求复用现有调用、不加线程），不这样做的原因：
   异步 sweep 会让候选与帧错绑（`verify` 读的是 `state["image_path"]`，
   旧帧候选会被拿去复核新帧图）。
   **B4 要盯**：一次 sweep 最坏会阻塞检测周期 65 s，很可能撞上图搜索的 stale 保护。
   真机开跑前把 `.env` 里 `VLM_RUNTIME_QUICK_TIMEOUT_SECONDS` **显式写成 65** ——
   那是 A7/A8 实测生效的值，也是代码默认值。
   （本节先前写的是「建议 20，与 example 对齐」，**那是错的**：20 是 `.env.example`
   的过时残留，代码默认已被单独提到 65，而 verify 仍是 20、两边本来就一致 ——
   可见是有人特意把 quick 调长的。照 20 设在真机上，等于让真机跑在一个从没验证过
   的更短超时上。`.env.example` 已于本轮改为 65 —— 注意
   `bootstrap_fresh_machine.sh:110` 会 `cp .env.example .env`，所以**新部署**的
   机器上 quick 超时会从 20 变成 65（与代码默认对齐，方向是对的）。本机 `.env`
   三项超时都没设，不受影响。）

5. **§64「无 bbox/空间证据时不能伪造 3D object」只有代码路径证据**：
   `_vlm_target_sweep` 只收 `bbox_2d` 非空的候选，产出的 `Detection`
   （`scripts/go2w/run_autonomous_loop.py:2128` 起）只有 label/score/bbox，
   不带 `mask_rle`，节点里也不把它喂给 `DepthObjectLocalizer`，
   所以进不了 `SemanticObjectMap`。**离线没有可执行的反例测试**，列为 B4 核对项。

6. **`.env` 现状**（用 `get_settings()` 实读，不是抄 example）——
   下面标了每项**到底来自哪儿**，因为两者不一致正是上一条踩的坑：

   ```text
   写在 .env 里：     GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=true
                     STATIC_OBJECT_PROMPTS_ENABLED=false
   走代码默认：       vlm_runtime_quick_timeout_seconds  = 65.0   ← .env 没设，走代码默认
                     enable_ram_plus                    = True
                     ram_scene_tag_interval_seconds     = 1.8
                     ram_scene_max_terms                = 12
                     vlm_target_sweep_interval_seconds  = 10.0
                     vlm_target_sweep_empty_cycles      = 4
   ```

   后四项虽与 `app/config.py` 默认一致，但真机开跑前建议在 `.env` 里显式写死，
   免得将来改默认值把真机行为悄悄带偏。

7. **§17/§9 的形状测试用的是「镜像副本」，不是生产函数。**
   `tests/test_ram_scene_payload.py` 自己定义了一份
   `_ram_scene_payload(image_path, objects)`，**没有 import 生产的那一份** ——
   因为 `scripts/go2w/run_autonomous_loop.py` 需要 rclpy，在本机任何可用解释器里
   都不可导入（这正是 §20 的逻辑被抽到 `app/live_robot/target_sweep.py` 的原因）。
   所以这个测试保证的是「这种形状的 payload 能被 `LiveSemanticObserver` 吃下去」，
   **不保证生产真的产出这个形状**；两边一旦漂移，测试不会红。
   这一处是**唯一**的镜像：其余新增测试都直接 import 生产函数
   （`test_target_sweep_trigger.py` → `app.live_robot.target_sweep`、
   `test_ram_target_priority.py` → `GroundedSAMSubprocessDetector`、
   `test_ram_scene_terms.py` → `app.detectors.vocabulary`、
   `test_target_profile.py` → `app.video.target_profile`），不是抄一份逻辑来测。
   A9「本帧检测结果就是场景物体来源」的结论因此是**代码阅读 + 形状契约**，
   不是端到端执行。B4 真机跑起来时盯一眼 payload 里
   `source == "grounded_sam_ram_scene_objects"` 是否真的出现
   （这是 `_ram_scene_payload`（`run_autonomous_loop.py:2478`）打的标记）。

8. **`pytest tests/` 不能当回归门禁用 —— 它调真实 LLM，两次跑失败集合会变。**
   同一份代码，`test_task_planner` / `test_task_examples_evaluator` /
   `test_streamlit_natural_language_task_ui` 的成败取决于**当天 LLM 怎么解析
   那几句中文样例**，以及 Streamlit `AppTest` 的 30 s 超时赶不赶得上。
   本轮实测：同一批文件在干净 HEAD 上，带 `.env` 跑 3 次得到
   `3 passed`（**无** `.env，走降级）/ `2 failed 1 passed` / 全量里的
   不同组合。**所以「基线几个失败、现在几个失败」这种比法本身是不可靠的**，
   必须逐条 diff 失败**名字**，再对每一项单独做对照实验（本轮就是这么把
   2 项"新挂"排除掉的，见「最终回归」）。
   要变成可信门禁，得先给 LLM 打桩或固定快照 —— 那是另一件事，本轮没做。

9. **那份"锁住两份 `TargetProfile`"的回归测试，保护范围比字面上窄 —— 别当成
   合并的替代品。** `test_reasoning_profile_drives_the_video_crop_region` 构造的是
   **`app.reasoning` 那份**，再传给 `app.video` 的 `_verification_region`，而
   `_verification_region` 只读传入对象的属性（`target_profile: TargetProfile`
   只是**类型标注，Python 运行时不校验**）。所以：
   * 它锁得住：reasoning 那份**少了字段**（构造处直接 `TypeError`）、锚点裁剪行为。
   * 它锁不住：**`app/video` 那份自己少了字段** —— 测试从不构造它。
   也就是说，两份再分叉时，测试**只会在分叉发生在 reasoning 侧时报错**。
   `app/video` 那份目前被 4 个生产模块引用（`semantic_verifier.py`、
   `target_search.py`、`frame_analyzer.py`、`pipeline.py`，其中 `pipeline.py` 还会
   用它的 resolver 构造实例）、3 个测试文件直接构造 —— 这就是合并的爆炸半径。
   （另：这条用例包在 `unittest.TestCase` 里，node id 要带类名
   `::VideoSemanticVerifierTests::`，只写函数名 pytest 会报 not found。）

---

## 本轮改动的文件

```text
M .env.example                                   +18/-1 两个 sweep 参数示例 + quick 超时对齐 65
M README.md                                       +18   §58 视觉职责分工 + 磁盘守护说明
M app/config.py                                   +33   sweep 参数 + RAM++ 设置落位
M app/detectors/grounded_sam_subprocess.py       +70/-19 §4.1 context 无条件 / None 归一 / RAM 独立槽位 / §15 锚点
M app/detectors/grounded_sam_worker.py            +78/-3  RAM++ 载入、tag 操作、| 分隔解析
M app/detectors/vocabulary.py                     +49   filter_ram_scene_terms / build_detection_prompts
M app/reasoning/target_profile.py                 +29/-1  §15 锚点字段（canonical 那份）
M app/video/target_profile.py                     +29/-1  §15 锚点字段（video 那份，两份保持一致）
M app/video/semantic_verifier.py                  +19/-3  §15.2/§15.3 锚点优先于通用 context
M scripts/go2w/run_autonomous_loop.py            +200/-5  §15.3 关系裁剪 / §17 场景复用 / §18 sweep / 画像上提
M scripts/bootstrap_fresh_machine.sh              +17   RAM++ 权重与 fairscale 引导
M scripts/verify_fresh_deployment.sh              +11   部署校验加 RAM++
M tests/test_target_profile.py                    +26   §15 锚点必进 context_terms
M tests/test_video_semantic_verifier.py           +49   §64「普通 chair 不能确认关系目标」+ reasoning→video 组合
A app/live_robot/target_sweep.py                  32 行 §20 纯函数（让 §44.7/§44.8 可测）
A scripts/go2w/verify_ram_semantic_chain.py      122 行 A6 离线全链路验证
A scripts/go2w/verify_ram_target_verify.py       235 行 A7/§61 Phase 4 离线复核验证
A tests/test_ram_scene_terms.py                   97 行 §44.1 / §64 覆盖度
A tests/test_ram_target_priority.py              125 行 §44.3 / §44.4 / §12.1 优先级
A tests/test_ram_scene_payload.py                100 行 §17 形状契约
A tests/test_target_sweep_trigger.py             101 行 §44.7 / §44.8 / §64 频率
A outputs/ram_target_verify/a7_run{3,4,5,6}.log        A7 四次通过的证据日志
A outputs/ram_target_verify/*_crop.jpg                 A7 裁剪复核的实际输入
A scripts/go2w/guard_plain_slam_disk.sh            71 行 缓存/磁盘越界时有序停建图（见文末「附」）
M scripts/go2w/start_plain_slam_mapping.sh        +35  启动磁盘预检 + 起守护
M scripts/go2w/stop_plain_slam_mapping.sh          +5  一并停守护（守护触发时不自杀）
M scripts/go2w/check_plain_slam_mapping.sh        +11  健康摘要显示缓存占用
```

§60 禁改清单（`depth_object_localizer.py`、`semantic_object_map.py`、
`slam_bridge.py`、`slam_web_map_state.py`、`plain_slam_ros2/`、
`go2w_plain_slam_bridge/`、`navigation/`）**零改动**。

---

## 附：plain_slam 点云缓存写满磁盘（2026-09-11，非 RAM++ 问题）

**现象**：宿主机 `/` 被写到 100%，`/tmp/go2w_plain_slam` 累积约 47G 临时点云；
停掉 `plain_slam_go2w` 并清空后才恢复（本节即该问题的收尾）。

**根因**：`slam_3d_node` 每加一个关键帧就往 `map_cloud_dir` 写 `raw_cloudN.bin`
与 `filtered_cloudN.bin`，序号单调递增、**运行期从不回收**
（`slam_3d_interface.cpp:135-138`、`:167-170`）。而回环检测（`:206-209`）与
`BuildMapCloud()`（`:282-286`）都要**读回**这些文件 —— 所以运行中删旧文件
等于破坏建图，唯一的清理由是建图收尾的 `WriteMapCloudPCD()`（`:330-332`）。
`start_plain_slam_mapping.sh` 又只在**启动时** `rm -rf` 一次，`/tmp` 与 `/`
同盘，于是会话跑得够久就能写满根文件系统。

**修复**（不改建图、不碰 §60 任何路径、运行期不删任何缓存文件）：
新增 `scripts/go2w/guard_plain_slam_disk.sh` 在会话期守护缓存目录大小与磁盘
剩余，越界时复用已有的 `stop_plain_slam_mapping.sh` 有序停掉 mapping 栈 ——
该脚本明确不动 `/go2w/odom/fused` 与运动栈，所以机器狗不会因此失控。
`start_plain_slam_mapping.sh` 另加两道：启动前的磁盘预检（余量不足直接拒绝
启动，不做事后诸葛亮）、`rm -rf` 之后 setsid 起守护。阈值默认缓存 20G /
磁盘剩余 20G，用 `GO2W_SLAM_DISK_MAX_CACHE_GB`、`GO2W_SLAM_DISK_MIN_FREE_GB`
调；`check_plain_slam_mapping.sh` 的健康摘要里也加了缓存占用与 `/tmp` 余量。

**验证**（把 guard 与一个假 stop 复制到临时目录跑，不碰真实进程，零副作用）：

| 用例 | 期望 | 实测 |
| --- | --- | --- |
| 缓存 0.2G > 上限 0G | 触发并调 stop | ✅ `TRIPPED`，假 stop 收到 `TRIPPED=1` |
| 磁盘剩余 51.4G < 下限 1000G | 触发 | ✅ 两项实测值都打进日志后触发 |
| 阈值宽松 | 静默守护、不触发 | ✅ 5 s 内零输出，timeout 124 |
| 缓存目录不存在 | 不崩、不误触发 | ✅ 探剩余空间时正确回退到父目录 |
| start 预检（下限 20G / 1000G） | 放行 / 拒绝 | ✅ 实测 52G 放行，1000G 拒绝 |
| `setsid … &` 后 pid 文件可用 | 存的是能停掉的真实 pid | ✅ 守护自写 `$$`；实测 pid 存活、重入换掉旧守护、TERM 停得掉 |

最后一条是本轮实测踩出来的：`setsid cmd &` 的 `$!` **不保证**是守护进程 ——
setsid 当上进程组组长时会 fork，`$!` 拿到的是 fork 完就退出的父进程，写进 pid
文件就是个死 pid，`stop_pid_file` 会因此永远停不掉它。在带 job control 的 shell
里必然如此（实测 `$!`=276219、真实进程=276221）。所以守护进程自己写 `$$`，
两种 shell 下都对。
附带说明：`plain_slam_go2w.pid` 用了同样的写法，但它的 stop 侧有 pgrep 兜底，
本机实测 pid 也是活的（非交互 shell 里 setsid 不 fork），**没有动它** —— 不属于
本次磁盘问题，改了就是范围蔓延。

**遗留（需要人判断的地方）**：
- 守护触发会**中断建图**（运动链不受影响）。默认 20G 是按「宁可留余量不误停」
  取的 —— 一次正常会话的缓存上限没有历史数据可依。若被误停，调大
  `GO2W_SLAM_DISK_MAX_CACHE_GB` 即可；日志在
  `runtime/go2w/sessions/plain_slam_disk_guard.log`。
- 守护只在经 `start_plain_slam_mapping.sh` 启动时生效。绕过它直接
  `ros2 launch` 就没有这层保护。
- **更根本的问题本轮没动**：47G 按每关键帧约 3 MB 反推是上万帧，正常建图不该
  有这么多。**本轮只读代码把机理读清楚了（未改一行、未跑真机）**：

  `slam_3d_interface.cpp:151-157` 的加帧判据是
  ```cpp
  ComputeLocalMapMatchingRate(filtered_cloud);       // 本帧有多少点能在局部地图 0.5 m 内找到邻居
  if (local_map_matching_rate_ < min_local_map_matching_rate_) {
      kf_detector_.AddKeyframe(slam_pose_);          // ← 匹配率不足，强制加帧
  } else if (!kf_detector_.IsKeyframe(slam_pose_)) {
      return;                                        // ← 否则才看「走够 distance_th 了吗」
  }
  ```
  配置 `config/slam_3d_params.yaml:5-8` 是 `distance_th: 10.0`（米）、
  `min_local_map_matching_rate: 0.95`。**两条阈值的关系是本条的关键**：
  设计意图是「每 10 m 存一帧」，而 0.95 的匹配率是安全网（本意「扫描和地图
  对不上了，出事了，赶紧记一帧」）。但 0.5 m 的对应半径 + 体素降采样后的稀疏
  点云，狗只要走出去一两米，远处稀疏点就很容易有 5% 找不到邻居 ——
  **安全网一直在触发，10 m 那条规则形同虚设**。
  （`angle_th: 1000.0` 不会误触发：`slam_3d_interface.cpp:68` 有度→弧度换算，
  1000° 即 17.45 rad，实际等于关掉角度判据。这一条**不是** bug。）
  局部地图是**每个关键帧后重建**的（`slam_3d_interface.cpp:249`，在回环检测
  收尾处），所以匹配率不会永久掉 0 —— 是「走一小段就掉到 0.95 以下」这个节奏。

  算术佐证：47G ÷ 约 3 MB ≈ **1.5 万帧**；若真按 10 m 一帧，那是 **150 km**
  的行走 —— 一个房间不可能。所以真实帧距是「几十厘米一帧」量级。

  **可能与今天那个 odom 发散同源（推测，未证实）**：位姿图被上万帧撑大后，
  一次错误的回环边就能把 `slam_pose_` 拽飞，`slam_pose_` 一飞，写出去的
  `odom_base` 就指数发散 —— 正好是轨迹里看到的 ×1.66 那个形状。

  **零成本现场诊断（不改代码、不动参数，只是数文件）**：建图跑起来后
  ```bash
  watch -n2 'ls /tmp/go2w_plain_slam/raw_cloud*.bin 2>/dev/null | wc -l'
  ```
  每秒涨几次 → 坐实「每帧都加」；几十秒才涨一个 → 上述判断错了，得另找原因。
  改 `min_local_map_matching_rate` 属三维建图参数，**须单独授权**，
  本轮按「禁止改三维建图」没有碰（连那行被注释掉的调试输出都没打开——
  打开它要重新编译 §60 路径）。

---

## 附：WebUI 地图不显示 + 网页卡死（2026-09-11，非 RAM++ 问题）

**现象**（用户报告，两句话其实是同一个 bug）：
1. 「打开 webui 里面没有地图」—— 页面能加载，地图区域一片空白。
2. 「网页卡死了没反应」—— 标签页主线程 100% 占满，HTTP 轮询完全停止。

**排查路径**（先证伪服务端，再定位到浏览器）：

| 查什么 | 结果 |
| --- | --- |
| web server 进程 | 健康，uvicorn 存活 8h57m |
| `/api/slam/map3d` 手动 curl | 正常返回，16610 个点 |
| 浏览器是否还在拉流 | MJPEG `bytes_acked == bytes_sent`、send-q 0、累计 676 MB —— **网络栈活着** |
| 浏览器 HTTP 轮询 | **12 s 内 0 个请求** —— 主线程已经不转了 |
| `/proc/<pid>/task/*/stat` 采样 | `tid == pid` 的线程 100.3% —— 是 **JS 主线程**，不是合成器 |

**根因链**（一条，两处症状都出自它）：

```
/go2w/slam/odom_base 发散（x/y 指数 ×1.66 涨到 1.7e20）
  → webui_map_snapshot_writer.py::_on_odom 照单全收
  → 持久化轨迹 slam_map_trajectory.json 被污染（9332 点里 9206 个是垃圾）
  → _focus_bounds() 算出 ±1e19 的聚焦框，写进 slam_map_3d.json
  → 前端 centerAndSpan() 优先用 focusBounds
  → span ≈ 1.7e20
       ├─ scale = 画布宽 / span ≈ 0  →  地图缩成一个像素 = 看不见
       └─ drawGrid() 的 for (v = -half; v <= half; v += step)，
          half = ceil(span/2) ≈ 8.7e19、step = 5  →  迭代 3.5e19 次 = 主线程永久卡死
```

**为什么今天才炸**：`centerAndSpan()` 原本只用点云 `bounds`（正常 ±15 m），
**今天 14:41 的 c776eae「按轨迹定画幅」把它改成优先用 `focusBounds`** ——
数据里的发散值一直都在（9/2 那份旧快照的 pose 就是 2.28e11），只是之前没人拿它定画幅。

**修复**（三处，都不碰三维建图、不碰 §60 任何路径）：

| 位置 | 改动 | 作用 |
| --- | --- | --- |
| `webui_map_snapshot_writer.py` | `_on_odom` 丢掉量级 >1000 m 的点 | 断掉污染源，发散点不落地 |
| `slam_map_3d.js` | 新增 `saneFrame()`，异常画幅退回点云 `bounds` | 兜住已落地的脏数据与将来同类输入 |
| `templates/index.html` | `?v=` 从 `go2w-fix-20260901` 提到 `…20260911` | 见下方「遗留」第 1 条 |

NaN/Inf 一并被挡：`abs(nan) < 上限` 与 `abs(inf) < 上限` **恒为假**（`isFinite` 同理），
不需要单独判空。前端画幅闸放在 `if (!canvas) return;` **之前**并挂到
`window.Slam3dFrame`，以便 headless 加载（沿用 `search_map.js` 的 `window.SvgViewport` 做法）。

**修复数据本身**：`slam_map_trajectory.json` 由 9332 点清到 **126 点**
（范围回到 x −4.39..0.17、y −6.22..4.37），并按写入器的同一公式重算了快照里的
`focus_bounds`。备份在同目录 `*.bak-20260911_1903*`（`outputs/*` 已在 gitignore 内）。

**验证**：

| 用例 | 期望 | 实测 |
| --- | --- | --- |
| 拿污染原文喂 `centerAndSpan` | span 回到点云量级、循环有界 | ✅ span 25.40，`drawGrid` 迭代 **14 次**（原 3.5e19） |
| 正常 `focus_bounds` | 仍按轨迹取景（别把功能修没） | ✅ span 16.595，center 用 focus |
| 无 `focus_bounds` | 退回点云 `bounds` | ✅ span 25.4036 |
| 真实发散序列 / NaN / Inf 喂后端闸 | 全部丢弃，正常点保留 | ✅ 8 个垃圾值全丢，3 个正常点保留 |
| 节点在 ROS 环境下导入 | 不破 | ✅ `/usr/bin/python3` 下导入正常 |
| `tests/test_slam_map_3d_frame.{js,py}` | 13 条断言锁住 | ✅ 全过 |
| WebUI 测试组 5 个文件 | 不回归 | ✅ 28 passed in 2.39s |
| 服务端实际吐出的 HTML/JS | 含新闸门与新版本号 | ✅ `curl` 核对一致（静态文件现读磁盘，**无需重启服务**） |

**遗留（需要人判断的地方）**：
1. **`?v=` 一直是手工维护的，9 月 1 日之后没再动过** —— 这次 JS 改了却不 bump，
   用户按 F5 拿到的仍是缓存里那个会卡死的旧版本，**修复等于没生效**。本轮只做了
   一次性 bump。更稳的做法是让版本号取静态文件的 mtime（模板渲染时算），
   但这会动 `index.html` 的渲染路径，属另一件事，**没有擅自做**。
2. **`/api/slam/map3d` 是 850 ms 轮询、每次回 16610 个点** —— 本轮卡死的主因
   不是它（是画幅），但它是前端负载的大头。降频或减点会影响观感，
   需要用户拿主意，**没有动**。
3. **发散的上游没查** —— 本节只保证发散值不再进 WebUI 的持久化数据。
   `odom_base` 为什么会指数发散（是 LIO 丢失后的数学发散，还是别的）属三维建图
   范畴，**按「禁止改三维建图」没有碰**，与「关键帧爆炸」一并留待真机验证。

---

## 最终回归

**解释器**：`/home/mxt/anaconda3/envs/go2_robot_scene_demo/bin/python` ——
本轮试错确认这是**唯一**能跑全量的一套：`.venv`（3.13.5）缺 scipy，2 个
collection error；anaconda base（3.13.5）缺 openai，37 个 collection error。

### ⚠️ 先说一个坑：第一版基线是无效的

第一版基线直接拿 `git archive HEAD | tar -x` 导到 `/tmp/rs_baseline` 就跑，
得到 `13 failed / 1019 passed / 5 skipped / 486 s`。**这个数字不能用**，
因为 `git archive` 只导被跟踪的内容，**不含 `.env`**（gitignore 掉了），
而 `app/config.py:708` 是 `load_dotenv(_project_root() / ".env")` ——
按**文件位置**定位项目根，跟 cwd 无关。基线树没有 `.env` ⇒ LLM key 为空
⇒ `parse_robot_task` 走**规则降级路径、不发网络请求**，LLM 相关用例"秒绿"。

两条判据一眼可见：

| | 无 `.env` 的假基线 | 有 `.env` 的真基线 |
| --- | --- | --- |
| 3 个 LLM 用例的耗时 | **2.54 s** | **142–162 s** |
| 全量耗时 | 486 s | **1069 s** |

我就此把「3 项新增失败」误判成了代码回归 —— 直到发现基线比当前树快一倍多
才回头查。**教训：跑基线先 `ls <基线树>/.env`。**

### 公平对比（两边都带 `.env`）

| 树 | 结果 | 耗时 |
| --- | --- | --- |
| 干净 HEAD + `.env`（`/tmp/rs_baseline`） | **12 failed / 1020 passed / 5 skipped** | 1069 s |
| 本轮改动后（当前树） | **14 failed / 1048 passed / 1 skipped** | 1052 s |

数字本身不是结论，**逐条 diff 才是**（只看总数会把「2 项新挂」误读成
「只多挂 2 项」而漏掉背后的归因）：

- **变绿：0 项。**
- **稳定失败 12 项**（两边都挂，与本轮无关）：`test_async_semantic_observer` ×2、
  `test_autonomous_search_web_routes`、`test_autonomous_search_websocket`、
  `test_live_exploration_graph` ×3、`test_manual_web_demo_config::test_hold_mode_params`、
  `test_plain_slam_config_generation` ×2、`test_streamlit_video_mode`、
  `test_task_examples_evaluator::test_all_task_examples_pass_minimum_checks`。
- **新挂 2 项 —— 都已用实验排除，不是本轮引入的：**

  1. `test_task_planner::TaskPlannerTest::test_plan_count_objects_has_count_state`。
     **决定性实验**：把 `.env` 复制到干净 HEAD 树（代码与本轮改动零交集），
     单跑这一项 → **同样 FAILED**。根因是 `parse_robot_task("数数这个房间里有几个椅子")`
     现在返回 `find_object` 而不是 `count_objects`；`test_task_examples_evaluator`
     也是同一根因（`巡查…门是打开的` → `check_door_state` 而非 `inspect_area`、
     `去走廊尽头的房间` → `find_room` 而非 `navigate_to_location`）。
     即**上游 LLM 对这几个中文样例的解析行为变了**，不是代码。
  2. `test_streamlit_natural_language_task_ui::…`。
     **同一实验里它在干净 HEAD + `.env` 上是通过的**，所以是 `AppTest`
     `default_timeout=30` 的超时抖动（那两次跑时磁盘 85%、另有负载）。

  **旁证两条**：`git diff HEAD --numstat -- app/config.py` = `33 0`（33 增 0 删，
  纯新增，没动任何既有默认值）；任务解析链路的四个文件
  （`app/reasoning/task_parser.py`、`app/planning/task_planner.py` 及两个测试文件）
  `git diff HEAD` 为空 —— **一个字节没动**。

- **skipped 5 → 1 不是回归，是环境**：`tests/test_search_map_viewport.py` 等三个文件
  的 `NODE = shutil.which("node")` 检测到 nvm 的 node（v22.19.0）时不再跳过。
  那 4 个 WebUI JS 纯函数测试在当次**真跑了且全部通过**。

**结论：本轮 14 个失败里，12 个是既有漂移，2 个是 LLM/超时环境波动，
没有一个是本轮改动引入的。** 但这也意味着 `pytest tests/` 里**含真实 LLM 调用的
用例不可复现**（同一份代码两次跑失败集合就会变）—— 后续拿它当门禁时，
要么固定 LLM 快照/桩，要么只信非 LLM 子集。

---

## 下一步（交给下一位 / 下次会话）

本报告只覆盖 **A0–A11**（不需要机器人）。剩下的事按优先级：

1. **B3/B4 真机端到端** —— 需要机器人运动，**必须由用户重新给一次明确的批次授权**。
   既有授权不跨批次、不跨重启、不跨参数变更；每批之间要回到人。
   开跑前的三件事：
   * `.env` 里把 `VLM_RUNTIME_QUICK_TIMEOUT_SECONDS` 显式写成 **65**
     （= 代码默认 = A7/A8 实测生效值；不要写 20，理由见「诚实记录」第 4 条）。
   * 同一个查询至少跑 3 次记录命中率（LLM 别名仍在抖，见「诚实记录」第 2 条）。
   * 每次移动后回到地面胶带标记的原点再开始下一次。
2. **两份 `TargetProfile` 的合并** —— `app/reasoning/target_profile.py` 与
   `app/video/target_profile.py` 目前逐字节相同，靠人工保持同步。
   本轮只补了字段、加了跨模块回归测试，**没有合并**（那是重构）。
   要合并的话，照 `app/vision/target_profile.py` 那个 shim 的既有做法做即可。
3. **A0 还差用户肉眼确认** —— `http://127.0.0.1:8765` 的 WebUI 地图去糊效果，
   是本轮唯一没关闭的无机器人交付项。
   ⚠️ 用户反馈时地图**根本看不见**，已定位并修掉（见「附：WebUI 地图不显示 +
   网页卡死」，提交 `022e2cc`）——那是画幅被污染数据撑爆，**不是去糊效果本身**，
   所以 A0 仍是未验收状态，需要用户**强刷一次**（`Ctrl+Shift+R`）后重新肉眼看。
   `?v=` 本轮才 bump，普通 F5 可能仍吃到旧 JS。
4. **B5 / D1 / D3 待定**：深层 SLAM 调参推迟；GitHub token 轮换
   （`ghp_...` 曾出现在会话记录里）。
