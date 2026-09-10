"""计划书 §8.4：IMU 时间对齐必须一次锁定，运动期间不许再拖动时间轴。"""

from __future__ import annotations

from ros2_ws.src.go2w_plain_slam_bridge.scripts.imu_timestamp_aligner import (
    ImuTimestampAligner,
)


def test_sensor_stamp_is_preserved_when_clock_is_close() -> None:
    aligner = ImuTimestampAligner()
    first = aligner.update(100.0, 100.02)
    second = aligner.update(100.01, 100.03)
    assert first.valid and second.valid
    assert first.mode == "PASSTHROUGH_SENSOR_STAMP"
    assert second.corrected_sec == 100.01


def test_large_constant_offset_is_locked_after_the_startup_window() -> None:
    aligner = ImuTimestampAligner(lock_after_samples=5)
    values = [aligner.update(10.0 + i * 0.01, 1010.0 + i * 0.01) for i in range(8)]
    # 锁定之前一个样本都不喂 LIO：偏移还在估计，时间轴不许由抖动的中位数决定。
    assert [item.valid for item in values] == [False] * 4 + [True] * 4
    assert values[0].mode == "OFFSET_ESTIMATING_SENSOR_STAMP"
    assert values[0].corrected_sec is None
    assert not values[0].offset_locked
    assert not values[0].restart_required
    assert values[-1].mode == "OFFSET_LOCKED_SENSOR_STAMP"
    assert values[-1].offset_locked
    assert abs(float(values[-1].corrected_sec) - 1010.07) < 1e-6


def test_startup_jitter_never_locks_out_the_imu_stream() -> None:
    """真机回归：/utlidar/imu 落后 ROS 约 730 s，222 Hz 下 raw 每次只增 4.5 ms。

    旧实现在启动窗口里用滚动中位数生成 corrected，中位数下沉一次就判定
    "corrected stamp would move backward" 并永久停止喂 LIO（实测第 8 个样本
    就闭锁，重启 session 必然复现）。
    """
    aligner = ImuTimestampAligner(lock_after_samples=10)
    jitter = [0.10, 0.10, 0.10] + [0.0] * 9
    results = [
        aligner.update(
            1788314743.0 + index * 0.0045,
            1788314743.0 + index * 0.0045 + 730.942 + extra,
        )
        for index, extra in enumerate(jitter)
    ]
    assert not any(item.restart_required for item in results)
    assert all(item.mode == "OFFSET_ESTIMATING_SENSOR_STAMP" for item in results[:9])
    assert aligner.offset_locked
    fed = [float(item.corrected_sec) for item in results if item.valid]
    assert len(fed) == 3
    assert fed == sorted(fed) and len(set(fed)) == 3


def test_callback_jitter_after_lock_never_moves_the_time_axis() -> None:
    """原地转向时 CPU 抖动会让 receive-sensor 跳几十毫秒；时间轴必须不动。"""
    aligner = ImuTimestampAligner(lock_after_samples=4)
    for index in range(4):
        aligner.update(10.0 + index * 0.002, 1010.0 + index * 0.002)
    locked = aligner.estimated_offset_sec
    assert aligner.offset_locked

    jittered = aligner.update(10.010, 1010.010 + 0.040)
    assert jittered.valid
    assert aligner.estimated_offset_sec == locked
    assert abs(float(jittered.corrected_sec) - (10.010 + locked)) < 1e-9
    assert abs(jittered.offset_drift_sec - 0.040) < 1e-6

    settled = aligner.update(10.012, 1010.012 - 0.030)
    assert settled.valid
    assert aligner.estimated_offset_sec == locked
    assert abs(float(settled.corrected_sec) - (10.012 + locked)) < 1e-9


def test_backward_stamp_stops_the_stream_until_a_session_restart() -> None:
    aligner = ImuTimestampAligner()
    assert aligner.update(50.0, 50.0).valid
    bad = aligner.update(49.0, 50.01)
    assert not bad.valid
    assert bad.mode == "IMU_SOURCE_RESTART_REQUIRED"
    assert bad.restart_required
    # 不再自动恢复：后续正常样本也不喂给 LIO。
    blocked = aligner.update(50.01, 50.02)
    assert not blocked.valid
    assert blocked.restart_required
    aligner.reset()
    assert aligner.update(50.02, 50.03).valid


def test_sensor_gap_drops_one_sample_and_then_keeps_feeding_lio() -> None:
    """真机回归：网线闪断让 /utlidar/imu 空洞 18 s，恢复后必须继续喂 LIO。

    空洞不改变已锁定的 offset，永久阻断只会让 LIO 退化成纯几何配准，
    WebUI 三维地图随即出现重影。
    """
    aligner = ImuTimestampAligner(lock_after_samples=3)
    for i in range(3):
        aligner.update(10.0 + i * 0.002, 756.0 + i * 0.002)
    assert aligner.offset_locked
    gap = aligner.update(28.0, 774.0)
    assert not gap.valid
    assert not gap.restart_required
    assert not aligner.restart_required
    assert "gap" in gap.reason
    resumed = aligner.update(28.002, 774.002)
    assert resumed.valid
    assert resumed.mode == "OFFSET_LOCKED_SENSOR_STAMP"
    assert abs(float(resumed.corrected_sec) - 774.002) < 1e-6


def test_sensor_gap_before_offset_lock_still_requires_restart() -> None:
    aligner = ImuTimestampAligner(lock_after_samples=500)
    aligner.update(10.0, 756.0)
    gap = aligner.update(28.0, 774.0)
    assert gap.restart_required
    assert not aligner.update(28.002, 774.002).valid


def test_duplicate_stamp_requires_restart() -> None:
    aligner = ImuTimestampAligner()
    assert aligner.update(50.0, 50.0).valid
    duplicate = aligner.update(50.0, 50.002)
    assert not duplicate.valid
    assert duplicate.restart_required
    assert "duplicate" in duplicate.reason


def test_clock_jump_after_lock_requires_restart() -> None:
    aligner = ImuTimestampAligner(lock_after_samples=3, max_offset_step_s=1.0)
    for index in range(3):
        aligner.update(10.0 + index * 0.002, 1010.0 + index * 0.002)
    jumped = aligner.update(10.006, 1010.006 + 30.0)
    assert not jumped.valid
    assert jumped.mode == "IMU_SOURCE_RESTART_REQUIRED"
    assert jumped.restart_required


def test_invalid_stamp_is_never_published() -> None:
    result = ImuTimestampAligner().update(0.0, 10.0)
    assert not result.valid
    assert result.corrected_sec is None
    assert result.mode == "IMU_SOURCE_INVALID_STAMP"


def test_diagnostics_expose_raw_corrected_offset_and_mode() -> None:
    aligner = ImuTimestampAligner(lock_after_samples=2)
    aligner.update(10.0, 1010.0)
    result = aligner.update(10.002, 1010.002)
    assert result.raw_sensor_sec == 10.002
    assert result.receive_ros_sec == 1010.002
    assert result.offset_samples == 2
    assert result.offset_locked
    assert result.mode == "OFFSET_LOCKED_SENSOR_STAMP"
    assert abs(result.estimated_offset_sec - 1000.0) < 1e-6
