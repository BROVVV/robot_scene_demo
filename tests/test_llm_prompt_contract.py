from __future__ import annotations

import unittest

from app.llm_clients.siliconflow_client import (
    _build_fast_user_prompt,
    _normalize_fast_result,
)
from app.schemas import SceneAnalysisResult


class LLMPromptContractTest(unittest.TestCase):
    def test_prompt_mentions_structured_reasoning_hints(self) -> None:
        prompt = _build_fast_user_prompt("找到手机")

        self.assertIn("task_understanding", prompt)
        self.assertIn("scene_reasoning_hints", prompt)
        self.assertIn("不要写完整思维链", prompt)

    def test_normalizer_ignores_optional_reasoning_fields(self) -> None:
        normalized = _normalize_fast_result(
            {
                "scene_summary_zh": "办公室场景。",
                "objects": [
                    {
                        "name": "desk",
                        "name_zh": "桌子",
                        "category": "furniture",
                        "relative_to_robot": "front",
                        "confidence": 0.8,
                    }
                ],
                "relations": [],
                "target_decision": {
                    "is_present": False,
                    "matched_indices": [],
                    "match_reason_zh": "没有看到手机。",
                    "confidence": 0.7,
                },
                "route_plan": {"route_type": "explore_likely_location", "steps": []},
                "task_understanding": {
                    "task_type": "find_object",
                    "entities": ["phone"],
                    "constraints": [],
                    "uncertainty": "桌面有遮挡。",
                },
                "scene_reasoning_hints": {
                    "scene_type": "office",
                    "candidate_locations": ["desk surface"],
                    "supporting_evidence": ["visible desk"],
                    "recommended_next_observation": "靠近桌面。",
                },
            },
            "找到手机",
        )

        result = SceneAnalysisResult.model_validate(normalized)
        self.assertFalse(result.target_decision.is_present)
        self.assertEqual(result.objects[0].name, "desk")


if __name__ == "__main__":
    unittest.main()
