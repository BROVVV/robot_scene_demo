from pathlib import Path

import pytest

from go2w_rgb_lidar_fusion.config_gate import load_extrinsics_gate, load_fusion_gate


def test_project_configs_open_fusion_with_experimental_override():
    root = Path(__file__).parents[4]
    fusion, _ = load_fusion_gate(
        root / "configs/go2w/rgb_lidar_fusion.yaml",
        root / "configs/go2w/camera_intrinsics.yaml",
        root / "configs/go2w/sensor_extrinsics.yaml",
    )
    assert fusion.get("enabled") is True
    assert "acceptance_override" in fusion


def test_project_configs_open_extrinsics_with_experimental_override():
    root = Path(__file__).parents[4]
    _, extrinsics = load_extrinsics_gate(
        root / "configs/go2w/camera_intrinsics.yaml",
        root / "configs/go2w/sensor_extrinsics.yaml",
    )
    assert extrinsics.get("confirmed") is True
    assert "acceptance_override" in extrinsics


def test_fusion_gate_still_fail_closed_when_disabled(tmp_path):
    root = Path(__file__).parents[4]
    source = (root / "configs/go2w/rgb_lidar_fusion.yaml").read_text(encoding="utf-8")
    candidate = tmp_path / "fusion_disabled.yaml"
    candidate.write_text(source.replace("enabled: true", "enabled: false"), encoding="utf-8")
    with pytest.raises(ValueError, match="disabled or unvalidated"):
        load_fusion_gate(
            candidate,
            root / "configs/go2w/camera_intrinsics.yaml",
            root / "configs/go2w/sensor_extrinsics.yaml",
        )
