"""Detection vocabulary for indoor robot scenes."""

from __future__ import annotations


TERM_ZH = {
    "object": "物体",
    "item": "物品",
    "base": "底座",
    "black base": "黑色底座",
    "green object": "绿色物体",
    "green item": "绿色物品",
    "chair": "椅子",
    "office chair": "办公椅",
    "clothing": "衣服",
    "yellow clothing": "黄色衣服",
    "coat": "外套",
    "jacket": "夹克",
    "shirt": "上衣",
    "desk": "桌子",
    "table": "桌子",
    "cabinet": "柜子",
    "cabinetry": "柜子",
    "cupboard": "柜子",
    "drawer": "抽屉",
    "furniture": "家具",
    "house": "室内空间",
    "room": "房间",
    "building": "建筑",
    "ceiling": "天花板",
    "window": "窗户",
    "curtain": "窗帘",
    "sofa": "沙发",
    "couch": "沙发",
    "bed": "床",
    "lamp": "灯",
    "light": "灯",
    "plant": "植物",
    "bookshelf": "书架",
    "counter": "台面",
    "countertop": "台面",
    "sink": "水槽",
    "appliance": "电器",
    "monitor": "显示器",
    "computer monitor": "显示器",
    "computer case": "电脑主机",
    "computer": "电脑",
    "robot": "机器人设备",
    "machine": "设备",
    "person": "人",
    "box": "箱子",
    "basket": "篮子",
    "trash bin": "垃圾桶",
    "shoe": "鞋",
    "bottle": "瓶子",
    "cup": "水杯",
    "cable": "线缆",
    "shelf": "架子",
    "door": "门",
    "wall": "墙",
    "floor": "地面",
    "backpack": "书包",
    "phone": "手机",
}

TERM_CATEGORY = {
    "object": "unknown",
    "item": "unknown",
    "base": "structure",
    "black base": "structure",
    "green object": "unknown",
    "green item": "unknown",
    "chair": "furniture",
    "office chair": "furniture",
    "desk": "furniture",
    "table": "furniture",
    "cabinet": "furniture",
    "cabinetry": "furniture",
    "cupboard": "furniture",
    "drawer": "furniture",
    "shelf": "furniture",
    "furniture": "furniture",
    "house": "structure",
    "room": "structure",
    "building": "structure",
    "ceiling": "structure",
    "window": "structure",
    "curtain": "structure",
    "sofa": "furniture",
    "couch": "furniture",
    "bed": "furniture",
    "lamp": "electronics",
    "light": "electronics",
    "plant": "unknown",
    "bookshelf": "furniture",
    "counter": "furniture",
    "countertop": "furniture",
    "sink": "structure",
    "appliance": "electronics",
    "clothing": "clothing",
    "yellow clothing": "clothing",
    "coat": "clothing",
    "jacket": "clothing",
    "shirt": "clothing",
    "monitor": "electronics",
    "computer monitor": "electronics",
    "computer case": "electronics",
    "computer": "electronics",
    "phone": "electronics",
    "robot": "robot",
    "machine": "equipment",
    "person": "person",
    "box": "container",
    "basket": "container",
    "trash bin": "container",
    "bottle": "container",
    "cup": "container",
    "shoe": "personal_item",
    "cable": "cable",
    "door": "structure",
    "wall": "structure",
    "floor": "structure",
    "backpack": "bag",
}

BASE_TERMS = [
    "chair",
    "office chair",
    "clothing",
    "yellow clothing",
    "coat",
    "jacket",
    "shirt",
    "desk",
    "table",
    "cabinet",
    "cabinetry",
    "drawer",
    "monitor",
    "computer monitor",
    "computer case",
    "computer",
    "robot",
    "machine",
    "person",
    "box",
    "basket",
    "trash bin",
    "shoe",
    "bottle",
    "cup",
    "cable",
    "shelf",
    "door",
    "backpack",
    "phone",
]

TARGET_TERM_RULES = {
    "绿色": ["green object", "green item", "object", "item"],
    "黑色": ["black base", "base", "object", "item"],
    "底座": ["base", "black base"],
    "物体": ["object", "item"],
    "黄衣服": ["yellow clothing", "clothing", "coat", "jacket", "shirt"],
    "衣服": ["clothing", "coat", "jacket", "shirt"],
    "椅子": ["chair", "office chair"],
    "凳子": ["chair"],
    "桌": ["desk", "table"],
    "手机": ["phone"],
    "显示器": ["monitor", "computer monitor"],
    "主机": ["computer case", "computer"],
    "电脑": ["computer", "computer monitor", "computer case"],
    "箱": ["box"],
    "篮": ["basket"],
    "鞋": ["shoe"],
    "杯": ["cup"],
    "瓶": ["bottle"],
    "包": ["backpack"],
    "人": ["person"],
    "机器人": ["robot", "machine"],
}


def build_detection_terms(target_text: str, max_terms: int = 36) -> list[str]:
    terms: list[str] = []
    for key, values in TARGET_TERM_RULES.items():
        if key in target_text:
            terms.extend(values)

    terms.extend(BASE_TERMS)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
        if len(deduped) >= max_terms:
            break
    return deduped


def build_detection_prompts(target_text: str, max_terms: int = 36) -> list[str]:
    target_terms: list[str] = []
    for key, values in TARGET_TERM_RULES.items():
        if key in target_text:
            target_terms.extend(values)
    target_terms = _dedupe(target_terms)

    prompts: list[str] = []
    if target_terms:
        prompts.append(terms_to_prompt(target_terms[:12]))

    base_terms = build_detection_terms(target_text, max_terms=max_terms)
    prompts.append(terms_to_prompt(base_terms))
    return _dedupe(prompts)


def terms_to_prompt(terms: list[str]) -> str:
    return ". ".join(terms) + "."


def label_zh(label: str) -> str:
    normalized = _normalize_label(label)
    if _contains_cjk(normalized):
        return normalized
    matched = _match_known_term(normalized, TERM_ZH)
    return TERM_ZH.get(matched, _fallback_zh_label(normalized))


def category_for_label(label: str) -> str:
    normalized = _normalize_label(label)
    matched = _match_known_term(normalized, TERM_CATEGORY)
    return TERM_CATEGORY.get(matched, "unknown")


def color_for_label(label: str) -> str | None:
    normalized = _normalize_label(label)
    if "yellow" in normalized:
        return "yellow"
    if "black" in normalized:
        return "black"
    if "white" in normalized:
        return "white"
    if "green" in normalized:
        return "green"
    return None


def _normalize_label(label: str) -> str:
    return label.lower().strip().strip(".")


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _fallback_zh_label(label: str) -> str:
    category = category_for_label(label)
    labels = {
        "person": "人",
        "furniture": "家具",
        "electronics": "电子设备",
        "container": "容器",
        "personal_item": "个人物品",
        "clothing": "衣物",
        "bag": "包",
        "cable": "线缆",
        "structure": "结构",
        "robot": "机器人设备",
        "equipment": "设备",
        "unknown": "物体",
    }
    return labels.get(category, "物体")


def _match_known_term(label: str, mapping: dict[str, str]) -> str:
    if label in mapping:
        return label
    matches = [
        term
        for term in mapping
        if term in label or all(part in label for part in term.split())
    ]
    if not matches:
        return label
    return max(matches, key=len)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
