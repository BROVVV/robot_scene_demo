# Copyright 2026 robot_scene_demo maintainers

"""注入校准：LIO「激光修正停摆」判据必须有分辨力，且必须锁存。

计划书 §13.4 的健康门此前是假绿（``web_server.py:377`` 的 ``slam_map_not_drifting``
恒真）。接线它之前，必须先证明两件事，否则就是拿一个未校准的闸门去拦真机搜索：

1. **有分辨力** —— 真实负样本上零误报，真实正样本上确实触发。
2. **锁存是真的在干活** —— 判据触发后会自己闭嘴（实测触发后几千帧里 run 跌回 0
   且只二次触发 1 次），所以「回落即复位」的写法会重新打开闸门。用例
   ``test_latch_survives_the_run_clearing_that_fools_a_self_clearing_judge``
   就是这条的**注入校准**：拿掉锁存它必须失败。

数据源：``artifacts/slam_validation/lio_divergence_20260915/``（2026-09-15 两次真机
发散 + 三轮控制变量实验的逐帧转录）。该目录**未被 git 跟踪**，所以依赖它的用例一律
``skipif`` 并在理由里写明预期数字 —— 数据丢了测试不能假装通过。不依赖它的用例跑
``tests/fixtures/lio_diag/`` 下两份真实日志节选（各 <30 KB，已入库）。

诚实边界：节选只能证明局部行为（「存在 8 帧连击不误报、10 帧连击报出」）；
「负样本最长 8 帧」这个全局结论依赖全量 artifacts，见 ``test_negative_samples_...``。
另外 ``lio_precursor_watch.py:85-87`` 自称的「负样本 22878 帧零误报」在本机复现不出来，
可复跑的是本文件里的数字。
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.spatial.lio_nofix_judge import (  # noqa: E402
    DEGRADED_LIO_DRIFT,
    HEALTHY,
    LioNofixJudge,
)

ARTIFACTS = PROJECT_ROOT / "artifacts" / "slam_validation" / "lio_divergence_20260915"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lio_diag"

_GAP = pytest.mark.skipif(
    not ARTIFACTS.is_dir(),
    reason=(
        "需要未被 git 跟踪的 artifacts/slam_validation/lio_divergence_20260915/"
        "（2026-09-15 真机发散的逐帧转录）：负样本应为 t_neg_drive=7837 帧/最长连击 8、"
        "t_neg_static=2600/0、t_neg_turn=441/0、lio_phase_frames_s2.csv=4690/0；"
        "正样本 t_pos_1240_end 触发@538、t_pos_1320 触发起点@66、frames_1320.csv 触发@12131"
    ),
)

# 真实正/负样本（csv 是离线转录，没有 GO2W-DIAG 字样，测试自己重建成判据吃的那一行）
NEGATIVE_LOGS = (
    "calibration/t_neg_drive.txt",
    "calibration/t_neg_static.txt",
    "calibration/t_neg_turn.txt",
)
NEGATIVE_CSV = ("lio_phase_frames_s2.csv",)
POSITIVE_LOGS = ("calibration/t_pos_1240_end.txt", "calibration/t_pos_1320.txt")
POSITIVE_CSV = ("frames_1320.csv",)

_NUM = r"(-?[\d.]+)"


def _diag_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(errors="replace").splitlines()
            if "GO2W-DIAG" in line]


def _csv_lines(path: Path) -> list[str]:
    """把离线转录的列还原成判据要吃的那一行（列序见 lio_phase_logger.py:15-17）。"""
    out: list[str] = []
    with path.open(errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if len(row) < 18:
                continue
            out.append(
                f"[GO2W-DIAG] iter={row[5]} fix_t={row[10]} v={row[11]} "
                f"act={row[3]} pos=({row[15]},{row[16]},{row[17]})"
            )
    return out


def _sample_lines(name: str) -> list[str]:
    path = ARTIFACTS / name
    return _csv_lines(path) if name.endswith(".csv") else _diag_lines(path)


def _feed(judge: LioNofixJudge, lines: list[str]) -> int | None:
    """喂完全部行，返回**health 首次变成 DEGRADED 的帧下标**（从未触发则 None）。"""
    fired = None
    for index, line in enumerate(lines):
        judge.feed(line)
        if fired is None and judge.health == DEGRADED_LIO_DRIFT:
            fired = index
    return fired


def _max_bad_run(lines: list[str]) -> int:
    """直接从原文复算最长连击 —— 用来把「余量」写成可断言的数字，而不是注释里的说法。"""
    best = run = 0
    for line in lines:
        match_it = re.search(r"iter=" + _NUM, line)
        match_fix = re.search(r"fix_t=" + _NUM, line)
        if not match_it or not match_fix:
            continue
        if float(match_it.group(1)) >= 10 and float(match_fix.group(1)) == 0.0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _good_lines(count: int) -> list[str]:
    return [f"[GO2W-DIAG] iter=1 fix_t=0.0003 v=0.02 act=0.96 pos=(0.00,0.00,0.00)"] * count


# --------------------------------------------------------------------------- #
# 1. 分辨力
# --------------------------------------------------------------------------- #

def test_committed_fixtures_prove_both_directions() -> None:
    """入库的两份真实日志节选：8 帧连击不误报、10 帧连击报出。"""
    negative = FIXTURES.joinpath("nofix_negative.txt").read_text().splitlines()
    positive = FIXTURES.joinpath("nofix_positive.txt").read_text().splitlines()

    negative_judge = LioNofixJudge()
    _feed(negative_judge, negative)
    assert negative_judge.health == HEALTHY
    # 这份节选是围着源数据里最长的那段连击（8 帧）截的，余量只有 2 帧。
    assert _max_bad_run(negative) == 8
    assert _max_bad_run(negative) < LioNofixJudge.MIN_CONSECUTIVE

    positive_judge = LioNofixJudge()
    assert _feed(positive_judge, positive) is not None
    assert positive_judge.health == DEGRADED_LIO_DRIFT
    assert "fix_t=0" in positive_judge.reason


@_GAP
@pytest.mark.parametrize("name", NEGATIVE_LOGS + NEGATIVE_CSV)
def test_negative_samples_never_trigger(name: str) -> None:
    """真实负样本零误报。t_neg_turn 是「急转时 iter 撞上限」的专门负样本。"""
    judge = LioNofixJudge()
    assert _feed(judge, _sample_lines(name)) is None
    assert judge.health == HEALTHY
    assert judge.reason == ""


@_GAP
def test_negative_margin_is_two_frames() -> None:
    """把「余量只有 2 帧」写成断言 —— 判据或数据漂了，这条会先响。"""
    worst = max(_max_bad_run(_sample_lines(name))
                for name in NEGATIVE_LOGS + NEGATIVE_CSV)
    assert worst == 8
    assert worst < LioNofixJudge.MIN_CONSECUTIVE


@_GAP
@pytest.mark.parametrize(
    ("name", "trigger_index"),
    [("calibration/t_pos_1240_end.txt", 538),
     ("calibration/t_pos_1320.txt", 75),
     ("frames_1320.csv", 12131)],
)
def test_positive_samples_trigger_at_the_calibrated_frame(name: str, trigger_index: int) -> None:
    """真实正样本触发，且落点与校准记录一致（连击起点 538/66/12131 的第 10 帧）。"""
    judge = LioNofixJudge()
    assert _feed(judge, _sample_lines(name)) == trigger_index
    assert judge.health == DEGRADED_LIO_DRIFT


@_GAP
def test_known_blind_spot_the_precursor_does_not_see_the_climb() -> None:
    """已知盲区（诚实性护栏）：12:40 死亡前的爬升期判据**完全无反应**。

    所以这个判据只能表述成「已经发散了就别再开搜索」的事后闸门，
    **不许**写成「防止发散」或早期预警。这条断言是为了防止有人日后
    拿它去当预警，然后以为预警没响就是安全。
    """
    judge = LioNofixJudge()
    assert _feed(judge, _sample_lines("calibration/t_pos_climb.txt")) is None
    assert judge.health == HEALTHY


# --------------------------------------------------------------------------- #
# 2. 锁存（本模块存在的理由）
# --------------------------------------------------------------------------- #

@_GAP
def test_latch_survives_the_run_clearing_that_fools_a_self_clearing_judge() -> None:
    """注入校准：判据触发后会自己闭嘴，锁存必须挡住这个回弹。

    实测（frames_1320.csv，触发起点 @12131）：触发那一波连击只有 33 帧，其后
    256 帧的最长连击掉到 3，且整段再没触发过。所以「回落即复位」的写法会把闸门
    重新打开，制造出比改动前更糟的假绿（现在恒真至少看得出来）。

    这条的注入校准：把 ``LioNofixJudge.health`` 里的 ``self._latched`` 判断去掉，
    本用例必须失败 —— 后半段那 256 帧会让一个未锁存的判据回到 HEALTHY。
    """
    lines = _sample_lines("frames_1320.csv")
    judge = LioNofixJudge()
    assert _feed(judge, lines) == 12131
    assert judge.health == DEGRADED_LIO_DRIFT

    # 触发那一波连击之后的帧：run 已跌回阈值以下。判据闭嘴了，但 LIO 没有恢复。
    tail = lines[12200:]
    assert len(tail) == 256
    assert _max_bad_run(tail) == 3
    assert _max_bad_run(tail) < LioNofixJudge.MIN_CONSECUTIVE
    for line in tail:
        judge.feed(line)
    assert judge.health == DEGRADED_LIO_DRIFT, "锁存被回落的好帧骗开了"

    # 对照：同样这批行，喂给一个全新判据就是 HEALTHY —— 证明尾段确实是「好帧」，
    # 上一条断言的通过只可能来自锁存。
    fresh = LioNofixJudge()
    for line in tail:
        fresh.feed(line)
    assert fresh.health == HEALTHY


def test_latch_clears_only_on_a_session_restart() -> None:
    """复位只有一条路：观察到 LIO 栈重启。"""
    judge = LioNofixJudge()
    bad_line = "[GO2W-DIAG] iter=10 fix_t=0.0000 v=1.20 act=0.90 pos=(0,0,0)"
    _feed(judge, [bad_line] * LioNofixJudge.MIN_CONSECUTIVE)
    assert judge.health == DEGRADED_LIO_DRIFT

    # 不调 note_session_restart()：再喂多少好帧都不许松开。
    _feed(judge, _good_lines(500))
    assert judge.health == DEGRADED_LIO_DRIFT

    judge.note_session_restart()
    assert judge.health is None, "重启瞬间不得直接回到 HEALTHY —— 那是个恒绿窗口"
    _feed(judge, _good_lines(9))
    assert judge.health is None, "少于 MIN_FRAMES_FOR_VERDICT 帧时判据在构造上不可能触发"
    _feed(judge, _good_lines(1))
    assert judge.health == HEALTHY


def test_no_verdict_before_minimum_frames() -> None:
    """帧数不足时不表态（调用方因此不写 mapping_health 字段）。"""
    judge = LioNofixJudge()
    _feed(judge, _good_lines(LioNofixJudge.MIN_FRAMES_FOR_VERDICT - 1))
    assert judge.health is None
    assert judge.reason == ""
    judge.feed(f"[GO2W-DIAG] iter=1 fix_t=0.0003")
    assert judge.health == HEALTHY


def test_malformed_lines_are_not_counted_as_frames() -> None:
    """不含诊断字段的行是空操作 —— 否则运维噪声能把判据推进到表态。"""
    judge = LioNofixJudge()
    _feed(judge, ["ddsi_udp_conn_write ... failed with retcode -1"] * 50)
    assert judge.health is None
    # 空操作之后紧跟最小帧数的好帧，必须正好在此刻表态（证明噪声没在计数）
    _feed(judge, _good_lines(LioNofixJudge.MIN_FRAMES_FOR_VERDICT))
    assert judge.health == HEALTHY


# --------------------------------------------------------------------------- #
# 3. 词表一致性（判据刻意不 import slam_web_map_state，靠这条兜住漂移）
# --------------------------------------------------------------------------- #

def test_vocabulary_matches_the_webui_map_state() -> None:
    from app.spatial import lio_nofix_judge, slam_web_map_state

    assert lio_nofix_judge.HEALTHY == slam_web_map_state.HEALTHY
    assert lio_nofix_judge.DEGRADED_LIO_DRIFT == slam_web_map_state.DEGRADED_LIO_DRIFT
