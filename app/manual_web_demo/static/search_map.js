/* SearchMapRenderer: SVG topological exploration map (plan book §52-§54).
 *
 * Renders nodes / edges / robot heading from the live ExplorationGraph
 * snapshot.  Pure display: coordinates are display-only (pose if available,
 * otherwise a heading-sector polar layout) and are NEVER used for robot
 * navigation.
 */
(function () {
  "use strict";

  var STATE_SYMBOLS = {
    CURRENT: "▲",
    VISITED: "●",
    OBSERVED: "○",
    UNSEEN: "○",
    SEMANTIC_INTEREST: "★",
    NEGATIVE: "◌",
    UNREACHABLE: "✕",
    TARGET_CANDIDATE: "◎",
    TARGET_CONFIRMED: "✓",
  };

  var STATE_COLORS = {
    CURRENT: "#38bdf8",
    VISITED: "#34d399",
    OBSERVED: "#8b95a3",
    UNSEEN: "#4b5563",
    SEMANTIC_INTEREST: "#fbbf24",
    NEGATIVE: "#6b7280",
    UNREACHABLE: "#f87171",
    TARGET_CANDIDATE: "#c084fc",
    TARGET_CONFIRMED: "#34d399",
  };

  var WIDTH = 600;
  var HEIGHT = 400;
  var PAD = 34;

  function SearchMapRenderer(svg, detailEl) {
    this.svg = svg;
    this.detailEl = detailEl;
    this.data = null;
  }

  SearchMapRenderer.prototype.render = function (mapData, spatialData) {
    this.data = mapData || this.data || {};
    var map = this.data;
    var nodes = Array.isArray(map.nodes) ? map.nodes.slice() : [];
    var edges = Array.isArray(map.edges) ? map.edges.slice() : [];
    var robot = map.robot || {};
    var currentId = map.current_node_id || null;

    // Merge PlaceGraph places into the node set so the SVG viewBox and base
    // topology include real spatial Places (plan §91-§95).
    var spatial = spatialData || null;
    if (spatial && spatial.place_graph && Array.isArray(spatial.place_graph.places)) {
      spatial.place_graph.places.forEach(function (place) {
        var node = placeToNode(place);
        if (!nodes.some(function (n) { return n.node_id === node.node_id; })) {
          nodes.push(node);
        }
      });
    }

    var layout = computeLayout(nodes, robot);

    // fit
    var bounds = fitBounds(layout, robot);
    var vb = boundsToViewBox(bounds);
    this.svg.setAttribute("viewBox", vb);

    var ns = "http://www.w3.org/2000/svg";
    this.svg.textContent = "";

    // grid hint
    drawGrid(this.svg, ns, bounds);

    // edges
    edges.forEach(function (edge) {
      var a = layout[edge.source_node_id];
      var b = layout[edge.target_node_id];
      if (!a || !b) return;
      var line = document.createElementNS(ns, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      var failed = edge.navigation_result && edge.navigation_result !== "succeeded";
      line.setAttribute("stroke", failed ? "#f87171" : "#2b333e");
      line.setAttribute("stroke-width", failed ? 1.6 : 1);
      line.setAttribute("stroke-dasharray", failed ? "4 3" : "none");
      this.svg.appendChild(line);
    }.bind(this));

    // nodes
    nodes.forEach(function (node) {
      var pos = layout[node.node_id];
      if (!pos) return;
      var g = document.createElementNS(ns, "g");
      g.setAttribute("transform", "translate(" + pos.x + "," + pos.y + ")");
      var state = resolveState(node, currentId);
      var color = STATE_COLORS[state] || "#8b95a3";
      var symbol = STATE_SYMBOLS[state] || "●";
      var r = node.node_id === currentId ? 12 : 7;
      var circle = document.createElementNS(ns, "circle");
      circle.setAttribute("r", r);
      circle.setAttribute("fill", color);
      circle.setAttribute("fill-opacity", "0.18");
      circle.setAttribute("stroke", color);
      circle.setAttribute("stroke-width", "1.5");
      g.appendChild(circle);
      var text = document.createElementNS(ns, "text");
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dominant-baseline", "central");
      text.setAttribute("fill", color);
      text.setAttribute("font-size", node.node_id === currentId ? "14" : "11");
      text.textContent = symbol;
      g.appendChild(text);
      var label = document.createElementNS(ns, "text");
      label.setAttribute("y", r + 12);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", "#8b95a3");
      label.setAttribute("font-size", "9");
      label.textContent = shortNodeLabel(node);
      g.appendChild(label);
      g.addEventListener("click", function (event) {
        event.stopPropagation();
        this.showDetail(node);
      }.bind(this));
      this.svg.appendChild(g);
    }.bind(this));

    // robot heading (arrow)
    if (robot && robot.x !== undefined && robot.y !== undefined) {
      var yaw = Number(robot.yaw || 0);
      var len = 22;
      var x2 = robot.x + len * Math.cos(yaw);
      var y2 = robot.y - len * Math.sin(yaw);
      var line = document.createElementNS(ns, "line");
      line.setAttribute("x1", robot.x); line.setAttribute("y1", robot.y);
      line.setAttribute("x2", x2); line.setAttribute("y2", y2);
      line.setAttribute("stroke", "#38bdf8");
      line.setAttribute("stroke-width", "2.5");
      line.setAttribute("marker-end", "url(#arrow)");
      this.svg.appendChild(line);
      var dot = document.createElementNS(ns, "circle");
      dot.setAttribute("cx", robot.x); dot.setAttribute("cy", robot.y);
      dot.setAttribute("r", "4"); dot.setAttribute("fill", "#38bdf8");
      this.svg.appendChild(dot);
    }

    // spatial overlays (frontiers / semantic objects / PSG regions / goal)
    if (spatial) {
      this.drawSpatialOverlay(spatial, layout, robot);
    }

    // arrow marker def
    var defs = document.createElementNS(ns, "defs");
    var marker = document.createElementNS(ns, "marker");
    marker.setAttribute("id", "arrow");
    marker.setAttribute("markerWidth", "6"); marker.setAttribute("markerHeight", "6");
    marker.setAttribute("refX", "4"); marker.setAttribute("refY", "3");
    marker.setAttribute("orient", "auto");
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M0,0 L6,3 L0,6 z");
    path.setAttribute("fill", "#38bdf8");
    marker.appendChild(path);
    defs.appendChild(marker);
    this.svg.insertBefore(defs, this.svg.firstChild);
  };

  SearchMapRenderer.prototype.drawSpatialOverlay = function (spatial, layout, robot) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = this.svg;

    // Occupancy / explored background (plan §92)
    var smap = spatial.spatial_map;
    if (smap && smap.origin && smap.resolution_m > 0) {
      var ox = smap.origin[0];
      var oy = smap.origin[1];
      var res = smap.resolution_m;
      var totalCells = (Array.isArray(smap.free) ? smap.free.length : 0) +
                       (Array.isArray(smap.occupied) ? smap.occupied.length : 0);
      if (totalCells > 0 && totalCells < 4000) {
        function cellRect(cell, fill) {
          var rect = document.createElementNS(ns, "rect");
          var x = ox + (cell[0] + 0.5) * res;
          var y = oy + (cell[1] + 0.5) * res;
          rect.setAttribute("x", x - res / 2);
          rect.setAttribute("y", -y - res / 2);
          rect.setAttribute("width", res);
          rect.setAttribute("height", res);
          rect.setAttribute("fill", fill);
          rect.setAttribute("opacity", "0.35");
          svg.appendChild(rect);
        }
        (smap.free || []).forEach(function (cell) { cellRect(cell, "#1e293b"); });
        (smap.occupied || []).forEach(function (cell) { cellRect(cell, "#f87171"); });
      }
    }

    // Frontiers (plan §94)
    var frontiers = Array.isArray(spatial.frontiers) ? spatial.frontiers : [];
    frontiers.forEach(function (f) {
      if (!f.position) return;
      var g = document.createElementNS(ns, "g");
      g.setAttribute("transform", "translate(" + f.position[0] + "," + (-f.position[1]) + ")");
      var circle = document.createElementNS(ns, "circle");
      circle.setAttribute("r", "7");
      circle.setAttribute("fill", "#fbbf24");
      circle.setAttribute("fill-opacity", "0.25");
      circle.setAttribute("stroke", "#fbbf24");
      circle.setAttribute("stroke-width", "1.5");
      g.appendChild(circle);
      var text = document.createElementNS(ns, "text");
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dominant-baseline", "central");
      text.setAttribute("fill", "#fbbf24");
      text.setAttribute("font-size", "8");
      text.textContent = f.frontier_id.replace("frontier_", "F").replace("relative_f_", "F");
      g.appendChild(text);
      svg.appendChild(g);
    });

    // Semantic objects with a real map position (plan §95)
    var objects = Array.isArray(spatial.semantic_objects) ? spatial.semantic_objects : [];
    objects.forEach(function (obj) {
      if (!obj.map_xyz) return;
      var x = obj.map_xyz[0];
      var y = -obj.map_xyz[1];
      var g = document.createElementNS(ns, "g");
      g.setAttribute("transform", "translate(" + x + "," + y + ")");
      var diamond = document.createElementNS(ns, "rect");
      diamond.setAttribute("x", "-4"); diamond.setAttribute("y", "-4");
      diamond.setAttribute("width", "8"); diamond.setAttribute("height", "8");
      diamond.setAttribute("transform", "rotate(45)");
      diamond.setAttribute("fill", obj.spatial_quality === "METRIC_RGBD" ? "#34d399" : "#38bdf8");
      diamond.setAttribute("fill-opacity", "0.6");
      g.appendChild(diamond);
      var label = document.createElementNS(ns, "text");
      label.setAttribute("y", "12");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", "#8b95a3");
      label.setAttribute("font-size", "8");
      label.textContent = String(obj.label || "").slice(0, 8);
      g.appendChild(label);
      svg.appendChild(g);
    });

    // PSG predicted regions (plan §96)
    var prior = spatial.psg_prior || {};
    var regions = Array.isArray(prior.region_hypotheses) ? prior.region_hypotheses : [];
    regions.forEach(function (region) {
      if (!region.center || region.state === "REJECTED") return;
      var circle = document.createElementNS(ns, "circle");
      circle.setAttribute("cx", region.center[0]);
      circle.setAttribute("cy", -region.center[1]);
      var radius = (region.radius_max_m || 1.0) * 40;
      circle.setAttribute("r", radius);
      circle.setAttribute("fill", "#c084fc");
      circle.setAttribute("fill-opacity", "0.08");
      circle.setAttribute("stroke", "#c084fc");
      circle.setAttribute("stroke-dasharray", "4 3");
      svg.appendChild(circle);
    });

    // Selected long-term goal (plan §97)
    var goal = spatial.long_term_goal;
    if (goal && goal.preferred_position) {
      var x = goal.preferred_position[0];
      var y = -goal.preferred_position[1];
      var g = document.createElementNS(ns, "g");
      g.setAttribute("transform", "translate(" + x + "," + y + ")");
      var star = document.createElementNS(ns, "text");
      star.setAttribute("text-anchor", "middle");
      star.setAttribute("dominant-baseline", "central");
      star.setAttribute("fill", "#f472b6");
      star.setAttribute("font-size", "16");
      star.textContent = "★";
      g.appendChild(star);
      svg.appendChild(g);
    }
  };

  SearchMapRenderer.prototype.showDetail = function (node) {
    if (!this.detailEl) return;
    var state = resolveState(node, this.data.current_node_id);
    var html =
      "<div><b>" + esc(node.node_id) + "</b> <span style='color:" +
      (STATE_COLORS[state] || "#8b95a3") + "'>" + esc(state) + "</span></div>" +
      "<div>时间 " + fmtTime(node.timestamp) + "</div>" +
      "<div>Pose 质量 " + esc(node.pose_quality || "unavailable") + "</div>" +
      "<div>访问次数 " + (node.visited_count || 0) + "</div>" +
      "<div>负证据 " + (node.negative_evidence_count || 0) + "</div>" +
      "<div>导航失败 " + (node.navigation_fail_count || 0) + "</div>" +
      "<div>目标匹配 " + esc(node.target_match_level || "none") + "</div>" +
      "<div>语义相关 " + (node.semantic_relevance || 0).toFixed(2) + "</div>" +
      "<div>信息增益 " + (node.information_gain || 0).toFixed(2) + "</div>" +
      "<div>Objects: " + esc((node.objects || []).join(", ") || "-") + "</div>";
    this.detailEl.innerHTML = html;
    this.detailEl.classList.remove("hidden");
  };

  // ------------------------------------------------------------------ //
  // helpers                                                            //
  // ------------------------------------------------------------------ //
  function resolveState(node, currentId) {
    if (node.node_id === currentId) return "CURRENT";
    var s = String(node.reachable_state || "").toUpperCase();
    if (STATE_COLORS[s]) return s;
    if (node.visited_count > 0) return "VISITED";
    return "OBSERVED";
  }

  function computeLayout(nodes, robot) {
    var layout = {};
    var hasPose = false;
    nodes.forEach(function (node) {
      var pose = node.pose || {};
      if (pose.x !== undefined && isFinite(pose.x) && pose.y !== undefined && isFinite(pose.y)) {
        layout[node.node_id] = { x: pose.x, y: -pose.y };
        hasPose = true;
      }
    });
    if (robot && robot.x !== undefined && robot.y !== undefined) hasPose = true;
    if (hasPose) return layout;
    // layout-only polar fallback by heading sector (display only)
    nodes.forEach(function (node) {
      var sector = node.heading_sector;
      var angle = ((sector == null ? 0 : sector) / 12) * 2 * Math.PI - Math.PI / 2;
      var radius = 60 + ((hashCode(node.node_id) % 3) * 24);
      layout[node.node_id] = {
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      };
    });
    return layout;
  }

  function fitBounds(layout, robot) {
    var xs = [];
    var ys = [];
    Object.keys(layout).forEach(function (id) {
      xs.push(layout[id].x); ys.push(layout[id].y);
    });
    if (robot && robot.x !== undefined && robot.y !== undefined) {
      xs.push(robot.x); ys.push(-(robot.y || 0));
    }
    if (!xs.length) return { minX: -WIDTH / 2, maxX: WIDTH / 2, minY: -HEIGHT / 2, maxY: HEIGHT / 2 };
    var minX = Math.min.apply(null, xs);
    var maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys);
    var maxY = Math.max.apply(null, ys);
    if (maxX - minX < 1) { minX -= 20; maxX += 20; }
    if (maxY - minY < 1) { minY -= 20; maxY += 20; }
    return { minX: minX, maxX: maxX, minY: minY, maxY: maxY };
  }

  function boundsToViewBox(b) {
    var w = Math.max(60, b.maxX - b.minX);
    var h = Math.max(60, b.maxY - b.minY);
    var scale = Math.min((WIDTH - 2 * PAD) / w, (HEIGHT - 2 * PAD) / h);
    var dw = (WIDTH - w * scale) / 2 / scale;
    var dh = (HEIGHT - h * scale) / 2 / scale;
    return (
      (b.minX - dw).toFixed(2) + " " +
      (b.minY - dh).toFixed(2) + " " +
      (w + 2 * dw).toFixed(2) + " " +
      (h + 2 * dh).toFixed(2)
    );
  }

  function drawGrid(svg, ns, bounds) {
    var step = 50;
    for (var x = Math.floor(bounds.minX / step) * step; x <= bounds.maxX; x += step) {
      var line = document.createElementNS(ns, "line");
      line.setAttribute("x1", x); line.setAttribute("y1", bounds.minY);
      line.setAttribute("x2", x); line.setAttribute("y2", bounds.maxY);
      line.setAttribute("stroke", "#161c24");
      line.setAttribute("stroke-width", "0.5");
      svg.appendChild(line);
    }
    for (var y = Math.floor(bounds.minY / step) * step; y <= bounds.maxY; y += step) {
      var hline = document.createElementNS(ns, "line");
      hline.setAttribute("x1", bounds.minX); hline.setAttribute("y1", y);
      hline.setAttribute("x2", bounds.maxX); hline.setAttribute("y2", y);
      hline.setAttribute("stroke", "#161c24");
      hline.setAttribute("stroke-width", "0.5");
      svg.appendChild(hline);
    }
  }

  function placeToNode(place) {
    return {
      node_id: place.place_id,
      pose: place.pose || null,
      objects: place.observed_object_ids || [],
      visited_count: place.visit_count || 0,
      reachable_state: place.target_confirmed ? "TARGET_CONFIRMED" : (place.visit_count > 0 ? "VISITED" : "OBSERVED"),
      heading_sector: null,
      timestamp: (place.provenance && place.provenance.created_at) || null,
      pose_quality: place.pose_quality || "unavailable",
      negative_evidence_count: place.negative_evidence || 0,
      target_match_level: place.target_candidate ? "candidate" : "none",
    };
  }

  function shortNodeLabel(node) {
    var objects = node.objects || [];
    if (!objects.length) return node.node_id;
    return objects.slice(0, 2).join("+");
  }

  function fmtTime(ts) {
    if (!ts) return "--";
    var d = new Date(ts * 1000);
    return d.toTimeString().slice(0, 8);
  }

  function hashCode(text) {
    var hash = 0;
    for (var i = 0; i < text.length; i++) {
      hash = (hash << 5) - hash + text.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash);
  }

  function esc(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.SearchMapRenderer = SearchMapRenderer;
})();
