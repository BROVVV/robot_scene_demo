from pathlib import Path

import pytest

from go2w_lidar_preprocessor.config import load_safety_ready_config


def test_unmeasured_project_configs_close_safety_gate():
    root = Path(__file__).parents[4]
    with pytest.raises(ValueError, match="not measured and confirmed"):
        load_safety_ready_config(
            root / "configs/go2w/lidar_preprocess.yaml",
            root / "configs/go2w/physical_measurements.yaml",
        )


def test_validated_stationary_config_accepts_pinned_official_geometry():
    root = Path(__file__).parents[4]
    config, parameters = load_safety_ready_config(
        root / "configs/go2w/lidar_preprocess.yaml",
        root / "configs/go2w/official_reference.yaml",
    )
    assert config["revalidation_required"] is False
    assert parameters.minimum_height == -0.588
    assert parameters.maximum_height == 0.972
    assert parameters.ground_height == -0.448
    assert parameters.self_half_length == 0.35
    assert parameters.self_half_width == 0.215
    assert parameters.front_corridor_half_width == 0.315
    assert parameters.rotation_envelope_radius == 0.511
