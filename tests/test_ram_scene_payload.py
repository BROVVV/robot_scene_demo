"""§17：Grounded-SAM + RAM++ 模式下，普通场景物体由检测结果提供，不再跑全场景 VLM。

这里验的是形状契约：`_ram_scene_payload` 交给语义观察的 payload，
必须能被 `LiveSemanticObserver` 原样吃下去（label / bbox_2d / mask 都不丢）。
"""

from __future__ import annotations

import unittest

from app.live_robot.semantic_observer import (
    SEMANTIC_STATUS_FRESH_QUICK,
    LiveSemanticObserver,
)


def _gsam_object(label: str, bbox, mask_area_ratio: float | None = None) -> dict:
    """`_gsam_to_dict` 的输出形状（run_autonomous_loop.py）。"""
    return {
        "label": label,
        "label_zh": label,
        "score": 0.8,
        "bbox_2d": list(bbox),
        "mask_rle": [[0, 4, 1], [10, 4, 1]],
        "mask_size": [720, 1280],
        "mask_area_ratio": mask_area_ratio,
    }


def _ram_scene_payload(image_path: str, objects: list[dict]) -> dict:
    """run_autonomous_loop._ram_scene_payload 的等价输出。"""
    frame_id = "bundle_137555"
    return {
        "scene_objects": objects,
        "scene_relations": [],
        "scene_summary_zh": (
            "场景物体：" + "、".join(
                str(item.get("label_zh") or item.get("label") or "object")
                for item in objects[:12]
            )
        ) if objects else "本帧检测未发现物体。",
        "image_path": image_path,
        "frame_id": frame_id,
        "source": "grounded_sam_ram_scene_objects",
        "semantic_status": SEMANTIC_STATUS_FRESH_QUICK,
        "semantic_quality": "grounded_sam_scene_objects",
        "semantic_source_frame_id": frame_id,
        "semantic_capture_timestamp": 1.0,
        "semantic_completed_timestamp": 1.0,
        "semantic_age_ms": 0.0,
    }


class RamScenePayloadTest(unittest.TestCase):
    def _observe(self, payload: dict):
        observer = LiveSemanticObserver(lambda _image, _profile: payload,
                                       now=lambda: 2.0)
        return observer.observe(
            target_profile=None,
            frame_or_bundle="/tmp/frame.jpg",
            robot_pose={"x": 0.0, "y": 0.0, "yaw_deg": 0.0},
        )

    def test_detections_become_scene_objects_with_geometry_and_masks(self) -> None:
        objects = [
            _gsam_object("office chair", [0.1, 0.2, 0.3, 0.9], 0.25),
            _gsam_object("backpack", [0.4, 0.3, 0.5, 0.6], 0.05),
        ]

        observation = self._observe(_ram_scene_payload("/tmp/frame.jpg", objects))

        labels = [item["label"] for item in observation.objects]
        self.assertEqual(labels, ["office chair", "backpack"])
        # §17 的意义就是这些普通物体照常进地图：bbox 与 SAM2 mask 都不能丢。
        first = observation.objects[0]
        self.assertEqual(first["bbox"], [0.1, 0.2, 0.3, 0.9])
        self.assertEqual(first["mask_rle"], [[0, 4, 1], [10, 4, 1]])
        self.assertEqual(first["mask_size"], (720, 1280))
        self.assertEqual(observation.semantic_status, SEMANTIC_STATUS_FRESH_QUICK)

    def test_scene_graph_is_built_from_detections(self) -> None:
        payload = _ram_scene_payload(
            "/tmp/frame.jpg", [_gsam_object("chair", [0.1, 0.1, 0.4, 0.6])]
        )

        observation = self._observe(payload)

        self.assertIsNotNone(observation.scene_graph)
        self.assertTrue(observation.scene_graph.nodes)

    def test_empty_detections_do_not_fake_a_scene(self) -> None:
        # §64：检测结果为空就是空场景 —— 那也是检测器的结论，不能再回去问 VLM
        # 一遍（full-scene VLM enumeration 必须 = 0），也绝不能凭空造物体。
        observation = self._observe(_ram_scene_payload("/tmp/frame.jpg", []))

        self.assertEqual(observation.objects, [])


if __name__ == "__main__":
    unittest.main()
