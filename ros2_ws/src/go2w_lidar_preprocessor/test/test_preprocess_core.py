import math

import numpy as np

from go2w_lidar_preprocessor.preprocess_core import (
    PreprocessParameters,
    directional_clearance,
    filter_points_base_link,
    laser_scan_ranges,
    transform_points,
)


def parameters():
    return PreprocessParameters(
        minimum_range=0.3,
        maximum_range=6.0,
        minimum_height=-0.2,
        maximum_height=1.5,
        ground_height=0.05,
        self_half_length=0.5,
        self_half_width=0.3,
        self_filter_margin=0.05,
        front_corridor_half_width=0.4,
        rotation_envelope_radius=1.0,
    )


def test_filter_removes_nonfinite_self_ground_and_out_of_range_points():
    points = np.array(
        [
            [math.nan, 0.0, 0.2],
            [0.4, 0.0, 0.2],
            [7.0, 0.0, 0.2],
            [1.0, 0.0, 0.0],
            [1.2, 0.1, 0.2],
        ]
    )
    filtered, obstacles = filter_points_base_link(points, parameters())
    assert filtered.shape == (2, 3)
    assert obstacles.shape == (1, 3)
    assert np.allclose(obstacles[0], [1.2, 0.1, 0.2])


def test_filter_removes_configured_head_region_but_keeps_close_external_obstacle():
    p = parameters()
    p = PreprocessParameters(
        minimum_range=p.minimum_range,
        maximum_range=p.maximum_range,
        minimum_height=p.minimum_height,
        maximum_height=p.maximum_height,
        ground_height=p.ground_height,
        self_half_length=p.self_half_length,
        self_half_width=p.self_half_width,
        self_filter_margin=p.self_filter_margin,
        front_corridor_half_width=p.front_corridor_half_width,
        rotation_envelope_radius=p.rotation_envelope_radius,
        self_regions=((0.28, 0.58, -0.31, 0.31, 0.30, 0.82),),
    )
    points = np.array(
        [
            [0.45, 0.0, 0.55],  # own head -> removed
            [0.60, 0.0, 0.55],  # just beyond head -> kept
            [0.70, 0.0, 0.60],  # table/box -> kept
            [1.0, 0.0, -0.10],  # below ground -> not an obstacle
        ]
    )
    filtered, obstacles = filter_points_base_link(points, p)
    assert filtered.shape == (3, 3)
    assert obstacles.shape == (2, 3)
    assert np.allclose(obstacles[:, 0], [0.60, 0.70])


def test_clearance_uses_x_forward_and_y_left_after_base_link_transform():
    obstacles = np.array(
        [
            [0.7, 0.0, 0.2],
            [0.4, 0.8, 0.2],
            [0.3, -0.6, 0.2],
            [-0.5, 0.0, 0.2],
        ]
    )
    clearance = directional_clearance(obstacles, parameters())
    assert clearance.front == 0.7
    assert abs(clearance.left - math.hypot(0.4, 0.8)) < 1e-9
    assert abs(clearance.right - math.hypot(0.3, 0.6)) < 1e-9


def test_laser_scan_keeps_nearest_obstacle_per_bin():
    obstacles = np.array([[1.0, 0.0, 0.2], [2.0, 0.0, 0.2]])
    ranges = laser_scan_ranges(
        obstacles,
        angle_min=-math.pi,
        angle_max=math.pi,
        angle_increment=math.pi / 180.0,
        range_min=0.3,
        range_max=6.0,
    )
    assert ranges[180] == 1.0


def test_transform_points_accepts_unitree_pitch_quaternion():
    pitch = 2.8782
    quaternion = (0.0, math.sin(pitch / 2.0), 0.0, math.cos(pitch / 2.0))
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    transformed = transform_points(
        points,
        (0.28945, 0.0, -0.046825),
        quaternion,
    )
    assert np.allclose(transformed[0], [0.28945, 0.0, -0.046825])
    assert np.allclose(
        transformed[1],
        [0.28945 + math.cos(pitch), 0.0, -0.046825 - math.sin(pitch)],
    )
