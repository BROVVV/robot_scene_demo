/* SearchMapRenderer: SVG renderer for the semantic topology page.
 *
 * 只有一个显示模式：语义拓扑。计量"空间地图"模式已于 2026-08-26 移除，
 * 其入口按钮 / 模式开关 / 图例于 2026-09-15 一并删除；文件末尾那段
 * renderSpatialMap 及其专属辅助函数因此**不可达**，留着只是没清理。
 * 页内可切换的是两个投影视图（见 this.topologyView）：
 *   "objects"    默认 - 只有 OBJECT 节点与物体间关系
 *   "navigation"      - 额外含 PLACE 中转站与 OBSERVED_FROM
 *
 * Semantic topology (plan §16-§24):
 *   - nodes are only persistent OBJECT ids (obj_xxx)
 *   - edges are backend-persisted OBJECT->OBJECT relations only
 *   - node screen positions come from a deterministic BFS/layered display
 *     layout; they NEVER use map_xyz / camera_xyz (pure display)
 *   - live updates keep the same structure at the same positions
 *
 * Spatial map keeps the original RTAB / Place / Frontier / Route / map_xyz
 * view for navigation debugging; it is never used by the topology layout.
 */
(function () {
  "use strict";

  var WIDTH = 600;
  var HEIGHT = 400;
  var PAD = 34;

  // Semantic topology layout constants (plan §19.7) - display only.
  var TOPO_PAD = 26;
  var LAYER_GAP_X = 180;
  var ROW_GAP_Y = 92;
  var COMPONENT_GAP_Y = 150;
  var ISOLATED_GAP_X = 150;
  var ISOLATED_GAP_Y = 90;
  var NODE_WIDTH = 124;
  var NODE_HEIGHT = 56;
  var NODE_RX = 8;

  var TOPO_STATUS_COLOR = {
    CONFIRMED: "#34d399",
    TENTATIVE: "#38bdf8",
    STALE: "#8b95a3",
  };

  var RELATION_ZH = {
    near: "邻近",
    adjacent_to: "相邻",
    left_of: "左侧",
    right_of: "右侧",
    in_front_of: "前方",
    behind: "后方",
    on: "位于其上",
    under: "位于其下",
    above: "上方",
    below: "下方",
    in: "内部",
    inside: "内部",
    contains: "包含",
    attached_to: "连接",
    blocks: "阻挡",
  };

  var RELATION_DIRECTED = {
    left_of: true, right_of: true, in_front_of: true, behind: true,
    above: true, below: true, under: true, contains: true, inside: true,
    in: true,
  };

  // ------------------------------------------------------------------ //
  // SVG viewport helpers (pure, testable) - wheel zoom + drag pan      //
  // ------------------------------------------------------------------ //
  var VIEWPORT_MIN_SPAN = 8;
  var VIEWPORT_MAX_SPAN = 2000000;

  function clampViewSpan(value) {
    return Math.max(VIEWPORT_MIN_SPAN, Math.min(VIEWPORT_MAX_SPAN, value));
  }

  // Zoom the viewBox by `factor` keeping the SVG-pixel point (px,py) fixed in
  // world space (factor < 1 zooms in, e.g. wheel up).
  function viewportZoom(view, factor, px, py, svgW, svgH) {
    if (!view) return null;
    var fx = (isFinite(factor) && factor > 0) ? factor : 1.0;
    var nw = clampViewSpan(view.width * fx);
    var nh = clampViewSpan(view.height * fx);
    var xr = svgW > 0 ? Math.max(0, Math.min(1, px / svgW)) : 0.5;
    var yr = svgH > 0 ? Math.max(0, Math.min(1, py / svgH)) : 0.5;
    var wx = view.minX + xr * view.width;
    var wy = view.minY + yr * view.height;
    return {
      minX: wx - xr * nw,
      minY: wy - yr * nh,
      width: nw,
      height: nh,
    };
  }

  // Pan the viewBox by a pixel delta (drag).
  function viewportPan(view, dxPx, dyPx, svgW, svgH) {
    if (!view) return null;
    var sx = svgW > 0 ? (dxPx / svgW) * view.width : 0;
    var sy = svgH > 0 ? (dyPx / svgH) * view.height : 0;
    return {
      minX: view.minX - sx,
      minY: view.minY - sy,
      width: view.width,
      height: view.height,
    };
  }

  function viewToBoxString(view) {
    if (!view) return null;
    return (
      view.minX.toFixed(2) + " " + view.minY.toFixed(2) + " " +
      view.width.toFixed(2) + " " + view.height.toFixed(2)
    );
  }

  function SearchMapRenderer(svg, detailEl) {
    this.svg = svg;
    this.detailEl = detailEl;
    // 计划书 §2.2：唯一图就是 `state.graph`（含 object_topology / places /
    // objects / frontiers / current_place_id）。前端不再持有第二份图。
    this.graph = null;
    this.spatial = null;

    // 语义拓扑页的投影视图。两份计划在这页画什么上冲突，裁决见
    // docs/GO2W_三维建图重影_与_拓扑碎片化_修复计划书_20260903.md 步骤 2.1/2.2：
    //   "objects"    = 只有 OBJECT 节点与物体间关系（默认；计划1 §7.4 的验收契约）
    //   "navigation" = 再加上 PLACE 中转站与 OBSERVED_FROM（计划3 判据 T2 连通分量==1）
    // 默认取 objects：物体关系没提出来时，界面呈现孤立点（难看但诚实），
    // 而不是用 "都从 P1 看到" 把关系提取失败糊成一张连通的图。
    this.topologyView = "objects";

    // Stable display layout cache for the semantic topology (pure display).
    this.topologyPositions = {};
    this.lastTopologyFingerprint = null;

    // Interactive viewport: once the operator zooms/pans, the viewBox is kept
    // across live re-renders instead of being re-fit every frame.
    this._view = null;            // {minX,minY,width,height}
    this._userTransformed = false;
    this._drag = null;            // {startX,startY,startView}
    this._bindViewportGestures();
  }

  // ------------------------------------------------------------------ //
  // Viewport plumbing                                                  //
  // ------------------------------------------------------------------ //
  SearchMapRenderer.prototype._svgSize = function () {
    var rect = this.svg.getBoundingClientRect ? this.svg.getBoundingClientRect() : null;
    return {
      w: (rect && rect.width) ? rect.width : (this.svg.clientWidth || 600),
      h: (rect && rect.height) ? rect.height : (this.svg.clientHeight || 400),
    };
  };

  SearchMapRenderer.prototype._readView = function () {
    if (this._view) return this._view;
    var vb = this.svg.getAttribute ? this.svg.getAttribute("viewBox") : "";
    var parts = String(vb || "").trim().split(/\s+/).map(Number);
    if (parts.length === 4 && isFinite(parts[0]) && parts[2] > 0) {
      return { minX: parts[0], minY: parts[1], width: parts[2], height: parts[3] };
    }
    return { minX: 0, minY: 0, width: 600, height: 400 };
  };

  SearchMapRenderer.prototype._applyView = function (view) {
    if (!view) return;
    this._view = view;
    var box = viewToBoxString(view);
    if (box && this.svg.setAttribute) this.svg.setAttribute("viewBox", box);
  };

  // Fit the content unless the operator has already zoomed/panned the view.
  SearchMapRenderer.prototype._fitViewBox = function (bounds) {
    if (this._userTransformed) {
      this._applyView(this._view || this._readView());
      return;
    }
    // boundsToViewBox returns a "minX minY width height" string; parse it into
    // the object form so subsequent wheel-zoom math has a real view.
    var box = boundsToViewBox(bounds);
    var parts = String(box).trim().split(/\s+/).map(Number);
    this._applyView({
      minX: parts[0], minY: parts[1], width: parts[2], height: parts[3],
    });
  };

  SearchMapRenderer.prototype._resetViewport = function () {
    this._userTransformed = false;
    this._view = null;
    this._drag = null;
    // Re-render so the next render() auto-fits the content.
    this.render(this.graph, this.spatial);
  };

  SearchMapRenderer.prototype._bindViewportGestures = function () {
    var self = this;
    var svg = this.svg;

    // Wheel zoom-in / zoom-out anchored at the cursor.
    svg.addEventListener("wheel", function (event) {
      if (!self.svg) return;
      event.preventDefault();
      var factor = event.deltaY < 0 ? 0.82 : 1.22; // wheel-up => zoom in
      var rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
      var px = rect ? (event.clientX - rect.left) : (event.offsetX || 0);
      var py = rect ? (event.clientY - rect.top) : (event.offsetY || 0);
      var size = self._svgSize();
      var current = self._readView();
      var next = viewportZoom(current, factor, px, py, size.w, size.h);
      if (next) {
        self._userTransformed = true;
        self._applyView(next);
      }
    }, { passive: false });

    // Drag to pan.
    svg.addEventListener("mousedown", function (event) {
      if (event.button !== 0) return;
      var rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
      self._userTransformed = true;
      self._drag = {
        startX: rect ? (event.clientX - rect.left) : (event.offsetX || 0),
        startY: rect ? (event.clientY - rect.top) : (event.offsetY || 0),
        startView: self._readView(),
      };
      svg.style.cursor = "grabbing";
      event.preventDefault();
    });

    svg.addEventListener("mousemove", function (event) {
      if (!self._drag) return;
      var rect = svg.getBoundingClientRect ? svg.getBoundingClientRect() : null;
      var px = rect ? (event.clientX - rect.left) : (event.offsetX || 0);
      var py = rect ? (event.clientY - rect.top) : (event.offsetY || 0);
      var size = self._svgSize();
      var next = viewportPan(
        self._drag.startView, px - self._drag.startX, py - self._drag.startY,
        size.w, size.h
      );
      if (next) self._applyView(next);
    });

    function endDrag() {
      if (!self._drag) return;
      self._drag = null;
      svg.style.cursor = "grab";
    }
    svg.addEventListener("mouseup", endDrag);
    svg.addEventListener("mouseleave", endDrag);

    // Double-click resets to auto-fit.
    svg.addEventListener("dblclick", function () {
      self._resetViewport();
    });

    svg.style.cursor = "grab";
  };

  // ------------------------------------------------------------------ //
  // Topology view switching                                            //
  // ------------------------------------------------------------------ //
  SearchMapRenderer.prototype.setTopologyView = function (view) {
    this.topologyView = view === "navigation" ? "navigation" : "objects";
    this.render(this.graph, this.spatial);
  };

  SearchMapRenderer.prototype.getTopologyView = function () {
    return this.topologyView;
  };

  // ------------------------------------------------------------------ //
  // Main render dispatch                                               //
  // ------------------------------------------------------------------ //
  SearchMapRenderer.prototype.render = function (graphData, spatialData) {
    this.graph = graphData || this.graph || {};
    this.spatial = spatialData || this.spatial || {};

    // 计划书 §7.2：语义拓扑页只投影"对象拓扑"。禁止优先把整张
    // semantic_navigation_graph_v1 交给 renderObjectTopology()，否则 P1/F01
    // 这些内部导航节点会漏到界面上。内部导航图仍然保留给规划器使用。
    var topology = this.graph.object_topology || null;
    var project = this.topologyView === "navigation" ? objectTopologyOnly : objectTopologyStrict;
    this.renderObjectTopology(
      project(topology),
      this.graph.objects || []
    );
  };

  // ================================================================== //
  // SEMANTIC TOPOLOGY VIEW                                            //
  // ================================================================== //
  SearchMapRenderer.prototype.renderObjectTopology = function (topology, semanticObjects) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = this.svg;
    this.svg.textContent = "";

    if (!topology || !Array.isArray(topology.nodes) || topology.nodes.length === 0) {
      this.drawTopologyEmptyState("尚未识别到可建立语义拓扑的物体（识别到第一个物体后自动开始建图，并实时更新）");
      return;
    }
    var nodes = topology.nodes || [];
    var edges = topology.edges || [];

    // Stable layout: recompute only when the graph structure changes; label /
    // status only refreshes in place otherwise (no jitter on every WS message).
    var fingerprint = topologyFingerprint(nodes, edges);
    if (fingerprint !== this.lastTopologyFingerprint) {
      this.topologyPositions = computeTopologyLayout(nodes, edges, this.topologyPositions);
      this.lastTopologyFingerprint = fingerprint;
    }
    var positions = this.topologyPositions;
    if (!positions || Object.keys(positions).length === 0) {
      this.drawTopologyEmptyState("已识别物体，正在计算语义拓扑布局…");
      return;
    }

    // Fit to all node positions (kept when the operator has zoomed/panned).
    var b = topoBounds(positions);
    this._fitViewBox(b);

    // arrow marker def for directed relation edges
    var defs = document.createElementNS(ns, "defs");
    var marker = document.createElementNS(ns, "marker");
    marker.setAttribute("id", "topo-arrow");
    marker.setAttribute("markerWidth", "7"); marker.setAttribute("markerHeight", "7");
    marker.setAttribute("refX", "6"); marker.setAttribute("refY", "3.5");
    marker.setAttribute("orient", "auto");
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M0,0 L7,3.5 L0,7 z");
    path.setAttribute("fill", "#c084fc");
    marker.appendChild(path);
    defs.appendChild(marker);
    this.svg.appendChild(defs);

    // 1) edges (under nodes)
    edges.forEach(function (edge) {
      this.drawTopologyEdge(edge, positions, ns, edges);
    }.bind(this));

    // 2) node cards
    var objectById = {};
    (semanticObjects || []).forEach(function (obj) { objectById[obj.object_id] = obj; });
    var edgesByNode = indexEdgesByNode(edges);
    nodes.forEach(function (node) {
      var pos = positions[node.node_id];
      if (!pos) return;
      this.drawTopologyNode(
        node,
        pos,
        ns,
        edgesByNode[node.node_id] || [],
        objectById[node.node_id]
      );
    }.bind(this));

    // 3) empty-relation hint
    if (!edges.length && nodes.length) {
      drawTopologyCaption(svg, ns, "已识别 " + nodes.length + " 个物体，暂未获得可靠物体关系");
    } else {
      var tentative = edges.filter(function (e) {
        return String(e.status || "").toUpperCase() === "TENTATIVE";
      }).length;
      if (tentative > 0) {
        drawTopologyCaption(svg, ns, "关系仍在确认中 · " + tentative + " 条待确认");
      }
    }
  };

  SearchMapRenderer.prototype.drawTopologyEmptyState = function (msg) {
    var ns = "http://www.w3.org/2000/svg";
    this._fitViewBox({ minX: 0, minY: 0, maxX: 600, maxY: 400 });
    var text = document.createElementNS(ns, "text");
    text.setAttribute("x", 300);
    text.setAttribute("y", 190);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("fill", "#64748b");
    text.setAttribute("font-size", "13");
    text.textContent = msg;
    this.svg.appendChild(text);
  };

  SearchMapRenderer.prototype.drawTopologyEdge = function (edge, positions, ns, allEdges) {
    var svg = this.svg;
    var a = positions[edge.from];
    var b = positions[edge.to];
    if (!a || !b) return;
    var ax = a.x + NODE_WIDTH / 2, ay = a.y + NODE_HEIGHT / 2;
    var bx = b.x + NODE_WIDTH / 2, by = b.y + NODE_HEIGHT / 2;
    var g = document.createElementNS(ns, "g");
    var status = String(edge.status || "TENTATIVE").toUpperCase();
    var scope = String(edge.relation_scope || "STRUCTURAL").toUpperCase();
    var structural = scope !== "VIEW_RELATIVE";
    var confirmed = status === "CONFIRMED" || status === "OPEN";
    var blocked = status === "BLOCKED" || status === "FAILED" || status === "DEGRADED";
    var line = document.createElementNS(ns, "line");
    line.setAttribute("x1", ax);
    line.setAttribute("y1", ay);
    line.setAttribute("x2", bx);
    line.setAttribute("y2", by);
    var color = blocked ? "#f87171" : (structural ? "#34d399" : "#c084fc");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", confirmed ? 1.6 : 1.1);
    line.setAttribute("stroke-opacity", confirmed ? 0.9 : (blocked ? 0.75 : 0.45));
    line.setAttribute("stroke-dasharray", blocked ? "4 3" : (structural ? "none" : "5 4"));
    if (edge.directed && RELATION_DIRECTED[edge.relation]) {
      line.setAttribute("marker-end", "url(#topo-arrow)");
    }
    g.appendChild(line);

    // Edge label (merged for a pair with multiple relations).
    var labelText = edgeLabelZh(edge, allEdges);
    if (labelText) {
      var mx = (ax + bx) / 2, my = (ay + by) / 2;
      var rect = document.createElementNS(ns, "rect");
      var w = 8 + labelText.length * 6.6;
      rect.setAttribute("x", mx - w / 2);
      rect.setAttribute("y", my - 8);
      rect.setAttribute("width", w);
      rect.setAttribute("height", 16);
      rect.setAttribute("rx", 3);
      rect.setAttribute("fill", "#0d1117");
      rect.setAttribute("stroke", color);
      rect.setAttribute("stroke-opacity", "0.35");
      rect.setAttribute("stroke-width", "0.5");
      g.appendChild(rect);
      var text = document.createElementNS(ns, "text");
      text.setAttribute("x", mx);
      text.setAttribute("y", my + 3.5);
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("fill", color);
      text.setAttribute("font-size", "8.5");
      text.setAttribute("font-weight", "bold");
      text.textContent = labelText;
      g.appendChild(text);
    }
    g.addEventListener("click", function (event) {
      event.stopPropagation();
      this.showTopologyEdgeDetail(edge, allEdges);
    }.bind(this));
    svg.appendChild(g);
  };

  SearchMapRenderer.prototype.drawTopologyNode = function (node, pos, ns, edges, objectEntry) {
    var svg = this.svg;
    var g = document.createElementNS(ns, "g");
    g.setAttribute("transform", "translate(" + pos.x + "," + pos.y + ")");
    var nodeType = String(node.node_type || (node.is_object ? "OBJECT" : "OBJECT")).toUpperCase();
    var status = String(node.status || node.state || "TENTATIVE").toUpperCase();
    if (nodeType === "PLACE") {
      status = node.current ? "CURRENT" : (node.visit_count > 0 ? "VISITED" : "UNSEEN");
    } else if (nodeType === "FRONTIER") {
      status = node.status || node.state || "OPEN";
    }
    status = String(status).toUpperCase();
    var color = TOPO_STATUS_COLOR[status] || (nodeType === "PLACE" ? "#38bdf8" : "#8b95a3");
    var confirmed = status === "CONFIRMED" || status === "CURRENT";
    var tentative = status === "TENTATIVE" || status === "OPEN" || status === "UNSEEN";
    var stale = status === "STALE" || status === "BLOCKED";

    var rect = document.createElementNS(ns, "rect");
    rect.setAttribute("width", NODE_WIDTH);
    rect.setAttribute("height", NODE_HEIGHT);
    rect.setAttribute("rx", NODE_RX);
    rect.setAttribute("fill", "rgba(20,27,36,0.92)");
    rect.setAttribute("stroke", color);
    rect.setAttribute("stroke-width", confirmed ? 1.6 : 1.1);
    rect.setAttribute("stroke-dasharray", tentative ? "4 3" : "none");
    rect.setAttribute("opacity", stale ? 0.45 : (tentative ? 0.72 : 1));
    g.appendChild(rect);

    var title = document.createElementNS(ns, "text");
    title.setAttribute("x", 8);
    title.setAttribute("y", 15);
    title.setAttribute("fill", color);
    title.setAttribute("font-size", "10");
    title.setAttribute("font-weight", "bold");
    var titleText = String(node.node_id || "");
    // 计划书 §12：frontier node_id 全局唯一，UI 标题显示短标签（F01 等）。
    if (nodeType === "FRONTIER" && node.label) {
      titleText = String(node.label);
    }
    if (node.is_target_confirmed || node.is_target_candidate) {
      titleText = "★ " + titleText;
    }
    title.textContent = titleText;
    g.appendChild(title);

    var label = document.createElementNS(ns, "text");
    label.setAttribute("x", 8);
    label.setAttribute("y", 31);
    label.setAttribute("fill", "#cbd5e1");
    label.setAttribute("font-size", "10");
    label.textContent = ellipsis(String(node.label || "object"), 15);
    g.appendChild(label);

    var meta = document.createElementNS(ns, "text");
    meta.setAttribute("x", 8);
    meta.setAttribute("y", 46);
    meta.setAttribute("fill", "#8b95a3");
    meta.setAttribute("font-size", "8.5");
    var metaText = status;
    if (node.observation_count != null) metaText += " · ×" + node.observation_count;
    meta.textContent = metaText;
    g.appendChild(meta);

    g.addEventListener("click", function (event) {
      event.stopPropagation();
      this.showTopologyNodeDetail(node, edges, objectEntry);
    }.bind(this));
    svg.appendChild(g);
  };

  // ------------------------------------------------------------------ //
  // Topology detail panels                                             //
  // ------------------------------------------------------------------ //
  SearchMapRenderer.prototype.showTopologyNodeDetail = function (node, edges, objectEntry) {
    if (!this.detailEl) return;
    var html =
      "<div><b>" + esc(node.node_id || "") + "</b> <span style='color:" +
      (TOPO_STATUS_COLOR[node.status] || "#8b95a3") + "'>" + esc(node.status || "TENTATIVE") +
      "</span></div>" +
      "<div>标签 " + esc(node.label || "") + "</div>" +
      "<div>置信度 " + (node.confidence || 0).toFixed(2) + "</div>" +
      "<div>观测次数 " + (node.observation_count || 0) + "</div>" +
      "<div>空间质量 " + esc(node.spatial_quality || "-") + "</div>";
    if (objectEntry) {
      if (objectEntry.first_seen) html += "<div>首次观测 " + fmtTime(objectEntry.first_seen) + "</div>";
      if (objectEntry.last_seen) html += "<div>最近观测 " + fmtTime(objectEntry.last_seen) + "</div>";
      if (objectEntry.seen_from_places && objectEntry.seen_from_places.length) {
        html += "<div>观测地点 " + esc(objectEntry.seen_from_places.join(", ")) + "</div>";
      }
      if (objectEntry.map_xyz) {
        html += "<div>map_xyz " + esc(objectEntry.map_xyz.map(function (v) { return v.toFixed(2); }).join(", ")) + "</div>";
      }
      if (objectEntry.association_score != null) {
        html += "<div>关联分 " + Number(objectEntry.association_score).toFixed(2) + "</div>";
      }
    }
    if (edges && edges.length) {
      html += "<div style='margin-top:6px'>关系 " + edges.length + " 条：</div>";
      edges.forEach(function (e) {
        var zh = RELATION_ZH[e.relation] || e.relation;
        var scope = e.relation_scope === "VIEW_RELATIVE" ? "(视角)" : "";
        html += "<div style='font-size:11px'>&rarr; " + esc(e.to || e.from) +
          " " + esc(zh + scope) + " ×" + (e.observation_count || 1) + "</div>";
      });
    }
    this.detailEl.innerHTML = html;
    this.detailEl.classList.remove("hidden");
  };

  SearchMapRenderer.prototype.showTopologyEdgeDetail = function (edge, allEdges) {
    if (!this.detailEl) return;
    var zh = RELATION_ZH[edge.relation] || edge.relation;
    var html =
      "<div><b>" + esc(zh) + "</b> <span style='color:" +
      (edge.relation_scope === "VIEW_RELATIVE" ? "#c084fc" : "#34d399") +
      "'>" + esc(edge.relation_scope || "STRUCTURAL") + "</span></div>" +
      "<div>源 " + esc(edge.from) + " &rarr; 目标 " + esc(edge.to) + "</div>" +
      "<div>状态 <span style='color:" + (TOPO_STATUS_COLOR[edge.status] || "#8b95a3") + "'>" +
      esc(edge.status || "TENTATIVE") + "</span></div>" +
      "<div>置信度 " + (edge.confidence || 0).toFixed(2) + "</div>" +
      "<div>观测次数 " + (edge.observation_count || 0) + "</div>";
    if (edge.first_seen) html += "<div>首次 " + fmtTime(edge.first_seen) + "</div>";
    if (edge.last_seen) html += "<div>最近 " + fmtTime(edge.last_seen) + "</div>";
    if (edge.descriptions_zh && edge.descriptions_zh.length) {
      html += "<div>描述 " + esc(edge.descriptions_zh[edge.descriptions_zh.length - 1]) + "</div>";
    }
    var samePair = (allEdges || []).filter(function (e) {
      return (e.from === edge.from && e.to === edge.to) || (e.from === edge.to && e.to === edge.from);
    });
    if (samePair.length > 1) {
      var other = samePair.filter(function (e) { return e.edge_id !== edge.edge_id; });
      if (other.length) {
        html += "<div style='margin-top:6px'>同对物体其他关系：</div>";
        other.forEach(function (e) {
          html += "<div style='font-size:11px'>&rarr; " + esc(RELATION_ZH[e.relation] || e.relation) +
            " ×" + (e.observation_count || 1) + "</div>";
        });
      }
    }
    this.detailEl.innerHTML = html;
    this.detailEl.classList.remove("hidden");
  };


  SearchMapRenderer.prototype.showObjectDetail = function (obj) {
    if (!this.detailEl) return;
    var html =
      "<div><b>" + esc(obj.object_id || "") + "</b> <span style='color:" +
      (String(obj.status || "").toUpperCase() === "CONFIRMED" ? "#34d399" : "#38bdf8") +
      "'>" + esc(obj.status || "TENTATIVE") + "</span></div>" +
      "<div>标签 " + esc(obj.label || "") + "</div>" +
      "<div>map_xyz " + esc((obj.map_xyz || []).map(function (v) { return v.toFixed(2); }).join(", ")) + "</div>" +
      "<div>置信度 " + (obj.confidence || 0).toFixed(2) + "</div>" +
      "<div>观测次数 " + (obj.observation_count || 0) + "</div>" +
      "<div>空间质量 " + esc(obj.spatial_quality || "-") + "</div>" +
      "<div>seen_from " + esc((obj.seen_from_places || []).join(", ") || "-") + "</div>" +
      "<div>关联分 " + (obj.association_score || 0).toFixed(2) + "</div>" +
      "<div>位置方差 " + esc((obj.position_variance_xyz || []).map(function (v) { return v.toFixed(3); }).join(", ") || "-") + "</div>";
    this.detailEl.innerHTML = html;
    this.detailEl.classList.remove("hidden");
  };

  // ------------------------------------------------------------------ //
  // helpers                                                            //
  // ------------------------------------------------------------------ //


  // ------------------------------------------------------------------ //
  // Semantic topology layout (deterministic, display-only)            //
  // ------------------------------------------------------------------ //

  // 计划书 §7.3：前端最小契约检查。后端 object_topology_snapshot() 已经按对象
  // 过滤，这里只做一层不信任上游的兜底：
  //   - 节点接受 node_type === "OBJECT" 或 "PLACE"（绝不用 ID 前缀猜类型）；
  //     PLACE 是跨视角中转站，去掉它对象图必然碎成多个连通分量；
  //   - 边的 from/to 必须都在当前节点集合内；
  //   - 拒绝 FRONTIER_TO / MOVED_TO 这类纯导航关系；
  //   - 没有关系时显示孤立对象节点，但绝不退回导航图。
  var NON_OBJECT_RELATIONS = { FRONTIER_TO: 1, MOVED_TO: 1 };

  function objectTopologyOnly(topology) {
    if (!topology || !Array.isArray(topology.nodes)) return null;
    var nodes = topology.nodes.filter(function (node) {
      return node && (node.node_type === "OBJECT" || node.node_type === "PLACE") && node.node_id;
    });
    var objectIds = {};
    nodes.forEach(function (node) { objectIds[node.node_id] = true; });
    var rawEdges = Array.isArray(topology.edges) ? topology.edges : [];
    var edges = rawEdges.filter(function (edge) {
      if (!edge || !objectIds[edge.from] || !objectIds[edge.to]) return false;
      return !NON_OBJECT_RELATIONS[String(edge.relation || "").toUpperCase()];
    });
    return {
      schema_version: topology.schema_version || "semantic_object_topology_v1",
      revision: topology.revision || 0,
      nodes: nodes,
      edges: edges,
      stats: topology.stats || { node_count: nodes.length, edge_count: edges.length },
    };
  }

  // 计划1 §7.3 的严格契约：节点只收 OBJECT，边只连接两个物体。
  // OBSERVED_FROM / FRONTIER_TO / MOVED_TO 三类边都有一端是 PLACE 或 FRONTIER，
  // 被"节点集合"这一步自然剔除——不需要再单列一张关系黑名单。
  function objectTopologyStrict(topology) {
    if (!topology || !Array.isArray(topology.nodes)) return null;
    var nodes = topology.nodes.filter(function (node) {
      return node && node.node_type === "OBJECT" && node.node_id;
    });
    var objectIds = {};
    nodes.forEach(function (node) { objectIds[node.node_id] = true; });
    var rawEdges = Array.isArray(topology.edges) ? topology.edges : [];
    var edges = rawEdges.filter(function (edge) {
      return edge && objectIds[edge.from] && objectIds[edge.to];
    });
    return {
      schema_version: topology.schema_version || "semantic_object_topology_v1",
      revision: topology.revision || 0,
      nodes: nodes,
      edges: edges,
      // 按过滤后的集合重算：objectTopologyOnly 直接透传上层 stats，
      // 会让 "N 个物体" 的数与画出来的节点数不一致（PLACE 被算成一个物体）。
      stats: { node_count: nodes.length, edge_count: edges.length },
    };
  }

  function topologyFingerprint(nodes, edges) {
    var nodeIds = (nodes || []).map(function (n) { return n.node_id; }).sort();
    var edgeKeys = (edges || []).map(function (e) {
      return [e.from, e.to, e.relation].join("~");
    }).sort();
    return JSON.stringify({ nodes: nodeIds, edges: edgeKeys });
  }

  function buildAdjacency(nodes, edges) {
    var adj = {};
    nodes.forEach(function (n) { adj[n.node_id] = []; });
    edges.forEach(function (e) {
      if (adj[e.from] && adj[e.to]) {
        adj[e.from].push(e.to);
        adj[e.to].push(e.from);
      }
    });
    return adj;
  }

  function findConnectedComponents(nodes, adj) {
    var seen = {};
    var comps = [];
    nodes.forEach(function (n) {
      var id = n.node_id;
      if (seen[id]) return;
      var stack = [id];
      var comp = [];
      seen[id] = true;
      while (stack.length) {
        var cur = stack.pop();
        comp.push(cur);
        (adj[cur] || []).forEach(function (nb) {
          if (!seen[nb]) { seen[nb] = true; stack.push(nb); }
        });
      }
      comps.push(comp);
    });
    return comps;
  }

  function selectStableRoot(component, nodeById) {
    var confirmedTarget = null;
    var candidateTarget = null;
    component.forEach(function (id) {
      var node = nodeById[id];
      if (!node) return;
      if (node.is_target_confirmed && !confirmedTarget) confirmedTarget = id;
      if (node.is_target_candidate && !candidateTarget) candidateTarget = id;
    });
    if (confirmedTarget) return confirmedTarget;
    if (candidateTarget) return candidateTarget;
    var best = null;
    var bestCount = -1;
    component.forEach(function (id) {
      var node = nodeById[id];
      var count = node ? (node.observation_count || 0) : 0;
      if (count > bestCount) { bestCount = count; best = id; }
    });
    if (best) return best;
    return component.slice().sort()[0];
  }

  function bfsLayers(root, component, adj) {
    var layers = [];
    var visited = {};
    var frontier = [root];
    visited[root] = true;
    var depth = 0;
    while (frontier.length) {
      layers[depth] = frontier;
      var next = [];
      frontier.forEach(function (id) {
        (adj[id] || []).forEach(function (nb) {
          if (component.indexOf(nb) >= 0 && !visited[nb]) {
            visited[nb] = true;
            next.push(nb);
          }
        });
      });
      frontier = next;
      depth += 1;
    }
    return layers;
  }

  function stableSortLayer(layer, previousPositions) {
    return layer.slice().sort(function (x, y) {
      var px = previousPositions && previousPositions[x];
      var py = previousPositions && previousPositions[y];
      if (px && py && px.y !== py.y) return px.y - py.y;
      if (px && !py) return -1;
      if (py && !px) return 1;
      return x < y ? -1 : (x > y ? 1 : 0);
    });
  }

  function computeTopologyLayout(nodes, edges, previousPositions) {
    previousPositions = previousPositions || {};
    var nodeById = {};
    nodes.forEach(function (n) { nodeById[n.node_id] = n; });
    var adj = buildAdjacency(nodes, edges);
    var comps = findConnectedComponents(nodes, adj);

    // Deterministic component ordering: target confirmed, target candidate,
    // then size desc, then lexicographic stable id.
    function componentRank(comp) {
      var hasTarget = false, hasCandidate = false, size = comp.length;
      comp.forEach(function (id) {
        var node = nodeById[id];
        if (node && node.is_target_confirmed) hasTarget = true;
        if (node && node.is_target_candidate) hasCandidate = true;
      });
      var minId = comp.slice().sort()[0];
      return [hasTarget ? 0 : 1, hasCandidate ? 0 : 1, -size, minId];
    }
    comps.sort(function (a, b) {
      var ra = componentRank(a), rb = componentRank(b);
      for (var i = 0; i < ra.length; i++) {
        if (ra[i] !== rb[i]) return ra[i] < rb[i] ? -1 : 1;
      }
      return 0;
    });

    var positions = {};
    var yOffset = TOPO_PAD;
    var connectedIds = {};
    comps.forEach(function (comp) {
      if (comp.length === 0) return;
      var root = selectStableRoot(comp, nodeById);
      var layers = bfsLayers(root, comp, adj);
      // Measure component height.
      var maxRows = 0;
      layers.forEach(function (layer) {
        if (layer.length > maxRows) maxRows = layer.length;
      });
      var layerGap = LAYER_GAP_X;
      for (var depth = 0; depth < layers.length; depth++) {
        var layer = stableSortLayer(layers[depth], previousPositions);
        for (var row = 0; row < layer.length; row++) {
          var id = layer[row];
          positions[id] = {
            x: TOPO_PAD + depth * layerGap,
            y: yOffset + row * ROW_GAP_Y,
          };
          connectedIds[id] = true;
        }
      }
      yOffset += maxRows * ROW_GAP_Y + COMPONENT_GAP_Y;
    });

    // Isolated nodes (no relation yet): grid at the bottom.
    var isolated = nodes.filter(function (n) { return !connectedIds[n.node_id]; });
    isolated.sort(function (a, b) {
      var pa = previousPositions[a.node_id], pb = previousPositions[b.node_id];
      if (pa && pb) return pa.y - pb.y;
      return a.node_id < b.node_id ? -1 : 1;
    });
    var cols = 4;
    isolated.forEach(function (node, i) {
      positions[node.node_id] = {
        x: TOPO_PAD + (i % cols) * ISOLATED_GAP_X,
        y: yOffset + Math.floor(i / cols) * ISOLATED_GAP_Y,
      };
    });
    return positions;
  }

  function topoBounds(positions) {
    var xs = Object.keys(positions).map(function (id) { return positions[id].x; });
    var ys = Object.keys(positions).map(function (id) { return positions[id].y; });
    xs.push(0); ys.push(0);
    xs.push(LAYER_GAP_X * 4 + NODE_WIDTH); ys.push(ROW_GAP_Y * 6 + NODE_HEIGHT);
    var minX = Math.min.apply(null, xs);
    var maxX = Math.max.apply(null, xs) + NODE_WIDTH;
    var minY = Math.min.apply(null, ys);
    var maxY = Math.max.apply(null, ys) + NODE_HEIGHT;
    return { minX: minX - TOPO_PAD, maxX: maxX + TOPO_PAD, minY: minY - TOPO_PAD, maxY: maxY + TOPO_PAD };
  }

  function indexEdgesByNode(edges) {
    var index = {};
    edges.forEach(function (e) {
      (index[e.from] = index[e.from] || []).push(e);
      (index[e.to] = index[e.to] || []).push(e);
    });
    return index;
  }

  function edgeLabelZh(edge, allEdges) {
    var zh = RELATION_ZH[edge.relation] || edge.relation;
    var label = zh + " · ×" + (edge.observation_count || 1);
    if (edge.relation_scope === "VIEW_RELATIVE") {
      label = zh + " · 视角";
    }
    // Merge labels for multiple relations between the same pair.
    var samePair = (allEdges || []).filter(function (e) {
      return ((e.from === edge.from && e.to === edge.to) ||
              (e.from === edge.to && e.to === edge.from)) && e.edge_id !== edge.edge_id;
    });
    if (samePair.length) {
      var parts = [zh];
      if (edge.relation_scope === "VIEW_RELATIVE") parts.push("视角");
      samePair.forEach(function (e) {
        if (parts.indexOf(RELATION_ZH[e.relation] || e.relation) < 0) {
          parts.push(RELATION_ZH[e.relation] || e.relation);
        }
      });
      label = parts.join(" · ");
    }
    return label;
  }

  function drawTopologyCaption(svg, ns, msg) {
    var text = document.createElementNS(ns, "text");
    text.setAttribute("x", 10);
    text.setAttribute("y", 16);
    text.setAttribute("fill", "#64748b");
    text.setAttribute("font-size", "11");
    text.textContent = msg;
    svg.appendChild(text);
  }

  function ellipsis(text, max) {
    if (text.length <= max) return text;
    return text.slice(0, max - 1) + "…";
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

  // Expose pure display-layout helpers for headless tests (plan §36).  They
  // never touch DOM / metric data.
  window.TopologyLayout = {
    computeLayout: computeTopologyLayout,
    fingerprint: topologyFingerprint,
    components: findConnectedComponents,
    relationLabel: edgeLabelZh,
    objectOnly: objectTopologyOnly,
    objectStrict: objectTopologyStrict,
  };

  // Pure SVG viewport math (wheel zoom / drag pan) for headless tests.
  window.SvgViewport = {
    zoom: viewportZoom,
    pan: viewportPan,
    toBoxString: viewToBoxString,
    clampSpan: clampViewSpan,
  };
})();
