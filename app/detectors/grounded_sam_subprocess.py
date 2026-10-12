"""Grounding DINO + SAM2 detector via one persistent external Python worker."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import Settings
from app.detectors.base import BaseObjectDetector, DetectedObject
from app.detectors.vocabulary import (
    build_detection_prompts,
    category_for_label,
    color_for_label,
    filter_ram_scene_term_pairs,
    filter_ram_scene_terms,
    normalize_scene_tag,
    resolve_label_zh,
    terms_to_prompt,
)
from app.perception.grounding_prompt_planner import (
    GroundingPromptError,
    GroundingPromptPlanner,
)
from app.task_understanding.schemas import GroundingPromptPlan
from app.reasoning.target_profile import TargetProfile


class DetectorRuntimeError(RuntimeError):
    """Raised when the external detector cannot run or returns invalid output."""


class GroundedSAMSubprocessDetector(BaseObjectDetector):
    def __init__(
        self,
        settings: Settings,
        target_profile: TargetProfile | None = None,
        parsed_task: Any | None = None,
        navigation_task: Any | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.target_profile = target_profile
        self.parsed_task = parsed_task
        self.navigation_task = navigation_task
        self.llm_client = llm_client
        self.grounding_prompt_plan: GroundingPromptPlan | None = None
        self.grounding_prompt_retry_plan: GroundingPromptPlan | None = None
        self._worker: subprocess.Popen | None = None
        self._worker_stderr: Any = None
        # RAM++ 最近一次 `_tag` 返回的 en -> zh 配对。只活在这个实例里：
        # 对外接口（`discover_scene_terms`）仍然只给英文词表，
        # GroundingDINO 的 prompt 契约不变（计划书 §6 阶段 B.4）。
        self._ram_term_zh: dict[str, str] = {}

    def detect(self, image_path: str, target_text: str) -> list[DetectedObject]:
        return self.detect_with_dynamic_terms(image_path, target_text, [])

    def detect_with_dynamic_terms(
        self,
        image_path: str,
        target_text: str,
        dynamic_terms: list[str],
        ram_scene_terms: list[str] | None = None,
    ) -> list[DetectedObject]:
        image = Path(image_path).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        self._ensure_worker()

        prompt_text = " || ".join(
            self._build_prompts(target_text, dynamic_terms, ram_scene_terms)
        )
        if self.settings.grounding_prompt_require_non_empty and not prompt_text.strip():
            raise GroundingPromptError(
                "GroundingDINO prompt is empty. Detection was not executed."
            )
        payload = self._request(
            image=image,
            text_prompt=prompt_text,
            box_threshold=self.settings.grounding_dino_box_threshold,
            text_threshold=self.settings.grounding_dino_text_threshold,
        )
        if not payload.get("objects"):
            payload = self._maybe_retry_prompt_expansion(
                image=image,
                target_text=target_text,
                previous_prompt=prompt_text,
                previous_payload=payload,
            )
        if not payload.get("objects"):
            payload = self._maybe_retry_high_recall_thresholds(
                image=image,
                text_prompt=(
                    self.grounding_prompt_retry_plan.grounding_prompt
                    if self.grounding_prompt_retry_plan is not None
                    else prompt_text
                ),
                previous_payload=payload,
            )

        return [
            _to_detected_object(
                item,
                self.target_profile,
                # 归一化必须和配对表建表时用的是同一套，否则查不到（表是空的
                # 时候表现为"RAM 中文全丢"，不会报错）。
                runtime_zh=self._ram_term_zh.get(
                    normalize_scene_tag(item.get("label") or "")
                ),
            )
            for item in payload.get("objects", [])
        ]

    def discover_scene_terms(self, image_path: str) -> list[str]:
        """问 RAM++「这一帧大概有哪些类别」，返回过滤后的英文物体词。

        只用于给 GroundingDINO 扩词，**不返回 embedding、不落库**。
        中文配对不从这里出去（prompt 只要英文），而是存在实例上供
        `_to_detected_object` 查 —— RAM++ 已经算好的中文不能再丢（计划书 §2.2）。
        """
        image = Path(image_path).resolve()
        if not image.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        self._ensure_worker()
        payload = self._request(image=image, operation="tag")
        pairs = filter_ram_scene_term_pairs(
            list(payload.get("tags_en", [])),
            list(payload.get("tags_zh", [])),
            self.settings.ram_scene_max_terms,
        )
        self._ram_term_zh = {term: zh for term, zh in pairs if zh}
        return [term for term, _zh in pairs]

    def close(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        try:
            if worker.stdin is not None:
                worker.stdin.close()
            worker.terminate()
            worker.wait(timeout=5)
        except Exception:
            try:
                worker.kill()
            except Exception:
                pass
        if self._worker_stderr is not None:
            try:
                self._worker_stderr.close()
            except Exception:
                pass
            self._worker_stderr = None

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.poll() is None:
            return
        self._worker = None
        env = os.environ.copy()
        if self.settings.grounded_sam_pythonpath:
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                self.settings.grounded_sam_pythonpath
                if not existing_pythonpath
                else f"{self.settings.grounded_sam_pythonpath}:{existing_pythonpath}"
            )
        command = [
            self.settings.grounded_sam_python,
            str(Path(__file__).with_name("grounded_sam_worker.py")),
            "--serve",
            "--root",
            self.settings.grounded_sam_root,
            "--grounding-config",
            self.settings.grounding_dino_config,
            "--grounding-checkpoint",
            self.settings.grounding_dino_checkpoint,
            "--sam2-config",
            self.settings.sam2_config,
            "--sam2-checkpoint",
            self.settings.sam2_checkpoint,
            "--device",
            self.settings.detection_device,
        ]
        if not self.settings.enable_sam2:
            command.append("--disable-sam2")
        if self.settings.ram_plus_root:
            command.extend(["--ram-root", self.settings.ram_plus_root])
        if self.settings.ram_plus_checkpoint:
            command.extend(["--ram-checkpoint", self.settings.ram_plus_checkpoint])
        if not self.settings.enable_ram_plus:
            command.append("--disable-ram-plus")
        self._worker_stderr = tempfile.NamedTemporaryFile(
            prefix="grounded_sam_worker_", suffix=".log", delete=False
        )
        self._worker = subprocess.Popen(
            command,
            cwd=self.settings.grounded_sam_root,
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._worker_stderr,
        )

    def _request(
        self,
        *,
        image: Path,
        text_prompt: str = "",
        box_threshold: float = 0.0,
        text_threshold: float = 0.0,
        operation: str = "detect",
    ) -> dict[str, Any]:
        self._ensure_worker()
        request = {
            "operation": operation,
            "image": str(image),
            "text_prompt": text_prompt,
            "box_threshold": box_threshold,
            "text_threshold": text_threshold,
            "max_objects": self.settings.max_detected_objects,
            # 轻量化计划 §20。**只走请求、不加命令行参数**：四个阈值里其余三个
            # 两边都传，但 worker 的 `request.get(key, args.key)` 永远优先取请求值
            # —— 命令行那份对 --serve 路径是够不着的死路径（看着像生效、其实从不
            # 生效）。worker 的 argparse 默认值已经等于这里的默认值，手动跑
            # --image 那条路照样有门，所以少传一份不丢能力。
            "sam2_min_box_score": self.settings.sam2_min_box_score,
        }
        assert self._worker is not None and self._worker.stdin is not None
        self._worker.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self._worker.stdin.flush()
        return self._read_response()

    def _read_response(self) -> dict[str, Any]:
        assert self._worker is not None and self._worker.stdout is not None
        while True:
            line = self._worker.stdout.readline()
            if line == "":
                raise DetectorRuntimeError(self._worker_died_message())
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue  # library stdout noise, not a response
            if "error" in payload:
                raise DetectorRuntimeError(
                    "Grounding DINO/SAM2 worker error: " + str(payload["error"])
                )
            return payload

    def _worker_died_message(self) -> str:
        tail = ""
        if self._worker_stderr is not None:
            try:
                self._worker_stderr.seek(0)
                tail = self._worker_stderr.read()[-2000:]
            except Exception:
                tail = ""
            if isinstance(tail, bytes):
                tail = tail.decode("utf-8", errors="replace")
        return "Grounding DINO/SAM2 worker exited unexpectedly. stderr tail:\n" + tail

    def _build_prompts(
        self,
        target_text: str,
        dynamic_terms: list[str],
        ram_scene_terms: list[str] | None = None,
    ) -> list[str]:
        # 参数默认是 None（没跑 RAM++ 就是没有场景词），下面两个分支都按 list 用。
        ram_scene_terms = ram_scene_terms or []
        if (
            self.settings.grounding_prompt_llm_expansion_enabled
            and self.parsed_task is not None
        ):
            planner = GroundingPromptPlanner(
                llm_client=self.llm_client,
                settings=self.settings,
                max_terms=self.settings.grounding_prompt_max_terms,
                min_terms=self.settings.grounding_prompt_min_terms,
            )
            self.grounding_prompt_plan = planner.build(
                parsed_task=self.parsed_task,
                navigation_task=self.navigation_task,
                target_profile=self.target_profile,
            )
            self._write_prompt_plan(
                self.grounding_prompt_plan,
                self.settings.grounding_prompt_debug_output,
            )
            terms = _prompt_to_terms(self.grounding_prompt_plan.grounding_prompt)
            terms.extend(dynamic_terms)
            # RAM 词排在 planner 的目标词之后，目标词永远不会被截断。
            terms.extend(
                filter_ram_scene_terms(
                    ram_scene_terms, self.settings.ram_scene_max_terms
                )
            )
            prompt = _terms_to_prompt(_dedupe_terms(terms))
            return [prompt] if prompt else []

        target_terms = (
            self.target_profile.detector_terms()
            if self.target_profile is not None
            else []
        )
        # §4.1：目标画像的上下文词（如「挂着书包的椅子」的 backpack）属于
        # mandatory target terms，必须无条件进 prompt —— 它跟 static base 词表
        # 是两回事，static_object_prompts_enabled 只该管后者。
        # 少这一条，「挂着书包的椅子」就只搜 chair，搜不到单独的 backpack（§15）。
        prompts = build_detection_prompts(
            target_text,
            dynamic_terms=[*target_terms, *dynamic_terms],
            context_terms=(
                [
                    *self.target_profile.context_labels_en,
                    # §15：关系锚点是关系目标的一半，必须一起搜。
                    *self.target_profile.relation_anchor_labels_en,
                ]
                if self.target_profile is not None
                else None
            ),
            include_base_terms=(
                self.settings.static_object_prompts_enabled and not target_terms
            ),
        )
        # RAM++ 的场景词单独占一个 prompt 槽位，不混进目标词那一槽。
        # 否则 RAM 返回的 tag 一多，目标词就会被该槽位的 16 个名额挤掉。
        ram_terms = filter_ram_scene_terms(
            ram_scene_terms, self.settings.ram_scene_max_terms
        )
        if ram_terms:
            ram_prompt = terms_to_prompt(ram_terms)
            if ram_prompt not in prompts:
                prompts.append(ram_prompt)
        if not prompts:
            prompts = build_detection_prompts(
                target_text,
                include_base_terms=self.settings.static_object_prompts_enabled,
            )
        return prompts

    def _maybe_retry_prompt_expansion(
        self,
        *,
        image: Path,
        target_text: str,
        previous_prompt: str,
        previous_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            self.grounding_prompt_plan is None
            or not self.settings.grounding_prompt_retry_on_empty
            or self.settings.grounding_prompt_max_retries <= 0
        ):
            return previous_payload
        planner = GroundingPromptPlanner(
            llm_client=self.llm_client,
            settings=self.settings,
            max_terms=self.settings.grounding_prompt_max_terms,
            min_terms=self.settings.grounding_prompt_min_terms,
        )
        summary = {
            "num_raw_candidates": len(previous_payload.get("objects", [])),
            "target": target_text,
        }
        self.grounding_prompt_retry_plan = planner.build_retry(
            parsed_task=self.parsed_task,
            previous_prompt=previous_prompt,
            detection_summary=summary,
            target_profile=self.target_profile,
            previous_plan=self.grounding_prompt_plan,
        )
        self._write_prompt_plan(
            self.grounding_prompt_retry_plan,
            self.settings.grounding_prompt_retry_debug_output,
        )
        return self._request(
            image=image,
            text_prompt=self.grounding_prompt_retry_plan.grounding_prompt,
            box_threshold=self.settings.grounding_dino_box_threshold,
            text_threshold=self.settings.grounding_dino_text_threshold,
        )

    def _maybe_retry_high_recall_thresholds(
        self,
        *,
        image: Path,
        text_prompt: str,
        previous_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not (
            self.settings.enable_gdino_high_recall
            and (
                self.settings.grounding_dino_box_threshold
                > self.settings.grounding_dino_high_recall_box_threshold
                or self.settings.grounding_dino_text_threshold
                > self.settings.grounding_dino_high_recall_text_threshold
            )
        ):
            return previous_payload
        try:
            return self._request(
                image=image,
                text_prompt=text_prompt,
                box_threshold=self.settings.grounding_dino_high_recall_box_threshold,
                text_threshold=self.settings.grounding_dino_high_recall_text_threshold,
            )
        except DetectorRuntimeError:
            return previous_payload

    @staticmethod
    def _write_prompt_plan(plan: GroundingPromptPlan, path_value: str) -> None:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _to_detected_object(
    item: dict,
    target_profile: TargetProfile | None = None,
    runtime_zh: str | None = None,
) -> DetectedObject:
    label = str(item.get("label") or "object").lower().strip()
    bbox = item.get("bbox_2d") or [0.0, 0.0, 1.0, 1.0]
    attributes = list(item.get("attributes") or [])
    attributes.append("detected_by_grounding_dino")
    if item.get("mask_area_ratio") is not None:
        attributes.append("segmented_by_sam2")
        attributes.append(f"mask_area_ratio={float(item['mask_area_ratio']):.4f}")

    is_direct_target = (
        target_profile is not None
        and _matches_any_term(label, target_profile.direct_terms())
    )
    # `runtime_zh` 是 RAM++ 同一次 `_tag` 返回的中文配对。它优先于静态词典：
    # RAM 说是「容器」就不该被我们写死成「垃圾桶」（计划书 §6 阶段 A.3）。
    label_zh_value = resolve_label_zh(
        label,
        runtime_zh=runtime_zh,
        target_zh=(
            target_profile.canonical_name_zh
            if is_direct_target and target_profile is not None
            else None
        ),
    )
    return DetectedObject(
        label=label,
        label_zh=label_zh_value,
        category=(
            target_profile.target_type
            if is_direct_target and target_profile is not None
            else category_for_label(label)
        ),
        color=color_for_label(label),
        bbox_2d=(
            _clamp(float(bbox[0])),
            _clamp(float(bbox[1])),
            _clamp(float(bbox[2])),
            _clamp(float(bbox[3])),
        ),
        score=_clamp(float(item.get("score", 0.5))),
        text_score=(
            None
            if item.get("text_score") is None
            else _clamp(float(item["text_score"]))
        ),
        attributes=attributes,
        mask_area_ratio=(
            None
            if item.get("mask_area_ratio") is None
            else _clamp(float(item["mask_area_ratio"]))
        ),
        mask_rle=_mask_rle(item.get("mask_rle")),
        mask_size=_mask_size(item.get("mask_size")),
        source="grounding_dino_sam2",
        source_prompt_term=str(item.get("source_prompt_term") or label),
    )


def _mask_rle(value: Any) -> list[list[int]] | None:
    if not isinstance(value, list) or not value:
        return None
    runs: list[list[int]] = []
    for run in value:
        if isinstance(run, list) and len(run) == 3:
            runs.append([int(run[0]), int(run[1]), int(run[2])])
    return runs or None


def _mask_size(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        return (int(value[0]), int(value[1]))
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _matches_any_term(label: str, terms: list[str]) -> bool:
    normalized_label = " ".join(
        re.sub(r"[^a-z0-9一-鿿]+", " ", label.lower()).split()
    )
    for term in terms:
        normalized_term = " ".join(
            re.sub(r"[^a-z0-9一-鿿]+", " ", term.lower()).split()
        )
        if normalized_term and normalized_term in normalized_label:
            return True
    return False


def _prompt_to_terms(prompt: str) -> list[str]:
    return [item.strip() for item in re.split(r"\s*\.\s*", prompt) if item.strip()]


def _terms_to_prompt(terms: list[str]) -> str:
    cleaned = _dedupe_terms(terms)
    return " . ".join(cleaned) + (" ." if cleaned else "")


def _dedupe_terms(terms: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = " ".join(
            re.sub(r"[^a-z0-9 -]+", " ", str(term).lower()).split()
        ).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
