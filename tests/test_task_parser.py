from __future__ import annotations

import unittest

from app.reasoning.task_parser import parse_robot_task, parse_robot_task_with_optional_llm


class TaskParserTest(unittest.TestCase):
    def test_parse_find_object(self) -> None:
        task = parse_robot_task("找到桌子上的手机")

        self.assertEqual(task.task_type, "find_object")
        self.assertEqual(task.target_object, "phone")
        self.assertIn("桌子上", task.constraints)

    def test_parse_count_objects(self) -> None:
        task = parse_robot_task("数数这个房间里有几个椅子")

        self.assertEqual(task.task_type, "count_objects")
        self.assertEqual(task.target_object, "chair")
        self.assertEqual(task.scope, "current_room")

    def test_parse_floor_door_inspection(self) -> None:
        task = parse_robot_task("巡查这层楼看看有几个房间的门是打开的")

        self.assertEqual(task.task_type, "inspect_area")
        self.assertEqual(task.target_object, "door")
        self.assertEqual(task.scope, "current_floor")
        self.assertEqual(task.parsed_slots["subtask"], "check_door_state")
        self.assertEqual(task.parsed_slots["state"], "open")

    def test_parse_specific_door_state(self) -> None:
        task = parse_robot_task("看看 503 是不是开着门")

        self.assertEqual(task.task_type, "check_door_state")
        self.assertEqual(task.target_object, "door")
        self.assertEqual(task.target_room, "503")
        self.assertEqual(task.parsed_slots["state"], "open")

    def test_parse_navigation(self) -> None:
        task = parse_robot_task("去走廊尽头的房间")

        self.assertEqual(task.task_type, "navigate_to_location")
        self.assertEqual(task.target_location, "走廊尽头")

    def test_optional_llm_parser_falls_back_to_rules(self) -> None:
        def bad_parser(_: str) -> dict:
            return {"invalid": "payload"}

        task = parse_robot_task_with_optional_llm("找到手机", bad_parser)

        self.assertEqual(task.task_type, "find_object")
        self.assertEqual(task.target_object, "phone")

    def test_optional_llm_parser_accepts_valid_json(self) -> None:
        def good_parser(text: str) -> dict:
            return {
                "task_id": "task_llm",
                "raw_text": text,
                "task_type": "summarize_scene",
                "target_object": None,
                "target_location": None,
                "target_room": None,
                "scope": "current_scene",
                "constraints": [],
                "parsed_slots": {},
                "confidence": 0.8,
            }

        task = parse_robot_task_with_optional_llm("总结当前场景", good_parser)

        self.assertEqual(task.task_type, "summarize_scene")
        self.assertEqual(task.task_id, "task_llm")


if __name__ == "__main__":
    unittest.main()
