# robot_scene_demo 从零部署与运行手册

本文档目标：把本文件交给 AI 或运维人员后，可以在一台“什么都没配置”的 Ubuntu 机器上，从零部署并跑通完整流程：

- mock 场景理解
- 硅基流动视觉 API 场景理解
- GroundingDINO + SAM2 本地开放词表检测
- 第一视角视频目标搜索与视频语义记忆
- 无人工先验的大模型自生成常识推理与观察记忆导航
- Streamlit Web UI
- ROS2 可接收的 `/cmd_vel` 指令数据 dry-run 输出
- 平台避障辅助下的自适应移动距离 Motion Horizon 输出

项目当前仍是离线/半离线 Demo，不直接控制真实机器狗。ROS2 部分默认只输出和预览 `geometry_msgs/msg/Twist` 数据；只有显式执行 `--execute` 且已经安装并 source ROS2 环境时，才会尝试发布到 `/cmd_vel`。
动态运动视界只决定高层建议移动距离，不实现深度图避障、代价地图、局部路径规划或急停；真实避障和打断仍由机械狗底层平台、SDK、ROS 安全层或操作员负责。

## 0. 推荐硬件与系统前提

推荐系统：

- Ubuntu 22.04 或 24.04 x86_64
- 至少 16 GB RAM
- 至少 20 GB 可用磁盘
- NVIDIA GPU 用于 GroundingDINO + SAM2，本项目已验证 RTX 4090 + CUDA PyTorch 可运行

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
SILICONFLOW_API_KEY=这里填你的硅基流动APIKey
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

本项目现在的“常识”来自运行时 LLM 假设和机器人观察记忆，不来自开发者写死的物体-位置先验。LLM 假设只能用于排序搜索区域、生成动态检测词、建议下一视角；目标是否找到必须通过 bbox/crop/mask/frame 等视觉证据门控。

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

### 6.1 单元测试

```bash
python -m unittest discover -s tests
```

期望：

```text
OK
```

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
outputs/evidence_gating_report.json
outputs/observation_memory_updates.json
outputs/prior_usage_report.json
outputs/knowledge_aware_result.json
outputs/parsed_task.json
outputs/retrieved_knowledge.json
outputs/predictive_scene_graph.graphml
outputs/hypotheses.json
outputs/knowledge_updates.json
outputs/reasoning_report.md
```

兼容说明：旧参数 `--enable-knowledge` 仍可使用，但会提示 deprecated，并映射为 LLM runtime prior + observation memory + evidence gating；默认不再启用静态 KB 或手写位置先验。

### 6.3 任务样例验证

```bash
python scripts/evaluate_task_examples.py
```

期望输出 JSON 中包含：

```json
"passed": true
```

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
GROUNDED_SAM_PYTHON=/root/miniconda3/envs/go2_robot_scene_demo/bin/python
GROUNDED_SAM_PYTHONPATH=/root/gpufree-data/Grounded-SAM-2:/root/gpufree-data/Grounded-SAM-2/grounding_dino
GROUNDING_DINO_CONFIG=grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py
GROUNDING_DINO_CHECKPOINT=gdino_checkpoints/groundingdino_swint_ogc.pth
GROUNDING_DINO_BOX_THRESHOLD=0.25
GROUNDING_DINO_TEXT_THRESHOLD=0.20
ENABLE_SAM2=true
SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
SAM2_CHECKPOINT=checkpoints/sam2.1_hiera_tiny.pt
MAX_DETECTED_OBJECTS=30
DETECTION_DEVICE=auto
DETECTOR_TIMEOUT_SECONDS=180
```

注意 `GROUNDED_SAM_PYTHON` 必须写成你机器上的真实 Python 路径。查询方式：

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

上面的 `--text-prompt` 只是 worker 手动 debug 示例。项目主流程默认不会依赖固定室内物体 prompt 表；检测词优先来自用户目标、LLM 运行时 prior、当前视觉摘要和 crop 复核摘要。

### 8.7 跑项目 GroundingDINO+SAM2 主流程

```bash
cd /root/gpufree-data/robot_scene_demo
python run_demo.py \
  --image "/root/gpufree-data/微信图片_20260617144106.jpg" \
  --target "巡查玄关区域，识别地面可通行区域和主要障碍物" \
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
...
```

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
- `任务模板`：选择常见任务。
- `目标描述`：可手动输入任务，例如 `找到手机`。
- `场景图片`：真实 API 和 GroundingDINO+SAM2 模式需要上传。
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

### 10.1 视频目标搜索

Web UI 的“运行模式”中选择“视频目标搜索”后，可以上传
`mp4/avi/mov/mkv` 视频，设置目标、检测器、关键帧采样 FPS 和最大分析帧数。

也可以直接使用命令行：

```bash
python run_video_demo.py \
  --video "/path/to/robot_walk.mp4" \
  --target "手机" \
  --detector mock \
  --sample-fps 1.0 \
  --max-frames 120 \
  --enable-knowledge \
  --enable-video-memory
```

真实检测时把 `mock` 替换为 `llm` 或 `grounded_sam`。视频模式会生成：

```text
outputs/video_target_search.json
outputs/video_target_timeline.json
outputs/video_object_tracks.json
outputs/video_candidate_regions.json
outputs/video_memory_graph.json
outputs/video_memory_graph.graphml
outputs/video_memory_updates.json
outputs/video_spatial_memory_snapshot.json
outputs/video_predictive_scene_graph.graphml
outputs/video_predictive_scene_graph.json
outputs/video_hypotheses.json
outputs/video_navigation_trace.json
outputs/video_reasoning_report.md
outputs/video_frames/
outputs/video_frames_annotated/
outputs/video_scene_results/
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

先安装 ROS2。Ubuntu 22.04 常用 ROS2 Humble：

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository universe
sudo apt update
sudo apt install -y ros-humble-ros-base
```

source ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
```

确认 Python 能导入 ROS2：

```bash
python - <<'PY'
import rclpy
from geometry_msgs.msg import Twist
print("ros2 python ok")
PY
```

先 dry-run 预览：

```bash
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
```

确认安全后再发布：

```bash
python scripts/publish_ros2_motion_plan.py \
  outputs/ros2_motion_plan.json \
  --execute \
  --allow-dry-run-plan
```

如果你的机器狗不是监听 `/cmd_vel`，可以改 topic：

```bash
python scripts/publish_ros2_motion_plan.py \
  outputs/ros2_motion_plan.json \
  --execute \
  --allow-dry-run-plan \
  --topic /your_robot/cmd_vel
```

安全要求：

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
  --target "巡查玄关区域，识别地面可通行区域和主要障碍物" \
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

### 15.5 Streamlit 端口占用

换端口：

```bash
bash scripts/start_web_ui.sh 8502
```

或查占用：

```bash
ss -ltnp | grep 8501
```

### 15.6 ROS2 发布脚本找不到 `rclpy`

说明当前 shell 没有 ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
python - <<'PY'
import rclpy
print("ok")
PY
```

### 15.7 API 超时

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
python -m unittest discover -s tests
python run_demo.py --mock --enable-knowledge
python scripts/evaluate_task_examples.py
python scripts/publish_ros2_motion_plan.py outputs/ros2_motion_plan.json
bash scripts/start_web_ui.sh
```

如果配置了真实 API：

```bash
python run_demo.py --image "/path/to/image.jpg" --target "找到手机" --detector llm --enable-knowledge
```

如果配置了 GroundingDINO+SAM2：

```bash
python run_demo.py --image "/path/to/image.jpg" --target "巡查玄关区域，识别地面可通行区域和主要障碍物" --detector grounded_sam --enable-knowledge
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
  --detector grounded_sam \
  --sample-fps 3.0 \
  --max-frames 300 \
  --enable-tracking \
  --enable-crop-verify \
  --enable-knowledge \
  --enable-video-memory
```

新增的主要调试输出包括：

```text
outputs/target_profile.json
outputs/candidate_objects.json
outputs/crop_verify_results.json
outputs/fused_objects.json
outputs/detection_debug_report.md
outputs/video_track_summary.json
outputs/video_crop_verify_results.json
outputs/video_tracking_debug_report.md
```

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
  --detector grounded_sam \
  --sample-fps 3.0 \
  --max-frames 300 \
  --enable-llm-prior \
  --enable-tracking \
  --enable-crop-verify \
  --enable-observation-memory \
  --enable-evidence-gating \
  --disable-handwritten-priors
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
