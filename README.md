# 机器狗场景理解与知识增强 Demo

这是一个“单张图片 + 用户任务”的机器狗场景理解 Demo。它可以输出基础视觉场景结构，也可以在开启知识增强后输出任务解析、知识检索、预测性场景图、推理假设和任务规划。

当前项目不是 ROS2 机器人闭环系统，不控制真实机器狗，不做 SLAM，也不保证真实距离精度。它适合作为离线演示、算法验证和后续接入机器狗系统前的结构化原型。

## 能力概览

输入：

- 一张场景图片，或内置 mock 数据。
- 一个自然语言任务，例如：
  - `桌子上的手机`
  - `找到手机`
  - `数数这个房间里有几个椅子`
  - `巡查这层楼看看有几个房间的门是打开的`
  - `找到 503 房间`

输出：

- `SceneAnalysisResult`：场景摘要、物体、关系、拓扑、目标判断、路线规划。
- `KnowledgeAwareSceneResult`：任务解析、检索知识、预测性场景图、场景假设、任务规划、知识更新、最终回答。
- CSV 表格、PNG 拓扑图、GraphML 图数据、JSON 文件、Markdown 推理报告。
- Streamlit 网站工作台。

运行模式：

- `模拟数据`：不需要 API Key，不需要真实图片，用于验证安装和流程。
- `真实 API`：调用硅基流动 OpenAI-compatible 视觉模型。
- `GroundingDINO+SAM2`：走本地开放词表检测器，需要额外模型代码和权重。
- `知识增强`：在任一基础场景结果上叠加本地知识库、PSG、推理假设和任务规划。

## 项目结构

```text
robot_scene_demo/
├── README.md
├── requirements.txt
├── .env.example
├── run_demo.py
├── streamlit_app.py
├── app/
│   ├── schemas.py
│   ├── config.py
│   ├── prompts.py
│   ├── detectors/
│   ├── llm_clients/
│   ├── services/
│   ├── knowledge/
│   ├── reasoning/
│   ├── planning/
│   └── utils/
├── data/
│   └── scene_kb/
├── docs/
├── examples/
│   ├── mock_scene_result.json
│   ├── mock_knowledge_aware_result.json
│   └── tasks/
├── scripts/
│   ├── query_scene_kb.py
│   ├── evaluate_task_examples.py
│   └── start_web_ui.sh
├── tests/
└── outputs/
```

## 给 AI 的从零部署指令

如果你让另一个 AI 在全新 Ubuntu 电脑上部署，可以直接给它下面这段任务：

```text
请在 Ubuntu 上从零部署 robot_scene_demo。
要求：
1. 不要污染系统 Python，优先创建 conda 环境 go2_robot_scene_demo。
2. 安装项目依赖。
3. 先运行 mock 和单元测试确认项目可用。
4. 启动 Streamlit 网站。
5. 不要把 .env、API Key、outputs、模型权重上传到 GitHub。
```

下面是人工或 AI 可以逐条执行的完整步骤。

## 1. 安装系统基础工具

```bash
sudo apt update
sudo apt install -y git curl wget ca-certificates build-essential
```

确认：

```bash
git --version
curl --version
```

## 2. 安装 Miniconda

如果机器已经有 conda，可以跳过本节。

```bash
cd /tmp
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

安装后重新打开终端，或执行：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda --version
```

## 3. 获取代码

如果已经在本机：

```bash
cd /home/user/go2_robot/robot_scene_demo
```

如果从 GitHub 拉取：

```bash
git clone https://github.com/BROVVV/robot_scene_demo.git
cd robot_scene_demo
```

## 4. 创建隔离 conda 环境

推荐使用 Python 3.11：

```bash
conda create -n go2_robot_scene_demo python=3.11 -y
conda activate go2_robot_scene_demo
```

确认当前 Python 在新环境中：

```bash
which python
python --version
```

路径应类似：

```text
/home/user/miniconda3/envs/go2_robot_scene_demo/bin/python
```

## 5. 安装依赖

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果下载慢，可以使用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 6. 验证安装

先跑单元测试：

```bash
python -m unittest discover -s tests
```

期望看到类似：

```text
Ran 32 tests ... OK
```

再跑 mock 知识增强流程：

```bash
python run_demo.py --mock --enable-knowledge
```

成功后会生成：

```text
outputs/scene_result.json
outputs/object_table.csv
outputs/relation_table.csv
outputs/topology_graph.png
outputs/topology_graph.graphml
outputs/knowledge_aware_result.json
outputs/parsed_task.json
outputs/retrieved_knowledge.json
outputs/predictive_scene_graph.graphml
outputs/hypotheses.json
outputs/knowledge_updates.json
outputs/reasoning_report.md
```

验证任务样例：

```bash
python scripts/evaluate_task_examples.py
```

期望输出：

```json
"passed": true
```

## 7. 启动网站 UI

推荐使用项目脚本：

```bash
bash scripts/start_web_ui.sh
```

默认端口是 `8501`。浏览器打开：

```text
http://localhost:8501
```

如果端口被占用：

```bash
bash scripts/start_web_ui.sh 8502
```

局域网其他设备访问时，把 `localhost` 换成运行电脑的 IP。

## 8. 网站 UI 怎么用

左侧是任务配置：

- `运行模式`
  - `模拟数据`：无需图片和 API Key。
  - `真实 API`：上传图片并调用视觉大模型。
  - `GroundingDINO+SAM2`：上传图片并调用本地检测器。
- `任务模板`
  - 模板名包含任务类型，例如 `find_object`、`count_objects`、`inspect_area`、`find_room`。
  - 选择模板后会自动填充目标描述。
- `目标描述`
  - 可以手动改成任何任务文本。
- `场景图片`
  - 真实 API 和 GroundingDINO+SAM2 模式需要上传。
- `知识增强流程`
  - 建议打开。会输出任务解析、知识检索、PSG、假设、任务规划和知识更新。
- `预测性场景图`
  - 显示 PSG 结构。
- `高精度复查`
  - 只对真实 API 模式有意义，会增加耗时。

右侧是结果工作台：

- 输入预览
- 运行状态
- 当前任务解析
- 场景结果
- 物体表、关系表、拓扑图、标注图
- 知识增强结果
- PSG、假设、任务规划、知识更新
- 输出文件下载

## 9. 配置真实视觉 API

复制配置文件：

```bash
cp .env.example .env
```

编辑：

```bash
nano .env
```

至少设置：

```text
SILICONFLOW_API_KEY=你的硅基流动APIKey
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-8B-Instruct
DETECTION_BACKEND=llm
OUTPUT_DIR=outputs
```

然后运行：

```bash
python run_demo.py --image /path/to/image.jpg --target "找到手机" --detector llm --enable-knowledge
```

注意：

- `.env` 不要上传 GitHub。
- API Key 不要写进 README、代码、命令历史或 git remote URL。
- 如果只跑模拟数据，可以不配置 API Key。

## 10. 配置 GroundingDINO+SAM2

该模式需要你本机已有 Grounded-SAM-2 代码、Python 环境和权重。

`.env.example` 中有这些配置：

```text
GROUNDED_SAM_ROOT=/home/user/python3.10.0/Grounded-SAM-2
GROUNDED_SAM_PYTHON=/home/user/python3.10/bin/python
GROUNDED_SAM_PYTHONPATH=/home/user/python3.10/lib/python3.10/site-packages
GROUNDING_DINO_CONFIG=grounding_dino/groundingdino/config/GroundingDINO_SwinT_OGC.py
GROUNDING_DINO_CHECKPOINT=gdino_checkpoints/groundingdino_swint_ogc.pth
ENABLE_SAM2=true
SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
SAM2_CHECKPOINT=checkpoints/sam2.1_hiera_tiny.pt
```

如果未配置 GroundingDINO+SAM2，先使用：

```bash
python run_demo.py --mock --enable-knowledge
```

或：

```bash
python run_demo.py --image /path/to/image.jpg --target "目标" --detector llm
```

## 11. 常用命令

启动网站：

```bash
conda activate go2_robot_scene_demo
bash scripts/start_web_ui.sh
```

运行 mock：

```bash
python run_demo.py --mock
python run_demo.py --mock --enable-knowledge
```

查询知识库：

```bash
python scripts/query_scene_kb.py --target "手机" --room_type office --location floor_5
```

验证任务样例：

```bash
python scripts/evaluate_task_examples.py
```

运行测试：

```bash
python -m unittest discover -s tests
```

## 12. 输出文件说明

基础输出：

```text
outputs/scene_result.json
outputs/object_table.csv
outputs/relation_table.csv
outputs/topology_graph.png
outputs/topology_graph.graphml
outputs/annotated_scene.png
```

知识增强输出：

```text
outputs/knowledge_aware_result.json
outputs/parsed_task.json
outputs/retrieved_knowledge.json
outputs/predictive_scene_graph.graphml
outputs/hypotheses.json
outputs/knowledge_updates.json
outputs/reasoning_report.md
```

## 13. GitHub 上传注意事项

不要上传：

- `.env`
- `.venv/`
- conda 环境目录
- `outputs/`
- `__pycache__/`
- 大模型权重
- 私人图片
- API Key、GitHub token、密码

检查将要提交的文件：

```bash
git status --short
git diff -- . ':!outputs'
```

如果 remote URL 里含 token，立刻改掉：

```bash
git remote set-url origin https://github.com/<用户名>/<仓库名>.git
```

推荐用 GitHub CLI 或浏览器登录，不要把 token 写进命令：

```bash
gh auth login
git push origin main
```

## 14. 当前边界

- 不接 ROS2。
- 不控制真实机器狗。
- 不做连续 SLAM。
- PSG 和任务规划是规则版。
- 知识库是本地 JSON/JSONL。
- `RoutePlan` 和 `TaskPlan` 是结构化文本计划，不是可执行底盘控制命令。
