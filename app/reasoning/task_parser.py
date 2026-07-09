"""Compatibility projection from LLM-first task understanding to RobotTask."""

from __future__ import annotations

import hashlib
from typing import Any

from app.schemas import RobotTask
from app.task_understanding.schemas import ParsedTask, SubtaskType, TargetCategory, TaskIntent
from app.task_understanding.task_pipeline import (
    TaskUnderstandingResult,
    run_task_understanding_pipeline,
)


def parse_robot_task(target_text: str) -> RobotTask:
    """
    Deprecated compatibility wrapper.

    New code should call
    app.task_understanding.task_pipeline.run_task_understanding_pipeline directly.
    """
    result = run_task_understanding_pipeline(target_text)
    return convert_new_task_to_legacy_format(result)


def parse_robot_task_with_optional_llm(
    target_text: str,
    llm_parser: object | None = None,
) -> RobotTask:
    if llm_parser is None:
        return parse_robot_task(target_text)

    try:
        raw = llm_parser(target_text)  # type: ignore[operator]
        if isinstance(raw, RobotTask):
            return raw
        if isinstance(raw, dict) and "task_type" in raw:
            return RobotTask.model_validate(raw)
        if isinstance(raw, TaskUnderstandingResult):
            return convert_new_task_to_legacy_format(raw)
        if isinstance(raw, ParsedTask):
            return _robot_task_from_parsed(raw)
    except Exception:
        return parse_robot_task(target_text)
    return parse_robot_task(target_text)


def convert_new_task_to_legacy_format(task_result: TaskUnderstandingResult) -> RobotTask:
    return _robot_task_from_parsed(task_result.parsed_task)


def _robot_task_from_parsed(parsed: ParsedTask) -> RobotTask:
    intent = _intent_value(parsed.primary_intent)
    target_category = _category_value(parsed.target.category)
    task_type = _legacy_task_type(intent, target_category)
    state_query = _first_state_query(parsed)
    target_object = _legacy_target_object(parsed, task_type)
    target_room = _legacy_target_room(parsed)
    target_location = _legacy_target_location(parsed, target_room)
    scope = _legacy_scope(parsed)
    parsed_slots: dict[str, str | int | float | bool | None] = {}
    if intent == TaskIntent.CHECK_DOOR_STATE.value or _has_subtask(
        parsed, SubtaskType.INSPECT_DOOR_STATE
    ):
        parsed_slots["subtask"] = "check_door_state"
    if state_query:
        parsed_slots["state"] = state_query

    constraints = []
    if parsed.area.name_zh:
        constraints.append(parsed.area.name_zh)
    constraints.extend(parsed.target.attributes)
    constraints.extend(parsed.target.relations)
    if state_query:
        constraints.append(state_query)

    return RobotTask(
        task_id=_task_id(parsed.raw_task),
        raw_text=parsed.raw_task,
        task_type=task_type,  # type: ignore[arg-type]
        target_object=target_object,
        target_location=target_location,
        target_room=target_room,
        scope=scope,
        constraints=constraints,
        parsed_slots=parsed_slots,
        confidence=max(
            parsed.confidence.intent,
            parsed.confidence.target,
            0.0,
        ),
    )


def _legacy_task_type(intent: str, target_category: str) -> str:
    if intent == TaskIntent.FIND_ROOM.value or target_category == TargetCategory.ROOM.value:
        return "find_room"
    if intent == TaskIntent.CHECK_DOOR_STATE.value:
        return "check_door_state"
    if intent in {
        TaskIntent.INSPECT_AREA.value,
        TaskIntent.PATROL_AREA.value,
        TaskIntent.CHECK_PASSABLE_AREA.value,
    }:
        return "inspect_area"
    if intent in {TaskIntent.LOCATE_AREA.value, TaskIntent.APPROACH_TARGET.value}:
        return "navigate_to_location"
    if intent == TaskIntent.NON_NAVIGATION.value:
        return "summarize_scene"
    return "find_object"


def _legacy_target_object(parsed: ParsedTask, task_type: str) -> str | None:
    category = _category_value(parsed.target.category)
    if task_type in {"find_room", "navigate_to_location", "summarize_scene", "compare_states"}:
        return "door" if category == TargetCategory.DOOR.value else None
    if category == TargetCategory.DOOR.value:
        return "door"
    if category == TargetCategory.ROOM.value:
        return None
    return parsed.target.name_en or parsed.target.name_zh or None


def _legacy_target_room(parsed: ParsedTask) -> str | None:
    if _category_value(parsed.target.category) == TargetCategory.ROOM.value:
        return parsed.target.name_zh or parsed.target.name_en or None
    if _category_value(parsed.area.category) == TargetCategory.ROOM.value:
        return parsed.area.name_zh or None
    return None


def _legacy_target_location(parsed: ParsedTask, target_room: str | None) -> str | None:
    if target_room:
        return target_room
    if parsed.area.name_zh:
        return parsed.area.name_zh
    if _category_value(parsed.target.category) in {
        TargetCategory.AREA.value,
        TargetCategory.FLOOR.value,
        TargetCategory.CORRIDOR.value,
    }:
        return parsed.target.name_zh or parsed.target.name_en or None
    return None


def _legacy_scope(parsed: ParsedTask) -> str | None:
    category = _category_value(parsed.area.category)
    if category == TargetCategory.FLOOR.value:
        return "current_floor"
    if category == TargetCategory.CORRIDOR.value:
        return "corridor"
    if category == TargetCategory.ROOM.value:
        return "current_room"
    return "current_scene"


def _first_state_query(parsed: ParsedTask) -> str | None:
    for subtask in parsed.requested_subtasks:
        if subtask.state_query:
            return subtask.state_query
    return None


def _has_subtask(parsed: ParsedTask, subtask_type: SubtaskType) -> bool:
    return any(_subtask_value(item.type) == subtask_type.value for item in parsed.requested_subtasks)


def _intent_value(intent: Any) -> str:
    return intent.value if isinstance(intent, TaskIntent) else str(intent)


def _category_value(category: Any) -> str:
    return category.value if isinstance(category, TargetCategory) else str(category)


def _subtask_value(subtask_type: Any) -> str:
    return subtask_type.value if isinstance(subtask_type, SubtaskType) else str(subtask_type)


def _task_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"task_{digest}"
