/* Lightweight dependency-free 3D point-cloud viewer for plain_slam.
 * The browser only consumes the display snapshot API; it never publishes a
 * command and cannot affect motion or ROS frame authority.
 */
(function () {
  "use strict";

  var canvas = document.getElementById("slam-map-3d");
  if (!canvas) return;
  var context = canvas.getContext("2d", { alpha: false });
  var meta = document.getElementById("slam3d-meta");
  var state = document.getElementById("slam3d-state");
  var capacity = document.getElementById("slam3d-capacity");
  var previewToggle = document.getElementById("slam3d-preview-toggle");
  var light = document.getElementById("light-slam");
  var points = [];
  var objects = [];
  var preview = [];
  var previewDrawable = false;
  var bounds = null;
  var focusBounds = null;
  var yaw = -0.72;
  // 默认接近正俯视。原来的 0.63 rad（36°）斜视下，投影式
  // vertical = cos(pitch)*dz - sin(pitch)*ry 里 sin(0.63)=0.59 把"远处的低点"
  // 和"近处的高点"挤进同一片屏幕区域 —— 用户 2026-09-11 描述的"地图像把很多次
  // 建的图重叠在一起、角度没对齐"就是这个，不是数据里有重影（分层渲染已证）。
  var pitch = 1.35;
  var zoom = 1.0;
  var dragging = false;
  var lastX = 0;
  var lastY = 0;
  var requestInFlight = false;

  function setLight(kind) {
    if (light) light.className = "light " + kind;
  }

  function resize() {
    var ratio = Math.min(2, window.devicePixelRatio || 1);
    var width = Math.max(320, Math.round(canvas.clientWidth * ratio));
    var height = Math.max(220, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  function centerAndSpan() {
    // focus_bounds（轨迹 bbox + 边距，由 webui_map_snapshot_writer.py 给）是
    // "狗真正建过图的区域"。用它定画幅，房间占约 60%；用点云 bbox 只有 26%，
    // 剩下的画幅全被 12 m 内的走廊占掉，细节看不清。
    var frame = focusBounds && focusBounds.min ? focusBounds : bounds;
    var min = frame && frame.min || [-5, -5, -1];
    var max = frame && frame.max || [5, 5, 2];
    var center = [
      (Number(min[0]) + Number(max[0])) / 2,
      (Number(min[1]) + Number(max[1])) / 2,
      (Number(min[2]) + Number(max[2])) / 2
    ];
    var span = Math.max(
      2, Number(max[0]) - Number(min[0]),
      Number(max[1]) - Number(min[1]),
      (Number(max[2]) - Number(min[2])) * 1.8
    );
    return { center: center, span: span };
  }

  function project(point, center, scale) {
    var dx = Number(point[0]) - center[0];
    var dy = Number(point[1]) - center[1];
    var dz = Number(point[2]) - center[2];
    var cy = Math.cos(yaw), sy = Math.sin(yaw);
    var cp = Math.cos(pitch), sp = Math.sin(pitch);
    var rx = cy * dx - sy * dy;
    var ry = sy * dx + cy * dy;
    var vertical = cp * dz - sp * ry;
    var depth = sp * dz + cp * ry;
    return [canvas.width * 0.5 + rx * scale, canvas.height * 0.54 - vertical * scale, depth];
  }

  function heightColor(z, zMin, zMax, alpha) {
    var t = zMax > zMin ? Math.max(0, Math.min(1, (z - zMin) / (zMax - zMin))) : 0.5;
    var r = Math.round(30 + 220 * t);
    var g = Math.round(210 - 75 * Math.abs(t - 0.45));
    var b = Math.round(250 - 190 * t);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function drawObjectDot(item, view, scale) {
    var p = project([Number(item.x), Number(item.y),
                     item.z == null ? 0.5 : Number(item.z)], view.center, scale);
    if (p[0] < -40 || p[0] > canvas.width + 40 ||
        p[1] < -40 || p[1] > canvas.height + 40) return;
    context.beginPath();
    context.arc(p[0], p[1], 5.5, 0, Math.PI * 2);
    context.fillStyle = "rgba(239, 68, 68, 0.95)";
    context.fill();
    context.lineWidth = 1.3;
    context.strokeStyle = "rgba(255, 255, 255, 0.9)";
    context.stroke();
    if (item.label) {
      context.font = "12px system-ui, sans-serif";
      context.fillStyle = "rgba(255, 243, 243, 0.96)";
      context.fillText(item.label, p[0] + 9, p[1] - 7);
    }
  }

  function drawObjectBox(item, view, scale) {
    var cx = Number(item.x), cy = Number(item.y), cz = Number(item.z);
    var hw = Number(item.size[0]) / 2;
    var hd = Number(item.size[1]) / 2;
    var hh = Number(item.size[2]) / 2;
    if (hh <= 0) hh = 0.12;  // 同深度平面无高度时给一个最小可见厚度
    var corners = [
      [cx - hw, cy - hd, cz - hh], [cx + hw, cy - hd, cz - hh],
      [cx + hw, cy + hd, cz - hh], [cx - hw, cy + hd, cz - hh],
      [cx - hw, cy - hd, cz + hh], [cx + hw, cy - hd, cz + hh],
      [cx + hw, cy + hd, cz + hh], [cx - hw, cy + hd, cz + hh]
    ].map(function (c) { return project(c, view.center, scale); });
    var edges = [
      [0, 1], [1, 2], [2, 3], [3, 0],
      [4, 5], [5, 6], [6, 7], [7, 4],
      [0, 4], [1, 5], [2, 6], [3, 7]
    ];
    context.lineWidth = 1.5;
    context.strokeStyle = "rgba(56, 189, 248, 0.92)";
    context.beginPath();
    edges.forEach(function (e) {
      var a = corners[e[0]], b = corners[e[1]];
      if (a[0] < -200 || a[0] > canvas.width + 200 ||
          b[0] < -200 || b[0] > canvas.width + 200) return;
      context.moveTo(a[0], a[1]);
      context.lineTo(b[0], b[1]);
    });
    context.stroke();
    // 顶部中心小标记点，便于定位。
    var top = corners[5];
    context.beginPath();
    context.arc(top[0], top[1], 3, 0, Math.PI * 2);
    context.fillStyle = "rgba(56, 189, 248, 0.95)";
    context.fill();
    if (item.label) {
      var conf = item.confidence != null ? " " + Number(item.confidence).toFixed(2) : "";
      context.font = "12px system-ui, sans-serif";
      context.fillStyle = "rgba(224, 242, 254, 0.97)";
      context.fillText(item.label + conf, top[0] + 7, top[1] - 6);
    }
  }

  function drawGrid(center, scale, span) {
    var half = Math.max(2, Math.ceil(span / 2));
    var step = span > 25 ? 5 : (span > 10 ? 2 : 1);
    context.lineWidth = Math.max(1, canvas.width / 1200);
    context.strokeStyle = "rgba(56, 189, 248, 0.13)";
    for (var value = -half; value <= half; value += step) {
      [[[-half, value, 0], [half, value, 0]], [[value, -half, 0], [value, half, 0]]].forEach(function (line) {
        var a = project([center[0] + line[0][0], center[1] + line[0][1], 0], center, scale);
        var b = project([center[0] + line[1][0], center[1] + line[1][1], 0], center, scale);
        context.beginPath(); context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.stroke();
      });
    }
  }

  function render() {
    resize();
    context.fillStyle = "#070b10";
    context.fillRect(0, 0, canvas.width, canvas.height);
    var view = centerAndSpan();
    var scale = Math.min(canvas.width, canvas.height) * 0.78 / view.span * zoom;
    drawGrid(view.center, scale, view.span);
    var size = Math.max(1.1, Math.min(3.2, canvas.width / 720));
    if (points.length) {
      var minZ = bounds && bounds.min ? Number(bounds.min[2]) : -1;
      var maxZ = bounds && bounds.max ? Number(bounds.max[2]) : 2;
      // Depth buckets preserve enough occlusion to read room geometry without
      // an O(n log n) sort for every interaction frame.
      var buckets = [[], [], [], [], [], []];
      points.forEach(function (point) {
        var p = project(point, view.center, scale);
        var bucket = Math.max(0, Math.min(5, Math.floor((p[2] / view.span + 0.5) * 6)));
        buckets[bucket].push([p[0], p[1], Number(point[2])]);
      });
      buckets.forEach(function (bucket, index) {
        var alpha = 0.48 + index * 0.08;
        bucket.forEach(function (p) {
          if (p[0] < 0 || p[0] > canvas.width || p[1] < 0 || p[1] > canvas.height) return;
          context.fillStyle = heightColor(p[2], minZ, maxZ, alpha);
          context.fillRect(p[0], p[1], size, size);
        });
      });
    }
    // 语义物体标注层：来自 Dataset D 的三维定位结果（快照的 objects 字段）。
    // 有 size（3D box）时画 AABB 线框 + 标签；否则退化为红点。
    if (objects.length) {
      objects.forEach(function (item) {
        var hasSize = Array.isArray(item.size) && item.size.length === 3;
        if (hasSize) {
          drawObjectBox(item, view, scale);
        } else {
          drawObjectDot(item, view, scale);
        }
      });
    }
    // 当前 scan 是调试图层：颜色不同、画在上面，永远不进永久地图。
    if (previewDrawable && preview.length && previewToggle && previewToggle.checked) {
      context.fillStyle = "rgba(251, 191, 36, 0.85)";
      preview.forEach(function (point) {
        var p = project(point, view.center, scale);
        if (p[0] < 0 || p[0] > canvas.width || p[1] < 0 || p[1] > canvas.height) return;
        context.fillRect(p[0], p[1], size * 1.3, size * 1.3);
      });
    }
  }

  function count(value) {
    return Number(value || 0).toLocaleString();
  }

  function age(value) {
    return value == null ? "--" : Number(value).toFixed(1) + "s";
  }

  function applySnapshot(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.points)) {
      state.textContent = "3D 点云接口返回了无法解析的快照";
      state.className = "slam3d-state error";
      setLight("red");
      return;
    }
    var health = snapshot.mapping_health || "UNKNOWN";
    var reason = snapshot.health_reason || snapshot.reason || "";
    points = snapshot.points;
    objects = Array.isArray(snapshot.objects) ? snapshot.objects : [];
    bounds = snapshot.bounds || bounds;
    focusBounds = snapshot.focus_bounds || null;
    var scan = snapshot.preview || {};
    // §9.1：pslam_odom 的 scan 和 pslam_map 的地图没有带时间戳的变换之前不许
    // 画在同一个坐标系里，所以只有两个 frame 相同时才允许显示调试层。
    previewDrawable = !!scan.frame_id && scan.frame_id === snapshot.canonical_frame;
    preview = previewDrawable && Array.isArray(scan.points) ? scan.points : [];
    if (!snapshot.available || !points.length) {
      state.textContent = "等待固定世界地图 · " + health + (reason ? " · " + reason : "");
      state.className = "slam3d-state error";
      setLight("red");
    } else {
      state.textContent = (snapshot.fresh ? "LIVE" : "STALE") + " · " + health +
        " · 地图 " + age(snapshot.map_age_seconds) + "前 · scan " +
        age(snapshot.preview_age_seconds) + "前" + (reason ? " · " + reason : "");
      state.className = "slam3d-state" + (snapshot.fresh && health === "HEALTHY" ? "" : " stale");
      setLight(snapshot.fresh && health === "HEALTHY" ? "green" : "yellow");
    }
    meta.textContent = count(snapshot.web_display_points) + " 显示点 · " +
      (snapshot.canonical_frame || "pslam_map") + " · voxel " +
      Number(snapshot.global_voxel_size_m || 0).toFixed(2) + "m · session " +
      count(snapshot.mapping_session_id) + " · r" + count(snapshot.map_revision);
    var extent = snapshot.map_extent_m || [0, 0, 0];
    if (capacity) {
      capacity.textContent = "SLAM 源点 " + count(snapshot.source_map_points) +
        " → 全局缓存体素 " + count(snapshot.global_cached_voxels) + "/" +
        count(snapshot.max_global_voxels) + " → 网页显示 " +
        count(snapshot.web_display_points) + "/" + count(snapshot.max_web_points) +
        " · 地图范围 " + Number(extent[0] || 0).toFixed(1) + "×" +
        Number(extent[1] || 0).toFixed(1) + "×" + Number(extent[2] || 0).toFixed(1) + "m" +
        (snapshot.capacity_limited ? " · 已整体降分辨率" : "") +
        " · 当前 scan " + count(scan.point_count) + " 点(" + (scan.frame_id || "--") + ")" +
        (previewDrawable ? "" : "，与地图坐标系不同故不叠加显示") +
        (snapshot.last_rejected_reason ? " · 最近拒绝 " + snapshot.last_rejected_reason : "");
      capacity.className = "slam3d-capacity" +
        (health === "HEALTHY" ? "" : " degraded");
    }
    render();
  }

  function poll() {
    if (requestInFlight) return;
    requestInFlight = true;
    fetch("/api/slam/map3d", { cache: "no-store" })
      .then(function (response) { return response.json(); })
      .then(applySnapshot)
      .catch(function () {
        state.textContent = "3D 点云接口暂不可用";
        state.className = "slam3d-state error";
        setLight("red");
      })
      .finally(function () { requestInFlight = false; });
  }

  canvas.addEventListener("pointerdown", function (event) {
    dragging = true; lastX = event.clientX; lastY = event.clientY;
    canvas.classList.add("dragging"); canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", function (event) {
    if (!dragging) return;
    yaw += (event.clientX - lastX) * 0.008;
    pitch = Math.max(-1.35, Math.min(1.35, pitch + (event.clientY - lastY) * 0.006));
    lastX = event.clientX; lastY = event.clientY; render();
  });
  canvas.addEventListener("pointerup", function () { dragging = false; canvas.classList.remove("dragging"); });
  canvas.addEventListener("pointercancel", function () { dragging = false; canvas.classList.remove("dragging"); });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    zoom = Math.max(0.25, Math.min(8, zoom * (event.deltaY > 0 ? 0.88 : 1.14)));
    render();
  }, { passive: false });
  canvas.addEventListener("dblclick", function () { yaw = -0.72; pitch = 0.63; zoom = 1; render(); });
  if (previewToggle) previewToggle.addEventListener("change", render);
  window.addEventListener("resize", render);
  render();
  poll();
  window.setInterval(poll, 850);
})();
