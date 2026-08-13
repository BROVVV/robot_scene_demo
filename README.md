# robot_scene_demo 从零部署与运行手册

本文档目标：把本文件交给 AI 或运维人员后，可以在一台“什么都没配置”的 Ubuntu 机器上，从零部署并跑通完整流程：

- mock 场景理解
- 硅基流动视觉 API 场景理解
- GroundingDINO + SAM2 本地开放词表检测
- LLM-first GroundingDINO Prompt Expansion：把“找到卧室 / 检查打开的门”等自然语言任务转成 DINO 可检测的英文物体、结构和锚点词表
- 第一视角视频目标搜索与视频语义记忆
- 无人工先验的大模型自生成常识推理与观察记忆导航
- Streamlit Web UI
- ROS2 可接收的 `/cmd_vel` 兼容 dry-run 输出
- ROS2 Humble Navigation2 的真实全局规划、导航执行、反馈、取消与路径可视化
- 平台避障辅助下的自适应移动距离 Motion Horizon 输出
- Go2-W 真机：内置 RGB/LiDAR/IMU 采集、LLM 视觉目标搜索（硅基流动 API）、
  wheel+LIO 融合里程计、短步搜索状态机与录像叠加（详见下方
  “Go2-W 真机项目当前进度还原指南”）

项目默认仍是安全的离线/半离线 Demo，不会控制真实机器狗。旧
`ros2_motion_plan.json` 只保留作兼容调试；正式导航链路使用独立的 ROS2 Humble
Nav2 Worker，并且默认 `disabled`。只有环境变量允许、CLI/Web UI 再次确认、
footprint 与急停确认全部通过时，`execute` 模式才会请求 Nav2 执行。

动态运动视界只决定候选观察位姿、搜索半径或停止距离，不直接生成正式避障轨迹。
全局/局部规划交给 Nav2，最终硬件保护仍由 Collision Monitor、机器狗底层、
厂商 SDK 和操作员急停共同负责。

## Go2-W 真机项目当前进度还原指南（2026-08-13）

> 本节是“照着做就能还原当前真机进度”的权威步骤。项目其余章节保留离线/视频/
> Nav2 软件流程。硬件证据（bag、录像、JSONL）体积大且含私人画面，**不进入
> 本仓库**；本节给出证据所在的本机路径。

### 0.1 当前能力状态（真机验收结论）

| 能力 | 状态 | 说明 / 证据 |
|---|---|---|
| 内置 RGB ROS2 桥（RPC） | PASS | `/camera/front/image_raw(+compressed)`、CameraInfo；损坏帧跳过 + 自动重连 |
| 相机内参（9×6，15 mm） | PASS | `configs/go2w/camera_intrinsics.yaml`；105 视角 |
| LiDAR/IMU 时间桥 | PASS | `configs/go2w/time_sync.yaml`；云 RMSE <1 ms |
| base→LiDAR TF | PASS（实机复核） | `official_reference.yaml`，pitch −15.09°（z-up） |
| LiDAR 预处理 /scan / clearance | PASS（静止实测） | `lidar_preprocess.yaml`；自过滤、720-bin |
| 轮式里程计 `/go2w/odom/wheel` | EXPERIMENTAL | 四轮 dq×0.089 m + Sport yaw；转弯跳过平移 |
| 融合里程计 `/go2w/odom/fused` | EXPERIMENTAL | 轮式平移 + Sport/LIO 融合航向；LIO 门禁自动回退 |
| Point-LIO 转向（yaw） | PASS | ±10° 实测 89% 幅度、符号正确（yaw_reflect） |
| Point-LIO 平移 | **BLOCKED** | 0.2/0.4 滤波均失败（偏 69°/塌缩/发散），只用其 yaw |
| USLAM `/uslam/*` | **BLOCKED** | 当前固件未启用 |
| LLM 快速检测（硅基流动） | PASS | `--detector llm`，默认 30B-A3B，单次 5–15 s |
| LLM 目标复核（`--verify`） | PASS | 到达前确认物体身份，防椅子/书包误判 |
| 自主搜索 scan360/level_a | PASS（小范围） | 找到→对齐→靠近→复核→`target_reached` |
| 状态机 `state_machine_search` | PASS（软件+真机） | `app/live_robot` 正式链路 |
| RGB–LiDAR 外参/3D 定位 | EXPERIMENTAL（未几何确认） | 仅静止演示，不用于导航 |
| Level D / 地图 / Nav2 | **BLOCKED** | `navigation_gate.yaml` fail-closed 未改动 |

### 0.2 硬件与网络前置

- Ubuntu 22.04 x86_64 + ROS2 Humble；至少 16 GB RAM；
- 主机直连网口 `enp6s0`：`192.168.123.99/24`，机器人 `192.168.123.18`；
- 机器狗处于 `ai-w` 运动模式，`/lf/sportmodestate` 的 `mode=1, error_code=0`；
- 运动授权：操作者明确授权 `GO2W_MOTION_READY`，小范围（半径 ≤1.0 m）活动与
  转向；场地清空、遥控器可急停。

### 0.3 需要提前就位的项目（不在本仓库内）

1. **unitree_go2w_control**（当前在 `/home/brov/robot/unitree_go2w_control`，
   软链 `/home/brov/unitree_go2w_control`）：提供
   `scripts/setup_go2w_ros2.sh`、`hold_sport_lease.py`、
   `go2w_motion_control`（Action `/go2w/motion`、服务 `/go2w/arm`、
   `/go2w/emergency_stop`）、`go2w_motion_interfaces`；
2. **unitree_ros2 / cyclonedds_ws**（Humble 消息 + CycloneDDS 配置）；
3. **Grounded-SAM-2**（可选，`--detector grounded_sam` 才需要）；
4. **Point-LIO 隔离环境**：`conda env go2w_point_lio_noetic` +
   `point_lio_ws`（构建时应用 `patches/go2w/point_lio_noetic_pcl115.patch`）。

### 0.4 环境与构建（一次性）

```bash
# 主项目：Conda 环境 go2_robot_scene_demo + 依赖
cd /home/brov/robot/robot_scene_demo
bash scripts/go2w/install_dependencies.sh

# ROS2 工作区（系统 Python /usr/bin/python3）
bash scripts/go2w/build_ros2.sh

# Point-LIO（Noetic 隔离；首次需要 clone 上游 + 打补丁）
bash scripts/go2w/setup_point_lio_noetic.sh

# 运动控制工作区（unitree_go2w_control 内）
cd /home/brov/robot/unitree_go2w_control
source /opt/ros/humble/setup.bash
colcon build --packages-select go2w_motion_interfaces go2w_motion_control
```

配置模板（**不含任何密钥**）：

```bash
cp .env.example .env          # 填入 SILICONFLOW_API_KEY（不要提交）
# .env.go2w 已在本仓库，按需使用
```

### 0.5 启动顺序（每次真机运行）

```bash
# 终端 1：只读感知栈（相机/LiDAR/时间/融合/Bundle）
cd /home/brov/robot/robot_scene_demo
bash scripts/go2w/start_live_perception.sh

# 终端 2：轮式 + 融合里程计（/go2w/odom/wheel 与 /go2w/odom/fused）
source /opt/ros/humble/setup.bash
source /home/brov/robot/unitree_ros2/cyclonedds_ws/install/setup.bash
source /home/brov/robot/robot_scene_demo/ros2_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file:///home/brov/robot/robot_scene_demo/configs/go2w/cyclonedds_go2w.xml"
ros2 launch go2w_lio_bringup wheel_odom.launch.py

# 终端 3：运动控制（lease holder + Action server）
cd /home/brov/robot/unitree_go2w_control
source scripts/setup_go2w_ros2.sh
ros2 launch go2w_motion_control go2w_motion_control.launch.py

# 终端 4（可选，供融合航向）：Point-LIO + 桥
cd /home/brov/robot/robot_scene_demo
POINT_LIO_OUTPUT_DIR=outputs/go2w_acceptance/lio_xxx \
POINT_LIO_USE_IMU_AS_INPUT=false \
POINT_LIO_FILTER_SIZE_SURF=0.2 POINT_LIO_FILTER_SIZE_MAP=0.2 \
scripts/go2w/run_point_lio_ros1.sh
# 另开终端：
ros2 launch go2w_lio_bringup point_lio.launch.py \
  lio_config:=configs/go2w/point_lio.yaml \
  reference_config:=configs/go2w/official_reference.yaml \
  time_config:=configs/go2w/time_sync.yaml
```

启动后自检：

```bash
ros2 topic hz /camera/front/image_raw      # ~15–28 Hz
ros2 topic hz /go2w/odom/wheel             # 20 Hz
ros2 topic hz /go2w/odom/fused             # 20 Hz
ros2 topic echo /go2w/odom/fused/status --once
ros2 topic echo /lf/sportmodestate --once  # mode=1 error_code=0
```

### 0.6 自主搜索运行命令

```bash
cd /home/brov/robot/robot_scene_demo
source /opt/ros/humble/setup.bash
source /home/brov/robot/unitree_ros2/cyclonedds_ws/install/setup.bash
source /home/brov/robot/unitree_go2w_control/ros2_ws/install/setup.bash
source /home/brov/robot/robot_scene_demo/ros2_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file:///home/brov/robot/robot_scene_demo/configs/go2w/cyclonedds_go2w.xml"

# 360° 扫描 → 高分提前停止 → 靠近 → LLM 复核 → 到达（推荐演示）
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode scan360_approach --target "灰色书包" --detector llm \
  --llm-model Qwen/Qwen3-VL-30B-A3B-Instruct \
  --target-score-min 0.45 --max-radius 1.0 --max-seconds 420 \
  --reach-area-ratio 0.08 --odom-topic /go2w/odom/fused \
  --record-video outputs/go2w_acceptance/scan360_demo.mp4 \
  --output outputs/go2w_acceptance/scan360_demo.jsonl

# Level A 摆动搜索（发现→对齐→靠近）
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode level_a_search --target "手机" --detector llm \
  --llm-model Qwen/Qwen3-VL-30B-A3B-Instruct \
  --max-radius 1.0 --max-seconds 300 --odom-topic /go2w/odom/fused \
  --output outputs/go2w_acceptance/level_a_demo.jsonl

# 正式 app/live_robot 状态机驱动
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode state_machine_search --target "灰色书包" --detector llm \
  --llm-model Qwen/Qwen3-VL-30B-A3B-Instruct \
  --reach-area-ratio 0.08 --max-radius 1.0 --max-seconds 300 \
  --odom-topic /go2w/odom/fused \
  --record-video outputs/go2w_acceptance/sm_demo.mp4 \
  --output outputs/go2w_acceptance/sm_demo.jsonl
```

每次运动都满足：短步（前进 2 s×0.12 m/s、转向 ≤30°）、轮式/融合里程计校验、
前向净空门禁、`mode/error` 检查、无位移自动重试/绕障、结束三次 STOP + disarm。

### 0.7 关键配置与代码索引

```text
configs/go2w/camera_intrinsics.yaml      # 相机内参（已标定）
configs/go2w/official_reference.yaml     # 官方几何 / base→LiDAR
configs/go2w/time_sync.yaml              # LiDAR/IMU 时间桥
configs/go2w/lidar_preprocess.yaml       # /scan、clearance、自过滤
configs/go2w/wheel_odom.yaml             # 轮式 + 融合里程计参数
configs/go2w/point_lio.yaml              # Point-LIO 桥接/门禁
configs/go2w/point_lio_unilidar_l2.yaml  # 官方 L2 基线（identity 外参）
configs/go2w/navigation_gate.yaml        # fail-closed，未改动
scripts/go2w/run_autonomous_loop.py      # pattern/wander/camera_guided/
                                         # level_a_search/scan360_approach/
                                         # state_machine_search
app/detectors/siliconflow_vision_worker.py   # LLM quick/verify worker
app/live_robot/step_planner.py               # 纯函数步进规划
app/live_robot/step_search_runner.py         # 状态机编排器
ros2_ws/src/go2w_lio_bringup/.../wheel_odom.py  # 融合里程计
reports/go2w_codex_handoff_20260807.md      # 最新交接报告（本机）
```

### 0.8 证据位置（本机，未入库）

```text
outputs/go2w_acceptance/camera_calibration_20260806/
outputs/go2w_acceptance/time_bridge_live/
outputs/go2w_acceptance/lidar_preprocessor_live_corrected_tf/
outputs/go2w_acceptance/imu_turn_verify_20260807/          # 转向矩阵/搜索演示
outputs/go2w_acceptance/restart_verify_20260807/           # 相机修复后 LLM 搜索
outputs/go2w_acceptance/lio_calibration_20260807/          # LIO 直线试验
outputs/go2w_acceptance/fusion_validation_20260807/        # 融合里程计试验
```

### 0.9 常见故障与解决

- **lease 3207**：旧 `go2w_motion_action_server`/`hold_sport_lease` 残留 →
  kill 后重启运动栈；
- **相机断流**：新版相机桥损坏帧跳过 + 3 s 读超时 + 自动重连（退避 1→10 s）；
- **Bundle 陈旧**：>5 s 且重试 6 次仍不更新 → 自主循环拒绝动作并安全中止；
- **goal 被拒 “motion is not armed”**：LLM 检测可能超过 60 s arm 时限，脚本
  每次发动作前自动重新 arm；
- **LLM 慢/超时**：默认 `--llm-model Qwen/Qwen3-VL-30B-A3B-Instruct`（约 5–15 s）；
  8B 更慢；`SILICONFLOW_TIMEOUT_SECONDS` 在 `.env` 中调大；
- **椅子被当书包**：到达前会先 `--verify` 复核，`is_target=false` 则右转 15°
  继续观察；仍误判时建议加颜色/结构属性约束；
- **LIO 平移不可用**：属已知 BLOCKED，用 `/go2w/odom/fused`（轮式平移 +
  融合航向）；轮半径 0.089 m 为标称值，尚未标定。

### 0.10 安全硬约束

禁止 `/lowcmd`、`LowCmd`、`ReleaseMode()`、`Damp()`、固件修改、关闭安全保护；
Level D–F / Nav2 保持 fail-closed（`navigation_gate.yaml` 未改动）；任何运行
前确认场地与遥控器。

## 0. 推荐硬件与系统前提

推荐系统：

- Ubuntu 22.04 或 24.04 x86_64
- 至少 16 GB RAM
- 至少 20 GB 可用磁盘
- NVIDIA GPU 用于 GroundingDINO + SAM2，本项目已验证 RTX 4090 + CUDA PyTorch 可运行

Nav2 正式规划/执行固定支持 **Ubuntu 22.04 + ROS2 Humble**。Ubuntu 24.04
仍可运行感知、推理、Web UI 和 `offline_preview`，但不属于本项目的 Humble
Worker 验收平台。

如果没有 NVIDIA GPU：

- `mock`、`真实 API`、LLM runtime prior、观察记忆、Streamlit UI 可以跑。
- `GroundingDINO+SAM2` 可能无法跑通或速度极慢，不建议作为验收标准。

检查 GPU：

```bash
nvidia-smi
```

如果 `nvidia-smi` 不存在或报错，先安装 NVIDIA 驱动并重启。Ubuntu 常用方式：

```bash
sudo ubuntu-drivers devices
sudo ubuntu-drivers autoinstall
sudo reboot
```

重启后再次确认：

```bash
nvidia-smi
```

## 1. 安装系统基础依赖

```bash
sudo apt update
sudo apt install -y \
  git curl wget ca-certificates build-essential pkg-config \
  libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
  tmux unzip aria2
```

确认：

```bash
git --version
curl --version
tmux -V
```

## 2. 安装 Miniconda

如果系统已经有 conda，可以跳过本节。

```bash
cd /tmp
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

安装时建议允许初始化 shell。安装完成后重新打开终端，或执行：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda --version
```

如果 conda 安装在 `/opt/conda`，则执行：

```bash
source /opt/conda/etc/profile.d/conda.sh
conda --version
```

## 3. 获取项目代码

选择一个工作目录，例如 `/root/gpufree-data` 或 `/home/$USER/workspace`。

```bash
mkdir -p /root/gpufree-data
cd /root/gpufree-data
git clone https://github.com/BROVVV/robot_scene_demo.git
cd robot_scene_demo
```

如果你已经有项目目录：

```bash
cd /root/gpufree-data/robot_scene_demo
```

确认结构：

```bash
ls
```

应看到：

```text
app  data  docs  examples  scripts  tests  run_demo.py  streamlit_app.py
```

## 4. 创建 Python 环境

```bash
conda create -n go2_robot_scene_demo python=3.11 -y
conda activate go2_robot_scene_demo
```

确认：

```bash
which python
python --version
```

应显示 Python 3.11，且路径在 `go2_robot_scene_demo` 环境内。

升级 pip 并安装项目基础依赖：

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果网络慢，可以换源：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 5. 配置 `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

基础配置建议：

```text
SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-8B-Instruct
SILICONFLOW_TIMEOUT_SECONDS=25
SILICONFLOW_MAX_TOKENS=2048
IMAGE_MAX_SIDE=1280
IMAGE_DETAIL=high
ENABLE_LOW_OBJECT_RETRY=true
MIN_OBJECTS_FOR_COMPLEX_SCENE=6
OUTPUT_DIR=outputs

DETECTION_BACKEND=llm
```

重要说明：

- `DETECTION_BACKEND=llm` 只需要硅基流动视觉 API，不需要本地 GPU 检测模型。
- `DETECTION_BACKEND=grounded_sam` 会调用本地 GroundingDINO + SAM2，并且默认先调用 LLM 生成 GroundingDINO 英文开放词表 prompt。
- 如果要跑 `grounded_sam` 主流程，建议同时配置 `SILICONFLOW_API_KEY`；否则 prompt expansion 会明确失败，避免 GroundingDINO 静默收到空 prompt。
- 如果只是调试本地 worker，可先不配置 API Key，直接使用第 8.6 节手写 `--text-prompt` 验证 GroundingDINO/SAM2 环境。

平台避障辅助动态运动视界建议配置：

```text
PLATFORM_OBSTACLE_AVOIDANCE_ASSUMED=true
ENABLE_DYNAMIC_MOTION_HORIZON=true
MOTION_HORIZON_PROFILE=platform_assisted_auto
MOTION_STRICT_SAFE_MAX_STEP_M=0.5
MOTION_PLATFORM_INDOOR_DEFAULT_STEP_M=1.2
MOTION_PLATFORM_INDOOR_MAX_STEP_M=2.0
MOTION_PLATFORM_OPEN_DEFAULT_STEP_M=3.0
MOTION_PLATFORM_OPEN_MAX_STEP_M=5.0
MOTION_ABSOLUTE_MAX_STEP_M=6.0
MOTION_TARGET_CONFIRM_MAX_STEP_M=0.8
MOTION_PLATFORM_FALLBACK_STEP_M=1.5
MOTION_DEFAULT_STOP_AND_REOBSERVE=true
MOTION_ENABLE_OBSERVE_WHILE_MOVING=false
MOTION_SHORTEN_ON_TARGET_CANDIDATE=true
MOTION_ALLOW_LLM_RECOMMENDED_HORIZON=true
MOTION_LLM_HORIZON_WEIGHT=0.6
```

无人工先验导航建议配置：

```text
STATIC_KNOWLEDGE_BASE_ENABLED=false
HANDWRITTEN_OBJECT_PRIORS_ENABLED=false
HANDWRITTEN_LOCATION_PRIORS_ENABLED=false
HANDWRITTEN_ROOM_PRIORS_ENABLED=false
STATIC_OBJECT_PROMPTS_ENABLED=false
ALLOW_HANDCRAFTED_SEARCH_RULES=false

LLM_COMMONSENSE_PRIOR_ENABLED=true
LLM_PRIOR_GENERATION_MODE=runtime
LLM_PRIOR_CAN_CONFIRM_TARGET=false
LLM_PRIOR_MAX_HYPOTHESES=8
LLM_PRIOR_MAX_DETECTOR_PROMPTS=12

EVIDENCE_GATING_ENABLED=true
TARGET_CONFIRMATION_REQUIRE_VISUAL_EVIDENCE=true
TARGET_CONFIRMATION_REQUIRE_BBOX=true
TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY=true
TARGET_CONFIRMATION_MIN_SCORE=0.72

OBSERVATION_MEMORY_ENABLED=true
OBSERVATION_MEMORY_STORE_PATH=data/memory/observational_memory.jsonl
OBSERVATION_MEMORY_WRITE_VISUAL_ONLY=true
OBSERVATION_MEMORY_REQUIRE_PROVENANCE=true

PRIOR_USAGE_AUDIT_ENABLED=true
PRIOR_USAGE_REPORT_PATH=outputs/prior_usage_report.json
```

视频目标搜索与建图辅助建议配置：

```text
VIDEO_MODE_DEFAULT=target_search
VIDEO_ENABLE_SCENE_MAPPING_DEFAULT=false
VIDEO_ENABLE_NAVIGATION_TOPOLOGY_DEFAULT=false
VIDEO_USE_SCENE_MAP_FOR_SEARCH_DEFAULT=true
VIDEO_ALLOW_SCENE_MAP_ONLY_DEBUG=false
VIDEO_TARGET_SEARCH_REQUIRED_WHEN_TARGET_PRESENT=true
VIDEO_SCENE_MAPPING_REUSE_TARGET_FRAMES=true
VIDEO_SCENE_MAPPING_REUSE_OBJECT_TRACKS=true
VIDEO_ENABLE_SCENE_MEMORY=true
VIDEO_FULL_SCENE_MAP_ENABLED=false
VIDEO_ALWAYS_WRITE_MEMORY=true
VIDEO_ENABLE_VIDEO_PSG=true
VIDEO_TOPOLOGY_ANNOTATE_TARGET_SEARCH=true
VIDEO_TOPOLOGY_ADD_TARGET_CANDIDATE_NODES=true
VIDEO_TOPOLOGY_ADD_TARGET_SEARCH_SCORES=true
```

视频运行模式的主任务永远是 `target_search`。Web UI 不再提供“视频全场景建图”作为顶层运行模式；全场景建图、导航拓扑图和拓扑辅助排序都只是“视频目标搜索”内部的辅助功能。`scene_map_only` 只保留给 CLI 高级调试，不适合真实导航目标搜索。

Video-to-Navigation Planning 默认启用，但只生成安全的视觉预览规划，不会控制机器人：

```text
VIDEO_NAVIGATION_ENABLED=true
VIDEO_NAVIGATION_MODE=visual_preview
VIDEO_POSE_BACKEND=auto
VIDEO_POSE_ALLOW_RELATIVE=true
VIDEO_POSE_REQUIRE_METRIC_FOR_NAV2=true
VIDEO_NAVIGATION_AUTO_PLAN=true
VIDEO_NAVIGATION_AUTO_EXPLORATION=true
VIDEO_NAVIGATION_MAX_FRAMES=300
VIDEO_NAVIGATION_FRAME_SAMPLE_INTERVAL=5
VIDEO_NAVIGATION_TARGET_OBSERVATION_DISTANCE=1.5
VIDEO_NAVIGATION_ENABLE_FRONTIER_EXPLORATION=true
VIDEO_NAVIGATION_EXPLORATION_MAX_CANDIDATES=8
VIDEO_NAVIGATION_ALLOW_NAV2_FROM_METRIC_VIDEO=false
VISUAL_NAV_EXECUTION_ENABLED=false
```

普通 RGB MP4 会标记为 `scale_status=relative`，Web UI 显示 `Visual Preview / Relative`，路径长度使用相对单位，不会伪装成米制 Nav2 路径。只有 RGB-D、双目、视觉惯性或外部标定提供可靠尺度与 `map` 坐标变换后，才允许进入真实 Nav2 handoff。CLI 可通过 `--video-map-transform-json` 提供 `T_map_video_map`：

```json
{
  "T_map_video_map": {
    "x": 1.2,
    "y": -0.4,
    "yaw": 0.0,
    "source": "external_calibration"
  }
}
```

GroundingDINO Prompt Expansion 建议配置：

```text
GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=true
GROUNDING_PROMPT_REQUIRE_NON_EMPTY=true
GROUNDING_PROMPT_FAIL_FAST_ON_EMPTY=true
GROUNDING_PROMPT_RETRY_ON_EMPTY=true
GROUNDING_PROMPT_MAX_RETRIES=1
GROUNDING_PROMPT_MAX_TERMS=24
GROUNDING_PROMPT_MIN_TERMS=3
GROUNDING_PROMPT_DEBUG_OUTPUT=outputs/grounding_prompt_plan.json
GROUNDING_PROMPT_RETRY_DEBUG_OUTPUT=outputs/grounding_prompt_retry_plan.json
```

这组配置是 GroundingDINO+SAM2 主流程的关键桥接层。系统不会把“卧室 / 房间 / 区域”这种抽象场景词直接作为唯一 DINO prompt，而是让 LLM 根据完整任务语义生成英文可见代理物体、结构锚点、门牌/入口/标识等开放词表，例如：

```text
bed . wardrobe . nightstand . curtain . window . door . doorway . room entrance .
```

门状态任务也不会只靠 DINO 判断“打开/关闭”，而是先检测 `door / doorway / door frame / door handle` 等可见对象，再交给后续 crop verify、视觉模型或状态判断模块确认。

如果你没有 API Key，又必须临时跑 `grounded_sam` 主流程，可以在 `.env` 中关闭 LLM prompt expansion：

```text
GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=false
```

关闭后系统会回到 TargetProfile / dynamic terms 兼容路径；这只适合作为离线调试或兼容旧流程，不是推荐实验配置。

本项目现在的“常识”来自运行时 LLM 假设和机器人观察记忆，不来自开发者写死的物体-位置先验。LLM 假设只能用于排序搜索区域、生成动态检测词、建议下一视角；目标是否找到必须通过 bbox/crop/mask/frame 等视觉证据门控。`TARGET_CONFIRMATION_REQUIRE_CROP_VERIFY=true` 表示有视觉 API Key 时启用 crop 复核硬门控；如果当前环境没有可用 API Key，系统会自动降级为 bbox/frame/mask/分数门控，避免本地 GroundingDINO+SAM2 检测结果因无法调用 crop verifier 而全部被拒绝。

策略档位说明：

- `strict_safe`：严格安全模式，恢复最大 0.5m 单段距离。
- `platform_assisted_indoor`：室内平台避障辅助，通常 0.8m 到 2.0m。
- `platform_assisted_open_area`：开放区域平台避障辅助，通常 2.0m 到 5.0m。
- `platform_assisted_auto`：根据场景类型、任务阶段、目标候选状态自动选择。

安全要求：

- 不要把真实 API Key 写入 README。
- 不要提交 `.env`。
- 不要把 `.env` 发给别人。
- 如果 API Key 曾经暴露在聊天记录或日志里，建议到硅基流动后台轮换一次。

确认 `.env` 没被 Git 跟踪：

```bash
git status --short .env
```

正常应显示：

```text
?? .env
```

或无输出；只要不是准备提交的 tracked 文件即可。

## 6. 先跑基础验收

### 6.1 核心 smoke test

```bash
python -m py_compile \
  app/config.py \
  app/perception/grounding_prompt_planner.py \
  app/detectors/grounded_sam_subprocess.py \
  app/detectors/grounded_sam_worker.py \
  run_demo.py \
  streamlit_app.py

python -m unittest \
  tests.test_grounding_prompt_planner \
  tests.test_grounded_sam_prompt_integration \
  tests.test_grounded_sam_runtime
```

期望：

```text
OK
```

可选全量回归：

```bash
python -m unittest discover -s tests
```

当前开发分支上，全量回归可能出现两类非部署阻塞问题：

- Streamlit AppTest 在无浏览器/低资源 bare mode 下超时。
- `scripts/evaluate_task_examples.py` 中部分 legacy task type 期望仍按旧任务模板断言，而当前系统已切到 LLM-first 自然语言任务理解。
- 部分新测试使用 pytest 风格，例如视频参数归一化和建图辅助链路；基础 `requirements.txt` 不包含 pytest。如需运行这些测试，先执行 `pip install pytest`，再运行 `python -m pytest tests/test_run_video_demo_args.py tests/test_scene_map_as_auxiliary.py`。

如果目标是验证“空机器能部署并跑通主流程”，优先以本节 smoke test、mock 流程、Web UI 健康检查和实际 `run_demo.py` 输出为准。

### 6.2 mock 流程

mock 不需要图片、不需要 API Key、不需要 GPU。

```bash
python run_demo.py --mock \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
```

成功后应生成：

```text
outputs/scene_result.json
outputs/object_table.csv
outputs/relation_table.csv
outputs/topology_graph.png
outputs/topology_graph.graphml
outputs/ros2_motion_plan.json
outputs/motion_horizon_decision.json
outputs/llm_generated_priors.json
outputs/dynamic_detector_prompts.json
outputs/grounding_prompt_plan.json
outputs/grounding_prompt_retry_plan.json
outputs/evidence_gating_report.json
outputs/observation_memory_updates.json
outputs/prior_usage_report.json
outputs/knowledge_aware_result.json
outputs/parsed_task.json
outputs/capability_gate_result.json
outputs/navigation_task.json
outputs/actionability_report.md
outputs/retrieved_knowledge.json
outputs/predictive_scene_graph.graphml
outputs/hypotheses.json
outputs/knowledge_updates.json
outputs/reasoning_report.md
```

兼容说明：旧参数 `--enable-knowledge` 仍可使用，但会提示 deprecated，并映射为 LLM runtime prior + observation memory + evidence gating；默认不再启用静态 KB 或手写位置先验。

### 6.3 任务样例回归

```bash
python scripts/evaluate_task_examples.py
```

理想输出 JSON 中包含：

```json
"passed": true
```

当前 LLM-first 任务理解改造后，部分样例仍使用旧版 `count_objects` / `navigate_to_location` 模板期望，可能输出 `passed=false`。只要失败项是 `legacy_task_type`，通常表示样例断言尚未同步新任务 schema，不代表部署失败。真正需要优先处理的是导入错误、配置错误、模型调用错误、输出文件缺失或主流程异常退出。

## 7. 跑硅基流动真实 API

准备一张图片，例如：

```bash
ls /root/gpufree-data/微信图片_20260617144106.jpg
```

运行：

```bash
python run_demo.py \
  --image "/root/gpufree-data/微信图片_20260617144106.jpg" \
  --target "巡查玄关区域，识别地面可通行区域和主要障碍物" \
  --detector llm \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
```

成功后会生成基础输出、LLM runtime prior、证据门控、观察记忆、审计报告和 ROS2 dry-run 指令文件。

如果报 `API 请求失败`：

1. 检查 `.env` 里的 `SILICONFLOW_API_KEY`。
2. 检查网络是否能访问 `https://api.siliconflow.cn/v1`。
3. 检查模型名 `Qwen/Qwen3-VL-8B-Instruct` 是否仍可用。
4. 临时调大超时：

```text
SILICONFLOW_TIMEOUT_SECONDS=60
```

## 8. 安装 GroundingDINO + SAM2

本节用于跑本地开放词表检测器。推荐有 NVIDIA GPU。

### 8.0 检查 CUDA Toolkit

GroundingDINO 本地扩展通常需要 `nvcc` 编译器。先检查：

```bash
nvcc --version
```

如果没有 `nvcc`，但 `nvidia-smi` 正常，可以安装 CUDA Toolkit。Ubuntu 22.04 + CUDA 12.8 示例：

```bash
cd /tmp
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-toolkit-12-8
```

加入环境变量：

```bash
echo 'export CUDA_HOME=/usr/local/cuda-12.8' >> ~/.bashrc
echo 'export PATH=$CUDA_HOME/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
nvcc --version
```

Ubuntu 24.04 时，把上面的 `ubuntu2204` 换成 `ubuntu2404`。如果你安装的是其他 CUDA 版本，把 `CUDA_HOME` 改成真实路径，例如 `/usr/local/cuda-12.1`。

### 8.1 安装 PyTorch GPU 版

进入项目环境：

```bash
conda activate go2_robot_scene_demo
cd /root/gpufree-data/robot_scene_demo
```

安装 CUDA 版 PyTorch。已验证 `cu128` 可用：

```bash
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

如果你的服务器驱动较旧，不支持 CUDA 12.8，可改用 PyTorch 官方给出的其他 CUDA wheel，例如 `cu121`：

```bash
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

验证：

```bash
python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
print("cuda", torch.version.cuda)
print("gpu", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
PY
```

`cuda_available` 应为 `True`。

### 8.2 下载 Grounded-SAM-2 源码

推荐放在项目同级目录：

```bash
cd /root/gpufree-data
git clone https://github.com/IDEA-Research/Grounded-SAM-2.git
cd Grounded-SAM-2
```

如果 `git clone` 很慢，可以先下载 zip 再解压：

```bash
cd /root/gpufree-data
wget -O Grounded-SAM-2.zip https://github.com/IDEA-Research/Grounded-SAM-2/archive/refs/heads/main.zip
unzip Grounded-SAM-2.zip
mv Grounded-SAM-2-main Grounded-SAM-2
cd Grounded-SAM-2
```

### 8.3 安装 Grounded-SAM-2 和 GroundingDINO 依赖

```bash
conda activate go2_robot_scene_demo
cd /root/gpufree-data/Grounded-SAM-2
```

安装 SAM2：

```bash
SAM2_BUILD_CUDA=1 SAM2_BUILD_ALLOW_ERRORS=1 \
python -m pip install --no-build-isolation -e .
```

安装 GroundingDINO 依赖：

```bash
python -m pip install \
  transformers==4.40.2 "tokenizers<0.20,>=0.19" \
  addict yapf timm opencv-python pycocotools "supervision>=0.22.0"
```

安装 GroundingDINO 本地包：

```bash
CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=8.9 \
python -m pip install --no-build-isolation -e /root/gpufree-data/Grounded-SAM-2/grounding_dino
```

说明：

- `TORCH_CUDA_ARCH_LIST=8.9` 适合 RTX 4090。
- 其他 GPU 可先不设置该变量，或按 GPU 架构调整。
- 如果没有 `/usr/local/cuda`，但 PyTorch CUDA 可用，可以先去掉 `CUDA_HOME=/usr/local/cuda` 重试。

验证导入：

```bash
PYTHONPATH=/root/gpufree-data/Grounded-SAM-2:/root/gpufree-data/Grounded-SAM-2/grounding_dino \
python - <<'PY'
import torch
import groundingdino
import groundingdino._C
from grounding_dino.groundingdino.util.inference import load_model, load_image, predict
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor
print("cuda_available", torch.cuda.is_available())
print("groundingdino ok")
print("sam2 ok")
PY
```

### 8.4 下载模型权重

进入 Grounded-SAM-2 目录：

```bash
cd /root/gpufree-data/Grounded-SAM-2
mkdir -p checkpoints gdino_checkpoints
```

SAM2 tiny 权重：

```bash
wget -O checkpoints/sam2.1_hiera_tiny.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
```

GroundingDINO SwinT 权重：

```bash
wget -O gdino_checkpoints/groundingdino_swint_ogc.pth \
  https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/groundingdino_swint_ogc.pth
```

如果 `wget` 很慢，可使用 `aria2c`：

```bash
aria2c -x 16 -s 16 -o groundingdino_swint_ogc.pth \
  -d gdino_checkpoints \
  https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/groundingdino_swint_ogc.pth
```

确认文件存在且大小合理：

```bash
ls -lh checkpoints/sam2.1_hiera_tiny.pt
ls -lh gdino_checkpoints/groundingdino_swint_ogc.pth
```

参考大小：

```text
sam2.1_hiera_tiny.pt            149M 左右
groundingdino_swint_ogc.pth     662M 左右
```

### 8.5 配置项目使用 GroundingDINO+SAM2

回到项目目录：

```bash
cd /root/gpufree-data/robot_scene_demo
nano .env
```

设置或确认：

```text
DETECTION_BACKEND=grounded_sam
GROUNDED_SAM_ROOT=/root/gpufree-data/Grounded-SAM-2
GROUNDED_SAM_PYTHON=python
GROUNDED_SAM_PYTHONPATH=/root/gpufree-data/Grounded-SAM-2:/root/gpufree-data/Grounded-SAM-2/grounding_dino
GROUNDING_DINO_CONFIG=grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py
GROUNDING_DINO_CHECKPOINT=gdino_checkpoints/groundingdino_swint_ogc.pth
GROUNDING_DINO_BOX_THRESHOLD=0.25
GROUNDING_DINO_TEXT_THRESHOLD=0.20
GROUNDING_DINO_HIGH_RECALL_BOX_THRESHOLD=0.10
GROUNDING_DINO_HIGH_RECALL_TEXT_THRESHOLD=0.08
ENABLE_GDINO_HIGH_RECALL=true
ENABLE_SAM2=true
SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
SAM2_CHECKPOINT=checkpoints/sam2.1_hiera_tiny.pt
MAX_DETECTED_OBJECTS=30
DETECTION_DEVICE=auto
DETECTOR_TIMEOUT_SECONDS=180

GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=true
GROUNDING_PROMPT_REQUIRE_NON_EMPTY=true
GROUNDING_PROMPT_FAIL_FAST_ON_EMPTY=true
GROUNDING_PROMPT_RETRY_ON_EMPTY=true
GROUNDING_PROMPT_MAX_RETRIES=1
GROUNDING_PROMPT_MAX_TERMS=24
GROUNDING_PROMPT_MIN_TERMS=3
GROUNDING_PROMPT_DEBUG_OUTPUT=outputs/grounding_prompt_plan.json
GROUNDING_PROMPT_RETRY_DEBUG_OUTPUT=outputs/grounding_prompt_retry_plan.json
```

如果运行前已经 `conda activate go2_robot_scene_demo`，`GROUNDED_SAM_PYTHON=python` 会使用当前环境。也可以写成你机器上的真实 Python 路径；查询方式：

```bash
conda activate go2_robot_scene_demo
which python
```

如果输出是 `/opt/conda/envs/go2_robot_scene_demo/bin/python`，则 `.env` 里应写：

```text
GROUNDED_SAM_PYTHON=/opt/conda/envs/go2_robot_scene_demo/bin/python
```

### 8.6 直接验证 worker

```bash
cd /root/gpufree-data/robot_scene_demo
PYTHONPATH=/root/gpufree-data/Grounded-SAM-2:/root/gpufree-data/Grounded-SAM-2/grounding_dino \
python app/detectors/grounded_sam_worker.py \
  --image "/root/gpufree-data/微信图片_20260617144106.jpg" \
  --output /tmp/grounded_sam_worker_test.json \
  --root /root/gpufree-data/Grounded-SAM-2 \
  --text-prompt "phone. smartphone. screen-like object." \
  --grounding-config grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py \
  --grounding-checkpoint gdino_checkpoints/groundingdino_swint_ogc.pth \
  --box-threshold 0.25 \
  --text-threshold 0.20 \
  --sam2-config configs/sam2.1/sam2.1_hiera_t.yaml \
  --sam2-checkpoint checkpoints/sam2.1_hiera_tiny.pt \
  --max-objects 20 \
  --device auto
```

检查结果：

```bash
python - <<'PY'
import json
p="/tmp/grounded_sam_worker_test.json"
data=json.load(open(p, encoding="utf-8"))
objs=data.get("objects", [])
print("objects", len(objs))
print("with_sam2_mask", sum(o.get("mask_area_ratio") is not None for o in objs))
print(objs[:2])
PY
```

期望：

- `objects` 大于 0。
- `with_sam2_mask` 大于 0。如果等于 0，通常是 SAM2 config 或 checkpoint 路径错误。

上面的 `--text-prompt` 只是 worker 手动 debug 示例。项目主流程默认不会依赖固定室内物体 prompt 表；GroundingDINO 检测词优先由 `GroundingPromptPlanner` 根据自然语言任务解析结果、导航任务和 TargetProfile 动态生成，LLM runtime prior 只作为后续搜索假设和动态复核线索，不能代替视觉证据确认目标。
worker 现在会拒绝空 `--text-prompt`。如果看到：

```text
text_prompt is empty. GroundingDINO requires a non-empty open-vocabulary prompt.
```

说明应该先检查 prompt 生成链路，而不是继续调低检测阈值。

### 8.6.1 验证 LLM-first GroundingDINO prompt expansion

GroundingDINO 不是整图场景理解模型，不能指望它直接检测“卧室”“房间”“区域”这类抽象目标。项目主流程会先解析自然语言任务，再调用 `GroundingPromptPlanner` 生成英文开放词表。

推荐先用房间类任务验证 prompt plan 是否生成：

```bash
python run_demo.py \
  --image "/root/gpufree-data/微信图片_20260617144106.jpg" \
  --target "找到卧室" \
  --detector grounded_sam \
  --disable-crop-verify \
  --disable-handwritten-priors
```

即使当前图片里没有卧室，也应该生成：

```text
outputs/grounding_prompt_plan.json
outputs/detection_debug_report.md
```

检查 prompt：

```bash
python - <<'PY'
import json
p="outputs/grounding_prompt_plan.json"
data=json.load(open(p, encoding="utf-8"))
print("target_category:", data.get("target_category"))
print("strategy:", data.get("grounding_strategy"))
print("prompt_valid:", data.get("is_valid_for_grounding_dino"))
print("prompt:", data.get("grounding_prompt"))
print("warnings:", data.get("warnings"))
PY
```

期望：

- `prompt_valid` 为 `True`。
- `grounding_prompt` 非空。
- room / area / scene / floor / corridor 任务中，prompt 不应只有 `bedroom .`、`room .` 这类抽象词。
- 如果第一次 DINO 返回 0 candidate 且开启 retry，会额外生成 `outputs/grounding_prompt_retry_plan.json`。

### 8.7 跑项目 GroundingDINO+SAM2 主流程

```bash
cd /root/gpufree-data/robot_scene_demo
python run_demo.py \
  --image "/root/gpufree-data/微信图片_20260617144106.jpg" \
  --target "找到卧室" \
  --detector grounded_sam \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
```

成功后应看到类似：

```text
场景摘要：本地 Grounding DINO/SAM2 检测到 ... 个物体，补全 ... 条空间关系。
已生成：
outputs/scene_result.json
outputs/object_table.csv
outputs/relation_table.csv
outputs/topology_graph.png
outputs/topology_graph.graphml
outputs/ros2_motion_plan.json
outputs/annotated_scene.png
outputs/grounding_prompt_plan.json
outputs/detection_debug_report.md
...
```

`detection_debug_report.md` 会记录：

- 原始任务、intent、目标类别
- prompt 来源和生成策略
- direct terms / proxy object terms / context anchor terms
- 最终 GroundingDINO prompt
- 0 candidate retry prompt
- 原始候选数、过滤候选数、候选融合结果

## 9. 启动 Streamlit Web UI

前台启动：

```bash
cd /root/gpufree-data/robot_scene_demo
conda activate go2_robot_scene_demo
bash scripts/start_web_ui.sh
```

默认地址：

```text
http://localhost:8501
```

如果端口被占用：

```bash
bash scripts/start_web_ui.sh 8502
```

后台启动：

```bash
tmux new-session -d -s robot_scene_demo_ui \
  'bash -lc "cd /root/gpufree-data/robot_scene_demo && conda run -n go2_robot_scene_demo streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true"'
```

检查健康状态：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8501/_stcore/health
```

期望：

```text
ok
```

只验证 Nav2 面板和离线路径、不连接 ROS 时：

```bash
NAV2_ENABLED=true NAV2_MODE=offline_preview \
  bash scripts/start_web_ui.sh
```

页面中的 `offline_preview` 会显式标记为“非 Nav2 真实路径 / 不可执行”，不会在
ROS 不可用时把 `plan_only` 或 `execute` 静默降级为模拟路径。

查看 UI 日志：

```bash
tmux attach -t robot_scene_demo_ui
```

退出 tmux 查看但不停止服务：按 `Ctrl+b`，再按 `d`。

停止 UI：

```bash
tmux kill-session -t robot_scene_demo_ui
```

## 10. Web UI 使用说明

左侧配置：

- `运行模式`
  - `模拟数据`：不需要图片、不需要 API Key。
  - `真实 API`：上传图片，调用硅基流动视觉模型。
  - `GroundingDINO+SAM2`：上传图片，调用本地检测器。
  - `视频目标搜索`：上传第一视角视频，先执行目标搜索，再按开关生成视频记忆、PSG、场景建图和导航拓扑辅助结果。
- `自然语言任务`：直接输入开放式任务，例如 `帮我找到手机`、`找到张三，然后在安全距离处报告位置`。
- `场景图片`：真实 API 和 GroundingDINO+SAM2 模式需要上传。
- `视频目标搜索` 模式下的辅助功能：
  - `启用视频记忆`：记录稳定场景、负目标证据和长期视频空间记忆。
  - `启用视频 PSG`：根据真实观察生成可探索候选区域，不能单独确认目标。
  - `启用全场景建图辅助`：在目标搜索过程中额外构建场景图，不替代目标搜索。
  - `生成导航拓扑图`：只有启用建图辅助后可选，输出 place/passage/free_space/obstacle/PSG 拓扑。
  - `使用拓扑图辅助目标搜索排序`：只给候选区域和 next-best-view 排序，目标确认仍必须依赖视觉证据。
- `知识增强流程`：建议打开。
- `预测性场景图`：显示 PSG。
- `高精度复查`：只对真实 API 模式有意义。
- `运动视界设置`
  - `运动策略档位`：严格安全、平台避障室内、平台避障开放区域、平台避障自动。
  - `假设机械狗已有基础避障`：开启后允许高层规划输出更长移动段。
  - `启用自适应移动视界`：关闭后恢复严格安全单步裁剪。
  - `开放区域最大移动距离` / `室内最大移动距离`：运行时覆盖 `.env` 中的最大距离。

结果区：

- 场景摘要
- 目标判断
- 路线规划
- 物体表
- 关系表
- 拓扑图
- 标注图
- 任务解析
- ROS2 指令 JSON
- 原始 JSON
- 知识增强结果
- 知识增强页签中的 `运动视界决策`：显示策略档位、场景类型、任务阶段、LLM 推荐距离、规则裁剪后距离、最终导出距离和原因。

### 10.1 自然语言任务理解与安全门控

Web UI 不再要求用户选择“找可见目标 / 找不可见目标”等任务模板。系统会先把自然语言任务解析为 `ParsedTask`，再经过能力与安全门控生成 `NavigationTask`。

可执行范围只包括观察、搜索、巡查、导航到更好视角、接近目标附近、安全距离停止和反馈。拿取、开柜、翻找、破坏、推撞、攻击、殴打、伤害、强行接触人员等子任务会被拦截。

如果任务同时包含可执行和不可执行部分，例如：

```text
找到张三，然后把他打一顿
```

系统只保留定位/搜索/安全距离观察/停止反馈部分，并在 `outputs/actionability_report.md` 中说明被拦截的伤害行为。目标是否可见不会由用户输入决定，解析阶段固定为 `initial_visibility_state="unknown"`，后续只能由视觉检测和 evidence gating 判断为 `visual_candidate`、`visual_confirmed` 或当前视角未确认。

### 10.2 视频目标搜索

Web UI 的“运行模式”中选择“视频目标搜索”后，可以上传
`mp4/avi/mov/mkv` 视频，设置目标、检测器、关键帧采样 FPS 和最大分析帧数。

当前产品语义：

- 顶层运行模式只有 `视频目标搜索`，没有 `视频全场景建图`。
- 只要提供目标，后端必须先执行目标搜索。
- `启用全场景建图辅助`、`生成导航拓扑图`、`使用拓扑图辅助目标搜索排序` 都是目标搜索内部的辅助开关。
- 看到客厅、沙发、电视柜等上下文，只能生成“可能搜索区域”和下一步观察建议，不能把目标升级为 `visual_confirmed`。
- `scene_map_only` 只用于 CLI 高级调试；带 `--target` 时会被防呆拒绝。
- 视频分析成功后会自动生成 Video-to-Navigation 规划；即使没有 ROS2、没有 Nav2 goal 或没有目标线索，也会降级显示 `Visual Preview` 或 `Exploration`，不会让导航区域空白。

也可以直接使用命令行：

```bash
python run_video_demo.py \
  --video "/path/to/robot_walk.mp4" \
  --target "手机" \
  --mode target_search \
  --detector mock \
  --sample-fps 1.0 \
  --max-frames 120 \
  --enable-video-memory \
  --enable-video-navigation \
  --video-navigation-mode visual_preview
```

RGB-D、双目或外部尺度已验证输入可以使用 metric preview；只有同时提供 `T_map_video_map` 时才会准备 Nav2 map-frame goal：

```bash
python run_video_demo.py \
  --video "/path/to/rgbd_walk.mp4" \
  --target "红色背包" \
  --detector llm \
  --enable-video-navigation \
  --video-navigation-mode metric_preview \
  --video-pose-backend metric \
  --depth-dir "/path/to/depth" \
  --video-map-transform-json "/path/to/T_map_video_map.json"
```

真实检测时把 `mock` 替换为 `llm` 或 `grounded_sam`。如需在目标搜索过程中启用场景建图和导航拓扑辅助：

```bash
python run_video_demo.py \
  --video "/path/to/robot_walk.mp4" \
  --target "电视" \
  --mode target_search \
  --detector llm \
  --sample-fps 2.0 \
  --max-frames 300 \
  --enable-video-memory \
  --enable-video-psg \
  --enable-scene-mapping \
  --enable-navigation-topology \
  --use-scene-map-for-search
```

兼容旧命令时，`--mode full_scene_map` 或 `--enable-full-scene-map` 如果同时带 `--target`，会自动归一化为 `target_search + --enable-scene-mapping + --enable-navigation-topology`。只有显式 `--scene-map-only` 且不提供 `--target` 时，才会只建图不搜索目标。

不启用建图辅助时，视频目标搜索主输出包括：

```text
outputs/video_target_profile.json
outputs/video_target_search.json
outputs/video_target_timeline.json
outputs/video_target_candidates.json
outputs/video_object_tracks.json
outputs/video_track_summary.json
outputs/video_crop_verify_results.json
outputs/video_tracking_debug_report.md
outputs/video_candidate_regions.json
outputs/video_navigation_trace.json
outputs/video_reasoning_report.md
outputs/video_llm_generated_priors.json
outputs/video_dynamic_detector_prompts.json
outputs/video_evidence_gating_report.json
outputs/video_observation_memory_updates.json
outputs/video_prior_usage_report.json
outputs/video_frames/
outputs/video_frames_annotated/
outputs/video_scene_results/
```

启用建图辅助后，会在上述目标搜索主输出之外额外生成：

```text
outputs/video_frame_observations.json
outputs/video_place_segments.json
outputs/video_all_objects.json
outputs/video_observed_scene_graph.json
outputs/video_observed_scene_graph.graphml
outputs/video_psg_layer.json
outputs/video_hybrid_scene_graph.json
outputs/video_hybrid_scene_graph.graphml
outputs/video_navigation_map.json
outputs/video_navigation_topology.json
outputs/video_navigation_topology.graphml
outputs/video_navigation_topology.png
outputs/video_navigation_topology_debug.md
outputs/video_topology_search_ranking.json
```

视频记忆和 PSG 相关输出包括：

```text
outputs/video_memory_graph.json
outputs/video_memory_graph.graphml
outputs/video_memory_updates.json
outputs/video_spatial_memory_snapshot.json
outputs/video_predictive_scene_graph.graphml
outputs/video_predictive_scene_graph.json
outputs/video_hypotheses.json
data/memory/video_spatial_memory.jsonl
```

视频模式采用“场景中心记忆 + 目标条件推理”。即使采样帧里没有目标或候选物，
系统仍会记录环境类型、稳定参照物、可通行区域和负目标证据，生成 PSG 搜索假设，
并把去重后的观察写入长期 JSONL 记忆库。重复运行同一视频时，长期记忆库会跳过
高度相似的条目，但本次运行的记忆更新和推理报告仍会生成。

视频模式默认处理过去录制的第一视角视频。没有 odom、SLAM 位姿、深度或地图时，
系统输出目标出现时间、画面位置、参照物、候选区域、环境记忆和回访建议，
但不生成真实可执行导航路线。

相关环境变量位于 `.env.example`，常用项包括：

```text
VIDEO_ENABLE_SCENE_MAPPING_DEFAULT=false
VIDEO_ENABLE_NAVIGATION_TOPOLOGY_DEFAULT=false
VIDEO_USE_SCENE_MAP_FOR_SEARCH_DEFAULT=true
VIDEO_ALLOW_SCENE_MAP_ONLY_DEBUG=false
VIDEO_ENABLE_SCENE_MEMORY=true
VIDEO_ALWAYS_WRITE_MEMORY=true
VIDEO_ENABLE_VIDEO_PSG=true
VIDEO_ENABLE_NEGATIVE_EVIDENCE=true
VIDEO_MEMORY_STORE_PATH=data/memory/video_spatial_memory.jsonl
VIDEO_ENABLE_MEMORY_RETRIEVAL=true
VIDEO_MEMORY_RETRIEVAL_TOP_K=10
```

视频目标支持自然语言开放词表描述，例如：

```text
请帮我找一台能打印 A3 纸的设备
找到红色把手的白色柜门
寻找靠近饮水机的蓝色垃圾桶
寻找挂在墙上的红色消防器材
```

每次视频运行会先生成 `outputs/video_target_profile.json`，其中包含核心实体、
中英文开放词表、属性、关系约束和上下文线索。LLM 模式直接按目标画像逐帧判断；
GroundingDINO 模式先动态生成检测提示词，对于带颜色、用途或关系约束的复杂目标，
再使用视觉 LLM 对候选帧进行语义复核和 bbox 对齐。目标画像解析失败时会降级为
原始目标文本，不会让整段视频直接失败。

## 11. ROS2 dry-run 指令数据

每次运行基础分析都会生成：

```text
outputs/ros2_motion_plan.json
outputs/motion_horizon_decision.json
```

`ros2_motion_plan.json` 是 ROS2 `/cmd_vel` 兼容数据。移动距离不再无条件固定为 0.5m，而是由 `Motion Horizon Planner` 根据场景、任务阶段、目标候选状态、LLM 建议和 `.env` 硬上限裁剪。核心字段：

```json
{
  "dry_run": true,
  "topic": "/cmd_vel",
  "message_type": "geometry_msgs/msg/Twist",
  "command_rate_hz": 10.0,
  "platform_obstacle_avoidance_assumed": true,
  "dynamic_motion_horizon_enabled": true,
  "motion_horizon_profile": "platform_assisted_auto",
  "motion_horizon_decision": {
    "motion_policy": "platform_assisted_open_area",
    "recommended_distance_m": 3.0,
    "max_allowed_distance_m": 5.0,
    "decision_reason_zh": "当前为开放区域搜索阶段，平台具备基础避障能力，允许较长移动段以提高搜索效率。"
  },
  "commands": [
    {
      "source_action": "move_forward",
      "distance_m": 3.0,
      "twist": {
        "linear": {"x": 0.25, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.0}
      },
      "duration_sec": 12.0,
      "interruptible_by_platform": true,
      "platform_obstacle_avoidance_assumed": true,
      "requires_stop_after_motion": true,
      "observe_while_moving": false
    }
  ]
}
```

`motion_horizon_decision.json` 是同一份动态距离决策的独立调试输出，便于查看最终距离为什么被放宽或缩短。

预览指令，不发布 ROS2：

```bash
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
```

示例输出：

```text
dry_run=True topic=/cmd_vel rate=10Hz
commands=2
cmd_001 step=1 action=move_forward duration=2s linear.x=0.25 angular.z=0
cmd_002 step=2 action=stop duration=1s linear.x=0 angular.z=0
```

后续在机器狗或 ROS2 主机上接收数据时，有两种方式。

### 11.1 方式 A：只把 JSON 交给 ROS2 节点

推荐实际工程中采用这种方式。流程：

1. `robot_scene_demo` 生成 `outputs/ros2_motion_plan.json`。
2. 你自己的 ROS2 节点读取这个 JSON。
3. 按 `commands` 顺序向 `/cmd_vel` 发布 `geometry_msgs/msg/Twist`。
4. 每条命令持续发布 `duration_sec` 秒。
5. 发布频率使用 `command_rate_hz`。
6. 结束后发布零速度 Twist。

### 11.2 方式 B：使用项目内置 publisher 脚本

在 Ubuntu 22.04 上安装 ROS2 Humble/Nav2。脚本会初始化 ROS2 APT 软件源并安装
本项目所需的 Nav2、Simple Commander、Collision Monitor、Velocity Smoother
和 colcon：

```bash
bash scripts/install_nav2_humble.sh
```

source ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
```

正确文件名必须是 `setup.bash`，不能写成 `setup.bas`，也不能拼成
`/opt/ros/humble/setup.bashe/setup.bas`。

确认 Python 能导入 ROS2：

```bash
/usr/bin/python3 - <<'PY'
import rclpy
from geometry_msgs.msg import Twist
print("ros2 python ok")
PY
```

这只验证 ROS2 系统 Python。默认双 Python 部署中，Conda Python 不直接加载
Humble 的 `rclpy`，系统 Python 也不自动包含主项目的 Pydantic 依赖。因此旧
publisher 的真实发布只作为兼容接口；推荐的正式执行方式是后文的隔离式 Nav2
Worker。

先 dry-run 预览：

```bash
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
```

只有在你已经准备了同时包含项目依赖与 Humble `rclpy` 的兼容 Python 环境时，
才能使用旧链路真实发布：

```bash
python scripts/publish_ros2_motion_plan.py \
  outputs/ros2_motion_plan.json \
  --execute \
  --allow-dry-run-plan \
  --force-legacy-while-nav2-inactive
```

如果你的机器狗不是监听 `/cmd_vel`，可以改 topic：

```bash
python scripts/publish_ros2_motion_plan.py \
  outputs/ros2_motion_plan.json \
  --execute \
  --allow-dry-run-plan \
  --force-legacy-while-nav2-inactive \
  --topic /your_robot/cmd_vel
```

安全要求：

- 活动的 Nav2 `execute` 任务存在时，旧 publisher 会拒绝发布，避免双重
  `/cmd_vel` 来源竞争；`--force-legacy-while-nav2-inactive` 不能绕过此互斥。
- 第一次必须架空机器狗或断开电机执行。
- 必须有急停。
- 必须确认机器狗底盘坐标系中 `linear.x > 0` 是前进。
- 必须确认 `angular.z > 0` 的旋转方向。
- 本项目估计距离来自单张图和规则，不等价于真实导航。
- 真机执行前应接入深度、避障、SLAM 或机器狗厂商 SDK 的安全策略。
- `platform_obstacle_avoidance_assumed=true` 只表示高层允许更长移动段，不表示本项目已经实现避障。
- `strict_safe` 模式仍可把单段移动恢复为 0.5m 上限。

## 12. 常用命令汇总

进入项目：

```bash
cd /root/gpufree-data/robot_scene_demo
conda activate go2_robot_scene_demo
```

测试：

```bash
python -m unittest discover -s tests
```

mock：

```bash
python run_demo.py --mock
python run_demo.py --mock \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
python run_demo.py --mock \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors \
  --motion-profile platform_assisted_auto \
  --platform-obstacle-avoidance
```

真实 API：

```bash
python run_demo.py \
  --image "/path/to/image.jpg" \
  --target "找到手机" \
  --detector llm \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors \
  --motion-profile platform_assisted_auto \
  --platform-obstacle-avoidance \
  --max-open-step 5.0
```

GroundingDINO+SAM2：

```bash
python run_demo.py \
  --image "/path/to/image.jpg" \
  --target "找到卧室" \
  --detector grounded_sam \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
```

Web UI：

```bash
bash scripts/start_web_ui.sh
```

ROS2 指令预览：

```bash
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
```

旧静态知识库查询（仅调试兼容，不参与默认 target found 判断）：

```bash
python scripts/query_scene_kb.py --target "手机" --room_type office --location floor_5
```

任务样例：

```bash
python scripts/evaluate_task_examples.py
```

## 13. 输出文件说明

基础输出：

```text
outputs/parsed_task.json              自然语言任务结构化解析，initial_visibility_state 固定为 unknown
outputs/capability_gate_result.json   机器狗能力边界与安全门控结果
outputs/navigation_task.json          可进入感知/导航管线的导航任务
outputs/actionability_report.md       已执行/已拦截子任务的人类可读报告
outputs/scene_result.json              场景结构化结果
outputs/object_table.csv               物体表
outputs/relation_table.csv             关系表
outputs/topology_graph.png             拓扑图图片
outputs/topology_graph.graphml         拓扑图 GraphML
outputs/annotated_scene.png            标注图，有原图时生成
outputs/ros2_motion_plan.json          ROS2 /cmd_vel dry-run 指令数据
outputs/motion_horizon_decision.json   自适应移动视界决策
```

LLM runtime prior / evidence gate 输出：

```text
outputs/llm_generated_priors.json        LLM 运行时自生成常识搜索假设，不能确认目标
outputs/dynamic_detector_prompts.json    用户目标 + LLM prior + 视觉摘要生成的动态检测词
outputs/grounding_prompt_plan.json       GroundingDINO prompt expansion 计划，记录策略、词表和最终 prompt
outputs/grounding_prompt_retry_plan.json 0 candidate 时的高召回 retry prompt，只有触发 retry 时生成
outputs/detection_debug_report.md        Grounded-SAM/crop/fusion 调试报告，含最终 GroundingDINO prompt
outputs/evidence_gating_report.json      目标状态与视觉证据门控结果
outputs/observation_memory_updates.json  本次观察记忆写入记录
outputs/prior_usage_report.json          本次是否使用静态/手写先验的审计报告
```

目标状态说明：

```text
llm_hypothesis_only：只有 LLM 常识假设，目标未确认。
visual_candidate：有视觉候选，但还未通过门控。
visual_confirmed：视觉证据通过门控，目标确认。
user_confirmed：用户确认。
```

视频目标搜索主输出：

```text
outputs/video_target_profile.json        视频目标画像与开放词表
outputs/video_target_search.json         视频目标搜索主结果，目标状态以视觉证据为准
outputs/video_target_timeline.json       目标候选/状态时间线
outputs/video_target_candidates.json     目标候选摘要
outputs/video_object_tracks.json         视频目标/物体 track 结果
outputs/video_track_summary.json         track-level 投票摘要
outputs/video_crop_verify_results.json   候选 crop 复核结果
outputs/video_tracking_debug_report.md   视频 tracking 调试报告
outputs/video_candidate_regions.json     未确认时的候选搜索区域
outputs/video_navigation_trace.json      下一步观察/导航建议轨迹
outputs/video_reasoning_report.md        视频目标搜索推理报告
```

视频记忆、运行时 prior 与 evidence gate 输出：

```text
outputs/video_memory_graph.json
outputs/video_memory_graph.graphml
outputs/video_memory_updates.json
outputs/video_spatial_memory_snapshot.json
outputs/video_predictive_scene_graph.json
outputs/video_predictive_scene_graph.graphml
outputs/video_hypotheses.json
outputs/video_llm_generated_priors.json
outputs/video_dynamic_detector_prompts.json
outputs/video_evidence_gating_report.json
outputs/video_observation_memory_updates.json
outputs/video_prior_usage_report.json
data/memory/video_spatial_memory.jsonl
```

视频目标搜索内的建图辅助输出。只有启用 `--enable-scene-mapping` 或 Web UI 的“启用全场景建图辅助”后才要求生成：

```text
outputs/video_frame_observations.json
outputs/video_place_segments.json
outputs/video_all_objects.json
outputs/video_observed_scene_graph.json
outputs/video_observed_scene_graph.graphml
outputs/video_psg_layer.json
outputs/video_hybrid_scene_graph.json
outputs/video_hybrid_scene_graph.graphml
outputs/video_navigation_map.json
outputs/video_navigation_topology.json
outputs/video_navigation_topology.graphml
outputs/video_navigation_topology.png
outputs/video_navigation_topology_debug.md
outputs/video_topology_search_ranking.json
```

视频目标状态补充：

```text
target_not_seen：没有任何目标候选。
target_candidate：检测到疑似目标，但证据不足。
target_visual_confirmed：目标被 bbox / crop verify / track voting / evidence gating 视觉确认。
target_lost_after_seen：之前看到过目标，后续帧丢失。
target_unconfirmed_but_likely_area_found：未看到目标，但拓扑或上下文发现可能搜索区域。
```

兼容输出：

```text
outputs/knowledge_aware_result.json
outputs/parsed_task.json
outputs/retrieved_knowledge.json
outputs/predictive_scene_graph.graphml
outputs/hypotheses.json
outputs/knowledge_updates.json
outputs/reasoning_report.md
outputs/quadruped_search_plan.json
outputs/quadruped_ros2_motion_plan.json
outputs/llm_search_hypotheses.json
outputs/actionability_report.md
outputs/visual_grounding_report.json
```

## 14. Git 与隐私注意事项

不要提交：

- `.env`
- API Key
- `outputs/`
- `__pycache__/`
- conda 环境目录
- 大模型权重
- 私人图片
- ROS2 机器狗真实地址、token、证书

检查：

```bash
git status --short
git diff -- . ':!outputs'
```

如果 remote URL 里含 token，立刻改掉：

```bash
git remote set-url origin https://github.com/<用户名>/<仓库名>.git
```

## 15. 故障排查

### 15.1 `ModuleNotFoundError: No module named app`

确认在项目根目录执行：

```bash
cd /root/gpufree-data/robot_scene_demo
python run_demo.py --mock
```

### 15.2 `cuda_available False`

检查：

```bash
nvidia-smi
python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.version.cuda)
PY
```

处理：

- 安装或修复 NVIDIA 驱动。
- 安装匹配的 PyTorch CUDA wheel。
- 确认没有装成 CPU-only PyTorch。

### 15.3 GroundingDINO 报 `BertModel.get_head_mask`

固定 transformers 版本：

```bash
pip install "transformers==4.40.2" "tokenizers<0.20,>=0.19"
```

### 15.4 SAM2 没有 mask

重点检查 `.env`：

```text
SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
SAM2_CHECKPOINT=checkpoints/sam2.1_hiera_tiny.pt
```

不要写成：

```text
SAM2_CONFIG=sam2/configs/sam2.1/sam2.1_hiera_t.yaml
```

### 15.5 GroundingDINO prompt 为空

如果报：

```text
GroundingDINO prompt is empty
```

或 worker 报：

```text
text_prompt is empty. GroundingDINO requires a non-empty open-vocabulary prompt.
```

处理顺序：

1. 检查 `.env` 是否启用：

```text
GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=true
GROUNDING_PROMPT_REQUIRE_NON_EMPTY=true
```

2. 检查是否配置了 `SILICONFLOW_API_KEY`。Grounded-SAM 主流程默认需要 LLM 生成开放词表。

3. 查看：

```bash
ls -lh outputs/grounding_prompt_plan.json outputs/detection_debug_report.md
cat outputs/detection_debug_report.md
```

4. 如果 `outputs/grounding_prompt_plan.json` 不存在，说明 prompt expansion 调用没有成功，优先检查 API Key、网络、模型名和超时。

5. 如果只是离线调试本地检测器，可以临时关闭：

```text
GROUNDING_PROMPT_LLM_EXPANSION_ENABLED=false
```

然后用第 8.6 节 worker 命令手动传入非空 `--text-prompt`。

### 15.6 GroundingDINO 0 candidate

0 candidate 不一定是 SAM2 问题。排查顺序：

1. 打开 `outputs/detection_debug_report.md`，看 `Final GroundingDINO Prompt` 是否是具体英文可见词。
2. 如果目标是房间/区域/场景，prompt 不应只有 `bedroom .`、`room .`、`area .`。
3. 查看是否触发 retry：

```bash
cat outputs/grounding_prompt_retry_plan.json
```

4. 临时降低阈值：

```text
GROUNDING_DINO_BOX_THRESHOLD=0.10
GROUNDING_DINO_TEXT_THRESHOLD=0.08
ENABLE_GDINO_HIGH_RECALL=true
```

5. 用第 8.6 节 worker 手动传一个确定能检测的词，例如 `person . chair . table . door .`，验证本地 DINO/SAM2 环境本身是否正常。

### 15.7 Streamlit 端口占用

换端口：

```bash
bash scripts/start_web_ui.sh 8502
```

或查占用：

```bash
ss -ltnp | grep 8501
```

### 15.8 ROS2/Nav2 设置脚本或 `rclpy` 不存在

先检查正确文件：

```bash
test -f /opt/ros/humble/setup.bash && echo "ROS2 setup ok"
```

若不存在，在 Ubuntu 22.04 上运行：

```bash
bash scripts/install_nav2_humble.sh
```

然后使用系统 Python 验证。不要用 Conda Python 直接导入 Humble 的 `rclpy`：

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 - <<'PY'
import rclpy
from nav2_simple_commander.robot_navigator import BasicNavigator
print("ok")
PY
```

如果曾把 `NAV2_SETUP_BASH` 错写成
`/opt/ros/humble/setup.bashe/setup.bas`，当前网关会在标准安装存在时修复到
`/opt/ros/humble/setup.bash`；仍建议同时修正 shell 或 `.env` 中的原始值。

Conda 环境中构建 ROS 工作区必须固定系统 Python：

```bash
source /opt/ros/humble/setup.bash
cd ros2_ws
colcon build --symlink-install --cmake-args \
  -DPython3_EXECUTABLE=/usr/bin/python3
```

### 15.9 API 超时

调大：

```text
SILICONFLOW_TIMEOUT_SECONDS=60
SILICONFLOW_MAX_TOKENS=2048
```

或先用：

```bash
python run_demo.py --mock --enable-knowledge
```

确认本地流程没有问题。

## 16. 最小验收清单

在全新 Ubuntu 上，至少完成以下命令才算部署成功：

```bash
python -m py_compile app/config.py app/perception/grounding_prompt_planner.py app/detectors/grounded_sam_subprocess.py run_demo.py streamlit_app.py
python -m unittest tests.test_grounding_prompt_planner tests.test_grounded_sam_prompt_integration tests.test_grounded_sam_runtime
python -m unittest discover -s tests -p 'test_nav2_*.py'
python run_demo.py --mock --enable-llm-prior --enable-observation-memory --enable-evidence-gating --disable-handwritten-priors
python run_demo.py --mock --enable-nav2 --nav2-mode offline_preview --nav2-goal-x 2 --nav2-goal-y 1 --nav2-wait
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
bash scripts/start_web_ui.sh
```

可选回归检查：

```bash
python -m unittest discover -s tests
python scripts/evaluate_task_examples.py
pip install pytest
python -m pytest tests/test_run_video_demo_args.py tests/test_scene_map_as_auxiliary.py
```

如果可选回归只失败在 Streamlit AppTest 超时或 legacy task type 断言，可先按第 6.1 和第 6.3 节说明判断，不把它作为部署失败。

如果配置了真实 API：

```bash
python run_demo.py --image "/path/to/image.jpg" --target "找到手机" --detector llm --enable-knowledge
```

如果配置了 GroundingDINO+SAM2：

```bash
python run_demo.py --image "/path/to/image.jpg" --target "找到卧室" --detector grounded_sam --enable-llm-prior --enable-observation-memory --enable-evidence-gating --disable-handwritten-priors
```

最终应能访问：

```text
http://localhost:8501
```

并能看到或下载：

```text
outputs/scene_result.json
outputs/knowledge_aware_result.json
outputs/ros2_motion_plan.json
outputs/motion_horizon_decision.json
outputs/grounding_prompt_plan.json
```
## 高精度目标识别模式

项目现在支持“目标画像与动态开放词表 → 高召回候选检测 → 候选 crop
视觉复核 → 多源分数融合 → 视频 track-level 投票”。没有 API Key 时会跳过
crop 复核；mock 仍可独立运行。ROS2 输出仍默认 `dry_run=true`。

推荐图片命令：

```bash
python run_demo.py \
  --image "/path/to/image.jpg" \
  --target "找到手机" \
  --detector grounded_sam \
  --high-recall \
  --enable-crop-verify \
  --enable-knowledge
```

推荐视频命令：

```bash
python run_video_demo.py \
  --video "/path/to/robot_walk.mp4" \
  --target "手机" \
  --mode target_search \
  --detector grounded_sam \
  --sample-fps 3.0 \
  --max-frames 300 \
  --enable-tracking \
  --enable-crop-verify \
  --enable-knowledge \
  --enable-video-memory \
  --enable-video-navigation \
  --video-navigation-mode visual_preview
```

如果需要在高精度视频搜索中同时生成场景拓扑辅助结果，在上述命令后追加：

```bash
  --enable-video-psg \
  --enable-scene-mapping \
  --enable-navigation-topology \
  --use-scene-map-for-search
```

新增的主要调试输出包括：

```text
outputs/grounding_prompt_plan.json
outputs/grounding_prompt_retry_plan.json
outputs/target_profile.json
outputs/candidate_objects.json
outputs/crop_verify_results.json
outputs/fused_objects.json
outputs/detection_debug_report.md
outputs/video_track_summary.json
outputs/video_crop_verify_results.json
outputs/video_tracking_debug_report.md
outputs/video_navigation/<request_id>/visual_navigation_plan.json
outputs/video_navigation/<request_id>/navigation_instructions.json
outputs/video_navigation/<request_id>/webui_manifest.json
```

如果普通 RGB 视频中没有找到目标，系统会自动生成探索式导航规划，并写入 frontier 候选点；如果找到目标或疑似目标，则生成目标/候选观察位姿，而不是把 bbox 中心直接当作机器人 goal。

内置示例标注可用于检查评估链路：

```bash
python scripts/evaluate_detection_accuracy.py
python scripts/evaluate_video_target_search.py
```
# 无人工先验的大模型自生成常识推理

本项目不是“完全无常识”，而是“无开发者预置常识库”：系统不默认读取 object-location、room-object、目标搜索规则等手写先验；LLM 可以在运行时根据目标、当前画面、可见物体、空间关系和观察记忆生成搜索假设。

这些假设的 `prior_source` 是 `llm_runtime_commonsense`，`can_confirm_target=false`。它们只能用于候选区域排序、动态检测词生成、下一视角建议和解释搜索策略。目标确认必须通过 `evidence_gating_report.json` 中的视觉门控。

推荐单图命令：

```bash
python run_demo.py \
  --image "/path/to/image.jpg" \
  --target "找到手机" \
  --detector grounded_sam \
  --enable-llm-prior \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
```

推荐视频命令：

```bash
python run_video_demo.py \
  --video "/path/to/robot_walk.mp4" \
  --target "手机" \
  --mode target_search \
  --detector grounded_sam \
  --sample-fps 3.0 \
  --max-frames 300 \
  --enable-llm-prior \
  --enable-tracking \
  --enable-crop-verify \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors \
  --enable-video-navigation \
  --video-navigation-mode visual_preview
```

# LLM-first 机械狗情境搜索

单图知识增强模式现在支持“视觉事实 → LLM 情境推理 → 视觉证据门控 →
机械狗动作门控 → PSG v2 → 下一视角计划”。推断节点不会把目标误标记为已找到，
超出机械狗能力的开柜、翻找、拿取和低头精查会被改写或标记为需要人工。

快速验收：

```bash
python run_demo.py --mock --enable-knowledge --enable-llm-reasoning --quadruped-mode
```

新增输出包括：

- `outputs/llm_search_hypotheses.json`
- `outputs/quadruped_search_plan.json`
- `outputs/reasoned_predictive_scene_graph.json`
- `outputs/reasoned_predictive_scene_graph.graphml`
- `outputs/actionability_report.md`
- `outputs/quadruped_ros2_motion_plan.json`
- `outputs/reasoned_annotated_scene.png`（提供原图时）
- `outputs/visual_grounding_report.json`

LLM API 不可用时流程不会中断，会明确标记推理不可用，并降级为基于当前视觉锚点
的保守转向/重观测方案。

当目标尚未视觉确认时，LLM 生成的 `suggested_detector_prompts_en` 会触发一次
可选二次视觉复核。只有复核结果具有 bbox/mask/crop 等视觉证据时，目标才会从
`inferred` 升级为 `observed`。GroundingDINO+SAM2 在无 CUDA 环境下可能超时，
系统会返回可读的 `DetectorRuntimeError`，不会输出伪造检测结果。

# 平台避障辅助动态运动视界

单图与知识增强模式现在支持“平台避障辅助 + 高层语义自适应移动距离”。旧逻辑中 ROS2 单次前进/后退会被固定裁剪到 0.5m；现在系统会根据场景类型、任务阶段、目标候选状态、LLM 推荐距离和配置硬上限动态计算高层移动段长度。

核心原则：

- 本项目不实现避障、局部路径规划、代价地图、深度图避障或急停。
- `platform_obstacle_avoidance_assumed=true` 表示真实安全由机械狗底层平台/SDK/ROS 安全层负责。
- 开放区域搜索可以生成 2m 以上移动段。
- 普通室内搜索可以生成 0.8m 到 2m 左右移动段。
- 目标候选出现、目标确认阶段或信息不足时会自动缩短到 0.3m 到 0.8m。
- LLM 不可用时，如果平台避障假设开启，会降级到保守的 1.0m 到 1.5m；如果平台避障假设关闭，则回到严格 0.5m。
- 运动后仍保留 stop 命令，不取消段后停稳和重观测。

快速验收：

```bash
python run_demo.py --mock --enable-knowledge \
  --motion-profile platform_assisted_open_area \
  --platform-obstacle-avoidance \
  --max-open-step 5.0
```

注意：mock 样例中的目标已经可见，因此会进入目标确认阶段并主动缩短距离。这是预期行为。若要验证开放区域长距离，可使用目标未出现、场景开阔的图片或测试：

```bash
python -m unittest tests.test_motion_horizon tests.test_ros2_motion_dynamic_horizon
```

典型 `outputs/motion_horizon_decision.json`：

```json
{
  "enabled": true,
  "profile": "platform_assisted_auto",
  "platform_obstacle_avoidance_assumed": true,
  "scene_type": "open_area",
  "task_phase": "search",
  "motion_policy": "platform_assisted_open_area",
  "recommended_distance_m": 3.0,
  "max_allowed_distance_m": 5.0,
  "requires_stop_after_motion": true,
  "observe_while_moving": false,
  "source": "mixed",
  "decision_reason_zh": "当前为开放区域搜索阶段，平台具备基础避障能力，允许较长移动段以提高搜索效率。"
}
```

如果需要恢复旧版保守行为：

```bash
python run_demo.py --mock --enable-knowledge \
  --motion-profile strict_safe \
  --disable-dynamic-motion-horizon
```

# Navigation2（ROS2 Humble）

### 架构与模式

项目采用主程序与 ROS Worker 隔离的架构：

```text
Streamlit / run_demo.py（Conda Python）
        ↓ 原子 JSON/JSONL + subprocess
nav2_bridge_worker.py（/usr/bin/python3 + ROS2 Humble）
        ↓ ComputePathToPose / NavigateToPose
Navigation2
```

现有感知、LLM-first 推理、证据门控、视频记忆、PSG、拓扑候选和 Motion Horizon
继续负责“去哪里观察”；Nav2 负责 map 坐标下的真实规划与执行。单图像素和视频帧
坐标不会被伪造成 map pose，自动目标必须包含可验证的坐标来源 `provenance`。

| 模式 | ROS/Nav2 | 是否执行 | 说明 |
|---|---:|---:|---|
| `disabled` | 不需要 | 否 | 默认值，保持旧流程 |
| `offline_preview` | 不需要 | 否 | 固定 fixture，只验证接口和 UI |
| `plan_only` | 需要 | 否 | 调用真实 `ComputePathToPose` |
| `execute` | 需要 | 是 | 先规划，再调用 `NavigateToPose` |

`plan_only` 和 `execute` 失败时不会自动降级为离线路径。

### 安装与构建

```bash
cd /root/gpufree-data/robot_scene_demo
bash scripts/install_nav2_humble.sh

source /opt/ros/humble/setup.bash
cd ros2_ws
colcon build --symlink-install --cmake-args \
  -DPython3_EXECUTABLE=/usr/bin/python3
source install/setup.bash
cd ..
```

Humble 的环境脚本固定是：

```text
/opt/ros/humble/setup.bash
```

不要写成 `setup.bas` 或 `/opt/ros/humble/setup.bashe/setup.bas`。项目会在标准安装
确实存在时自动纠正这类可识别拼写错误；自定义 ROS 安装则必须提供一个完整文件：

```text
NAV2_SETUP_BASH=/custom/ros/humble/setup.bash
NAV2_SYSTEM_PYTHON=/usr/bin/python3
NAV2_WORKSPACE_SETUP=/absolute/path/to/ros2_ws/install/setup.bash
```

### 启动 Nav2 和健康检查

先启动机器人底层、里程计、LaserScan、定位与 `map → base_link` TF，再加载地图。
纯规划使用不含 controller 或 `cmd_vel` 节点的 launch：

```bash
source /opt/ros/humble/setup.bash
source ros2_ws/install/setup.bash
ros2 launch robot_scene_nav_bringup robot_scene_nav2_plan_only.launch.py \
  map:=/absolute/path/to/map.yaml
```

完整 launch 默认 `execution_enabled:=false`，此时不会启动执行栈。只有 21 项门禁
通过后，外层受控启动器才可显式设置 true；速度链固定为
`controller_server → velocity_smoother → collision_monitor →
/go2w/nav2_cmd_vel → arbiter → cmd_vel_bridge`。footprint 采用固定来源的 Unitree
厂家站立外廓，但现场间隙和动态姿态尚未验证时，执行门禁仍不会开放。

另一个终端执行：

```bash
source /opt/ros/humble/setup.bash
source ros2_ws/install/setup.bash
/usr/bin/python3 scripts/check_nav2_runtime.py \
  --json outputs/nav2_health.json
```

健康检查会验证 ROS distro、系统 Python 导入、两个 Action Server、地图、TF、
里程计、`/cmd_vel` 与 Collision Monitor。外部 Nav2 图未启动时会在有限时间内
返回阻塞项，不会无限等待。Worker 本身也会按照
`NAV2_PLANNING_TIMEOUT_SECONDS` 返回 `NAV2_ACTION_SERVER_UNAVAILABLE` 或
`NAV2_PLANNING_TIMEOUT`。

### 离线预览与真实规划

无 ROS 的离线接口/UI 验收：

```bash
python run_demo.py --mock --enable-nav2 --nav2-mode offline_preview \
  --nav2-goal-x 2 --nav2-goal-y 1 --nav2-wait

NAV2_ENABLED=true NAV2_MODE=offline_preview \
  bash scripts/start_web_ui.sh
```

真实 Nav2 只规划：

```bash
source /opt/ros/humble/setup.bash
source ros2_ws/install/setup.bash
python run_demo.py --mock --enable-nav2 --nav2-mode plan_only \
  --nav2-goal-x 1 --nav2-goal-y 0 --nav2-goal-yaw 0 \
  --nav2-use-current-start --nav2-wait
```

也可以使用快捷脚本：

```bash
bash scripts/run_nav2_plan_only.sh 1.0 0.0 0.0
```

### 执行模式与安全门控

`execute` 必须通过 21 项实时能力门控和操作员二次确认。推荐只使用统一入口：

```bash
bash scripts/go2w/start_search_session.sh \
  --target "红色背包" --mode nav2_execute
```

仓库中的 footprint 使用 Unitree 厂家站立外廓，Collision Monitor 区域保守包络
该外廓；两者仍不代表现场动态间隙已经验收。直接构造旧式四复选框请求会因缺少
`capability_gate_result.json` 被请求模型和 Worker 双重拒绝。

### 输出与验收

每个任务保存在：

```text
outputs/nav2/jobs/<request_id>/
```

其中包括请求、状态、全局路径 JSON/CSV、路径图、指令预览、执行反馈、
`cmd_vel` 轨迹、Worker 日志和导航报告；`outputs/nav2_*` 是最近任务快捷副本。
所有 JSON 使用 UTF-8 原子写入，Web UI 不会读到半写文件。

Nav2 单元测试：

```bash
python -m unittest discover -s tests -p 'test_nav2_*.py'
```

更完整的部署、安全说明和 Web UI 验收步骤见：

- [`docs/NAV2_INTEGRATION.md`](docs/NAV2_INTEGRATION.md)
- [`docs/NAV2_WEBUI_TESTING.md`](docs/NAV2_WEBUI_TESTING.md)

## Go2-W 内置 RGB + LiDAR 真机部署

当前部署默认 fail-closed；小范围运动只在操作者明确授权
（`GO2W_MOTION_READY`）并通过 `scripts/go2w/run_autonomous_loop.py` 时执行。
已经实机通过的是内置 RGB 的只读采集、LiDAR/IMU 时间对齐、原子帧 Bundle，
以及带轮式里程计校验的小范围前进/转向/自主搜索。Go2-W 尺寸与内置 LiDAR/IMU 静态 TF 已采用
Unitree 官方产品页、固定提交的官方 URDF 和官方 LiDAR SDK，并通过隔离 ROS 域
`/tf_static` 验证。LiDAR 现场方向、地面/自身过滤、`/scan` 和 300 ms stale
门禁也已完成静止只读实机验收。官方 Unitree Point-LIO 固定版本已在隔离 Noetic
环境中通过 5 分钟静止只读验收；CameraInfo 已用实测 9×6/15 mm 棋盘完成标定，
相机外参、RGB-LiDAR 外参、移动里程计试验、地图和 Nav2 尚未完成物理验收。因此当前能力是
Level A/B 部分能力（观察、扫描、搜索、靠近、每步 STOP），Level C--F 仍阻断，不能把软件模块存在解释为
实机能力通过。

固定厂家参考位于 `configs/go2w/official_reference.yaml`：站立外廓为
`0.70 × 0.43 × 0.50 m`，`base_link -> utlidar_lidar` 为
`xyz=(0.28945, 0, -0.046825) m, rpy=(0, 2.8782, 0) rad`，
`utlidar_lidar -> utlidar_imu` 为纯平移
`(-0.007698, -0.014655, 0.00667) m`。该文件明确不授权运动；相机位姿和
`base_link` 离地高度没有在官方 Go2-W URDF 中公开，仍保持未知。

传感器与派生话题：

```text
/camera/front/image_raw
/camera/front/camera_info
/utlidar/cloud
/utlidar/imu
/go2w/lio_input/cloud_raw
/go2w/lio_input/imu_raw
/go2w/lidar/scan                 # 静止只读实机验收已通过
/lio/odom                        # Point-LIO 静止只读验收通过；默认不常驻启动
```

安装、构建和只读启动：

```bash
bash scripts/go2w/install_dependencies.sh
bash scripts/go2w/build_ros2.sh
bash scripts/go2w/start_live_perception.sh
```

启动器会先检查 `enp6s0` 的物理 carrier 和 `192.168.123.0/24` 主机地址；缺失时
直接 fail closed。Bundle 默认按 1 Hz 输出并只保留当前会话最近 30 个，避免长时间
运行无界写盘。计划要求的 10 分钟静止传输验收可用：

```bash
bash scripts/go2w/run_level_a_acceptance.sh
```

首轮 600 秒试验在约第 423 个 Bundle 后丢失有线 carrier，因此没有伪报 PASS；该
历史失败已被下述修复后通过结果取代。完整相机内参和 RGB-LiDAR 外参流程见
`docs/GO2W_REAL_ROBOT_DEPLOYMENT.md`。

恢复载波并修复长测器的跨会话统计与限频漂移后，最终 603.24 秒复验通过全部传输
门禁：489 帧、0.816 Hz、末帧 0.354 秒、30 帧/8.49 MiB、载波连续、内存稳定且
清理无残留。CameraInfo 已为 true；总 Level A 仍只因
`camera_tf_not_validated` 保持 false。证据位于
`outputs/go2w_acceptance/level_a_stationary_soak_fixed/result.json`。

当前内参来自 105 组 1920×1080 实拍，实时复验通过 10 组非零 K 的同步
Image/CompressedImage/CameraInfo；独立棋盘帧的平均/RMS/最大重投影误差为
0.859/1.024/3.375 px。证据位于
`outputs/go2w_acceptance/camera_calibration_20260806/`。相机 TF 和相机—雷达外参
仍保持未知，不能因 CameraInfo 通过而开启三维融合或导航。

RGB-LiDAR ROS 节点已接入只读启动器，但当前只发布闭锁门禁与诊断，不发布伪造的
三维目标。隔离回环验收连续收到 3 组
`/perception/fusion_ready=false` 和
`/perception/rgb_lidar_extrinsics_validated=false`，诊断同时给出
`authorizes_motion=false`。证据位于
`outputs/go2w_acceptance/rgb_lidar_fusion_blocked_runtime/result.json`。完成真实标定后，
节点才会用标定的 LiDAR→相机变换生成相机相对三维位置；它不会用网络图片猜相机 TF。

复现官方 TF/静止地面方向与 LiDAR 预处理验收：

```bash
/usr/bin/python3 scripts/go2w/audit_stationary_lidar_geometry.py \
  --reference-file configs/go2w/official_reference.yaml \
  --output outputs/go2w_acceptance/lidar_stationary_geometry/result.json
bash scripts/go2w/test_lidar_preprocessor_live.sh
bash scripts/go2w/setup_point_lio_noetic.sh
bash scripts/go2w/test_point_lio_stationary_live.sh
```

Point-LIO 使用官方 `point_lio_unilidar` 提交
`18ed5976d8fab2bd8a5148c26a40692bd3c0dc91`。最终 300 秒静止证据包含 4,615
帧里程计，频率 15.385 Hz，最大消息间隔 68.6 ms，最终/最大漂移
0.0785/0.0934 m，航向跨度 1.624°，桥丢包为 0；输入停止后 0.164 s 进入
stale 且不重发旧位姿。证据位于
`outputs/go2w_acceptance/point_lio_stationary/result_5min.json`。RKO-LIO 两组静止
A/B 试验均产生明显假运动，已在 `configs/go2w/lio.yaml` 中禁用。

该通过仅覆盖静止处理，不授权移动。直线、矩形、原地旋转、建图与 Nav2 验收均
未执行；在当前“狗不能移动”的约束下继续保持阻断。

生产入口固定使用只读 VideoHub RPC，最近联合验收得到 166 个 1920×1080 Bundle；
内容门禁还会拒绝已观察到的 H.264/DDS 纯绿色损坏帧。`/frontvideostream` 只保留为
显式诊断入口，不再由生产启动器自动选择。最新 Bundle 健康状态为
`camera=true, lidar=true, camera_info_calibrated=false, lio=false, tf=false`。

另一个终端可请求静止观察；当前传感器门禁关闭时会明确返回阻断项：

```bash
bash scripts/go2w/start_search_session.sh --target "手机" --mode observe_only
```

### 自主小范围运行与检测后端

真机自主运行使用 `scripts/go2w/run_autonomous_loop.py`，默认调用**硅基流动视觉
大模型 API**（`--detector llm`）做目标识别与场景理解，不再默认依赖
GroundingDINO+SAM2（可通过 `--detector grounded_sam` 显式回退）。LLM 快速检测
只请求“目标在不在 + 一个紧贴 bbox”，实测单次约 5–15 秒；完整场景理解（物体、
关系、路线建议）由同一 API 的非 quick 模式提供。默认视觉模型为
`Qwen/Qwen3-VL-30B-A3B-Instruct`，可用 `--llm-model
Qwen/Qwen3-VL-8B-Instruct` 切回更高细节模型。

```bash
# 360° 扫描 → 选最佳命中方向 → 靠近（半径限制 1.0 m，录像）
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode scan360_approach --target "黑色书包" --detector llm \
  --max-radius 1.0 --max-seconds 420 \
  --record-video outputs/live_sessions/scan360_llm.mp4 \
  --output outputs/live_sessions/scan360_llm.jsonl

# 摆动扫描 → 发现 → 对齐 → 靠近
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode level_a_search --target "手机" --detector llm \
  --max-radius 1.0 --max-seconds 300 \
  --output outputs/live_sessions/level_a_llm.jsonl
```

自主循环每步都校验轮式里程计、前向净空与 `mode/error`，无位移自动重试/绕障，
结束自动三次 STOP 并解除 arm。Bundle 超过 3 秒未更新时会拒绝继续动作并安全
中止（相机偶发抖动时最多等待重试 6 次，确认断流才中止，防止拿旧图盲动）。
每次发动作前会重新 arm，避免 LLM 检测耗时超过动作服务器 arm 时限后 goal 被拒；
任何未处理异常也会先急停再 disarm。`run_live_robot_demo.py` 的默认检测后端也
已改为 `llm`。

相机桥（`go2w_camera_bridge`）内置 RPC 逐帧容错与自动重连：损坏帧只跳过不
退出，子进程卡死超过 3 秒自动重启（退避 1→10 秒），避免相机流永久冻结。

LLM 快速检测/复核已接入 `app/live_robot` 正式搜索状态机
（`search_state_machine.py` + `step_planner.py` + `step_search_runner.py`），
自主脚本可用 `--mode state_machine_search` 直接驱动该链路：

```bash
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode state_machine_search --target "灰色书包" --detector llm \
  --reach-area-ratio 0.08 --max-radius 1.0 --max-seconds 300 \
  --record-video outputs/live_sessions/sm_search.mp4 \
  --output outputs/live_sessions/sm_search.jsonl
```

### wheel+LIO 融合里程计

`go2w_wheel_odom` 同时发布：

- `/go2w/odom/wheel`：纯轮式编码器 + Sport yaw（原语义）；
- `/go2w/odom/fused`：轮式平移沿 Sport+LIO 融合航向积分（推荐小范围里程计）。

融合航向 = Sport yaw delta + `lio_yaw_weight` ×（LIO yaw delta − Sport yaw
delta），默认权重 0.35。LIO 航向只有在新鲜（按主机接收时刻，≤0.5 s）、位置
范数 ≤5 m、数值有限且与 Sport 逐 tick 一致时才参与；任何违规自动回退 Sport，
连续 3 次不一致只告警一次。诊断见 `/go2w/odom/fused/status`。Point-LIO 平移
仍 BLOCKED，不参与融合；`navigation_gate.yaml` 保持 fail-closed。

自主运行脚本支持 `--odom-topic` 选择位移校验来源：

```bash
/usr/bin/python3 scripts/go2w/run_autonomous_loop.py \
  --mode scan360_approach --target "灰色书包" --detector llm \
  --odom-topic /go2w/odom/fused \
  --max-radius 1.0 --max-seconds 420 \
  --output outputs/live_sessions/search_fused.jsonl
```

`--record-video` 输出使用 Noto Sans CJK 渲染中文标签（不再出现 `????`），并在
视频左下角实时叠加当前运动指令（如“左转 30°”“前进 0.12 m/s × 2s”）、LLM
检测状态和复核结论。到达判定（bbox 面积占比 ≥ `--reach-area-ratio`）前会再调用
一次 `--verify` 复核，模型确认框内物体属于目标才写 `target_reached`；复核拒绝
时记录 `target_verification` 事件并右转 15° 继续观察，避免把椅子等相似物体
当成目标。360° 扫描遇到高分命中（score ≥ 0.80）会提前停止扫描、直接靠近，
不再多转一圈后绕回来。

短步搜索、Nav2 只规划和 Nav2 执行使用同一入口，但不会静默降级：

```bash
bash scripts/go2w/start_search_session.sh --target "手机" --mode step_search
bash scripts/go2w/start_search_session.sh --target "红色背包" --mode nav2_plan_only
bash scripts/go2w/start_search_session.sh --target "红色背包" --mode nav2_execute
```

`nav2_plan_only` 需要 Level D、厂家 footprint 的现场间隙复核、有效 `/scan`、
LIO、地图、TF 和 ComputePathToPose 全部通过。`nav2_execute` 还需要 Collision Monitor、Velocity
Smoother、lease、仲裁器、300 ms watchdog、急停、遥控器接管检测、零错误状态、
操作员 arm 和二次确认。当前结果保存在：

```text
outputs/go2w_acceptance/navigation_gate/plan_only.json
outputs/go2w_acceptance/navigation_gate/execute.json
```

执行速度链固定为：

```text
controller_server
→ velocity_smoother
→ collision_monitor
→ /go2w/nav2_cmd_vel
→ go2w_control_arbiter
→ go2w_cmd_vel_bridge
→ /go2w/motion（唯一 lease holder）
```

控制器上限为 `0.15 m/s` 和 `0.20 rad/s`，桥接器会再次限速/限加速度。完整
执行 launch 默认 `execution_enabled:=false`，此时不启动 Nav2 执行节点；纯规划
另有 `robot_scene_nav2_plan_only.launch.py`，其中不包含 controller、smoother、
collision monitor 或任何 `cmd_vel` 发布节点。

Streamlit 选择“Go2-W 实时目标搜索”可查看相机、LiDAR、LIO、TF、lease、控制源、
搜索状态、证据和全部导航门禁。门禁失败时按钮禁用并显示阻断项。

停止主机侧 Worker 并保留日志：

```bash
bash scripts/go2w/stop_all.sh
```

本次部署从未取得运动 lease，所以该脚本只停止项目自己的主机进程并写入取消/
执行禁用标记，不会向机器人发送 `StopMove`，也不声称能停止外部进程管理的
Sport lease。标定文件位于 `configs/go2w/`，会话
输出位于 `outputs/live_sessions/`，ROS 日志位于 `runtime/go2w/sessions/`。详细分级
状态和物理待办见 `reports/go2w_robot_scene_demo_deployment_report.md`。
