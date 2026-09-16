"""§12.4 WebUI session：archive 里有旧 terminal，**新进程的 live graph 必须是空的**
（§30 点名的本文件）。

场景原文（计划书 §12.4）：

    archive 中有 old terminal: P1, F01, F02...
    新 WebUI process 启动：history 可找到旧 session / live graph 必须空
    点击 start：仍先空 / 第一 observation 后才 P1

**不重抄哪些。** 前两句里的 archive 半已被
`tests/test_search_session_archive.py:120`（`test_service_keeps_terminal_snapshot_in_history_without_resurrecting_live_session`，
断言 fresh 服务端 `session_id==""` / `status=="IDLE"` / `spatial.semantic_graph is None`
而 history 仍查得到旧 session）与
`tests/test_search_persistent_history_web_contract.py:22`（JS 层 history 与 live state 分离）
覆盖。本文件只补**后两句** —— 全仓零断言：

* `SearchStateStore.reset()` 之后（= 点击 start）live graph 仍须为空；
* 把已经跑满的 store `reset()` 掉，旧 Places / 物件 / 时间线**一件都不许留**；
* P1 出现的**时机**：启动序列（`SESSION_CREATED` → `TASK_UNDERSTANDING` →
  `SESSION_STARTED` → `SEARCH_STATE_CHANGED` → 第一帧 `OBSERVATION_UPDATED`）
  **一个 Place 都不许造**。

**本文件实测出来的两条机制（不是读注释读出来的）：**

1. **真正让 live graph 出现 P1 的唯一路径是 `MAP_UPDATED` 携带
   `semantic_navigation_graph_v1` 的投影**（`search_state_store.py:717-718`）。
2. 因此 `PLACE_CREATED` / `PLACE_UPDATED` 这两条路**在真机上都不产 Place**：
   runner 实际发射的事件名里**没有** `place_created`（实测
   `grep -o '"event": "[a-z_]*"' scripts/go2w/run_semantic_exploration.py` 只得
   `place_updated`），所以 `_on_place_created` 是**死路径**；而 `_on_place_updated`
   只做**替换** —— `places` 为空时它的列表推导推不出任何元素，**建不出新 Place**。
   这两条都写进了用例，免得日后有人以为"空图"是靠这两条路守住的。

**诚实边界**：本文件是 store 层的契约测试，不驱动 WebUI/HTTP。它证明"给定事件序列，
store 的快照是什么"，不证明"真机跑起来事件序列就是这样"。后者要靠真机事件流。

**顺带记一条没能测的**：`reset()` 里的 `self._current_objects = []`（`:266`）
**只写不读** —— 全仓 `grep _current_objects` 只有三处赋值（`__init__` / `reset` /
`restore`）加 `_on_objects` 的一处覆盖，**没有任何读点**。所以"没清它"没有可观测
后果，本文件不为此编断言。同一段里的 `_map_revision` / `_timeline` / `_session_seen`
则各有真实读点，下面分别用"reset 之后**下一步**的后果"来测 —— 直接断言
`reset()` 之后的快照值是没有分辨力的（那些值由 `_empty_snapshot()` 无条件归零）。
"""

from __future__ import annotations

from app.live_robot.search_event import (
    MAP_UPDATED,
    OBJECTS_UPDATED,
    OBSERVATION_UPDATED,
    PLACE_CREATED,
    PLACE_UPDATED,
    SEARCH_STATE_CHANGED,
    SESSION_CREATED,
    SESSION_STARTED,
    TASK_UNDERSTANDING,
    SearchEvent,
)
from app.live_robot.search_state_store import (
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STARTING,
    SearchStateStore,
)
from app.spatial.place_graph import PlaceGraph
from app.spatial.semantic_navigation_graph import SemanticNavigationGraph

SESSION = "search_20260916_120000_abcd1234"


def _event(event_type: str, payload: dict | None = None, ts: float = 1.0) -> SearchEvent:
    return SearchEvent(
        event_id=1, session_id=SESSION, timestamp=ts,
        event_type=event_type, payload=payload or {},
    )


def _semantic_graph_dict(*, with_place: bool) -> dict:
    """用**真实生产者**造 `semantic_navigation_graph_v1`，不手写 P1。

    手写 `{"places": [{"place_id": "P1"}]}` 会让"P1 这个 id 长什么样"变成我的假设；
    走 `PlaceGraph.register_observation` 则钉住真实格式（`place_graph.py:188`
    的 `f"P{self._sequence}"`）。
    """
    place_graph = PlaceGraph()
    if with_place:
        place_graph.register_observation(
            observation_id="obs_0", heading_sector=0, objects=[],
        )
    return SemanticNavigationGraph(place_graph=place_graph).to_dict()


def _places(store: SearchStateStore) -> list[dict]:
    place_graph = store.spatial_snapshot()["place_graph"] or {}
    return list(place_graph.get("places") or [])


def _started_store() -> SearchStateStore:
    """跑到「点击 start 之后、第一帧 observation 之前」的那个时刻。"""
    store = SearchStateStore()
    store.reset(session_id=SESSION, target="垃圾桶")
    for event_type, payload in (
        (SESSION_CREATED, {"target": "垃圾桶"}),
        (TASK_UNDERSTANDING, {"task": {"canonical_target": "垃圾桶"}}),
        (SESSION_STARTED, {"phase": "OBSERVE"}),
        (SEARCH_STATE_CHANGED, {"phase": "OBSERVE"}),
    ):
        store.apply(_event(event_type, payload))
    return store


# --------------------------------------------------------------------------
# A. 新进程 / 点击 start：live graph 必须是空的
# --------------------------------------------------------------------------

def test_a_fresh_store_has_no_live_graph_at_all():
    """全新的 store（= 新 WebUI process）连 `place_graph` 都是 `None`，不是空列表。

    这个区别有意义：`None` 表示"这副图从来没有过"，`{"places": []}` 表示"有过、
    只是眼下没内容"。前端据此区分"还没开始"与"开始了但没建到 Place"。
    """
    store = SearchStateStore()
    assert store.spatial_snapshot()["place_graph"] is None
    assert store.spatial_snapshot()["semantic_objects"] == []
    assert store.spatial_snapshot()["frontiers"] == []
    assert store.map_snapshot()["nodes"] == []
    assert store.map_snapshot()["revision"] == 0
    assert store.snapshot()["status"] == STATUS_IDLE


def test_the_startup_sequence_never_creates_a_place():
    """**§12.4「点击 start 仍先空」。** 启动序列 + 第一帧 observation 走完，
    live graph 里**一个 Place 都不许有**。

    危险形状：某个 handler 为了"让页面早点有东西看"而在 `SESSION_STARTED` 时
    凭空塞一个 Place。那样狗还没动，图里就已经有个"当前所在 Place"，而它就是靠
    真实观测融合出来的位姿锚点 —— 一个没观测支撑的锚点会污染后续所有关联。
    """
    store = _started_store()
    store.apply(_event(OBSERVATION_UPDATED, {"bundle_id": "obs_0"}))
    assert _places(store) == [], "第一帧 observation 本身不是建 Place 的理由"
    assert store.map_snapshot()["nodes"] == []
    assert store.snapshot()["status"] == STATUS_RUNNING, (
        "start 之后状态必须已经 RUNNING —— 空的理由必须是'还没观测到'，"
        "而不是'压根没开始'"
    )


def test_an_empty_semantic_graph_projects_empty_places_not_none():
    """老会话的图还没到时也会收到 `MAP_UPDATED`，但里面没有 Place。

    此时 `place_graph` **从 `None` 变成 `{"places": []}`** —— 是"有图但空"。
    这条钉的是投影的形状：`places` / `edges` / `current_place_id` 三个键都在，
    前端不会因为缺键而渲染出错。
    """
    store = _started_store()
    store.apply(_event(MAP_UPDATED, {"graph": _semantic_graph_dict(with_place=False)}))
    place_graph = store.spatial_snapshot()["place_graph"]
    assert place_graph == {"places": [], "edges": [], "current_place_id": None}
    assert store.map_snapshot()["nodes"] == []


# --------------------------------------------------------------------------
# B. 第一帧真实 observation 之后，P1 才出现
# --------------------------------------------------------------------------

def test_the_first_place_appears_only_when_the_semantic_graph_carries_one():
    """**§12.4「第一 observation 后才 P1」。** 从「start 过、观测过、还没 Place」
    出发，把带 P1 的真实语义图送进来 —— `P1` 这时才出现，且 `current_place_id`
    一并跟上。

    **这是真机上唯一会产 Place 的路径**（见模块 docstring 第 1 条）：runner 不发
    `place_created`，而 `place_updated` 建不出新 Place。所以这条断言的正面意义是
    "空图守得住"，反面意义是"别指望给 PLACE_CREATED 接线就能管住 P1 的出现时机"。
    """
    store = _started_store()
    store.apply(_event(OBSERVATION_UPDATED, {"bundle_id": "obs_0"}))
    assert _places(store) == []

    store.apply(_event(MAP_UPDATED, {"graph": _semantic_graph_dict(with_place=True)}))

    assert [place["place_id"] for place in _places(store)] == ["P1"]
    assert store.spatial_snapshot()["place_graph"]["current_place_id"] == "P1"
    assert store.map_snapshot()["nodes"][0]["node_id"] == "P1"


def test_place_updated_cannot_create_a_place_that_is_not_there_yet():
    """`PLACE_UPDATED` 只做**替换**，在空图上它什么都不产生。

    这条钉的是一个容易误判的形状：`_on_place_updated` 的
    `places = [p if p.get("place_id") != place_id else place for p in places]`
    看起来像"插入或更新"，实际上**列表为空时推不出任何元素**。所以就算有人把
    runner 改成发 `place_updated` 来"提前建 Place"，图的出现时机也不会变 ——
    只会在真有一个 P1 之后把它的内容换掉。
    """
    store = _started_store()
    store.apply(_event(PLACE_UPDATED, {"place": {"place_id": "P7", "visit_count": 1}}))
    assert _places(store) == [], "空图上 PLACE_UPDATED 不许凭空建出 Place"

    # 有 P1 之后，它才轮到做替换。
    store.apply(_event(MAP_UPDATED, {"graph": _semantic_graph_dict(with_place=True)}))
    store.apply(_event(PLACE_UPDATED, {"place": {"place_id": "P1", "visit_count": 9}}))
    places = _places(store)
    assert [place["place_id"] for place in places] == ["P1"], "替换不是追加"
    assert places[0]["visit_count"] == 9


def test_the_runner_never_emits_place_created():
    """**源码级契约，诚实标注。** 上一条说"别指望 PLACE_CREATED"，这条把那个前提
    钉成事实：runner 发射的事件名里**没有** `place_created`。

    若哪天 runner 开始发 `place_created`，这条会红 —— 那是好事：说明 `_on_place_created`
    从死路径变活，§12.4 的 P1 出现时机需要按新路径重新验证，而不是继续按
    "只有 MAP_UPDATED 投影会建 Place"来推理。
    """
    import re
    from pathlib import Path

    runner = Path(__file__).resolve().parents[1] / "scripts/go2w/run_semantic_exploration.py"
    emitted = set(re.findall(r'"event": "([a-z_]+)"', runner.read_text(encoding="utf-8")))
    assert "place_updated" in emitted, "runner 应当发 place_updated"
    assert "place_created" not in emitted, (
        "runner 开始发 place_created 了 —— _on_place_created 从死路径变活，"
        "请同时验证它是否会在第一帧观测前造 Place"
    )


def test_place_created_would_append_if_the_runner_ever_emitted_it():
    """对照实验：`PLACE_CREATED` 确实是**追加**语义。

    这条不是为了让它发生，而是把两条路的差别摆在一起：`PLACE_CREATED` 追加、
    `PLACE_UPDATED` 只替换。前者一旦被接上就会凭空建 Place，所以"runner 不发它"
    这件事本身是空图的一道防线（上一条在守）。
    """
    store = _started_store()
    store.apply(_event(PLACE_CREATED, {"place": {"place_id": "P9"}}))
    assert [place["place_id"] for place in _places(store)] == ["P9"]


# --------------------------------------------------------------------------
# C. 点击 start 是一场真正的「live reset」：旧会话一件都不留
# --------------------------------------------------------------------------

def _populated_store() -> SearchStateStore:
    """跑满一轮：有 Place、有节点、有物件、有 frontier、有时间线、有已见计数。"""
    store = _started_store()
    graph = _semantic_graph_dict(with_place=True)
    graph["objects"] = [{"object_id": "O1", "label": "办公椅"}]
    graph["frontiers"] = [{"frontier_id": "F01"}]
    store.apply(_event(MAP_UPDATED, {"graph": graph}))
    store.apply(_event(OBSERVATION_UPDATED, {"bundle_id": "obs_0"}))
    # 旧会话反复看到同一把椅子 —— 让 ``_session_seen`` 攒起来。
    for _ in range(3):
        store.apply(_event(OBJECTS_UPDATED, {"current": [{"label": "办公椅"}]}))
    return store


def test_reset_clears_a_fully_populated_live_graph():
    """已经跑满的 store 被 `reset()` 之后，旧会话的 Place / 节点 / 物件 / frontier /
    目标**一件都不许留**。任何一处漏清，都会让新会话的页面在第一帧观测之前就显示
    上一个会话的地图 —— 而"上一个会话的地图"在狗已经移动过之后是**错的位置**，
    比空图危险得多。

    **注意这条证据的边界**：这些字段都是 `_empty_snapshot()` 无条件归零的，所以
    它证明的是"快照面干净"，**不证明** reset 有没有清那几个私有计数器 —— 后者由
    下面三条各自的可观测后果来证。别把这一条当成"reset 全对"的总结论。
    """
    store = _populated_store()
    # 先确认它真的满了 —— 否则下面"清空"的断言会退化成恒真。
    assert _places(store), "前置条件：reset 之前图里必须有 Place"
    assert store.map_snapshot()["nodes"], "前置条件：reset 之前必须有节点"
    assert store.spatial_snapshot()["semantic_objects"], "前置条件：必须有物件"
    assert store.spatial_snapshot()["frontiers"], "前置条件：必须有 frontier"
    assert store.snapshot()["timeline"], "前置条件：必须有时间线"
    assert store.objects_snapshot()["session_seen"], "前置条件：必须有已见计数"
    assert store.map_snapshot()["revision"] > 0, "前置条件：revision 必须已经推进过"

    store.reset(session_id=SESSION, target="垃圾桶")

    assert store.spatial_snapshot()["place_graph"] is None
    assert store.spatial_snapshot()["semantic_objects"] == []
    assert store.spatial_snapshot()["frontiers"] == []
    assert store.spatial_snapshot()["semantic_graph"] is None
    assert store.map_snapshot()["nodes"] == []
    assert store.map_snapshot()["edges"] == []
    assert store.snapshot()["selected_goal"] is None


def test_reset_reidentifies_the_session_and_restarts_the_revision():
    """`reset()` 把 `map.revision` 打回 0，并换上新的 session/target。

    revision 归零是有意的：前端持有旧 revision 时会看到"变小了"，据以判定
    "这是一张新图"。若 revision 接着往上涨，一个拿着旧图的页面就分不清
    "图更新了"和"换会话了"。
    """
    store = _populated_store()
    assert store.map_snapshot()["revision"] > 0

    store.reset(session_id="search_20260916_130000_beef0000", target="灭火器")

    assert store.map_snapshot()["revision"] == 0
    snapshot = store.snapshot()
    assert snapshot["session_id"] == "search_20260916_130000_beef0000"
    assert snapshot["target"] == "灭火器"
    assert snapshot["status"] == STATUS_STARTING, (
        "reset 之后是 STARTING —— 新会话还没跑起来，不该直接报 RUNNING"
    )
    assert snapshot["finished_at"] is None, "上一个会话的结束时间不许留到新会话"
    assert snapshot["result"] == ""
    assert snapshot["finish_reason"] == ""


def test_reset_restarts_the_revision_counter_not_just_the_snapshot_value():
    """**`_map_revision` 的判别性证据。** 光断言 `reset()` 之后
    `map.revision == 0` 是**没有分辨力的** —— `_empty_snapshot()` 本来就把它写成 0，
    注入"删掉 reset 里的 `self._map_revision = 0`"照样全绿（实测确认过）。

    私有计数器只在**下一条 `MAP_UPDATED`** 上显形：清过就是 1，没清就是旧值 +1。
    没清的后果是 revision 一路涨，前端永远看不到"变小"这个换会话信号。
    """
    store = _populated_store()
    before = store.map_snapshot()["revision"]
    assert before > 0

    store.reset(session_id=SESSION, target="垃圾桶")
    store.apply(_event(MAP_UPDATED, {"graph": _semantic_graph_dict(with_place=False)}))

    assert store.map_snapshot()["revision"] == 1, (
        f"新会话的第一张图必须是 revision=1（旧值 {before} 不许带过来）"
    )


def test_reset_restarts_the_timeline_not_just_the_snapshot_view():
    """**`_timeline` 的判别性证据。** 同上：`snapshot()["timeline"] == []` 由
    `_empty_snapshot()` 兜底，没有分辨力。

    `_timeline` 是 `deque`，被 `_append_timeline_locked` 持续追加。没清它，新会话
    的第一条事件就会把**上一个会话的整条时间线**带回页面 —— 操作员在新会话里看到
    上一轮的决策过程，会以为狗已经做过那些动作。
    """
    store = _populated_store()
    assert len(store.snapshot()["timeline"]) > 1

    store.reset(session_id=SESSION, target="垃圾桶")
    store.apply(_event(SESSION_CREATED, {"target": "垃圾桶"}))

    assert len(store.snapshot()["timeline"]) == 1, (
        "新会话的时间线必须只含新会话的事件"
    )


def test_reset_restarts_the_object_seen_counters():
    """**`_session_seen` 的判别性证据。** 它是 `Counter`，`_on_objects` 里
    `+= 1` 累加，而累加值直接进 `objects.session_seen`。

    没清它的后果比看起来严重：`session_seen` 的计数是"这个物件我确认过几次"的
    依据，旧会话攒下的次数会让新会话**第一次看到**就把某个标签当成"反复确认过"，
    于是刚开搜就出现一个不该有的高置信物件。
    """
    store = _populated_store()
    seen = {item["label"]: item["observations"]
            for item in store.objects_snapshot()["session_seen"]}
    assert seen == {"办公椅": 3}, f"前置条件：旧会话应已看到 3 次，实际 {seen}"

    store.reset(session_id=SESSION, target="垃圾桶")
    store.apply(_event(OBJECTS_UPDATED, {"current": [{"label": "办公椅"}]}))

    seen = {item["label"]: item["observations"]
            for item in store.objects_snapshot()["session_seen"]}
    assert seen == {"办公椅": 1}, (
        f"新会话第一次看到就该记 1 次，不是旧会话的 3 次加一：{seen}"
    )

