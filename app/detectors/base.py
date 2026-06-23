"""Shared detector interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectedObject:
    label: str
    label_zh: str
    category: str
    bbox_2d: tuple[float, float, float, float]
    score: float
    color: str | None = None
    attributes: list[str] = field(default_factory=list)
    mask_area_ratio: float | None = None
    source: str = "detector"
    caption: str | None = None
    raw_attributes: dict[str, str | int | float | bool | None] = field(default_factory=dict)


class BaseObjectDetector:
    def detect(self, image_path: str, target_text: str) -> list[DetectedObject]:
        raise NotImplementedError
