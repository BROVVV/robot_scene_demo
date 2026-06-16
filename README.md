# 机器狗场景理解 Demo

这是一个“单张图片 + 目标描述”的机器狗场景理解 Demo。

用户输入一张场景图片和一个目标描述，例如“桌子上的手机”“挂着黄衣服的椅子”。系统会输出：

- 场景摘要
- 物体表
- 关系表
- 拓扑图
- 原图物体检测框标注图
- 目标是否存在
- 一次性路线规划

当前项目是 Demo，不接入真实机器狗、不接入 ROS2、不做连续闭环搜索、不做真实 SLAM，也不保证厘米级距离精度。

## 功能概览

当前支持三种运行方式：

1. 模拟数据模式：不需要 API Key，不需要模型，适合验证部署是否成功。
2. 视觉大模型模式：调用硅基流动 OpenAI 兼容接口，低成本快速得到结构化结果。
3. Grounding DINO + SAM2 模式：本地检测物体框和分割结果，再由本地代码补全关系、拓扑和路线。

推荐从模拟数据模式开始部署，确认项目能跑通后，再配置真实 API 或本地检测器。

## 项目结构

```text
robot_scene_demo/
├── README.md
├── requirements.txt
├── .env.example
├── run_demo.py
├── streamlit_app.py
├── app/
│   ├── config.py
│   ├── prompts.py
│   ├── schemas.py
│   ├── detectors/
│   │   ├── base.py
│   │   ├── grounded_sam_subprocess.py
│   │   ├── grounded_sam_worker.py
│   │   └── vocabulary.py
│   ├── llm_clients/
│   │   ├── base.py
│   │   └── siliconflow_client.py
│   ├── services/
│   │   ├── detector_scene_builder.py
│   │   ├── image_annotator.py
│   │   ├── output_writer.py
│   │   ├── relation_enricher.py
│   │   ├── route_planner.py
│   │   ├── scene_analyzer.py
│   │   ├── scene_normalizer.py
│   │   ├── table_exporter.py
│   │   ├── target_matcher.py
│   │   └── topology_builder.py
│   └── utils/
│       └── json_utils.py
├── examples/
│   └── mock_scene_result.json
└── outputs/
```

## 从零部署

下面假设你是一台全新的 Ubuntu/Linux 电脑。如果你已经有 Python，可以跳过重复安装步骤。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
```

确认 Python 可用：

```bash
python3 --version
pip3 --version
git --version
```

建议 Python 版本使用 3.10 到 3.13。

### 2. 获取项目代码

如果代码已经在本机：

```bash
cd /home/user/go2_robot/robot_scene_demo
```

如果代码已经上传到 GitHub，另一台电脑可以这样下载：

```bash
git clone <你的仓库地址>
cd robot_scene_demo
```

### 3. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

激活后，命令行前面通常会出现 `(.venv)`。

升级 pip：

```bash
python -m pip install --upgrade pip
```

### 4. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

如果默认源下载很慢，可以使用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

如果遇到代理相关错误，例如 `socksio package is not installed`，本项目已经在 `requirements.txt` 中包含 `socksio`，重新执行安装即可。

### 5. 创建配置文件

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
nano .env
```

最小配置如下：

```text
SILICONFLOW_API_KEY=你的硅基流动APIKey
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-8B-Instruct
OUTPUT_DIR=outputs
```

注意：

- `.env` 里是私密配置，不要上传 GitHub。
- `SILICONFLOW_MODEL` 必须是支持图片输入的视觉模型。
- 如果只跑模拟数据模式，可以不填写 API Key。

## 运行方式一：模拟数据模式

这是最推荐的第一步验证方式，不需要 API Key，不需要真实图片，不调用任何外部 API。

```bash
python run_demo.py --mock
```

成功后会生成：

```text
outputs/scene_result.json
outputs/object_table.csv
outputs/relation_table.csv
outputs/topology_graph.png
outputs/topology_graph.graphml
```

模拟数据模式不会生成真实图片标注图，因为它没有输入原图。

## 运行方式二：视觉大模型 API

准备一张图片，例如：

```text
/home/user/test_scene.jpg
```

运行：

```bash
python run_demo.py --image /home/user/test_scene.jpg --target "挂着黄衣服的椅子" --detector llm
```

输出结果会写入 `outputs/`。

说明：

- 该模式只调用一次视觉大模型 API。
- 为降低延迟，图片会压缩到配置里的 `IMAGE_MAX_SIDE`。
- 如果模型没有返回真实 bbox，标注图会跳过不可用框，避免画出误导性的全图框。

## 运行方式三：Grounding DINO + SAM2 本地检测

这个模式不依赖大模型识别物体，而是使用本地 Grounding DINO + SAM2 检测物体框。

运行：

```bash
python run_demo.py --image /home/user/test_scene.jpg --target "挂着黄衣服的椅子" --detector grounded_sam
```

成功后会额外生成：

```text
outputs/annotated_scene.png
```

这张图会在原图上画出所有识别物体的框，并显示：

```text
中文名 置信度
```

### Grounding DINO + SAM2 环境要求

本项目默认假设 Grounded-SAM-2 已在下面位置：

```text
/home/user/python3.10.0/Grounded-SAM-2
```

默认 Python 解释器：

```text
/home/user/python3.10/bin/python
```

默认权重：

```text
gdino_checkpoints/groundingdino_swint_ogc.pth
checkpoints/sam2.1_hiera_tiny.pt
```

如果你的路径不同，在 `.env` 中修改：

```text
GROUNDED_SAM_ROOT=/你的/Grounded-SAM-2/路径
GROUNDED_SAM_PYTHON=/你的/python
GROUNDED_SAM_PYTHONPATH=/你的/site-packages
GROUNDING_DINO_CHECKPOINT=gdino_checkpoints/groundingdino_swint_ogc.pth
SAM2_CHECKPOINT=checkpoints/sam2.1_hiera_tiny.pt
```

如果你完全没有配置 Grounded-SAM-2，可以先使用模拟数据模式或视觉大模型模式。

## Streamlit 网页

启动网页：

```bash
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

浏览器打开：

```text
http://localhost:8501
```

如果在局域网其他设备访问，把 `localhost` 换成运行电脑的 IP。

网页支持：

- 模拟数据
- 真实 API
- GroundingDINO+SAM2
- 上传图片
- 输入目标描述
- 查看物体表
- 查看关系表
- 查看拓扑图
- 查看标注图
- 查看 JSON

## 输出文件说明

每次运行会写入 `outputs/`：

```text
outputs/scene_result.json       完整结构化结果
outputs/object_table.csv        物体表
outputs/relation_table.csv      关系表
outputs/topology_graph.png      拓扑图
outputs/topology_graph.graphml  GraphML 拓扑数据
outputs/annotated_scene.png     原图检测框标注图，有原图和可用 bbox 时生成
```

物体表主要字段：

- `id`：物体编号
- `英文名`：检测器或模型原始英文标签
- `中文名`：展示用中文名
- `类别`：中文类别
- `类别代码`：程序内部类别
- `颜色`
- `属性`
- `相对方向`
- `估计距离`
- `bbox_x1/y1/x2/y2`
- `置信度`

关系表主要字段：

- `source_id`
- `source中文名`
- `target_id`
- `target中文名`
- `relation_type`
- `中文描述`
- `估计距离`
- `置信度`

## 低延迟设计

为了降低总用时，项目默认做了这些取舍：

- 视觉大模型模式只调用一次 API。
- 本地关系补全不调用模型。
- 拓扑图由本地 `networkx` 生成。
- 图片会在发送给 API 前压缩。
- Grounding DINO + SAM2 模式最多保留 `MAX_DETECTED_OBJECTS` 个检测结果。
- 默认关闭低物体数二次复查：`ENABLE_LOW_OBJECT_RETRY=false`。

如果你想提高识别完整度，可以在 `.env` 中调高：

```text
MAX_DETECTED_OBJECTS=50
```

如果你愿意牺牲延迟换取更完整的大模型识别，可以开启：

```text
ENABLE_LOW_OBJECT_RETRY=true
```

## 常见问题

### 1. 为什么表格里有英文？

`英文名` 是模型或检测器的原始标签，保留它是为了调试。真正展示用的是 `中文名` 和 `类别`。

### 2. 为什么没有标注图？

标注图需要两个条件：

- 有原图路径。
- 每个物体有可用 bbox。

模拟数据没有原图，所以不会生成真实标注图。视觉大模型如果没有返回 bbox，也不会乱画全图框。

### 3. 为什么拓扑图之前是孤立节点？

模型有时只返回很少关系。现在项目会在本地根据 bbox、相对方向和估计距离自动补充空间关系，保证拓扑图尽量连通。

### 4. Grounding DINO/SAM2 报找不到图片

请使用绝对路径，或确认当前版本已把图片路径转为绝对路径。

推荐：

```bash
python run_demo.py --image /home/user/test_scene.jpg --target "目标" --detector grounded_sam
```

### 5. API 请求超时

可以降低图片大小：

```text
IMAGE_MAX_SIDE=512
```

也可以换更快的视觉模型，或改用本地 Grounding DINO + SAM2。

## GitHub 上传注意事项

不要上传：

- `.env`
- `.venv/`
- `outputs/`
- `__pycache__/`
- 大模型权重
- 私人图片
- API Key、密码、Token

本项目已经提供 `.gitignore` 排除这些内容。

第一次上传到 GitHub 的典型命令：

```bash
git init
git add .
git commit -m "Initial robot scene demo"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

GitHub 现在不支持用账户密码直接 `git push`。请使用 GitHub Personal Access Token，或者安装并登录 GitHub CLI。
