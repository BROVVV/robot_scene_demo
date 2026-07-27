#!/usr/bin/env python3
"""Check ROS/Nav2 imports and graph resources without importing application deps."""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--json", dest="json_path")
    parser.add_argument("--namespace", default=os.getenv("NAV2_NAMESPACE",""))
    parser.add_argument("--map-frame", default=os.getenv("NAV2_MAP_FRAME","map"))
    parser.add_argument("--base-frame", default=os.getenv("NAV2_BASE_FRAME","base_link"))
    args=parser.parse_args()
    checks=[]
    def check(name, command, required=True, timeout=8, success=None):
        try:
            result=subprocess.run(command, text=True, capture_output=True, timeout=timeout)
            message=(result.stdout or result.stderr).strip()[-500:]
            ok=result.returncode==0 and (success(message) if success else True)
            checks.append({"name":name,"ok":ok,"message":message,"required":required})
        except subprocess.TimeoutExpired:
            checks.append({"name":name,"ok":False,"message":f"{timeout} 秒内没有响应","required":required})
    def check_tf():
        try:
            import rclpy
            from rclpy.duration import Duration
            from rclpy.time import Time
            from tf2_ros import Buffer, TransformListener
            rclpy.init(args=None)
            node=rclpy.create_node("robot_scene_nav2_health_check")
            buffer=Buffer(); listener=TransformListener(buffer,node)
            deadline=__import__("time").monotonic()+5
            ok=False
            while __import__("time").monotonic()<deadline:
                rclpy.spin_once(node,timeout_sec=.2)
                if buffer.can_transform(args.map_frame,args.base_frame,Time(),Duration(seconds=.1)):
                    ok=True; break
            node.destroy_node(); rclpy.shutdown()
            checks.append({"name":"map_to_base_tf","ok":ok,
                "message":f"{args.map_frame} → {args.base_frame} "+("可用" if ok else "5 秒内不可用"),"required":True})
        except Exception as exc:
            checks.append({"name":"map_to_base_tf","ok":False,"message":str(exc)[-500:],"required":True})
    checks.append({"name":"ros_distro","ok":os.getenv("ROS_DISTRO")=="humble","message":os.getenv("ROS_DISTRO","未设置"),"required":True})
    try: import rclpy; checks.append({"name":"rclpy_import","ok":True,"message":"可导入","required":True})
    except ImportError as exc: checks.append({"name":"rclpy_import","ok":False,"message":str(exc),"required":True})
    try: from nav2_simple_commander.robot_navigator import BasicNavigator; checks.append({"name":"commander_import","ok":True,"message":"可导入","required":True})
    except ImportError as exc: checks.append({"name":"commander_import","ok":False,"message":str(exc),"required":True})
    prefix=args.namespace.rstrip("/")
    has_server=lambda message: "Action servers: 0" not in message
    check("navigate_to_pose_action",["ros2","action","info",f"{prefix}/navigate_to_pose"],True,success=has_server)
    check("compute_path_to_pose_action",["ros2","action","info",f"{prefix}/compute_path_to_pose"],True,success=has_server)
    check_tf()
    check("odom_topic",["ros2","topic","info","/odom"],False)
    check("map_topic",["ros2","topic","info","/map"],True)
    check("cmd_vel_topic",["ros2","topic","info","/cmd_vel"],False)
    check("collision_monitor",["ros2","node","info","/collision_monitor"],True)
    required_bad=[c["name"] for c in checks if c["required"] and not c["ok"]]
    payload={"healthy_for_plan":not any(n in required_bad for n in ("ros_distro","rclpy_import","commander_import","compute_path_to_pose_action","map_to_base_tf","map_topic")),
             "healthy_for_execute":not required_bad,"checks":checks,"blocking_errors":required_bad,
             "warnings":[c["name"] for c in checks if not c["required"] and not c["ok"]]}
    text=json.dumps(payload,ensure_ascii=False,indent=2); print(text)
    if args.json_path:
        target=Path(args.json_path); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(text+"\n",encoding="utf-8")
    return 0 if payload["healthy_for_plan"] else 2
if __name__=="__main__": raise SystemExit(main())
