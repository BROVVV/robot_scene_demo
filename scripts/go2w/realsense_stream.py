#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realsense_stream.py — Intel RealSense D435 RGB-D 网络流服务（运行在机器狗内部 Ubuntu）

功能（HTTP 端口默认 8080，机器狗 IP 192.168.123.18）：
  /             HTML 查看页：彩色画面 + 深度伪彩画面 + 设备/内参/外参/传感器信息
  /color        MJPEG 彩色流
  /depth        MJPEG 深度流（jet 伪彩 + 中心点距离叠加，深度对齐到彩色）
  /depth_raw    原始 16-bit 深度 PNG（z16，单位毫米）
  /info.json    设备信息、内参、外参、传感器选项、帧统计
  /health       服务健康检查（fps、帧龄）
  /snapshot     在机器狗上保存一帧快照（color.jpg + depth.jpg + depth_raw.png + info.json）
  /snap/        快照文件目录浏览与下载

用法：
  python3 realsense_stream.py [--width 640] [--height 480] [--fps 30]
                              [--port 8080] [--max-depth 6.0] [--snap-dir ~/realsense_snapshots]
依赖：pyrealsense2（aarch64 wheel，2.55.1）、numpy、opencv-python（机器狗已有 cv2 4.2.0）
"""
import argparse
import json
import os
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import cv2
import numpy as np
import pyrealsense2 as rs

# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------
ARGS = None
BUFFER = {}          # 最新帧缓存: color_jpg, depth_jpg, depth_raw_png, depth_raw, shape, stamp, info
BUFFER_LOCK = threading.Lock()
FRAME_CACHE = deque(maxlen=32)  # 原子 RGB-D frame 缓存（color/depth 来自同一 frameset）
STREAM_ACTIVE = threading.Event()
DEVICE_INFO = {}     # 设备/内参/外参缓存（由采集线程启动后填充一次）


# ---------------------------------------------------------------------------
# RealSense 采集线程
# ---------------------------------------------------------------------------
def rs_capture_loop():
    global BUFFER
    while True:
        try:
            ctx = rs.context()
            if len(ctx.devices) == 0:
                STREAM_ACTIVE.clear()
                print("[rs] no device, retry in 5s", flush=True)
                time.sleep(5)
                continue
            pipe = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.color, ARGS.width, ARGS.height, rs.format.bgr8, ARGS.fps)
            cfg.enable_stream(rs.stream.depth, ARGS.width, ARGS.height, rs.format.z16, ARGS.fps)
            profile = pipe.start(cfg)
            depth_sensor = profile.get_device().first_depth_sensor()
            color_sensor = profile.get_device().query_sensors()[1]
            align = rs.align(rs.stream.color)
            print("[rs] stream started %dx%d@%d" % (ARGS.width, ARGS.height, ARGS.fps), flush=True)

            # ---- 设备 / 内参 / 外参（从当前运行管线读取一次，避免二次打开设备冲突）----
            try:
                dev = profile.get_device()
                def gi(info):
                    try:
                        return dev.get_info(info)
                    except Exception:
                        return None
                streams = profile.get_streams()
                def intrinsics(s):
                    if s is None:
                        return None
                    i = s.as_video_stream_profile().get_intrinsics()
                    return {"width": i.width, "height": i.height, "fx": i.fx, "fy": i.fy,
                            "ppx": i.ppx, "ppy": i.ppy, "model": str(i.model),
                            "coeffs": list(i.coeffs)}
                color_stream = next((s for s in streams if s.stream_type() == rs.stream.color), None)
                depth_stream = next((s for s in streams if s.stream_type() == rs.stream.depth), None)
                def extrinsics(a, b):
                    try:
                        e = a.get_extrinsics_to(b)
                        return {"rotation": [round(x, 6) for x in e.rotation],
                                "translation": [round(x, 6) for x in e.translation]}
                    except Exception:
                        return None
                DEVICE_INFO.clear()
                DEVICE_INFO.update({
                    "device": {
                        "name": gi(rs.camera_info.name),
                        "serial_number": gi(rs.camera_info.serial_number),
                        "firmware_version": gi(rs.camera_info.firmware_version),
                        "usb_type": gi(rs.camera_info.usb_type_descriptor),
                        "product_line": gi(rs.camera_info.product_line),
                    },
                    "depth_intrinsics": intrinsics(depth_stream),
                    "color_intrinsics": intrinsics(color_stream),
                    "depth_to_color_extrinsics": extrinsics(depth_stream, color_stream),
                    "color_to_depth_extrinsics": extrinsics(color_stream, depth_stream),
                    "notes": "extrinsics 为相机内部标定；相对机器狗 base_link 的外参尚未标定",
                })
            except Exception as e:
                print("[rs] device info warn:", e, flush=True)

            # 传感器信息（启动后读取一次；精选常用选项，避免枚举不支持项）
            sensor_info = {}
            CURATED_OPTIONS = [
                "enable_auto_exposure", "exposure", "gain", "laser_power",
                "white_balance", "enable_auto_white_balance", "frames_queue_size",
                "depth_units", "visual_preset", "emitter_enabled",
            ]
            try:
                for sname, sens in (("depth", depth_sensor), ("color", color_sensor)):
                    opts = {}
                    for oname in CURATED_OPTIONS:
                        o = getattr(rs.option, oname, None)
                        if o is None:
                            continue
                        try:
                            opts[oname] = {
                                "value": sens.get_option(o),
                                "min": sens.get_option_range(o).min,
                                "max": sens.get_option_range(o).max,
                                "step": sens.get_option_range(o).step,
                            }
                        except Exception:
                            pass  # 该传感器不支持此选项
                    sensor_info[sname] = opts
            except Exception as e:
                print("[rs] sensor info warn:", e, flush=True)

            STREAM_ACTIVE.set()
            while True:
                frames = pipe.wait_for_frames()
                aligned = align.process(frames)
                color = aligned.get_color_frame()
                depth = aligned.get_depth_frame()
                if not color or not depth:
                    continue

                cimg = np.asanyarray(color.get_data())
                dimg = np.asanyarray(depth.get_data())  # uint16, mm

                # ---- 彩色 JPEG ----
                ok, cjpg = cv2.imencode(".jpg", cimg, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if not ok:
                    continue

                # ---- 深度伪彩 + 中心距离叠加 ----
                z_m = dimg.astype(np.float32) / 1000.0
                z_m = np.clip(z_m, 0.0, ARGS.max_depth)
                d8 = (z_m / ARGS.max_depth * 255.0).astype(np.uint8)
                djpg = cv2.applyColorMap(d8, cv2.COLORMAP_JET)
                cx, cy = dimg.shape[1] // 2, dimg.shape[0] // 2
                center_mm = int(dimg[cy, cx])
                cv2.circle(djpg, (cx, cy), 4, (255, 255, 255), -1)
                cv2.putText(djpg, "%.2f m" % (center_mm / 1000.0), (cx + 10, cy - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                ok, djpg_b = cv2.imencode(".jpg", djpg, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if not ok:
                    continue

                # ---- 原始 16-bit 深度 PNG ----
                ok, d16png = cv2.imencode(".png", dimg)
                if not ok:
                    continue

                stamp = time.time()
                frame_id = str(frames.frame_number)
                device_timestamp_ms = _safe_frame_timestamp_ms(color)
                color_intr = ((DEVICE_INFO.get("color_intrinsics") or {}) if DEVICE_INFO else {})
                info = {
                    "stamp": stamp,
                    "frame_number": frames.frame_number,
                    "frame_id": frame_id,
                    "device_timestamp_ms": device_timestamp_ms,
                    "color": {"width": color.get_width(), "height": color.get_height(),
                              "fps": ARGS.fps, "format": "bgr8"},
                    "depth": {"width": depth.get_width(), "height": depth.get_height(),
                              "fps": ARGS.fps, "format": "z16(mm)"},
                    "center_depth_mm": center_mm,
                    "depth_stats_m": {
                        "min": float(np.min(z_m)), "max": float(np.max(z_m)),
                        "mean": float(np.mean(z_m)),
                    },
                    "intrinsics": {
                        "fx": float(color_intr.get("fx", 0.0)),
                        "fy": float(color_intr.get("fy", 0.0)),
                        "cx": float(color_intr.get("ppx", 0.0)),
                        "cy": float(color_intr.get("ppy", 0.0)),
                        "width": int(color.get_width()),
                        "height": int(color.get_height()),
                    },
                    "depth_aligned_to_color": True,
                    "depth_unit_m": 0.001,
                    "sensor": sensor_info,
                }
                rgbd_frame = {
                    "frame_id": frame_id,
                    "device_timestamp_ms": device_timestamp_ms,
                    "host_timestamp": stamp,
                    "color_url": f"/rgbd/frame/{frame_id}/color.jpg",
                    "depth_url": f"/rgbd/frame/{frame_id}/depth.png",
                    "depth_aligned_to_color": True,
                    "depth_unit_m": 0.001,
                    "width": int(color.get_width()),
                    "height": int(color.get_height()),
                    "intrinsics": {
                        "fx": float(color_intr.get("fx", 0.0)),
                        "fy": float(color_intr.get("fy", 0.0)),
                        "cx": float(color_intr.get("ppx", 0.0)),
                        "cy": float(color_intr.get("ppy", 0.0)),
                    },
                    "info": info,
                    "color_jpg": cjpg.tobytes(),
                    "depth_raw_png": d16png.tobytes(),
                }
                with BUFFER_LOCK:
                    BUFFER.update({
                        "color_jpg": cjpg.tobytes(),
                        "depth_jpg": djpg_b.tobytes(),
                        "depth_raw_png": d16png.tobytes(),
                        "color_shape": cimg.shape,
                        "depth_shape": dimg.shape,
                        "stamp": stamp,
                        "info": info,
                    })
                    FRAME_CACHE.append(rgbd_frame)
        except rs.error as e:
            print("[rs] realsense error: %s, retry in 5s" % e, flush=True)
            STREAM_ACTIVE.clear()
            time.sleep(5)
        except Exception:
            print("[rs] unexpected: %s" % traceback.format_exc(), flush=True)
            STREAM_ACTIVE.clear()
            time.sleep(5)


# ---------------------------------------------------------------------------
# 设备/内参/外参信息
# ---------------------------------------------------------------------------
def _safe_frame_timestamp_ms(frame):
    """Best-effort device timestamp in milliseconds; never blocks the loop."""
    try:
        value = float(frame.get_timestamp())
        return value if value == value else 0.0  # NaN guard
    except Exception:
        return 0.0


def build_device_info():
    """返回采集线程缓存的设备级信息（不二次打开设备，避免 Device busy）"""
    if DEVICE_INFO:
        return DEVICE_INFO
    return {"loading": True, "note": "capture thread has not initialized yet"}


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------
HTML_PAGE = None

def get_html_page():
    global HTML_PAGE
    if HTML_PAGE is None:
        HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Go2-W RealSense D435 RGB-D</title>
<style>
body{background:#101418;color:#e8e8e8;font-family:monospace;margin:0;padding:16px}
h1{font-size:18px;color:#4fc3f7}
.grid{display:flex;gap:16px;flex-wrap:wrap}
.panel{background:#1a2026;border:1px solid #2a323a;border-radius:8px;padding:10px;flex:1;min-width:340px}
.panel h2{font-size:13px;color:#81c784;margin:4px 0 8px}
img{width:100%;max-width:640px;border-radius:4px;background:#000}
pre{font-size:11px;line-height:1.5;max-height:420px;overflow:auto;white-space:pre-wrap}
button{background:#1565c0;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px}
button:hover{background:#1976d2}
.status{font-size:12px;margin:8px 0}
#stamp{color:#ffb74d}
a{color:#4fc3f7}
</style></head><body>
<h1>Go2-W &rsaquo; Intel RealSense D435 (192.168.123.18:8080)</h1>
<div class="status">彩色 <span id="s_color">-</span> &nbsp; 深度 <span id="s_depth">-</span> &nbsp;
中心距离 <span id="s_center">-</span> &nbsp; 帧龄 <span id="s_age">-</span></div>
<div class="grid">
  <div class="panel"><h2>&#9654; COLOR (MJPEG)</h2><img id="img_color" src="/color"></div>
  <div class="panel"><h2>&#9654; DEPTH (jet, aligned to color)</h2><img id="img_depth" src="/depth"></div>
</div>
<div style="margin-top:12px">
  <button onclick="snap()">&#128247; 保存快照到机器狗</button>
  <span id="snap_res" style="font-size:12px;margin-left:10px"></span>
</div>
<div class="grid" style="margin-top:12px">
  <div class="panel"><h2>&#8505; 设备 / 内参 / 外参 / 传感器</h2><pre id="info">loading...</pre></div>
  <div class="panel"><h2>&#128193; 快照文件</h2><pre id="snaps">(点上方按钮保存)</pre></div>
</div>
<script>
function snap(){
  fetch('/snapshot').then(r=>r.json()).then(j=>{
    document.getElementById('snap_res').textContent = j.message || JSON.stringify(j);
    refreshSnaps();
  });
}
function refreshSnaps(){
  fetch('/snap/?list=1').then(r=>r.json()).then(j=>{
    let t = j.files ? j.files.map(f=>'<a href="/snap/'+f+'">'+f+'</a>').join('\\n') : '(空)';
    document.getElementById('snaps').innerHTML = t;
  });
}
setInterval(()=>{
  fetch('/info.json?_='+Date.now()).then(r=>r.json()).then(j=>{
    if(j.error){document.getElementById('info').textContent=JSON.stringify(j,null,1);return;}
    document.getElementById('info').textContent = JSON.stringify(j,null,1);
    if(j.color) document.getElementById('s_color').textContent = j.color.width+'x'+j.color.height+'@'+j.color.fps+'fps';
    if(j.depth) document.getElementById('s_depth').textContent = j.depth.width+'x'+j.depth.height+'@'+j.depth.fps+'fps';
    document.getElementById('s_center').textContent = (j.center_depth_mm/1000).toFixed(3)+' m';
  });
  fetch('/health?_='+Date.now()).then(r=>r.json()).then(j=>{
    document.getElementById('s_age').textContent = 'age '+j.age_s.toFixed(2)+'s / fps '+j.fps.toFixed(1);
  });
}, 1000);
refreshSnaps();
</script></body></html>"""
    return HTML_PAGE


def mjpeg_headers():
    return (b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n\r\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if ARGS and ARGS.verbose:
            print("[http] " + fmt % args, flush=True)

    # ---- helpers ----
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _stream_mjpeg(self, key, snap_key):
        """从共享缓存持续发送最新一帧 MJPEG"""
        self.wfile.write(mjpeg_headers())
        self.wfile.flush()
        last = -1
        while True:
            with BUFFER_LOCK:
                data = BUFFER.get(key)
                stamp = BUFFER.get("stamp", 0)
            if data is not None and stamp != last:
                last = stamp
                chunk = (b"--frame\r\nContent-Type: image/jpeg\r\n"
                         b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n")
                self.wfile.write(chunk)
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            else:
                time.sleep(0.01)

    # ---- routes ----
    def do_POST(self):
        # 与 GET 相同处理（快照按钮兼容 POST 调用）
        self.do_GET()

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        try:
            if path in ("/", "/index.html"):
                page = get_html_page().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif path == "/rgbd/latest.json":
                self._send_rgbd_latest()
            elif path.startswith("/rgbd/frame/"):
                self._send_rgbd_frame(path)
            elif path == "/color":
                self._stream_mjpeg("color_jpg", "stamp")
            elif path == "/depth":
                self._stream_mjpeg("depth_jpg", "stamp")
            elif path == "/depth_raw":
                with BUFFER_LOCK:
                    data = BUFFER.get("depth_raw_png")
                if data:
                    self._send_bytes(data, "image/png")
                else:
                    self._send_json({"error": "no frame yet"}, 503)
            elif path == "/info.json":
                with BUFFER_LOCK:
                    info = BUFFER.get("info")
                dev = build_device_info()
                out = dict(info or {})
                out.update({"device_info": dev})
                self._send_json(out)
            elif path == "/health":
                with BUFFER_LOCK:
                    stamp = BUFFER.get("stamp", 0)
                age = time.time() - stamp if stamp else 999
                self._send_json({
                    "ok": STREAM_ACTIVE.is_set() and age < 5,
                    "streaming": STREAM_ACTIVE.is_set(),
                    "age_s": round(age, 3),
                    "fps": round(1.0 / age, 2) if 0 < age < 5 else 0,
                })
            elif path == "/snapshot":
                self._do_snapshot()
            elif path == "/snap/":
                self._send_snap_list()
            elif path.startswith("/snap/"):
                self._send_snap_file(path[len("/snap/"):])
            else:
                self._send_json({"error": "not found: " + path}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self._send_json({"error": traceback.format_exc()}, 500)
            except Exception:
                pass

    def _rgbd_meta(self, frame: dict) -> dict:
        """Return the JSON-safe metadata view of one atomic RGB-D frame."""
        info = frame.get("info") or {}
        return {
            "frame_id": frame["frame_id"],
            "device_timestamp_ms": frame.get("device_timestamp_ms"),
            "host_timestamp": frame.get("host_timestamp"),
            "color_url": frame.get("color_url"),
            "depth_url": frame.get("depth_url"),
            "depth_aligned_to_color": bool(frame.get("depth_aligned_to_color", True)),
            "depth_unit_m": frame.get("depth_unit_m", 0.001),
            "width": frame.get("width"),
            "height": frame.get("height"),
            "intrinsics": frame.get("intrinsics") or {},
            "health": {
                "age_s": round(max(0.0, time.time() - float(frame.get("host_timestamp", 0.0))), 3),
                "streaming": STREAM_ACTIVE.is_set(),
                "center_depth_mm": info.get("center_depth_mm"),
                "depth_stats_m": info.get("depth_stats_m"),
            },
        }

    def _send_rgbd_latest(self):
        with BUFFER_LOCK:
            if not FRAME_CACHE:
                self._send_json({"error": "no frame yet"}, 503)
                return
            frame = FRAME_CACHE[-1]
        self._send_json(self._rgbd_meta(frame))

    def _send_rgbd_frame(self, path: str):
        parts = path[len("/rgbd/frame/"):].split("/", 1)
        if len(parts) != 2:
            self._send_json({"error": "expected /rgbd/frame/<id>/color.jpg|depth.png"}, 400)
            return
        frame_id, name = parts
        if name not in {"color.jpg", "depth.png"}:
            self._send_json({"error": "only color.jpg and depth.png are served"}, 404)
            return
        with BUFFER_LOCK:
            frame = next((item for item in FRAME_CACHE if item["frame_id"] == frame_id), None)
        if frame is None:
            self._send_json({"error": f"frame {frame_id} not in cache"}, 404)
            return
        data = frame.get("color_jpg" if name == "color.jpg" else "depth_raw_png")
        self._send_bytes(data, "image/jpeg" if name == "color.jpg" else "image/png")

    def _do_snapshot(self):
        with BUFFER_LOCK:
            if "color_jpg" not in BUFFER:
                self._send_json({"error": "no frame yet"}, 503)
                return
            color = BUFFER["color_jpg"]
            depth = BUFFER["depth_jpg"]
            raw = BUFFER["depth_raw_png"]
            info = BUFFER["info"]
        ts = time.strftime("%Y%m%d_%H%M%S")
        d = os.path.join(ARGS.snap_dir, ts)
        os.makedirs(d, exist_ok=True)
        paths = {
            "color.jpg": color,
            "depth.jpg": depth,
            "depth_raw.png": raw,
            "info.json": json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"),
        }
        for name, data in paths.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(data)
        self._send_json({"ok": True, "dir": d, "message": "saved: " + d,
                         "files": sorted(paths.keys())})

    def _send_snap_list(self):
        try:
            names = sorted(os.listdir(ARGS.snap_dir))
        except OSError:
            names = []
        files = []
        for n in names:
            p = os.path.join(ARGS.snap_dir, n)
            if os.path.isfile(p):
                files.append(n)
            else:
                files.extend(sorted(os.listdir(p)))
        self._send_json({"snapshots": names, "files": files})

    def _send_snap_file(self, rel):
        rel = os.path.normpath(rel).lstrip("/")
        fp = os.path.join(ARGS.snap_dir, rel)
        if not fp.startswith(os.path.realpath(ARGS.snap_dir)) or not os.path.isfile(fp):
            self._send_json({"error": "no such file"}, 404)
            return
        ext = rel.rsplit(".", 1)[-1].lower()
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                 "json": "application/json"}.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            self._send_bytes(f.read(), ctype)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="RealSense D435 RGB-D stream server")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--max-depth", type=float, default=6.0, help="depth colormap range (m)")
    ap.add_argument("--snap-dir", type=str,
                    default=os.path.expanduser("~/realsense_snapshots"))
    ap.add_argument("--verbose", action="store_true")
    ARGS = ap.parse_args()
    os.makedirs(ARGS.snap_dir, exist_ok=True)

    t = threading.Thread(target=rs_capture_loop, daemon=True)
    t.start()

    srv = ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler)
    print("[http] listening on 0.0.0.0:%d" % ARGS.port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
