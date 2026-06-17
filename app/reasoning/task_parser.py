"""Rule-based parser from user goals to RobotTask."""

from __future__ import annotations

import hashlib
import re

from app.schemas import RobotTask


OBJECT_ALIASES = {
    "手机": "phone",
    "phone": "phone",
    "椅子": "chair",
    "chair": "chair",
    "桌子": "desk",
    "办公桌": "desk",
    "desk": "desk",
    "table": "desk",
    "门": "door",
    "房门": "door",
    "door": "door",
    "显示器": "monitor",
    "monitor": "monitor",
    "键盘": "keyboard",
    "keyboard": "keyboard",
}


def parse_robot_task(target_text: str) -> RobotTask:
    text = target_text.strip()
    if not text:
        raise ValueError("目标描述不能为空。")

    normalized = text.lower()
    task_type = _infer_task_type(text, normalized)
    target_object = _extract_target_object(text, normalized, task_type)
    target_room = _extract_room(text)
    target_location = _extract_location(text, target_room)
    scope = _extract_scope(text, normalized)
    constraints = _extract_constraints(text)
    parsed_slots = {
        "subtask": "check_door_state"
        if task_type == "inspect_area" and _mentions_door(text, normalized)
        else None,
        "state": _extract_state(text, normalized),
    }

    return RobotTask(
        task_id=_task_id(text),
        raw_text=text,
        task_type=task_type,  # type: ignore[arg-type]
        target_object=target_object,
        target_location=target_location,
        target_room=target_room,
        scope=scope,
        constraints=constraints,
        parsed_slots={key: value for key, value in parsed_slots.items() if value},
        confidence=_confidence_for(task_type, target_object, target_room, target_location),
    )


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
        if isinstance(raw, dict):
            return RobotTask.model_validate(raw)
    except Exception:
        return parse_robot_task(target_text)
    return parse_robot_task(target_text)


def _infer_task_type(text: str, normalized: str) -> str:
    if any(keyword in text for keyword in ["比较", "变化", "前后", "不同"]):
        return "compare_states"
    if any(keyword in text for keyword in ["总结", "描述", "概括"]) or any(
        keyword in normalized for keyword in ["summarize", "describe"]
    ):
        return "summarize_scene"
    if _is_inspection_task(text, normalized):
        return "inspect_area"
    if _is_door_state_task(text, normalized):
        return "check_door_state"
    if _is_count_task(text, normalized):
        return "count_objects"
    if _is_navigation_task(text, normalized):
        return "navigate_to_location"
    if _is_find_room_task(text, normalized):
        return "find_room"
    if any(keyword in text for keyword in ["是否", "是不是", "确认", "验证"]):
        return "verify_condition"
    return "find_object"


def _is_count_task(text: str, normalized: str) -> bool:
    return any(keyword in text for keyword in ["数", "几个", "多少", "统计"]) or "count" in normalized


def _is_inspection_task(text: str, normalized: str) -> bool:
    return any(keyword in text for keyword in ["巡查", "巡视", "巡检"]) or "inspect" in normalized


def _is_door_state_task(text: str, normalized: str) -> bool:
    return _mentions_door(text, normalized) and any(
        keyword in text for keyword in ["开", "关", "状态", "是否", "是不是", "检查"]
    )


def _is_navigation_task(text: str, normalized: str) -> bool:
    return any(keyword in text for keyword in ["去", "前往", "走到", "导航到", "移动到"]) or any(
        keyword in normalized for keyword in ["go to", "navigate to", "move to"]
    )


def _is_find_room_task(text: str, normalized: str) -> bool:
    return ("房间" in text or "room" in normalized or _extract_room(text) is not None) and any(
        keyword in text for keyword in ["找", "寻找", "定位"]
    )


def _mentions_door(text: str, normalized: str) -> bool:
    return "门" in text or "door" in normalized


def _extract_target_object(text: str, normalized: str, task_type: str) -> str | None:
    if task_type in {"find_room", "navigate_to_location", "summarize_scene", "compare_states"}:
        return None

    for alias, canonical in OBJECT_ALIASES.items():
        if alias in text or alias in normalized:
            return canonical
    return None


def _extract_room(text: str) -> str | None:
    match = re.search(r"\b\d{3,5}\b", text)
    if match:
        return match.group(0)
    return None


def _extract_location(text: str, target_room: str | None) -> str | None:
    if target_room:
        return target_room
    for keyword in ["桌子上", "办公桌上", "走廊尽头", "这层楼", "当前房间", "桌面", "门口"]:
        if keyword in text:
            return keyword
    return None


def _extract_scope(text: str, normalized: str) -> str | None:
    if "这层" in text or "本层" in text or "floor" in normalized:
        return "current_floor"
    if "走廊" in text or "corridor" in normalized:
        return "corridor"
    if "房间" in text or "room" in normalized:
        return "current_room"
    return "current_scene"


def _extract_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    for keyword in ["桌子上", "办公桌上", "桌面", "走廊尽头", "打开", "关闭"]:
        if keyword in text:
            constraints.append(keyword)
    return constraints


def _extract_state(text: str, normalized: str) -> str | None:
    if "打开" in text or "开着" in text or "open" in normalized:
        return "open"
    if "关闭" in text or "关着" in text or "closed" in normalized:
        return "closed"
    return None


def _confidence_for(
    task_type: str,
    target_object: str | None,
    target_room: str | None,
    target_location: str | None,
) -> float:
    confidence = 0.62
    if task_type != "find_object":
        confidence += 0.12
    if target_object or target_room or target_location:
        confidence += 0.14
    return min(round(confidence, 2), 0.92)


def _task_id(text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"task_{digest}"
