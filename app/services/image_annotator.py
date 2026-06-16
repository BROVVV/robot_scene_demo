"""Draw detected object boxes on source images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.schemas import SceneAnalysisResult, SceneObject


BOX_COLORS = [
    "#ff4d4f",
    "#1677ff",
    "#52c41a",
    "#faad14",
    "#722ed1",
    "#13c2c2",
    "#eb2f96",
    "#fa541c",
]


def export_annotated_image(
    result: SceneAnalysisResult,
    image_path: str | Path,
    output_path: str | Path,
) -> Path:
    source_path = Path(image_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Image file not found: {source_path}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    font = _load_font(max(14, canvas.width // 90))

    for index, obj in enumerate(result.objects):
        if not _has_usable_bbox(obj):
            continue
        color = BOX_COLORS[index % len(BOX_COLORS)]
        _draw_object(draw, canvas.size, obj, color, font)

    canvas.save(output)
    return output


def _draw_object(
    draw: ImageDraw.ImageDraw,
    image_size: tuple[int, int],
    obj: SceneObject,
    color: str,
    font: ImageFont.ImageFont,
) -> None:
    width, height = image_size
    bbox = obj.bbox_2d
    x1 = int(bbox.x1 * width)
    y1 = int(bbox.y1 * height)
    x2 = int(bbox.x2 * width)
    y2 = int(bbox.y2 * height)
    label = f"{obj.name_zh} {obj.confidence:.2f}"

    line_width = max(2, width // 500)
    draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)

    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    label_y1 = max(0, y1 - text_height - 6)
    label_y2 = label_y1 + text_height + 6
    label_x2 = min(width, x1 + text_width + 8)
    draw.rectangle((x1, label_y1, label_x2, label_y2), fill=color)
    draw.text((x1 + 4, label_y1 + 3), label, fill="white", font=font)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _has_usable_bbox(obj: SceneObject) -> bool:
    bbox = obj.bbox_2d
    width = bbox.x2 - bbox.x1
    height = bbox.y2 - bbox.y1
    if width <= 0 or height <= 0:
        return False
    if width >= 0.98 and height >= 0.98:
        return False
    return True
