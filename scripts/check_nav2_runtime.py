#!/usr/bin/env python3
"""Check ROS/Nav2 imports and graph resources without importing application deps."""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--json", dest="json_path")
    parser.add_argument("--namespace", default=os.getenv("NAV2_NAMESPACE",""))
    args=parser.parse_args()
    checks=[]
    def check(name, command, required=True):
        result=subprocess.run(command, shell=True, text=True, capture_output=True)
        checks.append({"name":name,"ok":result.returncode==0,"message":(result.stdout or result.stderr).strip()[-500:],"required":required})
    checks.append({"name":"ros_distro","ok":os.getenv("ROS_DISTRO")=="humble","message":os.getenv("ROS_DISTRO","未设置"),"required":True})
    try: import rclpy; checks.append({"name":"rclpy_import","ok":True,"message":"可导入","required":True})
    except ImportError as exc: checks.append({"name":"rclpy_import","ok":False,"message":str(exc),"required":True})
    try: from nav2_simple_commander.robot_navigator import BasicNavigator; checks.append({"name":"commander_import","ok":True,"message":"可导入","required":True})
    except ImportError as exc: checks.append({"name":"commander_import","ok":False,"message":str(exc),"required":True})
    prefix=args.namespace.rstrip("/")
    check("navigate_to_pose_action",f"ros2 action info {prefix}/navigate_to_pose",True)
    check("compute_path_to_pose_action",f"ros2 action info {prefix}/compute_path_to_pose",True)
    check("map_to_base_tf","ros2 run tf2_ros tf2_echo map base_link --once",True)
    check("odom_topic","ros2 topic info /odom",False); check("map_topic","ros2 topic info /map",True)
    check("cmd_vel_topic","ros2 topic info /cmd_vel",False)
    check("collision_monitor","ros2 node info /collision_monitor",True)
    required_bad=[c["name"] for c in checks if c["required"] and not c["ok"]]
    payload={"healthy_for_plan":not any(n in required_bad for n in ("ros_distro","rclpy_import","commander_import","compute_path_to_pose_action","map_to_base_tf","map_topic")),
             "healthy_for_execute":not required_bad,"checks":checks,"blocking_errors":required_bad,
             "warnings":[c["name"] for c in checks if not c["required"] and not c["ok"]]}
    text=json.dumps(payload,ensure_ascii=False,indent=2); print(text)
    if args.json_path:
        target=Path(args.json_path); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(text+"\n",encoding="utf-8")
    return 0 if payload["healthy_for_plan"] else 2
if __name__=="__main__": raise SystemExit(main())
