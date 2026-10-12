from __future__ import annotations

import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import Bool, String

from .arbiter_core import CommandSample, Twist2D, select_command


class ControlArbiter(Node):
    def __init__(self) -> None:
        super().__init__("go2w_control_arbiter")
        self.declare_parameter("command_timeout_sec", 0.3)
        self._commands = {}
        self._emergency = False
        # `software_arm` / `remote_override` 从**话题**读，不从参数读。
        # 这两个话题由 `motion_network_proxy` 按动作服务器的**真实**状态发布
        # （`software_arm` 是那条 60 s 空闲武装租约的重建值），与
        # `go2w_cmd_vel_bridge` 消费的是同一组；`remote_override` 的唯一断言行在
        # 代理侧（`--remote-override-cleared`），不在这里再设第二个开关。
        #
        # 原先这里是 `software_control_armed` / `remote_override_verified` 两个参数，
        # 而**全仓没有任何启动器设置它们**（`start_nav2_autonomous_search.sh:204` 是裸命令
        # `ros2 run ... arbiter`，不带 `-p`；另有一份没人读的 `configs/go2w/control.yaml`）。
        # 于是仲裁器**无条件**拦掉所有运动 —— 那是一道没有输入的死门，不是一道收紧的门。
        # 初值保持拦住，所以"还没收到读数"与"读数说不许"同解。
        self._software_arm = False
        self._remote_override = True
        self._selected_pub = self.create_publisher(Twist, "/go2w/cmd_vel_selected", 10)
        self._source_pub = self.create_publisher(String, "/go2w/control_source", 10)
        self._status_pub = self.create_publisher(String, "/go2w/control_status", 10)
        for topic, source in (
            ("/go2w/manual_cmd_vel", "manual"),
            ("/go2w/nav2_cmd_vel", "nav2"),
            ("/go2w/search_cmd_vel", "search"),
        ):
            self.create_subscription(
                Twist, topic, lambda message, source=source: self._command(source, message), 10
            )
        for topic, attribute in (
            ("/go2w/software_arm", "_software_arm"),
            ("/go2w/remote_override", "_remote_override"),
        ):
            self.create_subscription(
                Bool,
                topic,
                lambda message, attribute=attribute: setattr(
                    self, attribute, bool(message.data)
                ),
                10,
            )
        # 急停**保持锁存**（`or`），这是刻意的：它是"一旦为真就要求人工介入"的信号，
        # 改成电平会在信号抖动时悄悄恢复放行。代价是没有复位路径，只能重启进程，
        # 所以要保证上游不谎报 —— 代理首发 fail-closed 那个缺陷就是这么把仲裁器
        # 永久卡死的（已修，见 `scripts/go2w/motion_network_proxy.py`）。
        self.create_subscription(Bool, "/go2w/emergency_stop", self._emergency_callback, 10)
        self.create_timer(0.02, self._select)

    def _command(self, source: str, message: Twist) -> None:
        self._commands[source] = CommandSample(
            Twist2D(message.linear.x, message.linear.y, message.angular.z), time.monotonic()
        )

    def _emergency_callback(self, message: Bool) -> None:
        self._emergency = self._emergency or bool(message.data)

    def _select(self) -> None:
        selected = select_command(
            self._commands,
            now=time.monotonic(),
            timeout=float(self.get_parameter("command_timeout_sec").value),
            software_armed=self._software_arm,
            emergency_stop=self._emergency,
            remote_override=self._remote_override,
        )
        message = Twist()
        message.linear.x = selected.command.linear_x
        message.linear.y = 0.0
        message.angular.z = selected.command.angular_z
        self._selected_pub.publish(message)
        self._source_pub.publish(String(data=selected.source))
        self._status_pub.publish(String(data=selected.blocked_reason or "selected"))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ControlArbiter()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
