/* Autonomous Semantic Search WebUI front-end (plan book §5-§9, §63-§70).
 *
 * Responsibilities only: target entry, start/pause/resume/stop buttons,
 * /ws/search (snapshot + incremental SearchEvents), status / evidence /
 * decision / candidates / timeline rendering, canvas overlay and the SVG map
 * driver.  NONE of the search decisions are made in the browser.
 */
(function () {
  "use strict";

  var appState = {
    search: {},
    observation: {},
    objects: {},
    targetMatch: {},
    selectedGoal: null,
    candidates: [],
    map: {},
    events: [],
    health: {},
  };

  var ws = null;
  var wsState = "OFFLINE";
  var wsAttempt = 0;
  var wsTimer = null;
  var lastDetectionFrame = null;
  var timelineCount = 0;

  var els = {
    // taskbar
    target: document.getElementById("search-target"),
    btnStart: document.getElementById("btn-search-start"),
    btnPause: document.getElementById("btn-search-pause"),
    btnResume: document.getElementById("btn-search-resume"),
    btnStop: document.getElementById("btn-search-stop"),
    btnEstop: document.getElementById("btn-search-estop"),
    chkDebug: document.getElementById("chk-debug"),
    chkMotion: document.getElementById("chk-motion"),
    sessionInfo: document.getElementById("search-session-info"),
    banner: document.getElementById("search-banner"),
    // camera
    cam: document.getElementById("search-camera"),
    overlay: document.getElementById("search-overlay"),
    scamFps: document.getElementById("scam-fps"),
    scamAge: document.getElementById("scam-age"),
    scamLabel: document.getElementById("scam-label"),
    scamCycle: document.getElementById("scam-cycle"),
    camStale: document.getElementById("search-camera-stale"),
    // status
    stTarget: document.getElementById("st-target"),
    stPhase: document.getElementById("st-phase"),
    stCycle: document.getElementById("st-cycle"),
    stElapsed: document.getElementById("st-elapsed"),
    stMatch: document.getElementById("st-match"),
    stAnchor: document.getElementById("st-anchor"),
    stAction: document.getElementById("st-action"),
    stPose: document.getElementById("st-pose"),
    stEvidence: document.getElementById("st-evidence"),
    // observation
    obsCurrent: document.getElementById("obs-current"),
    obsSeen: document.getElementById("obs-seen"),
    // decision
    decIntent: document.getElementById("dec-intent"),
    decReason: document.getElementById("dec-reason"),
    decScores: document.getElementById("dec-scores"),
    decCandidates: document.getElementById("dec-candidates"),
    // map
    mapMeta: document.getElementById("map-meta"),
    mapNodeDetail: document.getElementById("map-node-detail"),
    // timeline
    timeline: document.getElementById("search-timeline"),
    tlWsState: document.getElementById("tl-ws-state"),
    // lights
    lightSearch: document.getElementById("light-search"),
    lightRobot: document.getElementById("light-robot"),
    lightWs: document.getElementById("light-ws"),
    // system tab
    sysCamera: document.getElementById("sys-camera"),
    sysWorker: document.getElementById("sys-worker"),
    sysMotion: document.getElementById("sys-motion"),
    sysOwner: document.getElementById("sys-owner"),
    sysSearch: document.getElementById("sys-search"),
    sysLlm: document.getElementById("sys-llm"),
    sysReadiness: document.getElementById("sys-readiness"),
    sysHistory: document.getElementById("sys-history"),
    // debug
    debugPanel: document.getElementById("debug-panel"),
    debugGoalGraph: document.getElementById("debug-goal_graph"),
    debugSceneGraph: document.getElementById("debug-scene_graph"),
    debugCandidates: document.getElementById("debug-candidates"),
    debugRaw: document.getElementById("debug-raw"),
  };

  var mapRenderer = new window.SearchMapRenderer(
    document.getElementById("search-map"),
    els.mapNodeDetail
  );

  // ------------------------------------------------------------------ //
  // WebSocket /ws/search (plan book §22-§24, §65)                       //
  // ------------------------------------------------------------------ //
  function connect() {
    wsAttempt += 1;
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws/search");
    ws.onopen = function () {
      wsAttempt = 0;
      setWsState("LIVE");
    };
    ws.onmessage = function (event) {
      try {
        var message = JSON.parse(event.data);
        handleWsMessage(message);
      } catch (err) { /* ignore malformed */ }
    };
    ws.onclose = function () {
      setWsState("RECONNECTING");
      ws = null;
      scheduleReconnect();
    };
    ws.onerror = function () {
      try { ws.close(); } catch (e) {}
    };
  }

  function scheduleReconnect() {
    if (wsTimer) return;
    var delays = [1, 2, 5];
    var delay = delays[Math.min(wsAttempt - 1, delays.length - 1)] || 5;
    wsTimer = setTimeout(function () {
      wsTimer = null;
      if (ws === null) connect();
    }, delay * 1000);
  }

  function setWsState(state) {
    wsState = state;
    els.tlWsState.textContent = state;
    setLight(els.lightWs, state === "LIVE" ? "green" : state === "RECONNECTING" ? "yellow" : "red");
  }

  function handleWsMessage(message) {
    var type = message.type;
    if (type === "snapshot") {
      if (message.state) applyStateSnapshot(message.state);
    } else if (type === "events") {
      (message.events || []).forEach(applySearchEvent);
    } else if (type === "event") {
      applySearchEvent(message.event);
    } else if (type === "heartbeat") {
      /* keepalive only */
    }
  }

  // ------------------------------------------------------------------ //
  // State application                                                   //
  // ------------------------------------------------------------------ //
  function applyStateSnapshot(state) {
    if (!state || !state.session_id) return;
    appState.search = state;
    appState.observation = state.observation || {};
    appState.objects = state.objects || {};
    appState.targetMatch = state.targetMatch || {};
    appState.selectedGoal = state.selected_goal || null;
    appState.candidates = state.candidates || [];
    appState.map = state.map || {};
    appState.spatial = state.spatial || {};
    state.timeline = state.timeline || [];
    renderAll();
  }

  function applySearchEvent(event) {
    if (!event || !event.event_type) return;
    // stale-event guard: ignore events from a previous session
    if (appState.search.session_id && event.session_id &&
        appState.search.session_id !== event.session_id) {
      return;
    }
    var type = event.event_type;
    var payload = event.payload || {};
    timelineCount += 1;

    switch (type) {
      case "SESSION_CREATED":
        appState.search = {};
        appState.search.target = payload.target;
        appState.search.session_id = event.session_id;
        appState.search.status = "STARTING";
        appState.search.phase = payload.phase || "STARTING";
        appState.targetMatch = {};
        appState.selectedGoal = null;
        appState.candidates = [];
        appState.map = {};
        break;
      case "SESSION_STARTED":
        appState.search.status = "RUNNING";
        appState.search.phase = payload.phase || "BOOTSTRAP";
        break;
      case "OBSERVATION_UPDATED":
        appState.observation = {
          bundle_id: payload.bundle_id,
          timestamp: payload.timestamp,
          objects: payload.scene_objects || payload.objects || [],
          detections: payload.detections || [],
          target_present: payload.target_present,
          heading_sector: payload.heading_sector,
          pose: payload.pose,
          sensor_health: payload.sensor_health || {},
        };
        lastDetectionFrame = payload.detections || null;
        break;
      case "OBJECTS_UPDATED":
        appState.objects.current = payload.current || [];
        appState.objects.target_evidence = payload.target_evidence || {};
        break;
      case "TARGET_MATCH_UPDATED":
        appState.targetMatch = {
          level: payload.target_match_level || "none",
          target_score: payload.target_score,
          anchor_labels: payload.anchor_labels || [],
          explicit_anchor_found: payload.explicit_anchor_found,
          directive: payload.directive,
          graph_match: payload.graph_match,
        };
        appState.search.goal_graph = payload.goal_graph || appState.search.goal_graph;
        break;
      case "VERIFICATION_STARTED":
        appState.search.phase = "VERIFY";
        appState.verifying = payload;
        break;
      case "VERIFICATION_FINISHED":
        appState.search.phase = "VERIFY";
        appState.verification = payload;
        break;
      case "TARGET_CONFIRMED":
        appState.search.status = "TARGET_FOUND";
        appState.search.phase = "TARGET_FOUND";
        showBanner("✓ TARGET FOUND", "found");
        break;
      case "MEMORY_UPDATED":
        appState.search.phase = payload.phase || "UPDATE_MEMORY";
        break;
      case "CANDIDATES_GENERATED":
        appState.candidates = payload.candidates || [];
        break;
      case "GOAL_SELECTED":
        appState.selectedGoal = {
          goal: payload.goal || {},
          score: payload.score,
          components: payload.components || {},
          reasons: payload.reasons || [],
          planning_cycles: payload.planning_cycles,
        };
        break;
      case "ACTION_STARTED":
        appState.search.phase = "EXECUTE";
        appState.robotAction = "EXECUTING";
        break;
      case "ACTION_FINISHED":
        appState.robotAction = payload.status === "succeeded" ? "SUCCEEDED" : "FAILED";
        break;
      case "REPLAN":
        appState.search.phase = "RECOVER";
        break;
      case "PAUSED":
        appState.search.status = "PAUSED";
        appState.search.phase = "PAUSED";
        break;
      case "RESUMED":
        appState.search.status = "RUNNING";
        appState.search.phase = "OBSERVE";
        break;
      case "SEARCH_EXHAUSTED":
        appState.search.status = "SEARCH_EXHAUSTED";
        showBanner("搜索空间已穷尽（SEARCH_EXHAUSTED）", "exhausted");
        break;
      case "OPERATOR_STOP":
        appState.search.status = "OPERATOR_STOP";
        showBanner("操作员停止（OPERATOR_STOP）", "error");
        break;
      case "ERROR":
        appState.search.status = "FAILED";
        showBanner("错误: " + (payload.message || payload.error_type || "unknown"), "error");
        break;
      case "MAP_UPDATED":
        appState.map = {
          revision: payload.revision,
          map_mode: payload.map_mode || "topological",
          current_node_id: payload.current_node_id,
          robot: payload.robot,
          nodes: (payload.graph || {}).nodes || [],
          edges: (payload.graph || {}).edges || [],
        };
        break;
      case "RGBD_FRAME_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.rgbd_frame = payload;
        break;
      case "SPATIAL_POSE_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.spatial_pose = payload.pose || null;
        break;
      case "SPATIAL_MAP_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.spatial_map = payload.map || null;
        break;
      case "FRONTIERS_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.frontiers = payload.frontiers || [];
        break;
      case "PLACE_CREATED":
      case "PLACE_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.place_graph = appState.spatial.place_graph || { places: [], edges: [] };
        var place = payload.place || {};
        var places = appState.spatial.place_graph.places || [];
        var idx = places.findIndex(function (p) { return p.place_id === place.place_id; });
        if (idx >= 0) { places[idx] = place; } else { places.push(place); }
        appState.spatial.place_graph.places = places;
        break;
      case "SEMANTIC_OBJECT_LOCALIZED":
        appState.spatial = appState.spatial || {};
        appState.spatial.semantic_objects = appState.spatial.semantic_objects || [];
        var obj = payload.object || {};
        var objs = appState.spatial.semantic_objects;
        var oidx = objs.findIndex(function (o) { return o.object_id === obj.object_id; });
        if (oidx >= 0) { objs[oidx] = obj; } else { objs.push(obj); }
        break;
      case "PSG_PRIOR_UPDATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.psg_prior = payload.prior || null;
        break;
      case "SEMANTIC_REGION_CREATED":
        appState.spatial = appState.spatial || {};
        appState.spatial.psg_prior = appState.spatial.psg_prior || { region_hypotheses: [] };
        appState.spatial.psg_prior.region_hypotheses = appState.spatial.psg_prior.region_hypotheses || [];
        appState.spatial.psg_prior.region_hypotheses.push(payload.region || {});
        break;
      case "LONG_TERM_GOAL_SELECTED":
        appState.spatial = appState.spatial || {};
        appState.spatial.long_term_goal = payload.intent || null;
        break;
      case "LOCAL_GOAL_PROGRESS":
        appState.spatial = appState.spatial || {};
        appState.spatial.local_goal_progress = payload.progress || null;
        break;
      case "SEARCH_FINISHED":
        appState.search.status = payload.result === "TARGET_FOUND" ? "TARGET_FOUND"
          : payload.result === "OPERATOR_STOP" ? "OPERATOR_STOP"
          : payload.result === "SEARCH_EXHAUSTED" ? "SEARCH_EXHAUSTED"
          : "FINISHED";
        appState.search.result = payload.result;
        appState.search.finish_reason = payload.finish_reason;
        appState.search.summary = payload;
        if (!appState.search.status || appState.search.status === "FINISHED") {
          showBanner("搜索结束: " + (payload.result || "FINISHED"), "exhausted");
        }
        break;
      default:
        break;
    }
    pushTimeline(event);
    renderAll();
  }

  // ------------------------------------------------------------------ //
  // Controls                                                            //
  // ------------------------------------------------------------------ //
  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json(); });
  }

  function startSearch() {
    var target = els.target.value.trim();
    if (!target) { showBanner("请输入搜索目标", "error"); return; }
    api("/api/search/start", {
      target: target,
      enable_autonomous_motion: els.chkMotion.checked,
      operator_supervised_experiment: els.chkMotion.checked,
    }).then(function (data) {
      if (!data.ok) {
        if (data.error === "emergency_stop_latched") {
          showBanner("无法开始：请先点击顶部“解除急停”，确认状态正常后再搜索", "error");
        } else {
          showBanner("无法开始: " + (data.error || "unknown"), "error");
        }
      }
    }).catch(function () { showBanner("无法开始搜索（网络错误）", "error"); });
  }

  els.btnStart.addEventListener("click", startSearch);
  els.target.addEventListener("keydown", function (event) {
    if (event.key === "Enter") startSearch();
  });
  els.btnPause.addEventListener("click", function () {
    api("/api/search/pause").catch(function () {});
  });
  els.btnResume.addEventListener("click", function () {
    api("/api/search/resume").catch(function () {});
  });
  els.btnStop.addEventListener("click", function () {
    api("/api/search/stop").then(function () {
      showBanner("已请求停止…", "error");
    }).catch(function () {});
  });
  els.btnEstop.addEventListener("click", function () {
    api("/api/search/estop").then(function () {
      showBanner("急停已触发", "error");
    }).catch(function () {});
  });

  // ------------------------------------------------------------------ //
  // Rendering                                                           //
  // ------------------------------------------------------------------ //
  function renderAll() {
    renderStatus();
    renderObservation();
    renderDecision();
    renderCandidates();
    renderTimeline();
    renderButtons();
    renderDebug();
    var spatial = appState.spatial || null;
    var hasOldMap = appState.map && Array.isArray(appState.map.nodes) && appState.map.nodes.length;
    var hasPlaces = spatial && spatial.place_graph &&
      Array.isArray(spatial.place_graph.places) && spatial.place_graph.places.length;
    if (hasOldMap || hasPlaces) {
      mapRenderer.render(appState.map || {}, spatial);
      var placeCount = hasPlaces ? spatial.place_graph.places.length : 0;
      els.mapMeta.textContent = "rev " + ((appState.map || {}).revision || 0) +
        " nodes " + ((appState.map || {}).nodes || []).length +
        " places " + placeCount +
        " frontiers " + ((spatial && spatial.frontiers) || []).length;
    }
    drawOverlay();
  }

  function renderStatus() {
    var s = appState.search || {};
    els.stTarget.textContent = s.target || "--";
    els.stPhase.textContent = s.phase || (s.status || "IDLE");
    els.stCycle.textContent = String(s.cycle || 0);
    els.stElapsed.textContent = fmtDuration(s.elapsed_seconds);
    var m = appState.targetMatch || {};
    els.stMatch.textContent = m.level || "none";
    els.stAnchor.textContent = (m.anchor_labels || []).length
      ? m.anchor_labels.join(", ") : (m.explicit_anchor_found ? "(found)" : "--");
    els.stAction.textContent = appState.robotAction || "IDLE";
    var obs = appState.observation || {};
    els.stPose.textContent = obs.pose ? "relative" : "--";
    els.scamCycle.textContent = "cycle " + (s.cycle || "--");
    // search light
    var status = s.status || "IDLE";
    var light = status === "TARGET_FOUND" ? "green"
      : (status === "RUNNING" || status === "STARTING") ? "yellow"
      : (status === "FAILED" || status === "OPERATOR_STOP") ? "red"
      : (status === "PAUSED") ? "yellow"
      : "gray";
    setLight(els.lightSearch, light);
    // robot light from status poll (set in pollStatus)
    // evidence
    var evidence = (appState.objects.target_evidence) || {};
    var html = "";
    html += "<div><span class='" + (evidence.target_confirmed ? "ok" : "no") + "'>" +
      (evidence.target_confirmed ? "✓ 目标已确认 TARGET CONFIRMED" : "✕ 目标尚未确认") + "</span></div>";
    html += "<div>目标匹配等级: <b>" + esc(m.level || "none") + "</b></div>";
    html += "<div>锚点: " + ((m.anchor_labels || []).map(function (a) { return esc(a); }).join(", ") || "未发现") + "</div>";
    html += "<div>目标分数: " + (m.target_score == null ? "--" : Number(m.target_score).toFixed(2)) + "</div>";
    (obs.objects || []).forEach(function (item) {
      var label = item.label_zh || item.label || item.name || "";
      if (label && (m.anchor_labels || []).some(function (a) { return String(label).indexOf(a) >= 0 || a.indexOf(label) >= 0; })) {
        html += "<div class='ok'>✓ 锚点物体: " + esc(label) + "</div>";
      }
    });
    html += "<div class='pend'>关系验证: " + (appState.verification ? esc(String(appState.verification.reason_zh || "")) : "pending") + "</div>";
    els.stEvidence.innerHTML = html;
  }

  function renderObservation() {
    var obs = appState.observation || {};
    var current = (obs.objects || []);
    var html = "";
    if (!current.length) {
      html = '<div class="muted">等待观察…</div>';
    } else {
      current.forEach(function (item) {
        var label = item.label_zh || item.label || item.name || "object";
        var conf = item.confidence == null ? "" : " (" + Number(item.confidence).toFixed(2) + ")";
        var pos = item.position_2d ? " " + esc(String(item.position_2d)) : "";
        html += '<div class="row"><span>' + esc(label) + conf + "</span><span class='cnt'>" +
          (item.mask_area_ratio ? Math.round(item.mask_area_ratio * 100) + "%" : "") +
          esc(pos) + "</span></div>";
      });
    }
    els.obsCurrent.innerHTML = html;
    var seen = (appState.objects.session_seen || []);
    if (!seen.length) {
      els.obsSeen.textContent = "--";
    } else {
      els.obsSeen.innerHTML = seen.map(function (item) {
        return '<div class="row"><span>' + esc(item.label) + "</span><span class='cnt'>" +
          item.observations + " 次</span></div>";
      }).join("");
    }
  }

  function renderDecision() {
    var goal = appState.selectedGoal;
    if (!goal) {
      els.decIntent.textContent = "--";
      els.decReason.textContent = "";
      els.decScores.textContent = "";
      return;
    }
    var g = goal.goal || {};
    els.decIntent.textContent = g.goal_type || "--";
    var detail = goalDetailText(g);
    els.decReason.textContent = detail;
    var reasons = (goal.reasons || []).join("；");
    if (reasons) els.decReason.textContent += (detail ? " — " : "") + reasons;
    var comps = goal.components || {};
    var scoreRows = [
      ["semantic_relevance", "语义相关"],
      ["information_gain", "信息增益"],
      ["novelty", "新颖度"],
      ["frontier_bonus", "前沿奖励"],
      ["continuity_bonus", "连续性"],
      ["visited_penalty", "已访问惩罚"],
      ["negative_evidence_penalty", "负证据惩罚"],
      ["navigation_failure_penalty", "导航失败惩罚"],
      ["estimated_motion_cost", "运动代价"],
      ["oscillation_penalty", "振荡惩罚"],
      ["score", "总分"],
    ];
    els.decScores.innerHTML = scoreRows.map(function (row) {
      var key = row[0];
      var value = comps[key];
      if (value === undefined || value === null) return "";
      return '<div class="score-row"><span>' + row[1] + "</span><b>" +
        Number(value).toFixed(2) + "</b></div>";
    }).join("");
  }

  function goalDetailText(g) {
    if (g.semantic_reason) return g.semantic_reason;
    switch (g.goal_type) {
      case "ROTATE_VIEW": {
        var dyaw = g.relative_dyaw;
        if (dyaw === undefined || dyaw === null) return "旋转观察";
        return "旋转观察 " + Math.abs(Number(dyaw)).toFixed(0) + "° " + (dyaw > 0 ? "右侧" : "左侧");
      }
      case "RELATIVE_MOVE": {
        var dx = g.relative_dx;
        return "前进 " + (dx == null ? "--" : Number(dx).toFixed(2)) + " m";
      }
      case "INSPECT_ANCHOR":
        return "检查锚点" + (g.semantic_anchor ? "「" + g.semantic_anchor + "」" : "");
      case "REVISIT_NODE":
        return "重访节点 " + (g.target_node_id || "");
      case "REOBSERVE":
        return "重新观察当前视角";
      case "STOP":
        return "停止";
      default:
        return g.goal_type || "";
    }
  }

  function renderCandidates() {
    var list = appState.candidates || [];
    if (!list.length) {
      els.decCandidates.textContent = "";
      return;
    }
    var s = appState.selectedGoal || {};
    var selectedId = (s.goal || {}).goal_id;
    els.decCandidates.innerHTML = list.map(function (c) {
      var g = c.goal || {};
      var sel = selectedId && g.goal_id === selectedId;
      var score = c.score == null ? "" : " 总分 " + Number(c.score).toFixed(2);
      var label = goalDetailText(g) || g.goal_type;
      return '<div class="cand' + (sel ? " selected" : "") + '">' +
        esc(g.goal_type) + " · " + esc(label) +
        "<span class='cscore'>" + score + (c.selected ? " ★" : "") + "</span></div>";
    }).join("");
  }

  function pushTimeline(event) {
    appState.events.push({
      event_type: event.event_type,
      timestamp: event.timestamp,
      cycle: event.cycle,
    });
    if (appState.events.length > 400) appState.events = appState.events.slice(-400);
  }

  function renderTimeline() {
    var items = appState.events.slice(-120).reverse();
    if (!items.length) {
      els.timeline.innerHTML = '<div class="muted">等待事件…</div>';
      return;
    }
    els.timeline.innerHTML = items.map(function (item) {
      var t = new Date(item.timestamp * 1000).toTimeString().slice(0, 8);
      var meta = [];
      if (item.cycle !== undefined && item.cycle !== null) meta.push("c" + item.cycle);
      return '<div class="tline"><span class="ttype">' + esc(item.event_type) +
        '</span><span class="tmeta">' + t + (meta.length ? " · " + meta.join(",") : "") + "</span></div>";
    }).join("");
  }

  function renderButtons() {
    var status = (appState.search || {}).status || "IDLE";
    var running = status === "RUNNING" || status === "STARTING";
    var paused = status === "PAUSED";
    var finished = ["TARGET_FOUND", "SEARCH_EXHAUSTED", "OPERATOR_STOP", "FINISHED", "FAILED"].indexOf(status) >= 0;
    els.btnStart.disabled = running || paused;
    els.btnPause.disabled = !running;
    els.btnResume.disabled = !paused;
    els.btnStop.disabled = !(running || paused);
    els.btnEstop.disabled = false;
    els.sessionInfo.textContent = (appState.search && appState.search.session_id) ? appState.search.session_id : "";
  }

  function renderDebug() {
    if (!els.debugPanel.classList.contains("hidden")) {
      var s = appState.search || {};
      els.debugGoalGraph.textContent = pretty(s.goal_graph);
      els.debugSceneGraph.textContent = pretty({
        objects: (appState.observation || {}).objects || [],
        relations: (appState.observation || {}).relations || [],
      });
      els.debugCandidates.textContent = pretty(appState.candidates);
      els.debugRaw.textContent = pretty(appState);
    }
  }

  function drawOverlay() {
    var canvas = els.overlay;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    var detections = lastDetectionFrame;
    if (!detections || !detections.length) return;
    detections.forEach(function (item) {
      var bbox = item.bbox_2d || item.bbox;
      if (!Array.isArray(bbox) || bbox.length < 4) return;
      var x1 = bbox[0] * canvas.width;
      var y1 = bbox[1] * canvas.height;
      var x2 = bbox[2] * canvas.width;
      var y2 = bbox[3] * canvas.height;
      ctx.strokeStyle = "#38bdf8";
      ctx.lineWidth = 2;
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      var label = String(item.label || item.name || "object");
      var score = item.score == null ? "" : " " + Number(item.score).toFixed(2);
      ctx.fillStyle = "rgba(56, 189, 248, 0.9)";
      ctx.font = "bold 13px sans-serif";
      var text = label + score;
      var tw = ctx.measureText(text).width;
      ctx.fillRect(x1, Math.max(0, y1 - 18), tw + 8, 18);
      ctx.fillStyle = "#06121b";
      ctx.fillText(text, x1 + 4, Math.max(13, y1 - 5));
    });
  }

  function showBanner(text, kind) {
    els.banner.textContent = text;
    els.banner.className = "search-banner " + kind;
  }

  function setLight(el, cls) {
    if (el) el.className = "light " + cls;
  }

  function pretty(value) {
    try {
      return JSON.stringify(value, null, 2) || "null";
    } catch (e) {
      return String(value);
    }
  }

  function esc(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDuration(seconds) {
    if (seconds === undefined || seconds === null) return "00:00";
    var s = Math.max(0, Math.floor(seconds));
    var m = Math.floor(s / 60);
    var h = Math.floor(m / 60);
    m = m % 60;
    s = s % 60;
    return (h > 0 ? String(h).padStart(2, "0") + ":" : "") +
      String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  // ------------------------------------------------------------------ //
  // Tabs                                                                //
  // ------------------------------------------------------------------ //
  document.querySelectorAll("#tabs .tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("#tabs .tab").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      var tab = btn.dataset.tab;
      document.querySelectorAll(".tab-pane").forEach(function (pane) { pane.classList.remove("active"); });
      var pane = document.getElementById("tab-" + tab);
      if (pane) pane.classList.add("active");
    });
  });

  // Debug toggle
  els.chkDebug.addEventListener("change", function () {
    els.debugPanel.classList.toggle("hidden", !els.chkDebug.checked);
  });
  document.querySelectorAll(".dtab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".dtab").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll(".debug-pre").forEach(function (pre) { pre.classList.add("hidden"); });
      var target = document.getElementById("debug-" + btn.dataset.dtab);
      if (target) target.classList.remove("hidden");
    });
  });

  // Map node detail dismiss
  document.getElementById("search-map").addEventListener("click", function () {
    els.mapNodeDetail.classList.add("hidden");
  });

  // ------------------------------------------------------------------ //
  // Camera FPS                                                          //
  // ------------------------------------------------------------------ //
  var lastFrameAt = null;
  var fps = 0;
  els.cam.addEventListener("load", function () {
    var now = performance.now();
    if (lastFrameAt !== null) {
      var dt = (now - lastFrameAt) / 1000;
      if (dt > 0.01) fps = 1 / dt;
    }
    lastFrameAt = now;
    els.scamFps.textContent = Math.round(fps) + " FPS";
  });

  // ------------------------------------------------------------------ //
  // Polling: /api/status (lights) + /api/search/state (reconcile)       //
  // ------------------------------------------------------------------ //
  function pollStatus() {
    fetch("/api/status").then(function (response) { return response.json(); })
      .then(function (status) {
        var camera = status.camera || {};
        els.scamAge.textContent = "帧年龄 " + fmtAge(camera.age_seconds);
        els.camStale.classList.toggle("hidden", camera.fresh !== false || !camera.available);
        var motion = status.motion || {};
        setLight(els.lightRobot, motion.available ? (motion.state === "ESTOP" ? "red" : "green") : "gray");
        if (status.owner) {
          renderOwner(status.owner);
          if (status.owner.owner === "AUTONOMOUS" && !(appState.search || {}).session_id) {
            showBanner("自主搜索正在后端运行，页面刷新后已恢复会话", "exhausted");
          }
        }
        renderSystem(status);
      })
      .catch(function () {});
  }

  function pollSearchState() {
    fetch("/api/search/state").then(function (response) { return response.json(); })
      .then(function (state) {
        if (state.session_id) {
          if (!appState.search.session_id || appState.search.session_id === state.session_id) {
            var fresh = appState.search.session_id !== state.session_id;
            appState.search = {};
            appState.search.session_id = state.session_id;
            applyStateSnapshot(state);
            if (fresh) mapRenderer.render(appState.map, appState.spatial);
          }
        } else if (appState.search.session_id && !ws) {
          appState.search = {};
          renderAll();
        }
      })
      .catch(function () {});
  }

  function renderOwner(owner) {
    var el = document.getElementById("sys-owner");
    if (el) el.textContent = pretty(owner);
  }

  function renderSystem(status) {
    els.sysCamera.textContent = pretty(status.camera);
    els.sysWorker.textContent = pretty(status.worker);
    els.sysMotion.textContent = pretty(status.motion);
    els.sysSearch.textContent = pretty(status.search);
    els.sysLlm.textContent = pretty({
      enabled: status.llm && status.llm.enabled,
      analysis_status: status.llm && status.llm.analysis && status.llm.analysis.status,
      model: status.llm && status.llm.analysis && status.llm.analysis.model,
      error: status.llm && status.llm.analysis && status.llm.analysis.error,
    });
    fetch("/api/search/readiness").then(function (r) { return r.json(); })
      .then(function (data) { els.sysReadiness.textContent = pretty(data); })
      .catch(function () {});
    fetch("/api/search/history").then(function (r) { return r.json(); })
      .then(function (data) { els.sysHistory.textContent = pretty(data.sessions || []); })
      .catch(function () {});
  }

  function fmtAge(seconds) {
    if (seconds === null || seconds === undefined) return "--";
    return seconds.toFixed(1) + "s";
  }

  // ------------------------------------------------------------------ //
  // init                                                                //
  // ------------------------------------------------------------------ //
  pollStatus();
  pollSearchState();
  setInterval(pollStatus, 1000);
  setInterval(pollSearchState, 3000);
  connect();
})();
