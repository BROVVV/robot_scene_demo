#!/usr/bin/env python3
"""Autonomous small-range motion loop for the Go2-W (operator-authorized).

The robot arms itself, executes a configured pattern of forward steps and
relative turns through the audited /go2w/motion action server, verifies every
step with wheel odometry and the front-clearance gate, and finishes with a
triple STOP plus disarm. No user commands are required while it runs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import cv2
except Exception:  # pragma: no cover - recording is optional
    cv2 = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.live_robot.search_state_machine import SensorSnapshot
from app.live_robot.step_search_runner import (
    Detection,
    StepSearchConfig,
    StepSearchRunner,
    VerificationResult,
)

import rclpy
from go2w_motion_interfaces.action import MotionCommand
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile
from std_msgs.msg import Float32
from std_srvs.srv import SetBool, Trigger
from unitree_go.msg import LowState, SportModeState


DEFAULT_PATTERN = ["f", "l20", "f", "r20", "f", "l20", "f", "r20", "f"]

PROMPT_MAP = {
    "手机": "phone. cellphone. mobile phone. smartphone",
    "箱子": "cardboard box. carton box",
    "瓶子": "plastic bottle. water bottle",
    "杯子": "cup. mug. glass",
    "书": "book. textbook",
    "人": "person. human",
    "书包": "gray backpack. grey backpack. rucksack",
    "灰色书包": "gray backpack. grey backpack. rucksack",
}


class BundleVideoRecorder:
    """Record the live camera stream with a locked target overlay.

    Runs as its own ROS 2 node on a dedicated executor thread so the camera
    feed is captured continuously while the main runner blocks on detection
    subprocesses. The CSRT tracker keeps the detection box locked onto the
    target between detector updates.
    """

    def __init__(self, output_video: str,
                 fps: float = 10.0, scale: float = 0.5,
                 camera_topic: str = "/camera/front/image_raw") -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is unavailable; video recording disabled")
        self._output = Path(output_video)
        self._fps = fps
        self._scale = scale
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = None
        self._node = None
        self._writer: cv2.VideoWriter | None = None
        self._tracker = None
        self._pending: tuple[str, float, tuple[float, float, float, float]] | None = None
        self._locked_label = ""
        self._locked_score = 0.0
        self._tracker_ok = False
        self._command_text = ""
        self._cjk_font_path = self._find_cjk_font()
        self._frames_written = 0
        self._last_write_time = 0.0
        self._camera_topic = camera_topic
        self._output.parent.mkdir(parents=True, exist_ok=True)
        self._log_path = self._output.with_suffix(".jsonl")
        self._log = open(self._log_path, "a", encoding="utf-8")

    def start(self) -> None:
        if self._thread is not None:
            return
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image
        self._node = rclpy.create_node("go2w_video_recorder")
        self._node.create_subscription(
            Image, self._camera_topic, self._on_image,
            qos_profile_sensor_data,
        )
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._thread = threading.Thread(
            target=self._executor.spin, daemon=True
        )
        self._thread.start()

    def set_detection(self, label: str, score: float,
                      bbox_xyxy: tuple[float, float, float, float]) -> None:
        self._pending = (label, score, tuple(float(v) for v in bbox_xyxy))

    def clear_detection(self) -> None:
        self._pending = None
        self._tracker = None
        self._locked_label = ""
        self._locked_score = 0.0
        self._tracker_ok = False

    def set_command(self, text: str) -> None:
        self._command_text = str(text)

    @staticmethod
    def _find_cjk_font() -> str:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ]
        for path in candidates:
            if Path(path).is_file():
                return path
        return ""

    def _draw_cjk_text(
        self,
        frame,
        text: str,
        org: tuple[int, int],
        size: int,
        color: tuple[int, int, int],
        background: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Draw text (including CJK) with PIL when a CJK font is available.

        cv2.putText cannot render CJK and produces '????'; this helper falls
        back to ASCII-only text when no CJK font is installed.
        """
        if not text:
            return
        try:
            import numpy as np
            from PIL import Image, ImageDraw, ImageFont

            if self._cjk_font_path:
                font = ImageFont.truetype(self._cjk_font_path, size)
                display_text = text
            else:
                font = ImageFont.load_default()
                display_text = text.encode("ascii", "ignore").decode("ascii")
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb_frame)
            draw = ImageDraw.Draw(image)
            if background is not None:
                left, top = org
                bbox = draw.textbbox((0, 0), display_text, font=font)
                right = left + (bbox[2] - bbox[0])
                bottom = top + size + 6
                draw.rectangle(
                    (left - 4, top - 2, right + 4, bottom),
                    fill=background[:3] + (int(background[3]),)
                    if len(background) == 4 else background,
                )
            draw.text((org[0], org[1]), display_text, font=font,
                      fill=color)
            frame[:] = cv2.cvtColor(
                np.asarray(image), cv2.COLOR_RGB2BGR
            )
        except Exception:
            # Keep the recorder alive if text rendering fails for any reason.
            safe = text.encode("ascii", "ignore").decode("ascii")
            cv2.putText(frame, safe, org, cv2.FONT_HERSHEY_SIMPLEX,
                        max(0.5, size / 32.0), color, 2)

    def _on_image(self, message) -> None:
        now = time.monotonic()
        if self._frames_written > 0:
            interval = 1.0 / max(self._fps, 1.0)
            if now - self._last_write_time < interval:
                return
        import numpy as np
        frame = np.frombuffer(
            bytes(message.data), dtype=np.uint8
        ).reshape((message.height, message.width, 3))
        frame = frame.copy()
        if self._scale != 1.0:
            width = int(frame.shape[1] * self._scale)
            height = int(frame.shape[0] * self._scale)
            frame = cv2.resize(frame, (width, height),
                               interpolation=cv2.INTER_AREA)
        if self._writer is None:
            height, width = frame.shape[:2]
            for codec in ("avc1", "mp4v"):
                fourcc = cv2.VideoWriter_fourcc(*codec)
                writer = cv2.VideoWriter(
                    str(self._output), fourcc, self._fps, (width, height)
                )
                if writer.isOpened():
                    self._writer = writer
                    break
                writer.release()
            if self._writer is None:
                raise RuntimeError("no usable video codec (avc1/mp4v)")
        self._draw_overlay(frame, frame.shape[1], frame.shape[0])
        self._writer.write(frame)
        self._frames_written += 1
        self._last_write_time = now
        self._log.write(
            json.dumps({
                "stamp_ns": int(message.header.stamp.sec) * 1000000000
                            + int(message.header.stamp.nanosec),
                "frames_written": self._frames_written,
                "locked": self._tracker_ok,
                "label": self._locked_label,
                "score": self._locked_score,
                "command": self._command_text,
            }, ensure_ascii=False) + "\n"
        )
        self._log.flush()

    def _draw_overlay(self, frame, width: int, height: int):
        if self._pending is not None:
            label, score, bbox = self._pending
            x1, y1, x2, y2 = bbox
            pixel_box = (
                int(x1 * width), int(y1 * height),
                int(x2 * width), int(y2 * height),
            )
            try:
                self._tracker = cv2.TrackerCSRT_create()
                self._tracker.init(frame, pixel_box)
                self._locked_label = label
                self._locked_score = score
                self._tracker_ok = True
            except Exception:
                self._tracker = None
            self._pending = None
        if self._tracker is not None:
            ok, box = self._tracker.update(frame)
            self._tracker_ok = bool(ok)
            if ok:
                x, y, w, h = (int(v) for v in box)
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                text = f"{self._locked_label} {self._locked_score:.2f} LOCK"
                self._draw_cjk_text(
                    frame, text, (x, max(18, y - 8)), 20,
                    (0, 255, 0), (0, 0, 0, 160),
                )
            else:
                self._draw_cjk_text(
                    frame, "target lost, searching...", (12, 30), 22,
                    (0, 0, 255), (0, 0, 0, 160),
                )
        else:
            self._draw_cjk_text(
                frame, "searching...", (12, 30), 22,
                (0, 255, 255), (0, 0, 0, 160),
            )
        if self._command_text:
            self._draw_cjk_text(
                frame, f"指令: {self._command_text}",
                (12, height - 32), 22,
                (255, 255, 255), (0, 0, 0, 180),
            )

    def stop(self) -> None:
        self._stop.set()
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._node is not None:
            self._node.destroy_node()
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        self._log.close()


class AutonomousLoop(Node):
    def __init__(self, pattern: list[str], output: str, forward_vx: float,
                 forward_seconds: float, max_yaw_rate: float,
                 min_clearance_m: float, mode: str, max_seconds: float,
                 wander_front_go_m: float, wander_turn_deg: float,
                 max_radius_m: float, scan_turn_deg: float,
                 scan_span: int, pre_scan_turns: int, record_video: str,
                 video_fps: float, video_scale: float,
                 scan360_steps: int, scan360_turn_deg: float,
                 odom_topic: str = "/go2w/odom/wheel") -> None:
        super().__init__("go2w_autonomous_loop")
        self._odom_topic = odom_topic
        self._pattern = pattern
        self._forward_vx = forward_vx
        self._forward_seconds = forward_seconds
        self._max_yaw_rate = max_yaw_rate
        self._min_clearance = min_clearance_m
        self._mode = mode
        self._max_seconds = max_seconds
        self._wander_front_go = wander_front_go_m
        self._wander_turn_deg = wander_turn_deg
        self._max_radius = max_radius_m
        self._scan_turn_deg = scan_turn_deg
        self._scan_span = scan_span
        self._pre_scan_turns = pre_scan_turns
        self._record_video = record_video
        self._video_fps = video_fps
        self._video_scale = video_scale
        self._video: BundleVideoRecorder | None = None
        self._scan360_steps = scan360_steps
        self._scan360_turn_deg = scan360_turn_deg
        self._output = open(output, "a", encoding="utf-8")
        self._start_monotonic = time.monotonic()

        self._client = ActionClient(self, MotionCommand, "/go2w/motion")
        self._arm_client = self.create_client(SetBool, "/go2w/arm")
        self._stop_srv = self.create_client(Trigger, "/go2w/emergency_stop")

        self._sport: SportModeState | None = None
        self._low: LowState | None = None
        self._odom: Odometry | None = None
        self._clearance: float | None = None
        self._left_clearance: float | None = None
        self._right_clearance: float | None = None
        qos = QoSProfile(depth=20, reliability=2)  # BEST_EFFORT
        self.create_subscription(SportModeState, "/lf/sportmodestate",
                                 self._on_sport, qos)
        self.create_subscription(LowState, "/lf/lowstate", self._on_low, qos)
        self.create_subscription(Odometry, self._odom_topic, self._on_odom,
                                 QoSProfile(depth=50, reliability=2))
        self.create_subscription(Float32, "/go2w/safety/front_clearance",
                                 self._on_clearance, qos)
        self.create_subscription(Float32, "/go2w/safety/left_clearance",
                                 self._on_left_clearance, qos)
        self.create_subscription(Float32, "/go2w/safety/right_clearance",
                                 self._on_right_clearance, qos)

    def _host_s(self) -> float:
        return round(time.monotonic(), 6)

    def _write(self, row: dict) -> None:
        self._output.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._output.flush()

    def _on_sport(self, msg: SportModeState) -> None:
        self._sport = msg

    def _on_low(self, msg: LowState) -> None:
        self._low = msg

    def _on_odom(self, msg: Odometry) -> None:
        self._odom = msg

    def _on_clearance(self, msg: Float32) -> None:
        self._clearance = float(msg.data)

    def _on_left_clearance(self, msg: Float32) -> None:
        self._left_clearance = float(msg.data)

    def _on_right_clearance(self, msg: Float32) -> None:
        self._right_clearance = float(msg.data)

    def _yaw(self) -> float:
        if self._sport is None:
            return 0.0
        return float(self._sport.imu_state.rpy[2])

    def _wait_for(self, predicate, timeout: float, label: str) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            if predicate():
                return True
        self.get_logger().error(f"timeout waiting for {label}")
        return False

    def _safety_ok(self) -> tuple[bool, str]:
        if self._sport is None:
            return False, "no sport state"
        if int(self._sport.mode) != 1 or int(self._sport.error_code) != 0:
            return False, (f"robot mode={self._sport.mode} "
                           f"error={self._sport.error_code}")
        if (self._mode != "wander" and self._clearance is not None
                and self._clearance < self._min_clearance):
            return False, f"front clearance {self._clearance:.2f} m too low"
        return True, ""

    def _call_service(self, client, request, label: str):
        if not client.wait_for_service(timeout_sec=3.0):
            raise RuntimeError(f"{label} service unavailable")
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)
        if not future.done():
            raise RuntimeError(f"{label} service timeout")
        return future.result()

    def _arm(self, value: bool) -> None:
        request = SetBool.Request()
        request.data = value
        response = self._call_service(self._arm_client, request,
                                      "arm" if value else "disarm")
        if not response.success:
            raise RuntimeError(f"arm response failed: {response.message}")

    def _emergency_stop(self) -> None:
        response = self._call_service(self._stop_srv, Trigger.Request(),
                                      "emergency stop")
        if not response.success:
            self.get_logger().error(f"emergency stop failed: {response.message}")

    def _send_goal(self, goal) -> dict:
        if not self._client.wait_for_server(timeout_sec=5.0):
            raise RuntimeError("motion action server unavailable")
        future = self._client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)
        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            raise RuntimeError("motion goal rejected")
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future, timeout_sec=90.0)
        if not result_future.done():
            raise RuntimeError("motion goal timeout")
        return {
            "success": bool(result_future.result().result.success),
            "message": str(result_future.result().result.message),
            "yaw_deg": float(result_future.result().result.actual_relative_yaw_deg),
            "elapsed": float(result_future.result().result.elapsed_sec),
        }

    def _odom_snapshot(self) -> tuple[float, float, float]:
        if self._odom is None:
            return (0.0, 0.0, 0.0)
        pose = self._odom.pose.pose
        q = pose.orientation
        yaw = math.atan2(2.0 * (q.z * q.w), 1.0 - 2.0 * (q.z * q.z))
        return (float(pose.position.x), float(pose.position.y), yaw)

    def _describe_step(self, step: str) -> str:
        if step == "f":
            return (f"前进 {self._forward_vx:.2f} m/s × "
                    f"{self._forward_seconds:.0f}s")
        if step.startswith("l"):
            return f"左转 {step[1:]}°"
        if step.startswith("r"):
            return f"右转 {step[1:]}°"
        return step

    def _execute_step(self, index: int, step: str,
                      attempts: int = 2) -> tuple[bool, str]:
        if self._video is not None:
            self._video.set_command(self._describe_step(step))
        self._write({"event": "step_start", "index": index, "step": step,
                     "host_s": self._host_s(), "clearance": self._clearance})
        ok, reason = self._safety_ok()
        if not ok:
            self._write({"event": "abort", "index": index, "step": step,
                         "reason": reason, "host_s": self._host_s()})
            self.get_logger().error(f"abort before step {step}: {reason}")
            return False, reason
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                time.sleep(0.8)
            try:
                # The arm state expires after arm_timeout_sec on the action
                # server; long LLM detections can exceed it, so re-arm right
                # before every motion command (idempotent).
                self._arm(True)
            except RuntimeError as exc:
                self._write({"event": "abort", "index": index, "step": step,
                             "reason": f"arm failed: {exc}",
                             "host_s": self._host_s()})
                self.get_logger().error(f"arm failed before step {step}: {exc}")
                return False, f"arm failed: {exc}"
            before = self._odom_snapshot()
            goal = MotionCommand.Goal()
            if step == "f":
                goal.mode = MotionCommand.Goal.MODE_TIMED_VELOCITY
                goal.vx = self._forward_vx
                goal.duration_sec = self._forward_seconds
                goal.timeout_sec = self._forward_seconds + 8.0
            elif step.startswith("l") or step.startswith("r"):
                degrees = float(step[1:])
                if step.startswith("r"):
                    degrees = -degrees
                goal.mode = MotionCommand.Goal.MODE_RELATIVE_YAW
                goal.relative_yaw_deg = degrees
                goal.max_yaw_rate = self._max_yaw_rate
                goal.timeout_sec = max(
                    15.0, abs(degrees) / self._max_yaw_rate * 3.0 + 8.0
                )
            else:
                self.get_logger().error(f"unknown step {step}")
                return False, f"unknown step {step}"
            try:
                result = self._send_goal(goal)
            except (RuntimeError, OSError, ValueError) as exc:
                if attempt < attempts:
                    self.get_logger().warn(
                        f"step {step} attempt {attempt} send failed "
                        f"({exc}); retrying"
                    )
                    continue
                self._write({"event": "abort", "index": index, "step": step,
                             "reason": str(exc),
                             "host_s": self._host_s()})
                self.get_logger().error(
                    f"step {step} send failed: {exc}"
                )
                return False, str(exc)
            self._write({"event": "step_result", "index": index, "step": step,
                         "attempt": attempt, "host_s": self._host_s(),
                         "result": result})
            if not result["success"]:
                if attempt < attempts:
                    self.get_logger().warn(
                        f"step {step} attempt {attempt} failed "
                        f"({result['message']}); retrying after settle"
                    )
                    continue
                self._write({"event": "abort", "index": index, "step": step,
                             "reason": result["message"],
                             "host_s": self._host_s()})
                self.get_logger().error(f"step failed: {result['message']}")
                return False, result["message"]
            time.sleep(1.0)
            rclpy.spin_once(self, timeout_sec=0.2)
            after = self._odom_snapshot()
            distance = math.hypot(after[0] - before[0], after[1] - before[1])
            yaw_delta = abs(after[2] - before[2])
            verified = (distance > 0.03) if step == "f" else (
                yaw_delta > 3.0 * math.pi / 180.0
            )
            self._write({"event": "step_verified", "index": index, "step": step,
                         "attempt": attempt, "host_s": self._host_s(),
                         "distance_m": distance,
                         "yaw_delta_rad": yaw_delta,
                         "expected_turn_deg": abs(float(step[1:]))
                         if len(step) > 1 else 0.0,
                         "verified": bool(verified)})
            if verified:
                return True, ""
            self.get_logger().warn(
                f"step {step} attempt {attempt} not confirmed by wheel odometry"
            )
        self._write({"event": "abort", "index": index, "step": step,
                     "reason": "wheel odometry did not confirm motion after retries",
                     "host_s": self._host_s()})
        self.get_logger().error("wheel odometry did not confirm motion")
        return False, "wheel odometry did not confirm motion after retries"

    def _next_wander_step(self) -> str:
        front = self._clearance if self._clearance is not None else 0.0
        left = self._left_clearance if self._left_clearance is not None else 0.0
        right = self._right_clearance if self._right_clearance is not None else 0.0
        if front > self._wander_front_go:
            return "f"
        if left > right:
            return f"l{int(self._wander_turn_deg)}"
        return f"r{int(self._wander_turn_deg)}"

    def _run_camera_guided(
        self,
        target: str,
        spool_root: str,
        score_min: float,
        align_threshold: float,
        align_yaw_max_deg: float,
        reach_area_ratio: float,
    ) -> None:
        env = self._load_detector_env()
        prompt = PROMPT_MAP.get(target.strip(), f"{target.strip()}. {target.strip()} object")
        self._write({"event": "camera_guided_start", "host_s": self._host_s(),
                     "target": target, "prompt": prompt})
        started = time.monotonic()
        index = 0
        alternate = 0
        while time.monotonic() - started < self._max_seconds:
            ok, reason = self._safety_ok()
            if not ok:
                self._write({"event": "abort", "index": index,
                             "reason": reason, "host_s": self._host_s()})
                break
            try:
                image_path, frame_id = self._latest_bundle_image(spool_root)
                self._write({"event": "camera_bundle", "index": index,
                             "frame_id": frame_id, "host_s": self._host_s()})
                objects = self._detect(image_path, prompt, env)
            except (RuntimeError, OSError, ValueError) as exc:
                self._write({"event": "detection_error", "index": index,
                             "error": str(exc), "host_s": self._host_s()})
                self.get_logger().warn(f"detection failed: {exc}")
                if self._is_stale_error(exc):
                    self._write({"event": "abort", "index": index,
                                 "reason": str(exc),
                                 "host_s": self._host_s()})
                    break
                step = "l30" if alternate % 2 == 0 else "r30"
                alternate += 1
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            best = max(
                (item for item in objects if float(item.get("score", 0.0)) >= score_min),
                key=lambda item: float(item.get("score", 0.0)),
                default=None,
            )
            if best is None:
                self._write({"event": "target_not_found", "index": index,
                             "objects": len(objects),
                             "host_s": self._host_s()})
                step = "l30" if alternate % 2 == 0 else "r30"
                alternate += 1
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            bbox = [float(value) for value in best.get("bbox_2d", [0.0, 0.0, 1.0, 1.0])]
            x1, y1, x2, y2 = bbox
            center_x = (x1 + x2) / 2.0
            area_ratio = max(0.0, (x2 - x1) * (y2 - y1))
            offset = center_x - 0.5
            self._write({"event": "target_found", "index": index,
                         "label": str(best.get("label", "object")),
                         "score": round(float(best.get("score", 0.0)), 3),
                         "center_x": round(center_x, 3),
                         "area_ratio": round(area_ratio, 4),
                         "host_s": self._host_s()})
            self._feed_detection(
                str(best.get("label", "object")),
                float(best.get("score", 0.0)),
                (x1, y1, x2, y2),
            )
            self.get_logger().info(
                f"target {best.get('label')} "
                f"score={float(best.get('score', 0.0)):.2f} "
                f"cx={center_x:.2f} area={area_ratio:.3f}"
            )
            if area_ratio >= reach_area_ratio:
                self._write({"event": "target_reached", "index": index,
                             "host_s": self._host_s()})
                self.get_logger().info("target reached; stopping")
                break
            if abs(offset) > align_threshold:
                degrees = max(
                    -align_yaw_max_deg,
                    min(align_yaw_max_deg, -offset * align_yaw_max_deg * 2.0),
                )
                step = (
                    f"l{int(abs(degrees))}"
                    if degrees > 0.0
                    else f"r{int(abs(degrees))}"
                )
            else:
                step = "f"
            ok, _ = self._execute_step(index, step)
            if not ok:
                break
            index += 1
        else:
            self._write({"event": "camera_guided_time_limit",
                         "host_s": self._host_s()})

    def _scan_sequence(self) -> list[str]:
        deg = int(self._scan_turn_deg)
        right = [f"r{deg}"] * self._scan_span
        left = [f"l{deg}"] * self._scan_span
        # Sweep right, return, small forward, sweep left, return, small forward.
        # Net heading is zero and the tether is not progressively twisted.
        return right + left + ["f"] + left + right + ["f"]

    def _distance_from(self, origin: tuple[float, float, float]) -> float:
        current = self._odom_snapshot()
        return math.hypot(current[0] - origin[0], current[1] - origin[1])

    def _feed_detection(
        self, label: str, score: float,
        bbox_xyxy: tuple[float, float, float, float],
    ) -> None:
        if self._video is not None:
            self._video.set_detection(label, score, bbox_xyxy)

    def _run_level_a_search(
        self,
        target: str,
        spool_root: str,
        score_min: float,
        align_threshold: float,
        align_yaw_max_deg: float,
        reach_area_ratio: float,
    ) -> None:
        env = self._load_detector_env()
        prompt = PROMPT_MAP.get(
            target.strip(), f"{target.strip()}. {target.strip()} object"
        )
        origin = self._odom_snapshot()
        scan = self._scan_sequence()
        scan_index = 0
        self._write({
            "event": "level_a_start",
            "host_s": self._host_s(),
            "target": target,
            "prompt": prompt,
            "max_radius_m": self._max_radius,
            "scan_turn_deg": self._scan_turn_deg,
            "scan_span": self._scan_span,
        })
        self.get_logger().info(
            f"Level A search start: target={target} "
            f"radius_limit={self._max_radius if self._max_radius > 0 else 'unlimited'}"
        )
        started = time.monotonic()
        index = 0
        for _ in range(self._pre_scan_turns):
            ok, reason = self._safety_ok()
            if not ok:
                self._write({"event": "abort", "index": index,
                             "reason": reason, "host_s": self._host_s()})
                break
            step = scan[scan_index % len(scan)]
            scan_index += 1
            self._write({"event": "search_step", "index": index,
                         "step": step, "phase": "PRE_SCAN",
                         "host_s": self._host_s()})
            ok, _ = self._execute_step(index, step)
            if not ok:
                break
            index += 1
        while time.monotonic() - started < self._max_seconds:
            ok, reason = self._safety_ok()
            if not ok:
                self._write({"event": "abort", "index": index,
                             "reason": reason, "host_s": self._host_s()})
                break
            distance = self._distance_from(origin)
            if self._max_radius > 0.0 and distance > self._max_radius:
                self._write({"event": "range_limit", "index": index,
                             "distance_m": round(distance, 3),
                             "host_s": self._host_s()})
                self.get_logger().warn(
                    f"range limit reached ({distance:.2f} m); stopping"
                )
                break
            try:
                image_path, frame_id = self._latest_bundle_image(spool_root)
                self._write({"event": "camera_bundle", "index": index,
                             "frame_id": frame_id, "host_s": self._host_s()})
                objects = self._detect(image_path, prompt, env)
            except (RuntimeError, OSError, ValueError) as exc:
                self._write({"event": "detection_error", "index": index,
                             "error": str(exc), "host_s": self._host_s()})
                self.get_logger().warn(f"detection failed: {exc}")
                if self._is_stale_error(exc):
                    self._write({"event": "abort", "index": index,
                                 "reason": str(exc),
                                 "host_s": self._host_s()})
                    break
                step = "r30"
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            best = max(
                (item for item in objects
                 if float(item.get("score", 0.0)) >= score_min),
                key=lambda item: float(item.get("score", 0.0)),
                default=None,
            )
            if self._video is not None:
                if best is None:
                    self._video.set_command("LLM: 未发现目标，扫描中")
                else:
                    self._video.set_command(
                        f"LLM: {best.get('label')} "
                        f"score={float(best.get('score', 0.0)):.2f}"
                    )
            if best is None:
                step = scan[scan_index % len(scan)]
                scan_index += 1
                if step == "f":
                    step_estimate = (
                        self._forward_seconds * self._forward_vx * 0.6
                    )
                    if (self._max_radius > 0.0
                            and distance + step_estimate >= self._max_radius):
                        step = "r30"
                self._write({"event": "search_step", "index": index,
                             "step": step, "phase": "SEARCH",
                             "distance_m": round(distance, 3),
                             "objects": len(objects),
                             "host_s": self._host_s()})
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue

            bbox = [
                float(value)
                for value in best.get("bbox_2d", [0.0, 0.0, 1.0, 1.0])
            ]
            x1, y1, x2, y2 = bbox
            center_x = (x1 + x2) / 2.0
            area_ratio = max(0.0, (x2 - x1) * (y2 - y1))
            offset = center_x - 0.5
            self._write({"event": "target_found", "index": index,
                         "label": str(best.get("label", "object")),
                         "score": round(float(best.get("score", 0.0)), 3),
                         "center_x": round(center_x, 3),
                         "area_ratio": round(area_ratio, 4),
                         "distance_m": round(distance, 3),
                         "host_s": self._host_s()})
            self._feed_detection(
                str(best.get("label", "object")),
                float(best.get("score", 0.0)),
                (x1, y1, x2, y2),
            )
            self.get_logger().info(
                f"DISCOVERED {best.get('label')} score="
                f"{float(best.get('score', 0.0)):.2f} "
                f"cx={center_x:.2f} area={area_ratio:.3f} "
                f"range={distance:.2f} m"
            )
            if area_ratio >= reach_area_ratio:
                try:
                    verification = self._verify_target(image_path, bbox, env)
                except (RuntimeError, OSError, ValueError) as exc:
                    verification = {
                        "object_name_zh": "复核失败",
                        "is_target": False,
                        "confidence": 0.0,
                        "reason_zh": str(exc),
                    }
                self._write({"event": "target_verification", "index": index,
                             "label": str(best.get("label", "object")),
                             "area_ratio": round(area_ratio, 4),
                             "verification": verification,
                             "host_s": self._host_s()})
                if self._video is not None:
                    verdict = "通过" if verification.get("is_target") else "拒绝"
                    self._video.set_command(
                        f"复核: {verification.get('object_name_zh', '?')} {verdict}"
                    )
                if verification.get("is_target"):
                    self._write({"event": "target_reached", "index": index,
                                 "verification": verification,
                                 "host_s": self._host_s()})
                    self.get_logger().info(
                        f"target reached and verified: "
                        f"{verification.get('object_name_zh')} "
                        f"({verification.get('reason_zh')})"
                    )
                    break
                self.get_logger().warn(
                    f"target verification rejected: "
                    f"{verification.get('object_name_zh')} - "
                    f"{verification.get('reason_zh')}"
                )
                step = "r15"
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            if abs(offset) > align_threshold:
                degrees = max(
                    -align_yaw_max_deg,
                    min(align_yaw_max_deg, -offset * align_yaw_max_deg * 2.0),
                )
                step = (
                    f"l{int(abs(degrees))}"
                    if degrees > 0.0
                    else f"r{int(abs(degrees))}"
                )
            else:
                step = "f"
                step_estimate = (
                    self._forward_seconds * self._forward_vx * 0.6
                )
                if (self._max_radius > 0.0
                        and distance + step_estimate >= self._max_radius):
                    self._write({"event": "range_limit", "index": index,
                                 "phase": "APPROACH",
                                 "distance_m": round(distance, 3),
                                 "host_s": self._host_s()})
                    self.get_logger().warn(
                        "approach would exceed range limit; stopping"
                    )
                    break
            self._write({"event": "approach_step", "index": index,
                         "step": step, "phase": "APPROACH",
                         "host_s": self._host_s()})
            ok, _ = self._execute_step(index, step)
            if not ok:
                break
            index += 1
        else:
            self._write({"event": "level_a_time_limit",
                         "host_s": self._host_s()})

    def _run_scan360_approach(
        self,
        target: str,
        spool_root: str,
        score_min: float,
        align_threshold: float,
        align_yaw_max_deg: float,
        reach_area_ratio: float,
    ) -> None:
        env = self._load_detector_env()
        prompt = PROMPT_MAP.get(
            target.strip(), f"{target.strip()}. {target.strip()} object"
        )
        origin = self._odom_snapshot()
        start_yaw = origin[2]
        started = time.monotonic()
        index = 0
        best = None
        stale_aborted = False
        self._write({
            "event": "scan360_start",
            "host_s": self._host_s(),
            "target": target,
            "prompt": prompt,
            "steps": self._scan360_steps,
            "turn_deg": self._scan360_turn_deg,
            "max_radius_m": self._max_radius,
        })
        self.get_logger().info(
            f"360 scan start: target={target} "
            f"{self._scan360_steps} x {self._scan360_turn_deg} deg"
        )

        for step_index in range(self._scan360_steps):
            ok, reason = self._safety_ok()
            if not ok:
                self._write({"event": "abort", "index": index,
                             "reason": reason, "host_s": self._host_s()})
                break
            try:
                image_path, frame_id = self._latest_bundle_image(spool_root)
                objects = self._detect(image_path, prompt, env)
            except (RuntimeError, OSError, ValueError) as exc:
                self._write({"event": "detection_error", "index": index,
                             "error": str(exc), "host_s": self._host_s()})
                if self._is_stale_error(exc):
                    self._write({"event": "scan360_abort", "index": index,
                                 "reason": str(exc),
                                 "host_s": self._host_s()})
                    stale_aborted = True
                    break
                objects = []
            heading_deg = math.degrees(
                self._odom_snapshot()[2] - start_yaw
            )
            heading_deg = (heading_deg + 180.0) % 360.0 - 180.0
            candidates = [
                item for item in objects
                if float(item.get("score", 0.0)) >= score_min
            ]
            self._write({"event": "scan360_heading", "index": index,
                         "step": step_index, "heading_deg": round(heading_deg, 1),
                         "candidates": len(candidates),
                         "host_s": self._host_s()})
            if self._video is not None:
                if candidates:
                    top_candidate = candidates[0]
                    self._video.set_command(
                        f"LLM 命中: {top_candidate.get('label')} "
                        f"{float(top_candidate.get('score', 0.0)):.2f} "
                        f"@ {heading_deg:.0f}°"
                    )
                else:
                    self._video.set_command(
                        f"LLM: 未发现目标 @ {heading_deg:.0f}°"
                    )
            for item in candidates:
                score = float(item.get("score", 0.0))
                bbox = [float(v) for v in item.get("bbox_2d", [0.0, 0.0, 1.0, 1.0])]
                self._write({"event": "scan360_candidate", "index": index,
                             "heading_deg": round(heading_deg, 1),
                             "label": str(item.get("label", "object")),
                             "score": round(score, 3),
                             "host_s": self._host_s()})
                self._feed_detection(
                    str(item.get("label", "object")), score,
                    (bbox[0], bbox[1], bbox[2], bbox[3]),
                )
                if best is None or score > best["score"]:
                    best = {
                        "label": str(item.get("label", "object")),
                        "score": score,
                        "bbox": (bbox[0], bbox[1], bbox[2], bbox[3]),
                        "heading_deg": heading_deg,
                    }
            if best is not None and best["score"] >= 0.80:
                self._write({"event": "scan360_early_hit", "index": index,
                             "heading_deg": round(best["heading_deg"], 1),
                             "label": best["label"],
                             "score": round(best["score"], 3),
                             "host_s": self._host_s()})
                self.get_logger().info(
                    f"high-confidence target {best['label']} "
                    f"score={best['score']:.2f} at "
                    f"{best['heading_deg']:.0f} deg; stopping scan early"
                )
                break
            if step_index < self._scan360_steps - 1:
                step = f"r{int(self._scan360_turn_deg)}"
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1

        if stale_aborted:
            self.get_logger().error(
                "camera went stale during 360 scan; aborting"
            )
            return

        if best is None:
            self._write({"event": "scan360_no_target",
                         "host_s": self._host_s()})
            self.get_logger().warn(
                "no target found during 360 scan; falling back to search"
            )
            self._run_level_a_search(
                target, spool_root, score_min, align_threshold,
                align_yaw_max_deg, reach_area_ratio,
            )
            return

        self._write({"event": "scan360_best", "host_s": self._host_s(),
                     "label": best["label"], "score": round(best["score"], 3),
                     "heading_deg": round(best["heading_deg"], 1)})
        self.get_logger().info(
            f"360 scan best: {best['label']} score={best['score']:.2f} "
            f"at heading {best['heading_deg']:.0f} deg"
        )
        self._feed_detection(best["label"], best["score"], best["bbox"])

        current_heading = math.degrees(self._odom_snapshot()[2] - start_yaw)
        current_heading = (current_heading + 180.0) % 360.0 - 180.0
        turn_needed = best["heading_deg"] - current_heading
        turn_needed = (turn_needed + 180.0) % 360.0 - 180.0
        if abs(turn_needed) > 2.0:
            step = (
                f"l{int(abs(turn_needed))}"
                if turn_needed > 0.0
                else f"r{int(abs(turn_needed))}"
            )
            time.sleep(1.0)
            self._write({"event": "scan360_turn_to_target", "index": index,
                         "step": step, "host_s": self._host_s()})
            ok, _ = self._execute_step(index, step)
            if not ok:
                return
            index += 1

        while time.monotonic() - started < self._max_seconds:
            ok, reason = self._safety_ok()
            if not ok:
                self._write({"event": "abort", "index": index,
                             "reason": reason, "host_s": self._host_s()})
                break
            distance = self._distance_from(origin)
            if self._max_radius > 0.0 and distance > self._max_radius:
                self._write({"event": "range_limit", "index": index,
                             "distance_m": round(distance, 3),
                             "host_s": self._host_s()})
                break
            try:
                image_path, frame_id = self._latest_bundle_image(spool_root)
                objects = self._detect(image_path, prompt, env)
            except (RuntimeError, OSError, ValueError) as exc:
                self._write({"event": "detection_error", "index": index,
                             "error": str(exc), "host_s": self._host_s()})
                if self._is_stale_error(exc):
                    self._write({"event": "abort", "index": index,
                                 "reason": str(exc),
                                 "host_s": self._host_s()})
                    break
                step = "r30"
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            best2 = max(
                (item for item in objects
                 if float(item.get("score", 0.0)) >= score_min),
                key=lambda item: float(item.get("score", 0.0)),
                default=None,
            )
            if best2 is None:
                step = "r30"
                self._write({"event": "search_step", "index": index,
                             "step": step, "phase": "APPROACH_RECOVERY",
                             "host_s": self._host_s()})
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            bbox = [
                float(value)
                for value in best2.get("bbox_2d", [0.0, 0.0, 1.0, 1.0])
            ]
            x1, y1, x2, y2 = bbox
            center_x = (x1 + x2) / 2.0
            area_ratio = max(0.0, (x2 - x1) * (y2 - y1))
            offset = center_x - 0.5
            self._write({"event": "target_found", "index": index,
                         "label": str(best2.get("label", "object")),
                         "score": round(float(best2.get("score", 0.0)), 3),
                         "center_x": round(center_x, 3),
                         "area_ratio": round(area_ratio, 4),
                         "distance_m": round(distance, 3),
                         "host_s": self._host_s()})
            self._feed_detection(
                str(best2.get("label", "object")),
                float(best2.get("score", 0.0)),
                (x1, y1, x2, y2),
            )
            if area_ratio >= reach_area_ratio:
                try:
                    verification = self._verify_target(image_path, bbox, env)
                except (RuntimeError, OSError, ValueError) as exc:
                    verification = {
                        "object_name_zh": "复核失败",
                        "is_target": False,
                        "confidence": 0.0,
                        "reason_zh": str(exc),
                    }
                self._write({"event": "target_verification", "index": index,
                             "label": str(best2.get("label", "object")),
                             "area_ratio": round(area_ratio, 4),
                             "verification": verification,
                             "host_s": self._host_s()})
                if self._video is not None:
                    verdict = "通过" if verification.get("is_target") else "拒绝"
                    self._video.set_command(
                        f"复核: {verification.get('object_name_zh', '?')} {verdict}"
                    )
                if verification.get("is_target"):
                    self._write({"event": "target_reached", "index": index,
                                 "verification": verification,
                                 "host_s": self._host_s()})
                    self.get_logger().info(
                        f"target reached and verified: "
                        f"{verification.get('object_name_zh')} "
                        f"({verification.get('reason_zh')})"
                    )
                    break
                self.get_logger().warn(
                    f"target verification rejected: "
                    f"{verification.get('object_name_zh')} - "
                    f"{verification.get('reason_zh')}"
                )
                step = "r15"
                ok, _ = self._execute_step(index, step)
                if not ok:
                    break
                index += 1
                continue
            if abs(offset) > align_threshold:
                degrees = max(
                    -align_yaw_max_deg,
                    min(align_yaw_max_deg, -offset * align_yaw_max_deg * 2.0),
                )
                step = (
                    f"l{int(abs(degrees))}"
                    if degrees > 0.0
                    else f"r{int(abs(degrees))}"
                )
            else:
                step = "f"
                step_estimate = (
                    self._forward_seconds * self._forward_vx * 0.6
                )
                if (self._max_radius > 0.0
                        and distance + step_estimate >= self._max_radius):
                    self._write({"event": "range_limit", "index": index,
                                 "phase": "APPROACH",
                                 "distance_m": round(distance, 3),
                                 "host_s": self._host_s()})
                    break
            self._write({"event": "approach_step", "index": index,
                         "step": step, "phase": "APPROACH",
                         "host_s": self._host_s()})
            ok, _ = self._execute_step(index, step)
            if not ok:
                break
            index += 1
        else:
            self._write({"event": "scan360_time_limit",
                         "host_s": self._host_s()})

    def _run_state_machine_search(
        self,
        target: str,
        spool_root: str,
        score_min: float,
        align_threshold: float,
        align_yaw_max_deg: float,
        reach_area_ratio: float,
    ) -> None:
        """Run the formal app-layer state machine with the LLM workers."""
        env = self._load_detector_env()
        prompt = PROMPT_MAP.get(
            target.strip(), f"{target.strip()}. {target.strip()} object"
        )
        state: dict[str, object] = {"image_path": None}
        step_index = [0]

        def detect() -> list[Detection]:
            image_path, frame_id = self._latest_bundle_image(spool_root)
            state["image_path"] = image_path
            self._write({"event": "camera_bundle", "frame_id": frame_id,
                         "host_s": self._host_s()})
            objects = self._detect(image_path, prompt, env)
            detections = []
            for item in objects:
                bbox = [
                    float(value)
                    for value in item.get(
                        "bbox_2d", [0.0, 0.0, 1.0, 1.0]
                    )
                ]
                detections.append(
                    Detection(
                        label=str(item.get("label", "object")),
                        score=float(item.get("score", 0.0)),
                        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    )
                )
            return detections

        def verify(bbox: tuple[float, float, float, float]
                   ) -> VerificationResult:
            image_path = state["image_path"]
            if not isinstance(image_path, str):
                raise RuntimeError("no image available for verification")
            result = self._verify_target(image_path, list(bbox), env)
            return VerificationResult(
                object_name_zh=result.get("object_name_zh", ""),
                is_target=bool(result.get("is_target", False)),
                confidence=float(result.get("confidence", 0.0)),
                reason_zh=result.get("reason_zh", ""),
            )

        def execute_step(step: str) -> tuple[bool, str]:
            index = step_index[0]
            step_index[0] += 1
            return self._execute_step(index, step)

        def snapshot() -> SensorSnapshot:
            return SensorSnapshot(
                camera_fresh=True,
                lidar_fresh=self._clearance is not None,
                robot_stationary=True,
            )

        config = StepSearchConfig(
            target=target,
            max_seconds=self._max_seconds,
            max_radius_m=self._max_radius,
            score_min=score_min,
            align_threshold=align_threshold,
            align_yaw_max_deg=align_yaw_max_deg,
            reach_area_ratio=reach_area_ratio,
            scan_turn_deg=self._scan_turn_deg,
            scan_span=self._scan_span,
        )
        runner = StepSearchRunner(
            config,
            detect=detect,
            verify=verify,
            execute_step=execute_step,
            snapshot=snapshot,
            odometry=self._odom_snapshot,
        )
        result = runner.run()
        for event in result["events"]:
            self._write(event)
        self.get_logger().info(
            f"state machine search finished: {result['status']} - "
            f"{result['finish_reason']} "
            f"(steps={result['steps_executed']})"
        )

    @staticmethod
    def _load_detector_env() -> dict[str, str]:
        env_file = PROJECT_ROOT / ".env"
        values: dict[str, str] = {}
        if env_file.is_file():
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
        env = os.environ.copy()
        env["PYTHONPATH"] = values.get(
            "GROUNDED_SAM_PYTHONPATH",
            "/home/brov/robot/Grounded-SAM-2:"
            "/home/brov/robot/Grounded-SAM-2/grounding_dino",
        )
        env["GROUNDED_SAM_ROOT"] = values.get(
            "GROUNDED_SAM_ROOT", "/home/brov/robot/Grounded-SAM-2"
        )
        return env

    def _latest_bundle_image(self, spool_root: str,
                             retries: int = 6,
                             retry_delay_seconds: float = 3.0
                             ) -> tuple[str, int]:
        """Return the latest camera bundle, tolerating brief camera stalls.

        The camera RPC stream can hiccup for a few seconds after a robot
        restart. The loop only calls this while the robot is stationary, so
        bounded waiting is safe; if the bundle stays stale beyond the retry
        window the caller aborts instead of acting on an old frame.
        """
        directory = (Path(spool_root) / "latest").resolve()
        last_error: RuntimeError | None = None
        for attempt in range(retries + 1):
            if attempt:
                time.sleep(retry_delay_seconds)
            try:
                ready = directory / "READY"
                if not ready.is_file():
                    raise RuntimeError("no READY bundle available")
                payload = json.loads(
                    (directory / "frame_bundle.json").read_text(
                        encoding="utf-8"
                    )
                )
                image_path = directory / str(payload["image_path"])
                if not image_path.is_file():
                    raise RuntimeError("bundle image missing")
                receive_ns = payload.get("image_receive_time_ns")
                if isinstance(receive_ns, (int, float)) and receive_ns > 0:
                    age_seconds = (
                        time.time_ns() - int(receive_ns)
                    ) / 1.0e9
                else:
                    age_seconds = time.time() - os.path.getmtime(image_path)
                if age_seconds > 5.0:
                    raise RuntimeError(
                        f"camera bundle stale (age={age_seconds:.1f}s); "
                        "refusing to act on an old frame"
                    )
                return str(image_path), int(payload.get("frame_id", -1))
            except RuntimeError as exc:
                last_error = exc
        if last_error is None:
            last_error = RuntimeError("camera bundle unavailable")
        raise last_error

    @staticmethod
    def _is_stale_error(exc: Exception) -> bool:
        return "stale" in str(exc).lower()

    def _detect(self, image_path: str, prompt: str, env: dict[str, str]) -> list[dict]:
        if getattr(self, "_detector", "grounded_sam") == "llm":
            return self._detect_llm(image_path, env)
        root = env["GROUNDED_SAM_ROOT"]
        python = env.get(
            "GROUNDED_SAM_PYTHON",
            "/home/brov/miniconda3/envs/go2_robot_scene_demo/bin/python",
        )
        worker = PROJECT_ROOT / "app/detectors/grounded_sam_worker.py"
        command = [
            python,
            str(worker),
            "--image", image_path,
            "--output", str(PROJECT_ROOT / "runtime/go2w/detection_result.json"),
            "--root", root,
            "--text-prompt", prompt,
            "--grounding-config",
            env.get("GROUNDING_DINO_CONFIG",
                    "grounding_dino/groundingdino/config/"
                    "GroundingDINO_SwinT_OGC_local.py"),
            "--grounding-checkpoint",
            env.get("GROUNDING_DINO_CHECKPOINT",
                    "gdino_checkpoints/groundingdino_swint_ogc.pth"),
            "--box-threshold",
            env.get("GROUNDING_DINO_BOX_THRESHOLD", "0.12"),
            "--text-threshold",
            env.get("GROUNDING_DINO_TEXT_THRESHOLD", "0.10"),
            "--sam2-config", "configs/sam2.1/sam2.1_hiera_t.yaml",
            "--sam2-checkpoint", "checkpoints/sam2.1_hiera_tiny.pt",
            "--max-objects", "20",
            "--device", "auto",
            "--disable-sam2",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=150.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("detector timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"detector failed rc={completed.returncode}: "
                f"{completed.stderr[-400:]}"
            )
        output_path = PROJECT_ROOT / "runtime/go2w/detection_result.json"
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return list(payload.get("objects", []))

    def _detect_llm(self, image_path: str, env: dict[str, str]) -> list[dict]:
        python = env.get(
            "SILICONFLOW_PYTHON",
            env.get(
                "GROUNDED_SAM_PYTHON",
                "/home/brov/miniconda3/envs/go2_robot_scene_demo/bin/python",
            ),
        )
        worker = PROJECT_ROOT / "app/detectors/siliconflow_vision_worker.py"
        output_path = PROJECT_ROOT / "runtime/go2w/llm_detection_result.json"
        command = [
            python,
            str(worker),
            "--image", image_path,
            "--output", str(output_path),
            "--target", self._target,
            "--quick",
            "--model", getattr(self, "_llm_model", ""),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("SiliconFlow vision API timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"SiliconFlow vision worker failed rc={completed.returncode}: "
                f"{completed.stderr[-600:]}"
            )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        scene_summary = str(payload.get("scene_summary_zh") or "")
        if scene_summary:
            self.get_logger().info(
                f"LLM scene: {scene_summary} "
                f"(matched={len(payload.get('objects', []))}/"
                f"all={payload.get('all_objects_count', 0)})"
            )
        return list(payload.get("objects", []))

    def _verify_target(self, image_path: str, bbox: list[float],
                       env: dict[str, str]) -> dict:
        """Ask the vision LLM whether the object inside bbox is the target."""
        python = env.get(
            "SILICONFLOW_PYTHON",
            env.get(
                "GROUNDED_SAM_PYTHON",
                "/home/brov/miniconda3/envs/go2_robot_scene_demo/bin/python",
            ),
        )
        worker = PROJECT_ROOT / "app/detectors/siliconflow_vision_worker.py"
        output_path = PROJECT_ROOT / "runtime/go2w/llm_verify_result.json"
        bbox_text = ",".join(f"{float(value):.4f}" for value in bbox)
        command = [
            python,
            str(worker),
            "--image", image_path,
            "--output", str(output_path),
            "--target", self._target,
            "--verify",
            "--bbox", bbox_text,
            "--model", getattr(self, "_llm_model", ""),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120.0,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("SiliconFlow verification timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"SiliconFlow verification worker failed rc={completed.returncode}: "
                f"{completed.stderr[-600:]}"
            )
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return {
            "object_name_zh": str(payload.get("object_name_zh") or ""),
            "is_target": bool(payload.get("is_target", False)),
            "confidence": float(payload.get("confidence", 0.0)),
            "reason_zh": str(payload.get("reason_zh") or ""),
        }

    def run(self) -> int:
        if not self._wait_for(lambda: self._sport is not None and self._odom is not None,
                              10.0, "sport/odom"):
            return 2
        if self._record_video:
            try:
                self._video = BundleVideoRecorder(
                    self._record_video,
                    self._video_fps, self._video_scale,
                )
                self._video.start()
                self.get_logger().info(
                    f"recording camera to {self._record_video}"
                )
            except RuntimeError as exc:
                self.get_logger().warn(f"video recording disabled: {exc}")
        self._write({"event": "start", "host_s": self._host_s(),
                     "pattern": self._pattern, "mode": self._mode})
        try:
            self._arm(True)
        except RuntimeError as exc:
            self.get_logger().error(str(exc))
            return 3

        if self._mode == "wander":
            started = time.monotonic()
            index = 0
            while time.monotonic() - started < self._max_seconds:
                ok, reason = self._safety_ok()
                if not ok:
                    self._write({"event": "abort", "index": index,
                                 "reason": reason, "host_s": self._host_s()})
                    break
                step = self._next_wander_step()
                ok, reason = self._execute_step(index, step)
                if not ok:
                    if step == "f" and "did not confirm motion" in reason:
                        left = (self._left_clearance
                                if self._left_clearance is not None else 0.0)
                        right = (self._right_clearance
                                 if self._right_clearance is not None else 0.0)
                        turn = "l90" if left >= right else "r90"
                        self._write({"event": "forward_blocked",
                                     "index": index, "turn": turn,
                                     "host_s": self._host_s(),
                                     "left": left, "right": right})
                        self.get_logger().warn(
                            f"forward blocked; turning {turn} to find a path"
                        )
                        ok, reason = self._execute_step(index, turn)
                        if not ok:
                            self._write({"event": "abort", "index": index,
                                         "reason": reason,
                                         "host_s": self._host_s()})
                            break
                    else:
                        break
                index += 1
            else:
                self._write({"event": "wander_time_limit",
                             "host_s": self._host_s()})
        elif self._mode == "camera_guided":
            self._run_camera_guided(
                self._target,
                self._spool_root,
                self._target_score_min,
                self._align_threshold,
                self._align_yaw_max_deg,
                self._reach_area_ratio,
            )
        elif self._mode == "level_a_search":
            self._run_level_a_search(
                self._target,
                self._spool_root,
                self._target_score_min,
                self._align_threshold,
                self._align_yaw_max_deg,
                self._reach_area_ratio,
            )
        elif self._mode == "scan360_approach":
            self._run_scan360_approach(
                self._target,
                self._spool_root,
                self._target_score_min,
                self._align_threshold,
                self._align_yaw_max_deg,
                self._reach_area_ratio,
            )
        elif self._mode == "state_machine_search":
            self._run_state_machine_search(
                self._target,
                self._spool_root,
                self._target_score_min,
                self._align_threshold,
                self._align_yaw_max_deg,
                self._reach_area_ratio,
            )
        else:
            for index, step in enumerate(self._pattern):
                ok, reason = self._execute_step(index, step)
                if not ok:
                    break
            else:
                self._write({"event": "pattern_complete",
                             "host_s": self._host_s()})

        self._emergency_stop()
        try:
            self._arm(False)
        except RuntimeError as exc:
            self.get_logger().error(str(exc))
        end = self._odom_snapshot()
        self._write({"event": "finish", "host_s": self._host_s(),
                     "odom": list(end), "clearance": self._clearance})
        if self._video is not None:
            self._video.stop()
            self._video = None
        self.get_logger().info(
            f"finished at ({end[0]:.3f}, {end[1]:.3f}) yaw {math.degrees(end[2]):.1f} deg"
        )
        self._output.close()
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default=",".join(DEFAULT_PATTERN),
                        help="comma-separated steps: f or l<deg>/r<deg>")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--odom-topic",
        default="/go2w/odom/wheel",
        help="odometry topic used for step verification "
             "(e.g. /go2w/odom/fused)",
    )
    parser.add_argument("--forward-vx", type=float, default=0.12)
    parser.add_argument("--forward-seconds", type=float, default=2.0)
    parser.add_argument("--max-yaw-rate", type=float, default=0.15)
    parser.add_argument("--min-clearance", type=float, default=0.30)
    parser.add_argument(
        "--mode",
        choices=("pattern", "wander", "camera_guided", "level_a_search",
                 "scan360_approach", "state_machine_search"),
        default="pattern",
    )
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument(
        "--detector",
        choices=("llm", "grounded_sam"),
        default="llm",
        help="detection backend: llm uses the SiliconFlow vision API "
             "(default); grounded_sam uses local GroundingDINO",
    )
    parser.add_argument(
        "--llm-model",
        default="Qwen/Qwen3-VL-30B-A3B-Instruct",
        help="SiliconFlow vision model for quick robot-loop detection "
             "(fast default; use Qwen/Qwen3-VL-8B-Instruct for higher detail)",
    )
    parser.add_argument("--wander-front-go", type=float, default=0.45)
    parser.add_argument("--wander-turn-deg", type=float, default=30.0)
    parser.add_argument("--target", default="手机")
    parser.add_argument("--spool-root", default="runtime/go2w/spool")
    parser.add_argument("--target-score-min", type=float, default=0.15)
    parser.add_argument("--align-threshold", type=float, default=0.08)
    parser.add_argument("--align-yaw-max-deg", type=float, default=25.0)
    parser.add_argument("--reach-area-ratio", type=float, default=0.15)
    parser.add_argument("--max-radius", type=float, default=1.5,
                        help="search/approach radius limit in metres; "
                             "0 disables the limit (free exploration)")
    parser.add_argument("--scan-turn-deg", type=float, default=30.0)
    parser.add_argument("--scan-span", type=int, default=3)
    parser.add_argument(
        "--pre-scan-turns",
        type=int,
        default=0,
        help="blind scan turns before the first detection (e.g. 3 turns "
             "rotate the target out of view to demo finding an unseen object)",
    )
    parser.add_argument(
        "--record-video",
        default="",
        help="record the camera stream with locked target overlay to this "
             "MP4 path; empty disables recording",
    )
    parser.add_argument("--video-fps", type=float, default=15.0)
    parser.add_argument("--video-scale", type=float, default=0.4)
    parser.add_argument("--scan360-steps", type=int, default=8)
    parser.add_argument("--scan360-turn-deg", type=float, default=45.0)
    args = parser.parse_args()
    pattern = [item for item in args.pattern.split(",") if item]
    rclpy.init()
    node = AutonomousLoop(pattern, args.output, args.forward_vx,
                          args.forward_seconds, args.max_yaw_rate,
                          args.min_clearance, args.mode, args.max_seconds,
                          args.wander_front_go, args.wander_turn_deg,
                          args.max_radius, args.scan_turn_deg, args.scan_span,
                          args.pre_scan_turns, args.record_video,
                          args.video_fps, args.video_scale,
                          args.scan360_steps, args.scan360_turn_deg,
                          args.odom_topic)
    node._target = args.target
    node._detector = args.detector
    node._llm_model = args.llm_model
    node._spool_root = args.spool_root
    node._target_score_min = args.target_score_min
    node._align_threshold = args.align_threshold
    node._align_yaw_max_deg = args.align_yaw_max_deg
    node._reach_area_ratio = args.reach_area_ratio
    try:
        code = node.run()
    except Exception as exc:
        node.get_logger().error(f"unhandled runner exception: {exc}")
        try:
            node._emergency_stop()
        except Exception as stop_exc:
            node.get_logger().error(
                f"emergency stop during cleanup failed: {stop_exc}"
            )
        try:
            node._arm(False)
        except Exception as disarm_exc:
            node.get_logger().error(
                f"disarm during cleanup failed: {disarm_exc}"
            )
        try:
            node._output.close()
        except Exception:
            pass
        code = 4
    finally:
        node.destroy_node()
        rclpy.shutdown()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
