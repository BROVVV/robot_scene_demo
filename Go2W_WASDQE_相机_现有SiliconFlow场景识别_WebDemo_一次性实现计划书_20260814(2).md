# Go2-W WASD+QE 相机 + 现有 SiliconFlow 场景物体识别 Web Demo
# 一次性实现详细计划书

> 版本：2026-08-14  
> 项目基线：`/home/brov/robot/robot_scene_demo` 当前工作树  
> 目标：在不破坏现有 `robot_scene_demo`、UniGoal、Nav2、PandarXT-16 和真机搜索链的前提下，新增一个**独立、简洁、可单独启动的小型 Web Demo**。  
> 用户启动 Demo 后打开一个 WebUI；页面持续显示 Go2-W 内置相机画面；用户可以使用 WASD+QE 控制机器人，其中 W/S/A/D 分别对应前进/后退/左横移/右横移，Q/E 分别对应左转/右转；页面右侧每隔几秒异步调用项目中已经配置好的 SiliconFlow 视觉大模型分析当前画面，列出主要可见物体并自动刷新。

---

# 0. 给实现 AI 的总任务

请直接在当前 `robot_scene_demo` 工作树中实现本 Demo。

本 Demo 的核心效果：

```text
运行一条启动命令
        ↓
自动启动/检查相机与控制所需服务
        ↓
打开浏览器 WebUI
        ↓
┌──────────────────────────────────────────────┐
│ Go2-W Manual Camera Demo      [急停] [控制状态] │
├───────────────────────────┬──────────────────┤
│                           │ 当前场景主要物体   │
│                           │                  │
│       实时相机画面         │ 物体  数量  位置   │
│                           │ chair  2   左/中   │
│                           │ table  1   中间     │
│                           │ door   1   右侧     │
├───────────────────────────┴──────────────────┤
│ W前 S后 A左移 D右移 Q左转 E右转   当前：STOP     │
└──────────────────────────────────────────────┘
```

交互要求：

```text
轻按 W
→ 机器人向前走一个很小的离散步

按住 W
→ 后端连续发送小步
→ 机器人连续向前移动

松开 W
→ 立即停止继续发步
→ 当前动作尽快 STOP/cancel

S/A/D/Q/E 同理
```

场景识别：

```text
相机持续显示
      │
      ├── 用户控制完全不等待 LLM
      │
      └── 后台每隔数秒取一张最新帧
                 ↓
              视觉 LLM
                 ↓
          JSON 主要物体列表
                 ↓
            WebUI 表格刷新
```

最重要要求：

> **相机、键盘控制和 LLM 识别必须互相异步。LLM 慢时不能卡住相机，也不能卡住 WASD+QE。**

---

# 1. 开工前必须读取的现有资源

AI 不得重新造已有真机基础设施。

必须先完整阅读：

```text
README.md

go2w_codex_stage_handoff_20260813_pandarxt16.md

scripts/go2w/run_autonomous_loop.py
run_live_robot_demo.py

app/live_robot/frame_bundle_reader.py
app/live_robot/step_planner.py
app/live_robot/motion_bounds.py
app/live_robot/rotation_lease.py

app/detectors/siliconflow_vision_worker.py
app/llm_clients/siliconflow_client.py

app/live_robot/ui_status.py
app/ui/go2w_live_panel.py

scripts/go2w/start_live_perception.sh
scripts/go2w/stop_all.sh
```

还必须读取外部现有运动控制项目：

```text
/home/brov/robot/unitree_go2w_control
```

重点确认：

```text
go2w_motion_control
/go2w/motion
/go2w/arm
/go2w/emergency_stop
go2w_motion_interfaces
```

禁止根据本计划猜 Action message 字段。

必须直接读取当前：

```text
go2w_motion_interfaces
go2w_motion_control server
```

和本项目已有 Action client 调用方式，然后复用。

---

# 2. 当前已有资源，本 Demo 必须优先复用

## 2.1 相机

项目已经有正式 Go2-W RGB ROS2 bridge：

```text
/camera/front/image_raw
/camera/front/image_raw/compressed
/camera/front/camera_info
```

当前相机桥已经具备：

```text
损坏帧跳过
自动重连
CameraInfo
1920×1080
```

本 Demo **不允许再直接连接 Go2-W VideoHub RPC**。

正确：

```text
existing go2w_camera_bridge
        ↓
/camera/front/image_raw/compressed
        ↓
Demo
```

这样不会和现有相机进程抢设备。

## 2.2 运动控制

已有：

```text
go2w_motion_control
Action /go2w/motion
Service /go2w/arm
Service /go2w/emergency_stop
```

本 Demo 不新增底层 Unitree 控制协议。

禁止：

```text
LowCmd
/lowcmd
SDK 裸速度循环
直接操作关节
```

## 2.3 SiliconFlow 视觉识别：只复用项目现有配置

当前 `robot_scene_demo` 已经配置并实际使用 SiliconFlow API。

本 Demo **不得新建第二套 SiliconFlow 配置**，不得要求用户重新填写 API Key，也不得在 Demo 中复制一套 HTTP 请求逻辑。

必须直接复用当前项目已有：

```text
app/detectors/siliconflow_vision_worker.py
app/llm_clients/siliconflow_client.py
app/config.py
现有 .env / .env.go2w 中已经生效的 SiliconFlow 配置
```

模型、API endpoint、API key、timeout 等，以**当前项目已经配置好的值**为唯一来源。

本 Demo 只新增与 Demo 自身有关的调度参数，例如：

```text
MANUAL_DEMO_LLM_ENABLED
MANUAL_DEMO_LLM_INTERVAL_SECONDS
MANUAL_DEMO_LLM_HIDE_LOW_CONFIDENCE
```

不要新增：

```text
新的 SILICONFLOW_API_KEY
MANUAL_DEMO_SILICONFLOW_API_KEY
第二套 base URL
第二套模型账号配置
```

如果项目当前 SiliconFlow 配置可以正常完成现有 LLM quick/verify，则 Demo 应直接能调用同一个视觉能力。

## 2.4 双 Python

必须继续遵守现有项目部署方式：

```text
ROS2 / rclpy:
    /usr/bin/python3

Web / app / LLM:
    Conda go2_robot_scene_demo
```

不要让 Conda Python 直接 import Humble `rclpy`。

因此本 Demo 应采用：

```text
Web/LLM Process
        ↕ IPC
ROS Worker Process
```

---

# 3. 本 Demo 明确不需要的东西

为了保持“小、简单、稳定”，本 Demo 不要接：

```text
UniGoal
GoalGraph
PSG
Observation Memory
scene topology
Nav2
地图
RGB-LiDAR 3D
Pandar metric fusion
目标搜索状态机
GroundingDINO/SAM2
video tracking
target verify
```

这些都不是本 Demo 目标。

本 Demo 只需要：

```text
相机
人工 WASD+QE
SiliconFlow 场景物体表
基础运动安全
```

---

# 4. 推荐技术方案

不要直接在现有大型 `streamlit_app.py` 内实现。

原因：

```text
Streamlit 适合表单/状态面板
但不适合低延迟 keydown/keyup
也不适合 browser blur/hold heartbeat/deadman
```

本 Demo 新建一个非常小的：

```text
FastAPI
+
原生 HTML
+
原生 JavaScript
+
WebSocket
```

不使用 React/Vue。

减少前端依赖。

---

# 5. 进程架构

推荐：

```text
┌─────────────────────────────────────────┐
│ Conda: Manual Demo Web Process          │
│                                         │
│ FastAPI                                 │
│  ├── HTML/CSS/JS                        │
│  ├── MJPEG endpoint                     │
│  ├── keyboard websocket                 │
│  ├── object list API                    │
│  └── LLM background analyzer            │
│                                         │
│ existing SiliconFlow client/worker      │
└────────────────┬────────────────────────┘
                 │ local JSONL IPC
                 │
┌────────────────▼────────────────────────┐
│ /usr/bin/python3: ROS Worker            │
│                                         │
│ subscribe:                              │
│ /camera/front/image_raw/compressed      │
│ /lf/sportmodestate                      │
│ odom / safety topics where available    │
│                                         │
│ client:                                 │
│ /go2w/motion                            │
│ /go2w/arm                               │
│ /go2w/emergency_stop                    │
│                                         │
│ writes latest.jpg atomically            │
└─────────────────────────────────────────┘
```

这样：

```text
ROS 环境
和
Conda LLM/Web 环境
```

继续隔离。

---

# 6. 推荐新增目录

新增：

```text
app/manual_web_demo/
├── __init__.py
├── config.py
├── models.py
├── ros_worker_client.py
├── manual_drive_controller.py
├── scene_object_analyzer.py
├── web_server.py
├── templates/
│   └── index.html
└── static/
    ├── app.js
    └── style.css
```

ROS 侧：

```text
scripts/go2w/manual_web_demo_ros_worker.py
```

启动：

```text
scripts/go2w/start_manual_web_demo.sh
scripts/go2w/stop_manual_web_demo.sh
```

测试：

```text
tests/test_manual_web_demo_config.py
tests/test_manual_drive_controller.py
tests/test_manual_drive_deadman.py
tests/test_manual_scene_object_parser.py
tests/test_manual_scene_object_scheduler.py
tests/test_manual_web_demo_api.py
tests/test_manual_ros_worker_protocol.py
```

文档：

```text
docs/GO2W_MANUAL_WASD_WEB_DEMO.md
```

---

# 7. WebUI 设计

页面保持简洁。

推荐 70/30 布局：

```text
┌─────────────────────────────────────────────────┐
│ Go2-W Camera Demo         ●相机 ●控制 ●LLM  [STOP]│
├───────────────────────────────┬─────────────────┤
│                               │ 场景主要物体     │
│                               │                 │
│                               │ 名称 数量 位置   │
│        Camera                 │                 │
│                               │                 │
│                               │                 │
├───────────────────────────────┴─────────────────┤
│     W                                            │
│ A/D横移 Q/E转向   按键: W   当前: 前进   LLM: 3s前 │
└─────────────────────────────────────────────────┘
```

## 7.1 左侧

只显示：

```text
实时 RGB
```

可在左下角叠：

```text
FPS
当前按键
当前 motion 状态
```

不要画复杂检测框。

## 7.2 右侧

表格：

| 物体 | 数量 | 大致位置 | 置信 |
|---|---:|---|---|
| 椅子 | 2 | 左侧、中间 | 高 |
| 桌子 | 1 | 中间 | 高 |
| 门 | 1 | 右侧 | 中 |

表下面：

```text
最近识别：10:32:11
帧年龄：0.2s
LLM耗时：7.4s
状态：空闲 / 识别中
```

## 7.3 顶部

三个状态灯：

```text
Camera
Motion
LLM
```

红色：

```text
[ 紧急停止 ]
```

## 7.4 控制开关

页面加载时：

```text
键盘控制 = 禁用
```

用户必须点击一次：

```text
[启用键盘控制]
```

然后 WASD+QE 生效。

这是为了避免：

```text
用户打开页面
正在打字
误触 W
机器人突然移动
```

页面刷新/重连后默认重新禁用。

---

# 8. 相机实时显示

## 8.1 ROS Worker 优先订阅 compressed

订阅：

```text
/camera/front/image_raw/compressed
```

如果消息本身就是 JPEG：

```text
不要重新 OpenCV encode
```

直接把 `msg.data` 原子写成：

```text
outputs/manual_web_demo/runtime/latest.jpg
```

写法：

```text
latest.tmp
→ fsync/close
→ os.replace(latest.jpg)
```

避免 Web 读半帧。

## 8.2 状态

ROS worker 同时写：

```text
outputs/manual_web_demo/runtime/camera_status.json
```

包括：

```json
{
  "received_at": 0.0,
  "ros_stamp": 0.0,
  "frame_id": "",
  "width": 1920,
  "height": 1080,
  "fresh": true
}
```

## 8.3 Web MJPEG

FastAPI：

```text
GET /api/camera.mjpeg
```

循环读取：

```text
latest.jpg
```

以：

```text
multipart/x-mixed-replace
```

返回浏览器。

推荐 UI 输出 FPS：

```text
8~12 FPS
```

没有必要把 15~28Hz 全部传到浏览器。

## 8.4 相机 stale

如果最新帧：

```text
age > 1.0s
```

UI：

```text
Camera STALE
```

画面保留最后一帧，但加红色：

```text
相机断流 / 画面非实时
```

同时：

```text
WASD+QE 自动禁用
STOP
```

不要在旧图上继续驾驶。

---

# 9. WASD+QE 键盘协议

前端使用：

```javascript
keydown
keyup
window.blur
visibilitychange
beforeunload
WebSocket close
```

## 9.1 按键映射

固定映射：

```text
W = forward       前进
S = backward      后退
A = strafe_left   向左直接横移
D = strafe_right  向右直接横移
Q = turn_left     原地/小半径左转
E = turn_right    原地/小半径右转

Space = STOP
Esc   = EMERGENCY STOP
```

这是本 Demo 的最终交互定义，不允许再把 A/D 解释成左转/右转。

UI 必须明确显示：

```text
W 前进
S 后退
A 左移
D 右移
Q 左转
E 右转
```

## 9.2 横移能力的实现要求

AI 必须首先读取真实：

```text
go2w_motion_interfaces
go2w_motion_control
现有 Action client/helper
```

确认当前 `/go2w/motion` 是否已经支持：

```text
strafe_left
strafe_right
lateral velocity / lateral displacement
```

### 情况 A：现有 Action 已支持横移

直接复用现有高层 Action primitive。

### 情况 B：现有 Action 暂未暴露横移 primitive

允许在现有：

```text
/home/brov/robot/unitree_go2w_control
```

高层 Sport/Action 控制层中补充：

```text
short strafe left
short strafe right
```

要求：

```text
仍通过 go2w_motion_control
仍由 /go2w/motion 或现有统一高层接口执行
仍受 arm / watchdog / STOP / emergency stop 管理
```

禁止为了横移直接新增：

```text
/lowcmd
LowCmd
关节控制
绕过 go2w_motion_control 的裸 SDK 无限速度循环
```

如果当前高层 Sport API/Action 在现有机器和固件上确实无法安全提供横移，则：

```text
代码和 UI 保留 A/D 映射
后端返回 BLOCKED: lateral_motion_not_supported
```

不得偷偷把 A/D 改回转向。

## 9.3 Q/E 转向

Q/E 使用现有短角度 turn primitive：

```text
Q = left turn
E = right turn
```

并继续受：

```text
rotation clearance
rotation observability
rotation lease
当前硬件安全 gate
```

约束。

# 10. “按一下”和“按住”的实现

绝对不要：

```text
keydown
→ 发一个无限时长速度
```

正确做成：

```text
短脉冲 + heartbeat + deadman
```

## 10.1 浏览器

维护：

```javascript
pressedKeys = Set()
```

每：

```text
100 ms
```

发送：

```json
{
  "type": "control_heartbeat",
  "seq": 105,
  "pressed": ["w"],
  "control_enabled": true
}
```

## 10.2 单击

例如：

```text
keydown W
keyup W
```

即使很快，也必须产生至少：

```text
1 个 forward pulse
```

因此 backend 不能只依赖 heartbeat。

`keydown` 立即发送：

```json
{
  "type": "key_down",
  "key": "w"
}
```

## 10.3 按住

只允许：

```text
一个 motion pulse in flight
```

完成后如果：

```text
W 仍处于 pressed
且 heartbeat fresh
```

再发送下一小步。

效果：

```text
W一直按
→ step
→ step
→ step
→ step
```

而不是：

```text
一个长时间不可控 goal
```

---

# 11. Deadman / 断连停车

这是本 Demo 必须实现的核心。

后端记录：

```text
last_control_heartbeat
```

如果：

```text
now - heartbeat > 300 ms
```

立即：

```text
STOP/cancel
pressed keys clear
control_enabled=false
```

前端发生：

```text
window.blur
tab hidden
WebSocket disconnected
browser refresh
browser close
```

都发：

```text
release_all
```

如果无法发送：

```text
后端 300ms watchdog
```

自己 STOP。

---

# 12. STOP 和 Emergency Stop

必须区分。

## 12.1 普通 STOP

触发：

```text
keyup
Space
控制关闭
切换方向
heartbeat timeout
```

优先：

```text
cancel current /go2w/motion goal
+
existing normal stop path
```

AI 必须检查现有 Action server 的真实 stop/cancel 语义。

不要凭空 invent message。

## 12.2 Emergency Stop

触发：

```text
Esc
红色 STOP 按钮
ROS worker fatal error
无法确认当前 motion 状态
```

调用：

```text
/go2w/emergency_stop
```

然后：

```text
disable keyboard
clear pressed keys
```

需要显式重新启用控制。

---

# 13. 多按键冲突

为了保持 Demo 简单：

```text
不做斜向合成速度
同一时刻只执行一种运动 primitive
```

冲突：

```text
W + S => STOP
A + D => STOP
Q + E => STOP
```

其它组合，例如：

```text
W + A
W + Q
A + Q
```

采用：

```text
last_pressed_key wins
```

切换 primitive 前必须：

```text
STOP/cancel current pulse
```

然后才能发送新方向小步。

UI 始终只显示一个：

```text
当前实际运动命令
```

# 14. ManualDriveController

新增：

```text
app/manual_web_demo/manual_drive_controller.py
```

状态：

```python
DISABLED
READY
MOVING
STOPPING
ESTOP
BLOCKED
ERROR
```

数据：

```python
@dataclass
class ManualDriveState:
    control_enabled: bool
    pressed_key: str | None
    command: str
    motion_in_flight: bool
    last_heartbeat_monotonic: float
    blocked_reason: str | None
    last_motion_result: dict | None
```

职责：

```text
键盘状态
heartbeat
single pulse queue
direction switching
STOP
estop
ROS worker IPC
```

不要把 LLM 放进这个类。

---

# 15. 每个按键 pulse

不要写死 Action 字段。

AI 必须先读取已有：

```text
run_autonomous_loop.py
go2w_motion_interfaces
go2w_motion_control
```

然后复用当前“短步” action helper。

推荐 Demo 配置语义：

```text
forward pulse:
  ~0.05–0.10m equivalent

backward pulse:
  ~0.05–0.10m equivalent

strafe-left / strafe-right pulse:
  ~0.03–0.08m equivalent

turn-left / turn-right pulse:
  ~5–10 deg
```

但最终以当前 Action interface 的：

```text
speed / duration / angle / distance
```

真实语义实现。

配置：

```text
MANUAL_DEMO_FORWARD_STEP
MANUAL_DEMO_BACKWARD_STEP
MANUAL_DEMO_STRAFE_STEP
MANUAL_DEMO_TURN_STEP_DEG
MANUAL_DEMO_REPEAT_INTERVAL_MS
```

不要散落 magic numbers。

---

# 16. 当前项目安全 gate 必须继续生效

这个 Demo 是手动控制，不是允许绕过当前项目物理状态。

运动提交前至少检查现有可用项：

```text
camera fresh
mode=1
error_code=0
motion Action available
arm service available
current motion not faulted
```

并尽量复用现有：

```text
motion_bounds
front clearance
rotation gate
rotation lease
odom/radius
```

## 16.1 W

前进必须检查：

```text
front clearance
```

## 16.2 A/D：左右横移

A/D 不走“转向”语义。

它们分别请求：

```text
A -> strafe_left
D -> strafe_right
```

AI 必须检查现有项目是否已经存在可用于横向短步的：

```text
lateral clearance
side clearance
left/right clearance
或等价安全证据
```

如果当前没有经过验证的横移安全门：

默认：

```text
MANUAL_DEMO_ALLOW_STRAFE=false
```

WebUI 仍显示 A/D，但按下时明确返回：

```text
左移暂不可用：lateral safety not validated
右移暂不可用：lateral safety not validated
```

如果现有/本轮可复用的安全证据明确支持左右横移，则把 A/D 接入该 gate 后再允许执行。

禁止使用：

```text
“Pandar 是360°所以横移肯定安全”
```

这种推断直接放行。

## 16.3 Q/E：左右转向

Q/E 走当前项目现有 rotation safety chain。

如果：

```text
rotation_clearance_valid=false
```

则：

```text
Q/E UI 可以显示
但后端必须 BLOCKED
```

不能为了 Demo 把 rotation gate 改 true。

## 16.4 S：后退

当前项目是否存在正式 rear clearance 必须由 AI 检查代码。

如果没有已验证 rear safety：

默认：

```text
MANUAL_DEMO_ALLOW_BACKWARD=false
```

UI S 显示：

```text
后退：未启用
```

代码可以完整支持 S，但不能默认绕过未验证 rear safety。

如果当前项目已有可复用、已验证的 backward gate，则接入。

## 16.5 Demo 目标

代码要“一步到位实现完整”。

但真机某个方向被安全 gate 阻断时，正确 UI 是：

```text
方向暂不可用：<reason>
```

不是关闭 gate。

---

# 17. Arm 时序

复用当前项目已修正的：

```text
gate before arm
```

顺序必须：

```text
收到 key intent
↓
检查 camera/status/safety
↓
生成 short pulse
↓
全部 gate PASS
↓
arm
↓
Action
```

不能：

```text
先 arm
再检查 safety
```

---

# 18. ROS Worker

新增：

```text
scripts/go2w/manual_web_demo_ros_worker.py
```

使用：

```text
/usr/bin/python3
```

职责：

```text
1. rclpy init
2. subscribe compressed RGB
3. subscribe sport mode/status
4. subscribe Demo 需要的 safety/odom status
5. ActionClient /go2w/motion
6. clients /go2w/arm /go2w/emergency_stop
7. latest.jpg atomic writer
8. JSONL IPC
9. internal motion watchdog
```

---

# 19. ROS Worker IPC

不要使用文件轮询传键盘命令。

使用：

```text
stdin/stdout JSON Lines
```

Web process 启动 worker：

```text
/usr/bin/python3 scripts/go2w/manual_web_demo_ros_worker.py
```

发送：

```json
{"type":"status"}
{"type":"pulse","direction":"forward"}
{"type":"stop"}
{"type":"estop"}
{"type":"shutdown"}
```

返回：

```json
{"type":"ready"}
{"type":"motion_started"}
{"type":"motion_finished"}
{"type":"blocked","reason":"..."}
{"type":"camera_status"}
{"type":"error","message":"..."}
```

### 重要

stdout 只能写协议 JSON。

日志全部：

```text
stderr
```

否则会破坏 parser。

---

# 20. ROS Worker 自己也必须有 watchdog

不能只信浏览器。

如果 worker：

```text
超过 500ms
没收到 Web process 的 control keepalive
且当前有 motion
```

执行 STOP。

这样有两层：

```text
Browser → Web watchdog
Web → ROS worker watchdog
```

---

# 21. Web 后端

新增：

```text
app/manual_web_demo/web_server.py
```

推荐 FastAPI routes：

```text
GET  /
GET  /api/status
GET  /api/objects
GET  /api/camera.mjpeg
POST /api/control/enable
POST /api/control/disable
POST /api/estop
WS   /ws/control
```

不需要数据库。

所有 runtime state 内存保存。

---

# 22. WebSocket control protocol

浏览器 → server：

```json
{"type":"hello"}
{"type":"enable_control"}
{"type":"key_down","key":"w"}
{"type":"key_up","key":"w"}
{"type":"heartbeat","pressed":["w"],"seq":10}
{"type":"release_all"}
{"type":"estop"}
```

server → browser：

```json
{
  "type":"state",
  "camera":"ok",
  "motion":"ready",
  "control_enabled":true,
  "active_key":"w",
  "command":"forward"
}
```

blocked：

```json
{
  "type":"motion_blocked",
  "direction":"strafe_left",
  "reason":"rotation_clearance_invalid"
}
```

---

# 23. SiliconFlow 场景主要物体识别

新增：

```text
app/manual_web_demo/scene_object_analyzer.py
```

只允许复用当前项目已经配置好的 SiliconFlow 视觉调用链。

AI 必须优先直接调用：

```text
app/detectors/siliconflow_vision_worker.py
```

或其内部已验证的公共 helper；如果该 worker 不适合被进程内复用，再使用：

```text
app/llm_clients/siliconflow_client.py
```

但配置必须继续来自现有：

```text
app/config.py
.env / .env.go2w
```

不得在 Demo 中重新实现：

```text
API key loading
SiliconFlow base URL
Authorization header
HTTP retry stack
模型账号配置
```

Demo 只负责：

```text
取最新相机帧
→ 调现有 SiliconFlow 视觉接口
→ 请求“当前场景主要物体”
→ 解析 JSON
→ 更新右侧表格
```

# 24. LLM Prompt

任务不是目标搜索。

Prompt 要求：

```text
只识别当前图像中清晰可见的主要物体。
不要推测画面外物体。
不要根据常识补充不存在的东西。
忽略非常小、模糊、无法确认的物体。
相同物体尽量合并。
返回严格 JSON。
```

推荐输出：

```json
{
  "objects": [
    {
      "name_zh": "椅子",
      "name_en": "chair",
      "count": 2,
      "position": "左侧和中间",
      "confidence": "high"
    },
    {
      "name_zh": "桌子",
      "name_en": "table",
      "count": 1,
      "position": "中间",
      "confidence": "high"
    }
  ],
  "scene_summary": "室内办公区域"
}
```

## 24.1 数量

`count` 只是视觉估计。

如果无法确认：

```text
count = null
```

UI 显示：

```text
—
```

不要逼模型猜数。

## 24.2 confidence

只允许：

```text
high
medium
low
```

UI 默认可隐藏 low confidence。

---

## 24.2 SiliconFlow 调用配置来源

所有视觉请求必须使用主项目当前已配置并验证过的 SiliconFlow 配置。

本 Demo 不新增模型选择下拉框，也不新增 API Key 输入框。默认直接使用主项目当前视觉模型。

# 25. LLM 调度

现有大模型单次调用可能数秒。

所以不能真的：

```text
每5秒强行启动一个新请求
```

否则会重叠。

正确：

```text
ANALYSIS_INTERVAL_SECONDS = 5

if previous inference running:
    skip this tick
else:
    latest frame snapshot
    start inference
```

推荐：

```text
单 worker
max concurrent inference = 1
```

UI 文案：

```text
约每5秒尝试识别；上一轮未完成时跳过
```

---

# 26. LLM 不影响相机/WASD

LLM 放独立：

```text
background task/thread
```

禁止：

```text
WebSocket handler
await LLM 10 seconds
```

也禁止：

```text
camera MJPEG loop
调用 LLM
```

正确：

```text
camera producer
motion controller
LLM analyzer
```

三条独立链。

---

# 27. LLM 帧快照

开始推理时：

```text
读取 latest.jpg 到 immutable bytes
```

不要把一个持续变化的文件句柄直接交给远程请求。

可选保存：

```text
outputs/manual_web_demo/analysis_frames/<timestamp>.jpg
```

但默认关闭。

---

# 28. ObjectTableState

新增 model：

```python
@dataclass
class SceneObject:
    name_zh: str
    name_en: str | None
    count: int | None
    position: str | None
    confidence: str

@dataclass
class SceneObjectState:
    objects: list[SceneObject]
    scene_summary: str | None
    frame_timestamp: float | None
    analysis_started_at: float | None
    analysis_finished_at: float | None
    model: str | None
    status: str
    error: str | None
```

状态：

```text
idle
running
ok
error
```

出错时：

```text
保留最后一次成功结果
+ UI 显示“识别暂时失败”
```

不要把表清空。

---

# 29. JSON 解析鲁棒性

模型可能返回 fenced JSON 或夹解释文字。

优先复用项目现有 JSON extraction helper。

如果没有适合的 helper，只新增一个很小的：

```text
extract first valid JSON object
```

解析失败：

```text
记录截断后的 raw response
保留 previous table
```

日志里不得出现 API Key。

---

# 30. Web 前端 JavaScript

`static/app.js` 只做：

```text
keyboard events
WebSocket
status rendering
object polling
control buttons
```

不引入：

```text
React
Vue
npm
webpack
```

页面打开即可用。

---

# 31. 浏览器键盘焦点

只有：

```text
control_enabled=true
且
用户不在 input/textarea/select
```

时 WASD+QE 才截获。

`event.preventDefault()` 仅对：

```text
WASDQE
Space
Esc
```

执行。

---

# 32. 触屏备用按钮

顺手加四个简单按钮：

```text
W
A S D
```

支持：

```text
pointerdown
pointerup
pointercancel
```

逻辑和键盘完全复用。

不要另写第二套控制。

---

# 33. UI 状态颜色

简单：

```text
绿 = ready
黄 = processing/limited
红 = stale/blocked/error
灰 = disabled
```

不要做复杂 dashboard。

---

# 34. Runtime 目录

新增：

```text
outputs/manual_web_demo/runtime/
```

只存：

```text
latest.jpg
camera_status.json
worker.pid
web.pid
```

分析可选：

```text
outputs/manual_web_demo/logs/
outputs/manual_web_demo/analysis_frames/
```

默认不要无限保存 camera frames。

```text
MANUAL_DEMO_SAVE_ANALYSIS_FRAMES=false
```

---

# 35. 配置

新增到 `.env.example`，尽量不污染主配置：

```text
MANUAL_DEMO_HOST=127.0.0.1
MANUAL_DEMO_PORT=8765

MANUAL_DEMO_CAMERA_MAX_FPS=10
MANUAL_DEMO_CAMERA_STALE_SECONDS=1.0

MANUAL_DEMO_LLM_ENABLED=true
MANUAL_DEMO_LLM_INTERVAL_SECONDS=5
MANUAL_DEMO_LLM_HIDE_LOW_CONFIDENCE=true

# SiliconFlow 的 API key / endpoint / model 不在这里重复配置，
# 直接读取 robot_scene_demo 当前已经生效的现有配置。

MANUAL_DEMO_CONTROL_ENABLED=false

MANUAL_DEMO_DEADMAN_MS=300
MANUAL_DEMO_ROS_WORKER_DEADMAN_MS=500
MANUAL_DEMO_REPEAT_INTERVAL_MS=250

MANUAL_DEMO_ALLOW_FORWARD=true
MANUAL_DEMO_ALLOW_BACKWARD=false
MANUAL_DEMO_ALLOW_STRAFE=false
MANUAL_DEMO_ALLOW_TURN=true

MANUAL_DEMO_TURN_STEP_DEG=8
MANUAL_DEMO_SAVE_ANALYSIS_FRAMES=false
```

forward/backward pulse 的实际长度/时长：

> 从当前 `go2w_motion_interfaces` 和已有短步 helper 中读取真实语义后配置，不要在不知道 Action schema 的情况下猜变量名。

---

# 36. 启动脚本

新增：

```text
scripts/go2w/start_manual_web_demo.sh
```

一条命令：

```bash
bash scripts/go2w/start_manual_web_demo.sh
```

默认：

```text
相机 + LLM
运动键盘 disabled
```

显式允许运动：

```bash
bash scripts/go2w/start_manual_web_demo.sh --enable-motion
```

## 36.1 启动流程

```text
1. cd repo
2. 检查 enp6s0 / robot network
3. 检查 camera bridge
4. camera bridge 不在时启动/提示 start_live_perception
5. 检查 compressed camera topic
6. motion requested 时检查：
       /go2w/motion
       /go2w/arm
       /go2w/emergency_stop
7. 启动 ROS worker (/usr/bin/python3)
8. 启动 FastAPI (Conda)
9. 等待 /api/status ready
10. xdg-open http://127.0.0.1:8765
```

不要启动：

```text
Nav2
UniGoal
Pandar driver
Point-LIO
```

除非 Demo 真正依赖。

---

# 37. Motion stack 缺失时

如果：

```text
/go2w/motion 不存在
```

页面仍然打开：

```text
Camera = READY
LLM = READY
Motion = OFFLINE
```

WASD+QE 不生效。

不要让整个 WebUI 起不来。

---

# 38. 相机缺失时

如果 camera topic 不存在：

```text
等待相机...
```

并且：

```text
control disabled
LLM paused
```

相机恢复后自动恢复 camera/LLM。

运动控制需用户重新点击启用。

---

# 39. SiliconFlow 调用异常时

由于 `robot_scene_demo` 已经配置好 SiliconFlow，本 Demo 不增加“重新配置 API Key”的流程。

启动时只做只读检查：

```text
能否从现有 app/config.py / 环境加载当前 SiliconFlow 配置
```

如果运行时调用失败，例如：

```text
网络不可用
服务超时
当前现有配置失效
模型服务暂时异常
```

WebUI 仍保持：

```text
相机：正常
运动：按真实状态
视觉识别：暂时不可用
```

右表保留最后一次成功结果，并显示：

```text
SiliconFlow 识别暂时失败：<简短错误>
```

不得：

```text
要求用户在 Demo 页面重新填写 API Key
新建第二套 .env
把 key 写入日志
```

# 40. 停止脚本

新增：

```text
scripts/go2w/stop_manual_web_demo.sh
```

流程：

```text
1. Web server shutdown
2. ROS worker STOP
3. 如有 active goal cancel
4. emergency/normal stop according to state
5. 不 kill 非本 Demo 拥有的 camera bridge
6. 不 kill 用户其它 ROS nodes
```

---

# 41. FastAPI 依赖

AI 先检查当前环境：

```text
fastapi
uvicorn
```

如果没有，只添加：

```text
fastapi
uvicorn
```

不要引入大型前端依赖。

---

# 42. 测试：键盘状态机

新增：

```text
tests/test_manual_drive_controller.py
```

测试：

```text
keydown W
→ one forward pulse

W held + heartbeat
→ repeated pulses sequentially

keyup W
→ no more pulses

W then A
→ stop/cancel W before strafe-left

A then Q
→ stop/cancel strafe-left before turn-left

W+S
→ stop

A+D
→ stop

Q+E
→ stop
```

---

# 43. 测试：Deadman

`tests/test_manual_drive_deadman.py`

必须：

```text
heartbeat fresh
→ continue

heartbeat >300ms
→ stop
→ control disabled

websocket disconnect
→ stop

browser release_all
→ stop

ROS worker keepalive timeout
→ stop
```

---

# 44. 测试：不允许并发 motion goal

断言：

```text
active_goal_count <= 1
```

即使 browser 100ms heartbeat 很快。

不能：

```text
W按住1秒
→ 堆10个 Action goal
```

---

# 45. 测试：LLM scheduler

`tests/test_manual_scene_object_scheduler.py`

测试：

```text
interval=5s
first request running 10s
→ 期间没有第二请求

request done
→ 下一周期再开始

camera stale
→ skip LLM
```

---

# 46. 测试：LLM parser

`tests/test_manual_scene_object_parser.py`

覆盖：

```text
valid JSON
markdown fenced JSON
missing count
unknown confidence
duplicate objects
malformed response
empty response
```

简单规范化 duplicate 即可，不引入复杂 ontology。

---

# 47. 测试：API

`tests/test_manual_web_demo_api.py`

检查：

```text
GET /
GET /api/status
GET /api/objects
POST enable/disable
POST estop
WebSocket connect
```

不需要 ROS 真机。

Mock `ros_worker_client`。

---

# 48. ROS worker 协议测试

`tests/test_manual_ros_worker_protocol.py`

测试 JSONL：

```text
valid message
unknown type
malformed JSON
worker error
blocked response
```

---

# 49. 安全回归

必须证明：

```text
Demo 没有 import /lowcmd
Demo 没有 Unitree joint control
Demo 没有直接发布 raw SDK velocity
```

WASD 只能走：

```text
existing /go2w/motion
```

---

# 50. LLM object table 验收

先静态图/mock：

```text
办公室图
```

期望：

```text
camera/mock frame visible
object table appears
LLM failure doesn't break UI
```

再真机：

```text
相机对着桌椅
等待一轮
```

确认：

```text
table/chair 等出现在列表
```

不要求 bbox。

---

# 51. Camera 验收

启动：

```text
start_live_perception
start_manual_web_demo
```

要求：

```text
Web画面连续
FPS约8~12
无明显撕裂
Frame stale 能报警
断相机后 control 自动 disable
```

---

# 52. Motion 验收顺序

只有当前项目安全 gate 允许时才跑。

## Stage M0

```text
WebUI启动
Control disabled
按W/A/S/D
机器人不动
```

PASS。

## Stage M1

Enable control，按一下 W：

```text
只执行1个小 forward pulse
然后 stop
```

PASS。

## Stage M2

按住 W 1秒：

```text
多个连续小步
无 goal 堆积
松手立即停止继续发步
```

PASS。

## Stage M3

按住 W，然后切走浏览器窗口：

```text
<= deadman window STOP
control disabled
```

PASS。

## Stage M4

A/D 横移：

只有 lateral motion primitive 和对应安全 gate 真实允许时才测试。

测试：

```text
轻按 A → 一个左横移小步
轻按 D → 一个右横移小步
按住 A/D → 连续横移小步
松开 → STOP
```

如果 lateral safety 尚未验证，正确结果：

```text
UI显示 blocked
robot 不横移
```

## Stage M5

Q/E 转向：

只有当前 rotation gate 真实允许才测试。

否则正确结果：

```text
UI显示 blocked
robot 不转
```

## Stage M6

S：

只有 backward safety 明确通过后测试。

---

# 53. 操作提示

页面底部固定显示：

```text
W 前进
S 后退
A 左移
D 右移
Q 左转
E 右转
Space 停止
Esc 急停
```

如果某方向当前 blocked：

```text
S 后退（不可用）
```

---

# 54. 性能原则

```text
相机 UI：约 10 FPS
键盘 heartbeat：约 10 Hz
LLM：单 worker，约每5秒尝试一次
```

三者资源完全解耦。

---

# 55. 隐私和日志

默认不要存：

```text
完整相机录像
每一帧
所有 LLM 上传图
```

只保留：

```text
runtime latest frame
必要日志
```

分析帧保存默认关闭。

---

# 56. README 增加一小节

不要把 Demo 混成主项目能力。

新增：

```markdown
### Go2-W Manual WASD Web Demo

启动：
bash scripts/go2w/start_manual_web_demo.sh

允许运动：
bash scripts/go2w/start_manual_web_demo.sh --enable-motion
```

说明：

```text
独立手动 Demo
不使用 UniGoal/Nav2
相机来自正式 camera bridge
W/S/A/D/Q/E 通过现有高层运动控制链；W/S 前后、A/D 横移、Q/E 转向
直接复用项目现有 SiliconFlow 视觉配置，异步列主要物体
```

---

# 57. 新文档

`docs/GO2W_MANUAL_WASD_WEB_DEMO.md`

必须包含：

```text
功能
架构
安装
启动
安全
WASD+QE映射
LLM间隔
故障排查
测试
停止
```

---

# 58. 给 AI 的推荐实现顺序

严格按：

```text
1. 审计现有 camera + motion + 已配置 SiliconFlow 视觉调用链
2. 建 manual_web_demo package
3. 写 ROS worker protocol
4. 相机 latest.jpg
5. MJPEG Web
6. 页面完成
7. keyboard websocket
8. ManualDriveController
9. STOP/deadman
10. 接 /go2w/motion
11. 接 safety gate
12. scene LLM analyzer
13. object table
14. launch/stop scripts
15. tests
16. docs
17. readonly UI验收
18. 最后才真机 motion
```

不要先做运动后补 watchdog。

---

# 59. AI 最终完成定义

## Web

- [ ] 一条命令能打开 WebUI。
- [ ] 页面简洁。
- [ ] 相机持续刷新。
- [ ] 表格自动刷新。
- [ ] Camera/Motion/LLM 状态可见。

## WASD+QE

- [ ] W 单击 = 一个小前进 pulse。
- [ ] W 按住 = 连续前进小 pulse。
- [ ] S = 后退，安全 gate 决定是否可用。
- [ ] A = 左横移，不允许解释成左转。
- [ ] D = 右横移，不允许解释成右转。
- [ ] Q = 左转。
- [ ] E = 右转。
- [ ] A/D 横移和 Q/E 转向都支持按一下一个小 pulse、按住连续 pulse。
- [ ] 任意方向松开 = 停止继续 pulse。
- [ ] 不支持危险的 action goal 堆积。
- [ ] Space STOP。
- [ ] Esc Emergency Stop。
- [ ] 浏览器失焦 STOP。
- [ ] WebSocket 断线 STOP。
- [ ] Web heartbeat timeout STOP。
- [ ] ROS worker timeout STOP。

## Camera

- [ ] 使用现有 `/camera/front/image_raw/compressed`。
- [ ] 不重新打开 VideoHub。
- [ ] 使用 atomic latest.jpg。
- [ ] stale 自动禁用 control。

## SiliconFlow 视觉识别

- [ ] 只复用 robot_scene_demo 已经配置好的 SiliconFlow 客户端/worker。
- [ ] 使用现有 app/config.py + 当前 .env/.env.go2w 配置。
- [ ] 不新增第二套 API Key / endpoint / model 账号配置。
- [ ] 最大并发=1。
- [ ] LLM慢不会卡 WASD。
- [ ] LLM慢不会卡相机。
- [ ] 输出严格解析成 object table。
- [ ] 失败保留上次成功结果。

## ROS

- [ ] rclpy worker 使用 `/usr/bin/python3`。
- [ ] Web/LLM 使用 Conda。
- [ ] 只调用现有 `/go2w/motion`、arm、estop。
- [ ] 不出现 `/lowcmd`。
- [ ] gate 在 arm 前执行。

## 安全

- [ ] A/D 是横移；没有 validated lateral safety 时必须 blocked。
- [ ] Q/E 是转向；当前 rotation gate blocked 时 Q/E 不绕过。
- [ ] 后退无正式 safety 时默认 disabled。
- [ ] camera stale 时不允许继续移动。
- [ ] motion service offline 时页面仍能看相机。
- [ ] LLM offline 时页面仍能驾驶（前提是真实 motion gate 允许）。
- [ ] control 默认 disabled。
- [ ] 页面刷新后 control 默认 disabled。

## 测试

- [ ] keyboard tests PASS。
- [ ] deadman tests PASS。
- [ ] one-action-in-flight tests PASS。
- [ ] LLM scheduler/parser PASS。
- [ ] API tests PASS。
- [ ] ROS protocol tests PASS。
- [ ] 现有相关 Go2-W 回归无新增失败。
- [ ] `git diff --check` PASS。

---

# 60. 最终目标架构

```text
Go2-W Camera Bridge
        │
        ↓
compressed RGB
        │
        ├──────────────→ Browser Live Video
        │
        └──每隔数秒──→ SiliconFlow VLM
                             │
                             ↓
                       Main Objects
                             │
                             ↓
                         Web Table


Browser WASD+QE
     │
     ↓
WebSocket
     │
     ↓
ManualDriveController
     │
     ↓
Safety / Deadman
     │
     ↓
ROS Worker (/usr/bin/python3)
     │
     ↓
existing /go2w/motion
```

---

# 61. 一句话任务定义

> **在当前 `robot_scene_demo` 中新增一个完全独立的 Go2-W 手动 Web Demo：通过现有 ROS2 相机桥实时显示内置 RGB，通过浏览器 WASD+QE + WebSocket + deadman 控制现有高层运动链（W/S 前后、A/D 左右横移、Q/E 左右转向），并在后台以非阻塞方式直接调用 `robot_scene_demo` 已经配置好的 SiliconFlow 视觉能力识别当前画面中清晰可见的主要物体，在相机右侧以简单表格持续刷新；不接 UniGoal/Nav2/3D，不重复已有相机和运动底层，不允许 LLM 阻塞控制，不允许浏览器断连后继续移动。**
