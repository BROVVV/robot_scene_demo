"""§12.1：RAM++ 返回再多 tag，也不能把用户目标词挤出 GroundingDINO prompt。"""

from __future__ import annotations

import unittest
from unittest import mock

from app.config import Settings
from app.detectors.grounded_sam_subprocess import GroundedSAMSubprocessDetector
from app.task_understanding.schemas import GroundingPromptPlan
from app.video.target_profile import TargetProfile


def _yellow_chair_profile() -> TargetProfile:
    return TargetProfile(
        raw_query="找黄色椅子",
        canonical_name_zh="黄色椅子",
        primary_labels_en=["chair", "yellow chair"],
        aliases_en=["yellow seat"],
        colors=["yellow"],
    )


def _flood_of_ram_terms() -> list[str]:
    # 远超槽位上限的一批 RAM tag，用来逼出「目标词被挤掉」的回归。
    return [f"scene object {index}" for index in range(30)]


class RamTargetPriorityTest(unittest.TestCase):
    def _detector(self, **overrides) -> GroundedSAMSubprocessDetector:
        settings = Settings(
            siliconflow_api_key="",
            grounding_prompt_llm_expansion_enabled=False,
            static_object_prompts_enabled=True,
            **overrides,
        )
        return GroundedSAMSubprocessDetector(
            settings=settings, target_profile=_yellow_chair_profile()
        )

    def test_target_terms_survive_ram_flood(self) -> None:
        detector = self._detector()

        prompts = detector._build_prompts("找黄色椅子", [], _flood_of_ram_terms())

        self.assertTrue(prompts)
        self.assertIn("chair", prompts[0])
        self.assertIn("yellow chair", prompts[0])
        # RAM 词进自己的槽位，不参与目标词那一槽的 16 个名额竞争。
        self.assertNotIn("scene object 0", prompts[0])
        self.assertTrue(any("scene object 0" in prompt for prompt in prompts[1:]))

    def test_ram_terms_are_capped_not_dropped_entirely(self) -> None:
        detector = self._detector(ram_scene_max_terms=5)

        prompts = detector._build_prompts("找黄色椅子", [], _flood_of_ram_terms())

        ram_prompts = [prompt for prompt in prompts if "scene object" in prompt]
        self.assertEqual(len(ram_prompts), 1)
        self.assertEqual(ram_prompts[0].count("scene object"), 5)

    def test_ram_terms_do_not_enter_the_target_slot(self) -> None:
        detector = self._detector()

        prompts = detector._build_prompts("找黄色椅子", [], ["stool", "plant", "monitor"])

        self.assertNotIn("stool", prompts[0])
        self.assertTrue(any("stool" in prompt for prompt in prompts[1:]))

    def test_base_terms_still_present_without_a_target_profile(self) -> None:
        detector = GroundedSAMSubprocessDetector(
            settings=Settings(
                siliconflow_api_key="",
                grounding_prompt_llm_expansion_enabled=False,
                static_object_prompts_enabled=True,
            )
        )

        prompts = detector._build_prompts("", [], ["plant"])

        joined = " ".join(prompts)
        self.assertIn("plant", joined)
        # RAM 扩词之后不能把静态基线词表整个丢掉（否则又退回封闭词表）。
        self.assertIn("chair", joined)

    def test_prompts_keep_period_separators(self) -> None:
        # worker 是按 "." 把 prompt 拆成单 term 逐个推理的；一旦分隔符被
        # 归一化掉，整串会当成一个 term 喂进去，检出的 label 就变成整条 prompt。
        detector = self._detector()

        prompts = detector._build_prompts("找黄色椅子", [], ["stool", "plant"])

        ram_prompt = [prompt for prompt in prompts if "stool" in prompt][0]
        worker_terms = [t.strip() for t in ram_prompt.split(".") if t.strip()]
        self.assertEqual(worker_terms, ["stool", "plant"])

    def test_llm_expansion_branch_appends_ram_after_target_terms(self) -> None:
        plan = GroundingPromptPlan(
            grounding_prompt="chair. yellow chair. table.",
            is_valid_for_grounding_dino=True,
        )
        detector = GroundedSAMSubprocessDetector(
            settings=Settings(
                siliconflow_api_key="", grounding_prompt_llm_expansion_enabled=True
            ),
            target_profile=_yellow_chair_profile(),
            parsed_task=object(),
            llm_client=object(),
        )

        with mock.patch(
            "app.detectors.grounded_sam_subprocess.GroundingPromptPlanner"
        ) as planner_cls:
            planner_cls.return_value.build.return_value = plan
            prompts = detector._build_prompts("找黄色椅子", [], ["stool", "plant"])

        self.assertEqual(len(prompts), 1)
        prompt = prompts[0]
        # 目标词来自 LLM planner，排在 RAM 词前面，永远不会被 RAM 截断。
        self.assertLess(prompt.index("yellow chair"), prompt.index("stool"))
        self.assertIn("plant", prompt)


if __name__ == "__main__":
    unittest.main()
