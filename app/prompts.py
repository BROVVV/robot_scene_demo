"""Prompt templates for the vision scene understanding model."""

from __future__ import annotations


SYSTEM_PROMPT = """你是机器狗的快速视觉场景理解模块。

目标是在单张图片中快速识别与目标定位、避障、导航相关的关键物体，并输出合法 JSON。

要求：
1. 优先速度和稳定性，不要输出冗长解释。
2. 输出 6 到 12 个关键独立物体；复杂场景最多 15 个。
3. 不要重复输出同一个物体。
4. 对“挂着黄衣服的椅子”这类目标，要把椅子和衣服分开作为 object，并在 relations 中写出关系。
5. 所有距离、角度都是估计值，必须保守。
6. 只输出 JSON 对象，不要 Markdown，不要解释文本。
"""


def build_user_prompt(target_text: str, extra_instructions: str | None = None) -> str:
    prompt = f"""用户目标描述：{target_text}

请快速分析图片，返回下面结构的 JSON：

{{
  "scene_summary_zh": "一句话中文场景摘要",
  "objects": [
    {{
      "id": "obj_001",
      "name": "chair",
      "name_zh": "椅子",
      "category": "furniture",
      "color": "black",
      "attributes": ["key visible attributes"],
      "visible": true,
      "position": {{
        "horizontal": "left | center | right",
        "vertical": "front | middle | back",
        "relative_to_robot": "front-left",
        "estimated_distance_m": 1.2
      }},
      "bbox_2d": {{"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}},
      "confidence": 0.8
    }}
  ],
  "relations": [
    {{
      "source_id": "obj_001",
      "target_id": "obj_002",
      "relation_type": "left_of | right_of | in_front_of | behind | on | under | above | below | in | near | far | contains | occluding",
      "description_zh": "中文关系",
      "estimated_distance_m": null,
      "confidence": 0.7
    }}
  ],
  "topology": {{"nodes": [], "edges": []}},
  "target_decision": {{
    "target_text": "{target_text}",
    "is_present": true,
    "matched_object_ids": ["obj_001"],
    "match_reason_zh": "中文原因",
    "confidence": 0.8
  }},
  "route_plan": {{
    "route_type": "approach_visible_target | explore_likely_location",
    "summary_zh": "中文路线摘要",
    "steps": [
      {{
        "step_id": 1,
        "action": "move_forward | move_backward | turn_left | turn_right | stop",
        "distance_m": 1.0,
        "turn_angle_deg": null,
        "description_zh": "中文动作"
      }}
    ],
    "safety_notes_zh": ["单张图片估计，仅用于 Demo"]
  }}
}}

约束：
- objects 只列关键物体，目标相关物体、障碍物、桌椅、人物、显示器、主机、箱子、鞋、篮子、线缆等优先。
- relations 优先输出强语义关系：承载、悬挂、遮挡、靠近、左右、前后。至少覆盖目标相关物体及明显相邻/承载物体。
- 不要为了关系数量枚举所有两两组合；系统会根据 bbox 自动补全稀疏空间关系以保证拓扑连通。
- 不要为了凑数量重复列出同一张桌子、同一台显示器、同一台主机。
- 目标是“挂着黄衣服的椅子”时，matched_object_ids 应包含相关椅子和黄色衣服。
- bbox_2d 坐标必须在 0 到 1。
- confidence 必须在 0 到 1。
- 不确定距离或角度用 null。
- 只输出 JSON。"""

    if extra_instructions:
        prompt += f"\n\n额外复查要求：\n{extra_instructions}"

    return prompt
