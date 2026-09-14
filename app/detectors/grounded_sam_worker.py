"""Worker script run by a Python environment that has Grounding DINO/SAM2 deps.

Two modes:

* one-shot (legacy): ``--image`` + ``--output``, run once and write a JSON file.
* ``--serve``: load GroundingDINO/SAM2 exactly once, then read one JSON request
  per line from stdin and write one JSON response per line to stdout.  This is
  what the persistent detector uses so a whole search session loads each model
  a single time instead of once per frame.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--image", required=False)
    parser.add_argument("--output", required=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--text-prompt", required=False)
    parser.add_argument("--grounding-config", required=True)
    parser.add_argument("--grounding-checkpoint", required=True)
    parser.add_argument("--box-threshold", type=float, required=False)
    parser.add_argument("--text-threshold", type=float, required=False)
    parser.add_argument("--sam2-config", required=True)
    parser.add_argument("--sam2-checkpoint", required=True)
    parser.add_argument("--max-objects", type=int, required=False)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--disable-sam2", action="store_true")
    parser.add_argument("--ram-root", default="")
    parser.add_argument("--ram-checkpoint", default="")
    parser.add_argument("--disable-ram-plus", action="store_true")
    return parser.parse_args()


RAM_IMAGE_SIZE = 384


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)

    try:
        import numpy as np
        import torch
        from torchvision.ops import box_convert, nms
        from grounding_dino.groundingdino.util.inference import (
            load_image,
            load_model,
            predict,
        )
    except Exception as exc:
        print(
            "Missing Grounding DINO runtime dependencies. "
            "Install torch, torchvision, numpy and the local Grounded-SAM-2 package "
            f"for this Python interpreter. Original error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    device = _select_device(args.device, torch)
    grounding_model = load_model(
        model_config_path=str(root / args.grounding_config),
        model_checkpoint_path=str(root / args.grounding_checkpoint),
        device=device,
    )
    sam2_predictor = _build_sam2_predictor(root, args, device, torch)
    ram_model, ram_transform = _build_ram_plus(args, device, torch)

    if args.serve:
        _serve(
            args, grounding_model, sam2_predictor, ram_model, ram_transform,
            device, torch, np, load_image, predict, box_convert, nms,
        )
        return 0

    if not args.text_prompt or not args.text_prompt.strip():
        raise ValueError(
            "text_prompt is empty. GroundingDINO requires a non-empty "
            "open-vocabulary prompt."
        )
    objects = _detect(
        args, args.image, args.text_prompt,
        args.box_threshold, args.text_threshold, args.max_objects,
        grounding_model, sam2_predictor, device, torch, np,
        load_image, predict, box_convert, nms,
    )
    _write_json(args.output, {"objects": objects})
    return 0


def _serve(args, grounding_model, sam2_predictor, ram_model, ram_transform,
           device, torch, np, load_image, predict, box_convert, nms) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_stdout({"error": f"bad request json: {exc}"})
            continue
        if request.get("operation", "detect") == "tag":
            try:
                _write_stdout(_tag(ram_model, ram_transform, request["image"], device, torch))
            except Exception as exc:  # 一帧坏了不能弄死整个会话
                _write_stdout({"error": f"{type(exc).__name__}: {exc}"})
            continue
        if not request.get("text_prompt") or not str(request["text_prompt"]).strip():
            _write_stdout({"error": "empty text_prompt"})
            continue
        try:
            objects = _detect(
                args, request["image"], str(request["text_prompt"]),
                float(request.get("box_threshold", args.box_threshold or 0.35)),
                float(request.get("text_threshold", args.text_threshold or 0.25)),
                int(request.get("max_objects", args.max_objects or 20)),
                grounding_model, sam2_predictor, device, torch, np,
                load_image, predict, box_convert, nms,
            )
            _write_stdout({"objects": objects})
        except Exception as exc:  # a bad frame must not kill the session
            _write_stdout({"error": f"{type(exc).__name__}: {exc}"})


def _detect(args, image_path, text_prompt, box_threshold, text_threshold,
            max_objects, grounding_model, sam2_predictor, device, torch, np,
            load_image, predict, box_convert, nms) -> list[dict]:
    image_source, image = load_image(image_path)
    height, width, _ = image_source.shape

    boxes, confidences, labels = _predict_all_prompts(
        grounding_model=grounding_model,
        image=image,
        text_prompt=text_prompt,
        box_threshold=box_threshold,
        text_threshold=text_threshold,
        device=device,
        torch=torch,
        predict=predict,
    )
    if len(boxes) == 0:
        return []

    pixel_boxes = boxes * torch.tensor([width, height, width, height])
    xyxy = box_convert(boxes=pixel_boxes, in_fmt="cxcywh", out_fmt="xyxy")
    keep = _filter_boxes(xyxy, confidences, width, height, torch)
    xyxy = xyxy[keep]
    confidences = confidences[keep]
    labels = [labels[index] for index in keep.detach().cpu().numpy().tolist()]
    if len(xyxy) == 0:
        return []

    keep = nms(xyxy, confidences, iou_threshold=0.5)[: max_objects]
    xyxy = xyxy[keep].detach().cpu().numpy()
    confidences_list = confidences[keep].detach().cpu().numpy().tolist()
    labels_list = [labels[index] for index in keep.detach().cpu().numpy().tolist()]

    masks = _predict_masks(sam2_predictor, image_source, xyxy, device, torch, np)

    objects = []
    for index, (label, score, box) in enumerate(zip(labels_list, confidences_list, xyxy.tolist())):
        mask = masks[index] if masks is not None else None
        mask_rle = None
        mask_size = None
        refined_box = box
        mask_area_ratio = None
        if mask is not None:
            mask_size = [int(height), int(width)]
            mask_rle = _encode_mask_runs(mask)
            mask_area_ratio = float(mask.sum() / (width * height))
            ys, xs = np.nonzero(mask)
            if xs.size > 0:
                refined_box = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
        x1, y1, x2, y2 = refined_box
        objects.append(
            {
                "label": str(label).lower().strip(),
                "score": float(score),
                "text_score": float(score),
                "source_prompt_term": str(label).lower().strip(),
                "bbox_2d": [
                    _clamp(x1 / width),
                    _clamp(y1 / height),
                    _clamp(x2 / width),
                    _clamp(y2 / height),
                ],
                "mask_area_ratio": mask_area_ratio,
                "mask_size": mask_size,
                "mask_rle": mask_rle,
                "attributes": [],
            }
        )

    return objects


def _build_ram_plus(args: argparse.Namespace, device: str, torch):
    """载入 RAM++（swin_l, 384）。返回 (model, transform)，未启用时 (None, None)。

    只在 worker 启动时调用一次 —— RAM++ 的职责是「这帧大致有哪些类别」，
    不是 tracking，绝不能每帧重新载入。
    """
    if args.disable_ram_plus or not args.ram_root or not args.ram_checkpoint:
        return None, None
    ram_root = Path(args.ram_root).resolve()
    sys.path.insert(0, str(ram_root))
    try:
        from ram import get_transform
        from ram.models import ram_plus
    except Exception as exc:
        print(f"RAM++ import failed, continuing without tags: {exc}", file=sys.stderr)
        return None, None
    checkpoint = Path(args.ram_checkpoint)
    if not checkpoint.is_absolute():
        checkpoint = ram_root / checkpoint
    try:
        model = ram_plus(
            pretrained=str(checkpoint),
            image_size=RAM_IMAGE_SIZE,
            vit="swin_l",
        )
        model.eval()
        return model.to(device), get_transform(image_size=RAM_IMAGE_SIZE)
    except Exception as exc:
        print(f"RAM++ build failed, continuing without tags: {exc}", file=sys.stderr)
        return None, None


def _tag(ram_model, ram_transform, image_path: str, device: str, torch) -> dict:
    if ram_model is None or ram_transform is None:
        raise RuntimeError(
            "RAM++ is not loaded in this worker (missing --ram-root/--ram-checkpoint "
            "or --disable-ram-plus was passed)."
        )
    from PIL import Image
    from ram import inference_ram

    image = ram_transform(Image.open(image_path).convert("RGB")).unsqueeze(0).to(device)
    tags_en, tags_zh = inference_ram(image, ram_model)
    return {
        "tags_en": _split_tags(tags_en),
        "tags_zh": _split_tags(tags_zh),
    }


def _split_tags(raw) -> list[str]:
    """RAM++ 返回的是 `|` 分隔的一整串（实测，不是逗号），拆成列表。"""
    return [
        part.strip()
        for part in re.split(r"[|,]", str(raw))
        if part.strip()
    ]


def _build_sam2_predictor(root: Path, args: argparse.Namespace, device: str, torch):
    if args.disable_sam2:
        return None
    try:
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
    except Exception as exc:
        print(f"SAM2 import failed, continuing without masks: {exc}", file=sys.stderr)
        return None
    try:
        sam2_model = build_sam2(
            args.sam2_config,
            str(root / args.sam2_checkpoint),
            device=device,
        )
        return SAM2ImagePredictor(sam2_model)
    except Exception as exc:
        print(f"SAM2 build failed, continuing without masks: {exc}", file=sys.stderr)
        return None


def _predict_masks(predictor, image_source, boxes, device: str, torch, np):
    """Return one bool mask (HxW) per box, or None for a box that failed."""
    if predictor is None or len(boxes) == 0:
        return None
    try:
        predictor.set_image(image_source)
        with torch.inference_mode():
            masks, _, _ = predictor.predict(
                point_coords=None,
                point_labels=None,
                box=boxes,
                multimask_output=False,
            )
        if masks.ndim == 4:
            masks = masks.squeeze(1)
        return [np.asarray(mask, dtype=bool) for mask in masks]
    except Exception as exc:
        print(f"SAM2 prediction failed, continuing without masks: {exc}", file=sys.stderr)
        return None


def _encode_mask_runs(mask) -> list[list[int]]:
    """Row-run encode a bool mask as [row, start_x, length] runs."""
    runs: list[list[int]] = []
    height, width = mask.shape
    for y in range(height):
        row = mask[y]
        x = 0
        while x < width:
            if not row[x]:
                x += 1
                continue
            start = x
            while x < width and row[x]:
                x += 1
            runs.append([y, start, x - start])
    return runs


def _select_device(requested: str, torch) -> str:
    if requested != "auto":
        return requested
    has_cuda_runtime = getattr(torch.version, "cuda", None) is not None
    return "cuda" if has_cuda_runtime and torch.cuda.is_available() else "cpu"


def _predict_all_prompts(
    grounding_model,
    image,
    text_prompt: str,
    box_threshold: float,
    text_threshold: float,
    device: str,
    torch,
    predict,
):
    boxes_list = []
    confidences_list = []
    labels: list[str] = []
    for prompt in [item.strip() for item in text_prompt.split("||") if item.strip()]:
        # 一个 prompt 里可能用 "." 串了多个 term（"trash can. trash bin. ..."）。
        # 多 term 一起喂给 GroundingDINO 时，phrase 会把所有超过 text_threshold 的
        # token 连成一串（"trash can trash bin waste bin garbage bin"），下游查不到
        # 词表就兜底成"物体"，所有物体标签雷同、被错误合并。拆成单 term 逐个推理。
        for term in [part.strip() for part in prompt.split(".") if part.strip()]:
            boxes, confidences, _ = predict(
                model=grounding_model,
                image=image,
                caption=term.lower(),
                box_threshold=box_threshold,
                text_threshold=text_threshold,
                device=device,
            )
            if len(boxes) == 0:
                continue
            boxes_list.append(boxes)
            confidences_list.append(confidences)
            # 不用 predict 回传的 phrase：text_threshold=0.10 很低，query 的激活常落在
            # [CLS]/[SEP] 上，解出来是空串或 "[unk] [unk] [unk]"。这里每次只问一个
            # term，检出来的就是这个 term，直接用它当标签最稳。
            labels.extend([term] * len(boxes))

    if not boxes_list:
        return torch.empty((0, 4)), torch.empty((0,)), []
    return torch.cat(boxes_list, dim=0), torch.cat(confidences_list, dim=0), labels


def _filter_boxes(xyxy, confidences, width: int, height: int, torch):
    box_widths = xyxy[:, 2] - xyxy[:, 0]
    box_heights = xyxy[:, 3] - xyxy[:, 1]
    area_ratio = (box_widths * box_heights) / float(width * height)
    valid = (area_ratio < 0.85) & (box_widths > 2) & (box_heights > 2)
    indexes = torch.nonzero(valid).flatten()
    if len(indexes) > 0:
        return indexes
    return torch.argsort(confidences, descending=True)[:1]


def _write_json(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_stdout(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


if __name__ == "__main__":
    raise SystemExit(main())
