from __future__ import annotations

import unittest

from app.reasoning.evidence_gate import EvidenceGateConfig, evaluate_candidate


class EvidenceGateTest(unittest.TestCase):
    def test_llm_commonsense_cannot_confirm(self) -> None:
        report = evaluate_candidate(
            {
                "candidate_id": "hyp",
                "source": "llm_commonsense",
                "has_visual_evidence": False,
                "bbox": None,
                "detector_score": 0.99,
            },
            EvidenceGateConfig(require_crop_verify=False),
        )

        self.assertFalse(report["target_found"])
        self.assertIn("LLM_COMMONSENSE_CANNOT_CONFIRM", report["blocking_rules"])

    def test_visual_candidate_requires_crop_verify_and_score(self) -> None:
        report = evaluate_candidate(
            {
                "candidate_id": "obj_001",
                "source": "visual_detector",
                "has_visual_evidence": True,
                "bbox": [0.1, 0.1, 0.2, 0.2],
                "detector_score": 0.85,
                "crop_verify_score": 0.82,
            },
            EvidenceGateConfig(require_crop_verify=True, min_score=0.72),
        )

        self.assertTrue(report["target_found"])
        self.assertEqual(report["target_status"], "visual_confirmed")


if __name__ == "__main__":
    unittest.main()
