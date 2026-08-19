#!/usr/bin/env python3
"""Acquire and renew a Unitree Sport lease until terminated."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time

from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import (
    MotionSwitcherClient,
)
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.sport.sport_client import SportClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interface", required=True)
    parser.add_argument("--ready-file")
    parser.add_argument("--ros-status", action="store_true")
    args = parser.parse_args()

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    ChannelFactoryInitialize(0, args.interface)
    client = SportClient(enableLease=True)
    client.SetTimeout(5.0)
    client.Init()
    deadline = time.monotonic() + 5.0
    while client.GetLeaseId() == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    lease_id = int(client.GetLeaseId())
    if lease_id == 0:
        print('{"event":"lease_error","message":"acquisition timeout"}', flush=True)
        return 1

    if args.ready_file:
        temporary_path = f"{args.ready_file}.tmp.{os.getpid()}"
        with open(temporary_path, "w", encoding="ascii") as output:
            output.write(f"{lease_id}\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, args.ready_file)
        os.chmod(args.ready_file, 0o600)
    print(
        json.dumps({"event": "lease_ready", "lease_id": lease_id}), flush=True
    )

    ros_node = None
    alive_publisher = None
    id_publisher = None
    name_publisher = None
    form_publisher = None
    if args.ros_status:
        import rclpy
        from rclpy.signals import SignalHandlerOptions
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile
        from std_msgs.msg import Bool, String, UInt64

        rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
        ros_node = rclpy.create_node("go2w_sport_lease_holder")
        latched_qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        id_publisher = ros_node.create_publisher(
            UInt64, "/go2w/sport_lease/id", latched_qos
        )
        alive_publisher = ros_node.create_publisher(
            Bool, "/go2w/sport_lease/alive", 10
        )
        name_publisher = ros_node.create_publisher(
            String, "/go2w/motion_mode/name", latched_qos
        )
        form_publisher = ros_node.create_publisher(
            String, "/go2w/motion_mode/form", latched_qos
        )

    switcher = MotionSwitcherClient()
    switcher.SetTimeout(1.0)
    switcher.Init()
    motion_name = ""
    robot_form = ""
    next_mode_check = 0.0
    next_status_publish = 0.0
    while not stop_event.wait(0.05):
        if ros_node is None:
            continue
        import rclpy
        from std_msgs.msg import Bool, String, UInt64

        rclpy.spin_once(ros_node, timeout_sec=0.0)
        now = time.monotonic()
        if now < next_status_publish:
            continue
        next_status_publish = now + 0.5
        current_id = int(client.GetLeaseId())
        id_msg = UInt64()
        id_msg.data = max(0, current_id)
        id_publisher.publish(id_msg)
        alive_msg = Bool()
        alive_msg.data = current_id != 0
        alive_publisher.publish(alive_msg)
        if now >= next_mode_check:
            raw_mode = switcher.CheckMode()
            if (
                isinstance(raw_mode, tuple)
                and len(raw_mode) >= 2
                and raw_mode[0] == 0
                and isinstance(raw_mode[1], dict)
            ):
                motion_name = str(raw_mode[1].get("name", ""))
                robot_form = str(raw_mode[1].get("form", ""))
            else:
                motion_name = ""
                robot_form = ""
            next_mode_check = now + 1.0
        name_msg = String()
        name_msg.data = motion_name
        name_publisher.publish(name_msg)
        form_msg = String()
        form_msg.data = robot_form
        form_publisher.publish(form_msg)

    for attempt in range(1, 4):
        try:
            raw = client.StopMove()
            print(
                json.dumps(
                    {
                        "event": "lease_holder_stop",
                        "attempt": attempt,
                        "raw_return_repr": repr(raw),
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "lease_holder_stop_error",
                        "attempt": attempt,
                        "message": str(exc),
                    }
                ),
                flush=True,
            )
        time.sleep(0.1)
    if ros_node is not None:
        from std_msgs.msg import Bool

        false_message = Bool()
        false_message.data = False
        for _ in range(3):
            alive_publisher.publish(false_message)
            time.sleep(0.02)
        ros_node.destroy_node()
        import rclpy

        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
