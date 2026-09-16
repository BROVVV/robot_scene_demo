"""LIO「激光修正停摆」判据 —— 计划书 §13.4 健康门的可观测层接线。

本模块是**纯逻辑，不 import rclpy**。理由与 ``app/spatial/slam_web_map_state.py``
开头那段相同：生产调用方 ``scripts/go2w/webui_map_snapshot_writer.py`` 是个 ROS
节点，而离线测试环境没有 rclpy，所以判据必须住在能被 pytest 直接喂日志原文的地方。

判据本身**不是新发明的**：逐字复刻
``artifacts/slam_validation/lio_divergence_20260915/tools/lio_precursor_watch.py``
里那条**唯一在两次真机发散中都命中**的判据，包括 ``fix == 0.0`` 这个严格浮点
比较 —— 改任何一点都会让那份校准失效。

2026-09-16 在 ``artifacts/slam_validation/lio_divergence_20260915/`` 上逐帧复算：

  负样本（有分辨力的证据）
    calibration/t_neg_drive.txt      7837 帧  max_bad_run = 8   ← 余量只有 2 帧
    calibration/t_neg_static.txt     2600 帧  max_bad_run = 0
    calibration/t_neg_turn.txt        441 帧  max_bad_run = 0   ← 急转不误报
    lio_phase_frames_s2.csv          4690 帧  max_bad_run = 0
  正样本
    calibration/t_pos_1240_end.txt   2605 帧  触发@538
    calibration/t_pos_1320.txt        400 帧  触发@75
    frames_1320.csv                 12456 帧  触发@12131

  已知盲区（**不许**把它说成预警）
    calibration/t_pos_climb.txt      5336 帧  完全不触发 —— 那是 12:40 死亡前的
    爬升期。这个判据覆盖的是终末段，13:20 那次只提前 0.4 秒。

**为什么必须锁存（本模块与那个离线工具最重要的区别）**：触发之后 ``bad_run``
会迅速跌回 0，且其后几千帧再也不触发 —— 而 LIO 已经死了。实测：

    t_pos_1240_end.txt  触发后 2067 帧中 2050 帧 run<10，只二次触发 1 次
    t_pos_1320.txt      触发后  325 帧中  299 帧 run<10，只二次触发 1 次
    lio_phase_frames.csv 触发后 5037 帧中 5011 帧 run<10，只二次触发 1 次

所以照搬离线工具那种「回落即复位」的写法，会在几秒内把闸门重新打开，制造出
比现在更糟的假绿。**复位只有一条路：观察到 LIO 栈被重启**（调用方在 launch
日志被截断时调 ``note_session_restart()``）。``bad_run`` 归零不是恢复的证据。
"""

from __future__ import annotations

import re

# 词表必须与 app/spatial/slam_web_map_state.py:19-21 一致：读取端
# app/manual_web_demo/web_server.py:377 判 != DEGRADED_LIO_DRIFT，
# 前端 static/slam_map_3d.js:274 判 === HEALTHY 才亮绿灯。
# 这里刻意不 import 那个模块 —— 为了两个字面量把 app 包依赖拉进一个 ROS 节点是
# 负收益。漂移由 tests/test_lio_nofix_gate_calibration.py 的一条断言兜住。
HEALTHY = "HEALTHY"
DEGRADED_LIO_DRIFT = "DEGRADED_LIO_DRIFT"

_NUM = r"(-?[\d.]+)"
_POS = re.compile(r"pos=\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\)")


def _value(line: str, key: str) -> float | None:
    match = re.search(key + r"=" + _NUM, line)
    return float(match.group(1)) if match else None


class LioNofixJudge:
    """吃 ``[GO2W-DIAG]`` 行，回答「LIO 的激光修正是不是停摆了」。"""

    # 单帧出现 iter 撞上限是转弯瞬态（健康数据里最长连续 8 帧），连续 ≥10 帧才报警。
    # 这两个常数是上面那份校准的一部分，不要改。
    MIN_CONSECUTIVE = 10
    # 少于 10 帧时判据**在构造上不可能触发**，此时说 HEALTHY 是逻辑上的假绿，
    # 所以宁可不表态（health 返回 None，调用方不写这个字段）。
    MIN_FRAMES_FOR_VERDICT = 10

    def __init__(self) -> None:
        self._frames = 0
        self._bad_run = 0
        self._latched = False
        self._reason = ""

    def feed(self, line: str) -> None:
        """喂一行 launch log 原文。不含诊断字段的行是空操作，不计帧。"""
        it = _value(line, "iter")
        fix = _value(line, "fix_t")
        if it is None or fix is None:
            return
        self._frames += 1
        self._bad_run = self._bad_run + 1 if (it >= 10 and fix == 0.0) else 0
        if self._bad_run >= self.MIN_CONSECUTIVE and not self._latched:
            self._latched = True
            self._reason = self._describe(line, it)

    def note_session_restart(self) -> None:
        """调用方观察到 LIO 栈重启（launch 日志被截断）时调用 —— 唯一的复位路径。

        同时把帧数清零：复位后必须再攒够 MIN_FRAMES_FOR_VERDICT 帧好帧才会回到
        HEALTHY，否则重启瞬间会有一个恒绿窗口。
        """
        self._frames = 0
        self._bad_run = 0
        self._latched = False
        self._reason = ""

    @property
    def health(self) -> str | None:
        """``DEGRADED_LIO_DRIFT`` / ``HEALTHY`` / ``None``（不表态）。

        ``None`` 是设计的一部分，不是错误状态：日志路径不对、LIO 根本没起、
        或帧数不足时，调用方应当**不写** mapping_health 字段，让前端显示 "--"、
        让就绪闸门放行。这延续了写端脚本自己立的原则 ——
        「拿不到的一律不编，好过一个假的 0 或者假的 HEALTHY」。
        """
        if self._latched:
            return DEGRADED_LIO_DRIFT
        if self._frames < self.MIN_FRAMES_FOR_VERDICT:
            return None
        return HEALTHY

    @property
    def reason(self) -> str:
        return self._reason if self._latched else ""

    def _describe(self, line: str, it: float) -> str:
        pos = _POS.search(line)
        where = (
            f"pos=({float(pos.group(1)):.2f},{float(pos.group(2)):.2f},"
            f"{float(pos.group(3)):.2f})" if pos else "pos=?"
        )
        return (
            f"激光修正停摆 {self._bad_run} 帧"
            f"（iter={it:.0f} fix_t=0，阈值 {self.MIN_CONSECUTIVE} 帧）"
            f" v={_value(line, 'v') or 0.0:.2f}"
            f" act={_value(line, 'act') or 0.0:.3f}"
            f" {where}"
            " —— 大概率已发散，需重启 LIO 栈后才能继续"
        )
