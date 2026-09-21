"""计划书 §12.3：走廊分析测试。

全部用人工构造的占据栅格，不依赖 ROS、不依赖地图文件。
"""

from __future__ import annotations

import math

import pytest

from app.navigation.corridor_analyzer import (
    DECISION_OK,
    DECISION_TOO_NARROW,
    FOOTPRINT_PADDING_M,
    HALF_BODY_WIDTH_M,
    HARD_SIDE_GAP_M,
    CorridorGrid,
    center_score,
    classify_path_clearance,
    measure_path_clearance,
    path_center_score,
    speed_limit_from_clearance,
)

UNKNOWN = -1
FREE = 0
WALL = 100

RESOLUTION = 0.05
BODY_INSET = HALF_BODY_WIDTH_M + FOOTPRINT_PADDING_M
# 光线步进按栅格分辨率走，量到的距离天然会有 ±1 格（5 cm）的量化误差。
MEASURE_TOL = RESOLUTION + 1e-6


def _grid(x_min: float, x_max: float, y_min: float, y_max: float) -> CorridorGrid:
    width = int(round((x_max - x_min) / RESOLUTION))
    height = int(round((y_max - y_min) / RESOLUTION))
    return CorridorGrid(
        resolution=RESOLUTION,
        origin_x=x_min,
        origin_y=y_min,
        width=width,
        height=height,
        data=[FREE] * (width * height),
    )


def _paint(
    grid: CorridorGrid, x_min: float, x_max: float, y_min: float, y_max: float, value: int
) -> None:
    """把矩形区域涂成 value；两端的格子都算在内（含边界那格）。"""
    data = list(grid.data)
    col_lo = max(0, int(math.floor((x_min - grid.origin_x) / grid.resolution)))
    col_hi = min(
        grid.width, int(math.floor((x_max - grid.origin_x) / grid.resolution)) + 1
    )
    row_lo = max(0, int(math.floor((y_min - grid.origin_y) / grid.resolution)))
    row_hi = min(
        grid.height, int(math.floor((y_max - grid.origin_y) / grid.resolution)) + 1
    )
    for row in range(row_lo, row_hi):
        for column in range(col_lo, col_hi):
            data[row * grid.width + column] = value
    object.__setattr__(grid, "data", tuple(data))


def _corridor_along_x(half_width: float, *, x_max: float = 6.0) -> CorridorGrid:
    grid = _grid(-1.0, x_max, -3.0, 3.0)
    _paint(grid, -1.0, x_max, half_width, 3.0, WALL)
    _paint(grid, -1.0, x_max, -3.0, -half_width, WALL)
    return grid


def _straight_path(
    y: float, *, length: float = 3.0, x0: float = 0.0
) -> list[tuple[float, float]]:
    return [(x0, y), (x0 + length, y)]


def _body_gap(distance_m: float) -> float:
    return distance_m - BODY_INSET


# ---------------------------------------------------------------------------
# §12.3 宽直走廊
# ---------------------------------------------------------------------------


def test_wide_straight_corridor_measures_symmetric_and_runs_full_speed() -> None:
    grid = _corridor_along_x(1.25)
    samples = measure_path_clearance(_straight_path(0.0), grid)
    assert samples
    assert all(
        item.left_distance_m == pytest.approx(1.25, abs=MEASURE_TOL) for item in samples
    )
    assert all(
        item.right_distance_m == pytest.approx(1.25, abs=MEASURE_TOL) for item in samples
    )

    decision = classify_path_clearance(samples)
    assert decision.decision == DECISION_OK
    assert decision.reject_reason == ""
    assert decision.min_side_gap_m == pytest.approx(_body_gap(1.25), abs=MEASURE_TOL)
    assert decision.min_left_body_gap_m == pytest.approx(_body_gap(1.25), abs=MEASURE_TOL)
    assert decision.min_right_body_gap_m == pytest.approx(
        _body_gap(1.25), abs=MEASURE_TOL
    )
    assert decision.corridor_width_m == pytest.approx(2.50, abs=2 * MEASURE_TOL)
    assert decision.center_error_m == pytest.approx(0.0, abs=2 * MEASURE_TOL)
    assert decision.center_score > 0.95
    assert decision.speed_limit_mps == pytest.approx(0.10)


def test_path_is_sampled_every_twenty_centimetres_and_trimmed_at_both_ends() -> None:
    grid = _corridor_along_x(1.25)
    samples = measure_path_clearance(_straight_path(0.0, length=3.0), grid)
    assert samples[0].s_m == pytest.approx(0.20)
    assert samples[-1].s_m == pytest.approx(2.80)
    for previous, current in zip(samples, samples[1:]):
        assert current.s_m - previous.s_m == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# §12.3 可通过的窄走廊
# ---------------------------------------------------------------------------


def test_passable_narrow_corridor_slows_down_within_band() -> None:
    grid = _corridor_along_x(0.90)
    decision = classify_path_clearance(measure_path_clearance(_straight_path(0.0), grid))
    assert decision.decision == DECISION_OK
    assert decision.min_side_gap_m == pytest.approx(_body_gap(0.90), abs=MEASURE_TOL)
    assert 0.05 <= decision.speed_limit_mps < 0.10


def test_speed_formula_matches_plan_section_7_4() -> None:
    assert speed_limit_from_clearance(HARD_SIDE_GAP_M) == pytest.approx(0.05)
    assert speed_limit_from_clearance(0.60) == pytest.approx(0.075)
    assert speed_limit_from_clearance(0.80) == pytest.approx(0.10)
    assert speed_limit_from_clearance(5.0) == pytest.approx(0.10)
    assert speed_limit_from_clearance(0.39) == 0.0
    assert speed_limit_from_clearance(None) == 0.0


# ---------------------------------------------------------------------------
# §12.3 机身单侧净空小于 0.40 m
# ---------------------------------------------------------------------------


def test_single_side_gap_below_hard_clearance_is_too_narrow() -> None:
    grid = _corridor_along_x(0.55)
    decision = classify_path_clearance(measure_path_clearance(_straight_path(0.0), grid))
    assert decision.min_side_gap_m == pytest.approx(_body_gap(0.55), abs=MEASURE_TOL)
    assert decision.min_side_gap_m < HARD_SIDE_GAP_M
    assert decision.decision == DECISION_TOO_NARROW
    assert decision.reject_reason == "TOO_NARROW"
    assert decision.speed_limit_mps == 0.0


def test_asymmetric_corridor_is_limited_by_the_near_wall() -> None:
    """只有一侧贴墙也必须拒绝，不能拿另一侧的宽敞平均掉。"""
    grid = _grid(-1.0, 6.0, -3.0, 3.0)
    _paint(grid, -1.0, 6.0, 0.60, 3.0, WALL)
    _paint(grid, -1.0, 6.0, -3.0, -1.90, WALL)
    decision = classify_path_clearance(measure_path_clearance(_straight_path(0.0), grid))
    assert decision.min_left_body_gap_m == pytest.approx(_body_gap(0.60), abs=MEASURE_TOL)
    assert decision.min_right_body_gap_m == pytest.approx(
        _body_gap(1.90), abs=MEASURE_TOL
    )
    assert decision.min_side_gap_m == pytest.approx(_body_gap(0.60), abs=MEASURE_TOL)
    assert decision.decision == DECISION_TOO_NARROW


def test_empty_path_is_fail_closed() -> None:
    grid = _corridor_along_x(1.25)
    decision = classify_path_clearance(measure_path_clearance([], grid))
    assert decision.decision == DECISION_TOO_NARROW
    assert decision.reject_reason == "NO_PATH_SAMPLES"
    assert decision.speed_limit_mps == 0.0
    assert not decision.passable


# ---------------------------------------------------------------------------
# §12.3 路径偏左 → center_score 低于中线候选
# ---------------------------------------------------------------------------


def test_off_centre_path_scores_lower_than_the_centre_line() -> None:
    grid = _corridor_along_x(1.20)
    centre = classify_path_clearance(measure_path_clearance(_straight_path(0.0), grid))
    off_centre = classify_path_clearance(measure_path_clearance(_straight_path(0.20), grid))
    assert centre.decision == DECISION_OK
    assert off_centre.decision == DECISION_OK
    assert centre.center_score > 0.9
    assert centre.center_error_m < off_centre.center_error_m
    # 路径偏离中线 0.20 m ⇒ 两侧机身净空相差 0.40 m，评分必须跟着降。
    assert off_centre.center_error_m == pytest.approx(0.40, abs=2 * MEASURE_TOL)
    assert 0.0 < off_centre.center_score < centre.center_score


def test_center_score_is_one_in_the_middle_and_stays_in_range() -> None:
    assert center_score(1.0, 1.0) == pytest.approx(1.0)
    assert center_score(2.0, 0.0) == pytest.approx(0.0)
    assert 0.0 <= center_score(0.05, 4.0) <= 1.0


def test_path_center_score_matches_the_binding_sample_of_classify() -> None:
    """§7.5 的候选打分必须和 §7.4 的判定看同一个约束点，不能各算各的。"""
    grid = _corridor_along_x(1.20)
    path = _straight_path(0.20)
    decision = classify_path_clearance(measure_path_clearance(path, grid))
    assert path_center_score(path, grid) == pytest.approx(decision.center_score)


def test_path_center_score_is_none_when_there_is_nothing_to_measure() -> None:
    grid = _corridor_along_x(1.20)
    # 整段落在地图外：量不到就是量不到，不能当成「居中」。
    assert path_center_score([(50.0, 50.0), (50.5, 50.0)], grid) is None
    assert path_center_score([], grid) is None


def test_path_center_score_refuses_to_score_a_path_that_does_not_fit() -> None:
    """被堵死的地方没有「居中」可言 —— 这里必须给 None 而不是满分。

    左右光线都在第一格撞墙时两侧距离相等，照对称公式算出来是 1.0；候选排序
    一旦拿到这个 1.0，就会把一条过不去的路当成「走得最正」的那条。
    """
    grid = _corridor_along_x(0.55)
    path = _straight_path(0.0)
    # §7.4 的判定照旧给一个 center_score（那是给人看的证据）。
    assert classify_path_clearance(measure_path_clearance(path, grid)).decision == (
        DECISION_TOO_NARROW
    )
    assert path_center_score(path, grid) is None


# ---------------------------------------------------------------------------
# §12.3 弯道使用局部法向
# ---------------------------------------------------------------------------


def test_corner_path_uses_local_normal_not_a_global_axis() -> None:
    """L 形走廊：第二段沿 +y 走，左右必须横跨 x，而不是继续用全局 ±y。"""
    grid = _grid(-2.0, 5.0, -2.0, 6.0)
    _paint(grid, -2.0, 5.0, -2.0, -1.40, WALL)  # 下墙
    _paint(grid, -2.0, -1.50, -2.0, 6.0, WALL)  # 横廊左端
    _paint(grid, 3.40, 5.0, -2.0, 6.0, WALL)  # 竖廊右墙
    _paint(grid, -2.0, 0.60, 1.40, 6.0, WALL)  # 横廊上墙 + 竖廊左墙
    _paint(grid, -2.0, 5.0, 5.00, 6.0, WALL)  # 竖廊顶端

    path = [(0.0, 0.0), (2.0, 0.0), (2.0, 4.0)]
    samples = measure_path_clearance(path, grid)
    # 只取横廊那一段（x > 0.6 就是拐角开口，上方本来就是通的）。
    first_leg = [item for item in samples if item.y == pytest.approx(0.0) and item.x < 0.5]
    second_leg = [item for item in samples if item.y > 1.5]
    assert first_leg and second_leg

    for item in first_leg:
        assert item.left_distance_m == pytest.approx(1.40, abs=MEASURE_TOL)
        assert item.right_distance_m == pytest.approx(1.40, abs=MEASURE_TOL)
    # 第二段如果沿用全局横轴，会量到竖廊顶端（2.0 m 上限），而不是两侧墙的 1.40 m。
    for item in second_leg:
        assert item.left_distance_m == pytest.approx(1.40, abs=MEASURE_TOL)
        assert item.right_distance_m == pytest.approx(1.40, abs=MEASURE_TOL)


def test_rotation_in_place_path_still_yields_one_sample() -> None:
    """原地转向的目标会产生零长度路径，不能因此判成不可通行。"""
    grid = _corridor_along_x(1.25)
    samples = measure_path_clearance([(1.0, 0.0)], grid)
    assert len(samples) == 1
    assert samples[0].s_m == 0.0
    decision = classify_path_clearance(samples)
    assert decision.decision == DECISION_OK
    assert decision.speed_limit_mps == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# §12.3 未知栅格按障碍处理
# ---------------------------------------------------------------------------


def test_unknown_cells_count_as_obstacles() -> None:
    grid = _grid(-1.0, 6.0, -3.0, 3.0)
    _paint(grid, -1.0, 6.0, -3.0, -1.25, WALL)
    _paint(grid, -1.0, 6.0, 1.25, 3.0, UNKNOWN)
    decision = classify_path_clearance(measure_path_clearance(_straight_path(0.0), grid))
    assert decision.min_left_body_gap_m == pytest.approx(_body_gap(1.25), abs=MEASURE_TOL)
    assert decision.min_right_body_gap_m == pytest.approx(
        _body_gap(1.25), abs=MEASURE_TOL
    )
    assert decision.decision == DECISION_OK


def test_unknown_wall_is_as_blocking_as_a_known_wall() -> None:
    known = _corridor_along_x(1.00)
    unknown = _grid(-1.0, 6.0, -3.0, 3.0)
    _paint(unknown, -1.0, 6.0, -3.0, -1.00, WALL)
    _paint(unknown, -1.0, 6.0, 1.00, 3.0, UNKNOWN)
    a = classify_path_clearance(measure_path_clearance(_straight_path(0.0), known))
    b = classify_path_clearance(measure_path_clearance(_straight_path(0.0), unknown))
    assert a.min_side_gap_m == pytest.approx(b.min_side_gap_m, abs=MEASURE_TOL)
    assert a.speed_limit_mps == pytest.approx(b.speed_limit_mps)
    assert a.decision == b.decision


def test_off_map_coordinates_are_blocked() -> None:
    grid = _corridor_along_x(1.25, x_max=2.0)
    samples = measure_path_clearance([(0.0, 0.0), (10.0, 0.0)], grid)
    inside = [item for item in samples if item.x < 1.9]
    outside = [item for item in samples if item.x > 2.1]
    assert inside and outside
    assert all(item.min_side_gap_m > 0.0 for item in inside)
    assert all(item.min_side_gap_m < 0.0 for item in outside)
    assert classify_path_clearance(samples).decision == DECISION_TOO_NARROW


def test_grid_rejects_non_positive_resolution() -> None:
    grid = CorridorGrid(0.0, 0.0, 0.0, 4, 4, [0] * 16)
    with pytest.raises(ValueError):
        grid.value_at(0.0, 0.0)
