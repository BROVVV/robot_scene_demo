"""Standalone Florence-2 object detection worker.

The main app calls this file in a subprocess so optional heavy dependencies do
not affect mock, LLM, or Grounded-SAM modes.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Florence-2 object detection.")
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-id", default="microsoft/Florence-2-base")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--max-objects", type=int, default=40)
    parser.add_argument("--confidence-threshold", type=float, default=0.15)
    parser.add_argument("--task-prompt", default="<OD>")
    parser.add_argument("--allow-mock", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = Path(args.image)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    try:
        payload = run_florence2(
            image_path=image_path,
            model_id=args.model_id,
            device=args.device,
            max_objects=args.max_objects,
            confidence_threshold=args.confidence_threshold,
            task_prompt=args.task_prompt,
        )
    except Exception as exc:
        if not args.allow_mock and os.getenv("FLORENCE2_ALLOW_MOCK", "").lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise RuntimeError(
                "Florence-2 inference failed. Install optional dependencies "
                "(torch, transformers) and model weights, or set "
                "FLORENCE2_ALLOW_MOCK=true for smoke tests."
            ) from exc
        payload = mock_payload(image_path, str(exc))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def run_florence2(
    *,
    image_path: Path,
    model_id: str,
    device: str,
    max_objects: int,
    confidence_threshold: float,
    task_prompt: str,
) -> dict[str, Any]:
    _ensure_writable_hf_caches(model_id)
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "Florence-2 requires optional packages: torch and transformers."
        ) from exc

    selected_device = _select_device(device, torch)
    torch_dtype = torch.float16 if selected_device == "cuda" else torch.float32
    device_name = (
        torch.cuda.get_device_name(0)
        if selected_device == "cuda" and torch.cuda.is_available()
        else "cpu"
    )

    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
        attn_implementation="sdpa",
    ).to(selected_device)
    model.eval()

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        inputs = processor(text=task_prompt, images=rgb, return_tensors="pt")
        inputs = {
            key: _to_device(value, selected_device, torch_dtype)
            for key, value in inputs.items()
            if hasattr(value, "to")
        }
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            num_beams=3,
            do_sample=False,
        )
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            generated_text,
            task=task_prompt,
            image_size=(width, height),
        )

    items = _parse_objects(parsed, task_prompt, width, height)
    objects = [
        item
        for item in items
        if item["confidence"] >= confidence_threshold
    ][:max_objects]
    for index, item in enumerate(objects, start=1):
        item["id"] = f"obj_{index:03d}"

    return {
        "backend": "florence2",
        "objects": objects,
        "raw": {
            "model_id": model_id,
            "task_prompt": task_prompt,
            "device": selected_device,
            "device_name": device_name,
            "torch_dtype": str(torch_dtype),
            "parsed": parsed,
        },
    }


def _parse_objects(
    parsed: dict[str, Any],
    task_prompt: str,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    data = parsed.get(task_prompt, parsed)
    labels = data.get("labels") or data.get("class_names") or []
    boxes = data.get("bboxes") or data.get("boxes") or []
    scores = data.get("scores") or data.get("confidence") or []
    objects: list[dict[str, Any]] = []
    for index, box in enumerate(boxes):
        label = str(labels[index] if index < len(labels) else "object").strip().lower()
        if not label:
            continue
        score = float(scores[index]) if index < len(scores) else 0.5
        x1, y1, x2, y2 = _clamp_box(box, width, height)
        if x2 <= x1 or y2 <= y1:
            continue
        objects.append(
            {
                "id": "",
                "label": label,
                "bbox_xyxy": [x1, y1, x2, y2],
                "confidence": max(0.0, min(1.0, score)),
                "source": "florence2",
                "caption": None,
                "attributes": {},
                "mask": None,
                "mask_area_ratio": None,
            }
        )
    return objects


def _to_device(value: Any, device: str, torch_dtype: Any) -> Any:
    if getattr(value, "is_floating_point", lambda: False)():
        return value.to(device=device, dtype=torch_dtype)
    return value.to(device=device)


def _ensure_writable_hf_caches(model_id: str) -> None:
    model_path = Path(model_id)
    _patch_local_florence2_cpu_imports(model_path)
    cache_root = (
        model_path.parent / ".hf_runtime_cache"
        if model_path.exists()
        else Path.cwd() / ".hf_runtime_cache"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_root / "hub"))
    os.environ.setdefault("HF_MODULES_CACHE", str(cache_root / "modules"))


def _patch_local_florence2_cpu_imports(model_path: Path) -> None:
    modeling_path = model_path / "modeling_florence2.py"
    if not modeling_path.is_file():
        return
    text = modeling_path.read_text(encoding="utf-8")
    replacements = {
        "from flash_attn.bert_padding import index_first_axis, pad_input, unpad_input  # noqa": (
            '_flash_attn_padding = __import__("flash_attn.bert_padding", '
            'fromlist=["index_first_axis", "pad_input", "unpad_input"])\n'
            "    index_first_axis = _flash_attn_padding.index_first_axis  # noqa\n"
            "    pad_input = _flash_attn_padding.pad_input  # noqa\n"
            "    unpad_input = _flash_attn_padding.unpad_input  # noqa"
        ),
        "from flash_attn import flash_attn_func, flash_attn_varlen_func": (
            '_flash_attn = __import__("flash_attn", '
            'fromlist=["flash_attn_func", "flash_attn_varlen_func"])\n'
            "    flash_attn_func = _flash_attn.flash_attn_func\n"
            "    flash_attn_varlen_func = _flash_attn.flash_attn_varlen_func"
        ),
    }
    patched = text
    for old, new in replacements.items():
        patched = patched.replace(old, new)
    if patched != text:
        modeling_path.write_text(patched, encoding="utf-8")


def mock_payload(image_path: Path, warning: str) -> dict[str, Any]:
    with Image.open(image_path) as image:
        width, height = image.size
    box_w = width * 0.18
    box_h = height * 0.18
    cx = width * 0.5
    cy = height * 0.62
    return {
        "backend": "florence2",
        "objects": [
            {
                "id": "obj_001",
                "label": "phone",
                "bbox_xyxy": [
                    round(cx - box_w / 2, 2),
                    round(cy - box_h / 2, 2),
                    round(cx + box_w / 2, 2),
                    round(cy + box_h / 2, 2),
                ],
                "confidence": 0.5,
                "source": "florence2",
                "caption": None,
                "attributes": {"mock": True},
                "mask": None,
                "mask_area_ratio": None,
            }
        ],
        "raw": {
            "mock": True,
            "warning": warning,
        },
    }


def _select_device(device: str, torch_module: Any) -> str:
    if device == "auto":
        return "cuda" if torch_module.cuda.is_available() else "cpu"
    if device == "cuda" and not torch_module.cuda.is_available():
        raise RuntimeError("FLORENCE2_DEVICE=cuda requested but CUDA is unavailable.")
    return device


def _clamp_box(box: Any, width: int, height: int) -> tuple[float, float, float, float]:
    values = [float(value) for value in list(box)[:4]]
    x1 = max(0.0, min(float(width), values[0]))
    y1 = max(0.0, min(float(height), values[1]))
    x2 = max(0.0, min(float(width), values[2]))
    y2 = max(0.0, min(float(height), values[3]))
    return x1, y1, x2, y2


if __name__ == "__main__":
    raise SystemExit(main())
