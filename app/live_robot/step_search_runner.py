"""Framework-level short-step search runner for the Go2-W.

The runner drives the fail-closed ``SearchStateMachine`` and the pure
``step_planner`` helpers. All hardware access is injected as callables, so the
module stays ROS-independent and unit-testable; the real Go2-W executor wires
these callables to the LLM quick worker, the LLM verify worker and the
``/go2w/motion`` action server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.live_robot.search_state_machine import (
    SearchMode,
    SearchState,
    SearchStateMachine,
    SensorSnapshot,
    VisualEvidence,
)
from app.live_robot.step_planner import (
    PlanKind,
    StepPlan,
    plan_approach_step,
    plan_scan_step,
    verify_rejection_step,
)


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    bbox: tuple[float, float, float, float]

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def area_ratio(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, (x2 - x1) * (y2 - y1))


@dataclass(frozen=True)
class VerificationResult:
    object_name_zh: str
    is_target: bool
    confidence: float
    reason_zh: str = ""


@dataclass(frozen=True)
class StepSearchConfig:
    target: str
    max_seconds: float = 420.0
    max_radius_m: float = 1.0
    score_min: float = 0.45
    align_threshold: float = 0.08
    align_yaw_max_deg: float = 25.0
    reach_area_ratio: float = 0.15
    scan_turn_deg: float = 30.0
    scan_span: int = 3
    stop_settle_seconds: float = 0.8
    forward_estimate_m: float = 0.15
    verify_rejection_turn_deg: float = 15.0


class StepSearchRunner:
    def __init__(
        self,
        config: StepSearchConfig,
        *,
        detect: Callable[[], list[Detection]],
        verify: Callable[[tuple[float, float, float, float]],
                         VerificationResult],
        execute_step: Callable[[str], tuple[bool, str]],
        snapshot: Callable[[], SensorSnapshot],
        odometry: Callable[[], tuple[float, float, float]],
    ) -> None:
        self.config = config
        self._detect = detect
        self._verify = verify
        self._execute_step = execute_step
        self._snapshot = snapshot
        self._odometry = odometry
        self.events: list[dict[str, Any]] = []

    def _event(self, name: str, **details: Any) -> None:
        self.events.append(
            {"event": name, "host_s": round(time.monotonic(), 6), **details}
        )

    def _best(self, detections: list[Detection]) -> Detection | None:
        return max(
            (item for item in detections
             if item.score >= self.config.score_min),
            key=lambda item: item.score,
            default=None,
        )

    def _execute_planned(self, machine: SearchStateMachine,
                         plan: StepPlan) -> tuple[bool, str]:
        machine.plan_step(plan.step)
        machine.motion_started(plan.step)
        self._event("motion_start", step=plan.step,
                    description=plan.description_zh, phase=plan.phase)
        ok, reason = self._execute_step(plan.step)
        machine.motion_completed(plan.step, ok)
        self._event("motion_result", step=plan.step, ok=ok, reason=reason)
        return ok, reason

    def _enter_plan_step(self, machine: SearchStateMachine,
                         detected: bool, evidence_ok: bool = False) -> None:
        """Drive the state machine from DETECT_TARGET to PLAN_STEP."""
        machine.detection(detected)
        if detected:
            machine.verify(
                VisualEvidence(
                    bbox=True,
                    mask=True,
                    crop_verify=evidence_ok,
                    track_vote=True,
                    evidence_gate=True,
                    source="llm_quick",
                    require_mask=False,
                    require_track_vote=False,
                )
            )
        machine.next_view_unavailable()
        machine.safety_checked(True)

    def run(self) -> dict[str, Any]:
        machine = SearchStateMachine(
            mode=SearchMode.STEP_SEARCH,
            motion_allowed=True,
            stop_settle_seconds=self.config.stop_settle_seconds,
        )
        machine.start()
        origin = self._odometry()
        started = time.monotonic()
        scan_index = 0
        index = 0
        status = "finished"
        finish_reason = ""
        steps_executed = 0

        self._event("step_search_start", target=self.config.target,
                    max_radius_m=self.config.max_radius_m,
                    max_seconds=self.config.max_seconds)
        while time.monotonic() - started < self.config.max_seconds:
            snapshot = self._snapshot()
            machine.sensors(snapshot)
            if machine.state == SearchState.WAIT_FOR_SENSORS:
                status = "sensor_gate_closed"
                finish_reason = "camera/lidar not fresh or robot not stationary"
                self._event("sensor_gate_closed")
                break
            machine.observation_elapsed(self.config.stop_settle_seconds)
            machine.scene_understood()

            current = self._odometry()
            distance = (
                (current[0] - origin[0]) ** 2
                + (current[1] - origin[1]) ** 2
            ) ** 0.5
            if (self.config.max_radius_m > 0.0
                    and distance > self.config.max_radius_m):
                status = "range_limit"
                finish_reason = (
                    f"radius {distance:.2f}m > "
                    f"{self.config.max_radius_m:.1f}m"
                )
                self._event("range_limit", distance_m=round(distance, 3))
                break

            try:
                detections = self._detect()
            except Exception as exc:
                self._event("detection_error", error=str(exc))
                if "stale" in str(exc).lower():
                    status = "camera_stale"
                    finish_reason = str(exc)
                    self._event("abort", reason=finish_reason)
                    break
                plan = plan_scan_step(
                    scan_index,
                    scan_turn_deg=self.config.scan_turn_deg,
                    scan_span=self.config.scan_span,
                    distance_m=distance,
                    max_radius_m=self.config.max_radius_m,
                    forward_estimate_m=self.config.forward_estimate_m,
                )
                scan_index += 1
                self._enter_plan_step(machine, detected=False)
                ok, reason = self._execute_planned(machine, plan)
                if not ok:
                    status = "motion_failed"
                    finish_reason = reason
                    break
                steps_executed += 1
                index += 1
                continue

            best = self._best(detections)
            if best is None:
                plan = plan_scan_step(
                    scan_index,
                    scan_turn_deg=self.config.scan_turn_deg,
                    scan_span=self.config.scan_span,
                    distance_m=distance,
                    max_radius_m=self.config.max_radius_m,
                    forward_estimate_m=self.config.forward_estimate_m,
                )
                scan_index += 1
                self._event("target_not_found", objects=len(detections))
                self._enter_plan_step(machine, detected=False)
                ok, reason = self._execute_planned(machine, plan)
                if not ok:
                    status = "motion_failed"
                    finish_reason = reason
                    break
                steps_executed += 1
                index += 1
                continue

            self._event("target_found", label=best.label,
                        score=round(best.score, 3),
                        center_x=round(best.center_x, 3),
                        area_ratio=round(best.area_ratio, 4),
                        distance_m=round(distance, 3))
            plan = plan_approach_step(
                center_x=best.center_x,
                area_ratio=best.area_ratio,
                distance_m=distance,
                align_threshold=self.config.align_threshold,
                align_yaw_max_deg=self.config.align_yaw_max_deg,
                reach_area_ratio=self.config.reach_area_ratio,
                max_radius_m=self.config.max_radius_m,
                forward_estimate_m=self.config.forward_estimate_m,
            )
            if plan.kind == PlanKind.ABORT_RADIUS:
                status = "range_limit"
                finish_reason = plan.description_zh
                self._event("range_limit", phase="APPROACH")
                break
            if plan.kind == PlanKind.VERIFY:
                verification = self._verify(best.bbox)
                self._event("target_verification",
                            label=best.label,
                            area_ratio=round(best.area_ratio, 4),
                            verification={
                                "object_name_zh": verification.object_name_zh,
                                "is_target": verification.is_target,
                                "confidence": round(
                                    verification.confidence, 3
                                ),
                                "reason_zh": verification.reason_zh,
                            })
                machine.detection(True)
                machine.verify(
                    VisualEvidence(
                        bbox=True,
                        mask=True,
                        crop_verify=verification.is_target,
                        track_vote=True,
                        evidence_gate=True,
                        source="llm_quick",
                        require_mask=False,
                        require_track_vote=False,
                    )
                )
                if machine.state == SearchState.LOCALIZE_TARGET:
                    machine.localization(False)
                    machine.finish_confirmed()
                    status = "target_reached"
                    finish_reason = (
                        f"{verification.object_name_zh} "
                        f"({verification.reason_zh})"
                    )
                    self._event("target_reached",
                                verification=verification.object_name_zh)
                    break
                plan = verify_rejection_step(
                    self.config.verify_rejection_turn_deg
                )
                self._event("verification_rejected",
                            object_name=verification.object_name_zh,
                            reason=verification.reason_zh)
                machine.next_view_unavailable()
                machine.safety_checked(True)
                ok, reason = self._execute_planned(machine, plan)
                if not ok:
                    status = "motion_failed"
                    finish_reason = reason
                    break
                steps_executed += 1
                index += 1
                continue

            self._event("approach_step", step=plan.step,
                        phase=plan.phase)
            self._enter_plan_step(machine, detected=True)
            ok, reason = self._execute_planned(machine, plan)
            if not ok:
                status = "motion_failed"
                finish_reason = reason
                break
            steps_executed += 1
            index += 1
        else:
            status = "time_limit"
            finish_reason = f"max_seconds={self.config.max_seconds:.0f}s"
            self._event("time_limit")

        self._event("step_search_finish", status=status,
                    reason=finish_reason, steps_executed=steps_executed)
        return {
            "status": status,
            "finish_reason": finish_reason,
            "steps_executed": steps_executed,
            "events": self.events,
            "state_machine_trace": machine.trace,
            "final_state": machine.state.value,
        }
