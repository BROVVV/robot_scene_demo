"""MoGe-2 backend interface.

This module intentionally keeps the real model invocation behind a subprocess
contract. If no local MoGe installation is configured, callers should use the
heuristic fallback instead.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from app.geometry.types import GeometryConfig, PointMapResult


class GeometryBackend:
    def infer_point_map(self, image_path: str | Path) -> PointMapResult:
        raise NotImplementedError


class MoGeSubprocessBackend(GeometryBackend):
    def __init__(self, config: GeometryConfig) -> None:
        self.config = config

    def is_available(self) -> bool:
        return bool(self.config.moge_root) and Path(self.config.moge_root).exists()

    def infer_point_map(self, image_path: str | Path) -> PointMapResult:
        if not self.is_available():
            raise RuntimeError("MOGE_ROOT is not configured or does not exist.")
        python_bin = self.config.moge_python or "python"
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "moge_geometry.json"
            command = [
                python_bin,
                "-m",
                "moge_worker",
                "--image",
                str(Path(image_path).resolve()),
                "--output",
                str(output_path),
            ]
            if self.config.moge_model_id:
                command.extend(["--model-id", self.config.moge_model_id])
            completed = subprocess.run(
                command,
                cwd=self.config.moge_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "MoGe worker failed.\n"
                    f"Command: {' '.join(command)}\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.is_file():
                raise RuntimeError("MoGe worker did not create geometry JSON.")
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        point_map = np.load(payload["point_map_path"]).astype(np.float32)
        depth = np.load(payload["depth_path"]).astype(np.float32)
        return PointMapResult(
            point_map=point_map,
            depth=depth,
            camera=dict(payload.get("camera") or {}),
            backend="moge",
            metric_reliable=bool(payload.get("metric_reliable", True)),
            warning=payload.get("warning"),
        )
