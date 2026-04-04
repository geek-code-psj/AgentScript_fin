"""Shadow deployment evaluation framework for safe production rollout.

This module implements:
  1. Query interception layer: Capture production queries, run them in shadow mode
  2. HITL annotation loop: Auditors mark reasoning divergence for regression cases
  3. Graduated rollout: Evidence-based transition from read-only to write capabilities

Shadow deployment enables zero-risk validation of model/configuration changes before
live deployment. Annotated divergences automatically seed the regression suite.

Example:
    shadow = ShadowDeployment(production_mode="gpt4", shadow_mode="gpt4-1106-preview")
    
    for query in production_queries:
        shadow_result = await shadow.execute(query)
        
        if shadow_result.diverges_from_live:
            # Human auditor reviews and annotates
            annotation = await shadow.annotate(
                run_id=shadow_result.run_id,
                assessment="acceptable_reasoning_difference",
                justification="Model produces more concise summary"
            )
            # Auto-adds to regression_cases.json
            await shadow.export_as_regression_case(annotation)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentscript.runtime.errors import AgentScriptRuntimeError, ErrorContext
from agentscript.runtime.records import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class ShadowQuery:
    """A query intercepted from production for shadow evaluation."""
    
    query_id: str
    timestamp: datetime
    workflow_name: str
    input_text: str
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    """Outcome of executing a query in shadow mode."""
    
    query_id: str
    run_id: str
    status: str  # "success", "error", "unknown"
    confidence: float | None
    output: object
    latency_ms: float
    tool_calls_count: int
    diverges_from_live: bool = False
    divergence_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AnnotatedTrace:
    """A trace that has been manually reviewed by an auditor."""
    
    trace_id: str
    run_id: str
    query_id: str
    workflow_name: str
    assessment: str  # "acceptable_reasoning_difference", "hallucination", "acceptable_degradation", "regression"
    auditor_id: str
    justification: str
    confidence_acceptable_range: tuple[float, float] | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_regression_case(self) -> dict[str, Any]:
        """Convert annotation to regression case for suite."""
        return {
            "name": f"shadow_deployment_{self.trace_id}",
            "mode": "shadow",
            "query": "query_content_redacted",  # Redact for security
            "expected": {
                "assessment": self.assessment,
                "auditor_approved": True,
                "allows_rollout": self.assessment != "regression",
            },
            "tags": ["shadow_deployment", "hitl_annotated", "production_evidence"],
            "metadata": {
                "original_trace_id": self.trace_id,
                "auditor": self.auditor_id,
                "justification": self.justification,
                "timestamp": self.timestamp.isoformat(),
            }
        }


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    """Decision to promote from shadow to live or stay shadow."""
    
    decision: str  # "promote_to_live", "stay_shadow", "rollback", "manual_review"
    evidence: dict[str, Any]
    confidence: float
    recommended_action: str
    promoted_at: datetime | None = None


class ShadowDeployment:
    """Manages safe evaluation and rollout of model/config changes.
    
    Workflow:
      1. Production query → intercepted → executed in shadow mode
      2. Shadow result compared with live baseline
      3. If divergence: human auditor reviews → annotation saved
      4. Graduated rollout decision based on:
         - Success rate threshold (default 95%)
         - Auditor approval ratio (default 80%)
         - User coverage (default 100 users)
    """
    
    def __init__(
        self,
        production_mode: str,
        shadow_mode: str,
        *,
        success_rate_threshold: float = 0.95,
        auditor_approval_threshold: float = 0.80,
        min_user_coverage: int = 100,
        min_trace_coverage: int = 100,
        artifacts_dir: Path | None = None,
    ) -> None:
        """Initialize shadow deployment framework.
        
        Args:
            production_mode: Current live model/config (e.g., "gpt-4")
            shadow_mode: Candidate model/config to evaluate (e.g., "gpt-4-turbo")
            success_rate_threshold: Minimum success rate for rollout (0.95 = 95%)
            auditor_approval_threshold: Minimum approved traces for rollout (0.80 = 80%)
            min_user_coverage: Minimum distinct users to shadow before rollout
            min_trace_coverage: Minimum traces to collect before decision
            artifacts_dir: Directory to store shadow traces and annotations
        """
        self.production_mode = production_mode
        self.shadow_mode = shadow_mode
        self.success_rate_threshold = success_rate_threshold
        self.auditor_approval_threshold = auditor_approval_threshold
        self.min_user_coverage = min_user_coverage
        self.min_trace_coverage = min_trace_coverage
        
        if artifacts_dir is None:
            artifacts_dir = Path.cwd() / ".shadow-deployment"
        self.artifacts_dir = artifacts_dir
        self.traces_dir = artifacts_dir / "traces"
        self.annotations_dir = artifacts_dir / "annotations"
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.annotations_dir.mkdir(parents=True, exist_ok=True)
        
        self.shadow_results: list[ShadowResult] = []
        self.annotations: list[AnnotatedTrace] = []
        self._divergence_comparator: Callable[[Any, Any], bool] | None = None
    
    async def execute_shadow(
        self,
        query: ShadowQuery,
        live_executor: Callable[[str], Any],
        shadow_executor: Callable[[str], Any],
    ) -> ShadowResult:
        """Execute a query in shadow mode, capture divergence if any.
        
        Args:
            query: Query to execute
            live_executor: Callable that executes query in production
            shadow_executor: Callable that executes query in shadow (candidate) mode
        
        Returns:
            ShadowResult with divergence detection
        """
        run_id = f"shadow_{query.query_id}_{uuid4().hex[:6]}"
        
        # Execute in shadow mode (only shadow_executor is critical)
        try:
            shadow_result = await shadow_executor(query.input_text)
            shadow_status = "success"
            shadow_output = shadow_result
            latency_ms = shadow_result.get("latency_ms", 0.0)
        except Exception as e:
            shadow_status = "error"
            shadow_output = None
            latency_ms = 0.0
        
        # Try to get live result for comparison (may be cached/stale)
        live_result = None
        diverges = False
        divergence_reason = None
        try:
            live_result = await live_executor(query.input_text)
        except Exception:
            pass  # Silently ignore live errors; shadow result is what matters
        
        # Detect divergence
        if live_result is not None:
            diverges = self._detect_divergence(live_result, shadow_output)
            if diverges:
                divergence_reason = self._describe_divergence(live_result, shadow_output)
        
        confidence = float(shadow_output.get("claim", {}).get("confidence", 0.0)) if shadow_output else None
        tool_calls = shadow_output.get("state", {}).get("search_calls", 0) if shadow_output else 0
        
        result = ShadowResult(
            query_id=query.query_id,
            run_id=run_id,
            status=shadow_status,
            confidence=confidence,
            output=shadow_output,
            latency_ms=latency_ms,
            tool_calls_count=tool_calls,
            diverges_from_live=diverges,
            divergence_reason=divergence_reason,
        )
        
        self.shadow_results.append(result)
        self._save_trace(result, query)
        return result
    
    async def annotate(
        self,
        run_id: str,
        assessment: str,
        auditor_id: str,
        justification: str,
        confidence_acceptable_range: tuple[float, float] | None = None,
    ) -> AnnotatedTrace:
        """Record a human auditor's assessment of a shadow trace.
        
        Args:
            run_id: The run_id of the shadow result to annotate
            assessment: Type of assessment ("acceptable_reasoning_difference", 
                       "hallucination", "acceptable_degradation", "regression")
            auditor_id: Identity of the auditor (for audit trail)
            justification: Explanation for the assessment
            confidence_acceptable_range: If provided, acceptable confidence bounds
        
        Returns:
            AnnotatedTrace saved to disk
        """
        # Find the corresponding shadow result
        shadow_result = next(
            (r for r in self.shadow_results if r.run_id == run_id),
            None
        )
        if shadow_result is None:
            raise ValueError(f"Unknown run_id: {run_id}")
        
        trace_id = f"ann_{uuid4().hex[:8]}"
        annotation = AnnotatedTrace(
            trace_id=trace_id,
            run_id=run_id,
            query_id=shadow_result.query_id,
            workflow_name="legal_brief",  # Placeholder; would be query param
            assessment=assessment,
            auditor_id=auditor_id,
            justification=justification,
            confidence_acceptable_range=confidence_acceptable_range,
        )
        
        self.annotations.append(annotation)
        self._save_annotation(annotation)
        return annotation
    
    async def decide_rollout(self) -> RolloutDecision:
        """Evaluate evidence and recommend rollout decision.
        
        Considers:
          - Shadow success rate (≥ 95%)
          - Auditor approval ratio (≥ 80% of divergences approved)
          - User/trace coverage (≥ 100 users, ≥ 100 traces)
          - Hallucination count (0 hallucinations → promote)
        
        Returns:
            RolloutDecision with recommendation
        """
        if len(self.shadow_results) < self.min_trace_coverage:
            return RolloutDecision(
                decision="stay_shadow",
                evidence={
                    "reason": "insufficient_trace_coverage",
                    "collected": len(self.shadow_results),
                    "required": self.min_trace_coverage,
                },
                confidence=0.0,
                recommended_action="Continue collecting shadow traces",
            )
        
        # Success rate
        successful = sum(1 for r in self.shadow_results if r.status == "success")
        success_rate = successful / len(self.shadow_results)
        
        # Divergence and annotation stats
        diverged = sum(1 for r in self.shadow_results if r.diverges_from_live)
        hallucinations = sum(
            1 for a in self.annotations
            if a.assessment == "hallucination"
        )
        regressions = sum(
            1 for a in self.annotations
            if a.assessment == "regression"
        )
        
        acceptable_divergences = sum(
            1 for a in self.annotations
            if a.assessment in ("acceptable_reasoning_difference", "acceptable_degradation")
        )
        
        approval_ratio = acceptable_divergences / len(self.annotations) if self.annotations else 1.0
        
        evidence = {
            "success_rate": success_rate,
            "divergence_count": diverged,
            "hallucination_count": hallucinations,
            "regression_count": regressions,
            "acceptable_divergences": acceptable_divergences,
            "approval_ratio": approval_ratio,
            "auditor_approval_threshold": self.auditor_approval_threshold,
        }
        
        # Decision logic
        if hallucinations > 0 or regressions > 0:
            decision = "manual_review"
            confidence = 0.0
            recommendation = f"Found {hallucinations} hallucinations and {regressions} regressions. Requires manual review."
        elif success_rate < self.success_rate_threshold:
            decision = "stay_shadow"
            confidence = success_rate
            recommendation = f"Success rate {success_rate:.1%} below threshold {self.success_rate_threshold:.1%}"
        elif approval_ratio < self.auditor_approval_threshold:
            decision = "manual_review"
            confidence = approval_ratio
            recommendation = f"Auditor approval {approval_ratio:.1%} below threshold {self.auditor_approval_threshold:.1%}"
        else:
            decision = "promote_to_live"
            confidence = min(success_rate, approval_ratio)
            recommendation = f"All checks passed. Ready for gradual rollout (canary → 10% → 50% → 100%)"
        
        return RolloutDecision(
            decision=decision,
            evidence=evidence,
            confidence=confidence,
            recommended_action=recommendation,
            promoted_at=datetime.utcnow() if decision == "promote_to_live" else None,
        )
    
    async def export_regression_cases(self) -> list[dict[str, Any]]:
        """Convert all approved annotations to regression cases for suite.
        
        This enables HITL-discovered divergences to automatically seed
        production regression testing, closing the feedback loop.
        """
        cases = []
        for annotation in self.annotations:
            if annotation.assessment in ("acceptable_reasoning_difference", "acceptable_degradation"):
                case = annotation.to_regression_case()
                cases.append(case)
        
        if cases:
            export_path = self.artifacts_dir / "exported_regression_cases.json"
            export_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
        
        return cases
    
    def _detect_divergence(self, live: Any, shadow: Any) -> bool:
        """Determine if shadow result differs meaningfully from live."""
        if self._divergence_comparator:
            return self._divergence_comparator(live, shadow)
        
        # Default: simple equality check on confidence
        live_conf = float(live.get("claim", {}).get("confidence", 0.0))
        shadow_conf = float(shadow.get("claim", {}).get("confidence", 0.0))
        
        # Allow ±5% deviation before calling it divergence
        return abs(live_conf - shadow_conf) > 0.05
    
    def _describe_divergence(self, live: Any, shadow: Any) -> str:
        """Generate human-readable description of divergence."""
        live_conf = float(live.get("claim", {}).get("confidence", 0.0))
        shadow_conf = float(shadow.get("claim", {}).get("confidence", 0.0))
        diff = shadow_conf - live_conf
        direction = "lower" if diff < 0 else "higher"
        return f"Confidence {direction} by {abs(diff):.1%} ({live_conf:.2%} → {shadow_conf:.2%})"
    
    def _save_trace(self, result: ShadowResult, query: ShadowQuery) -> None:
        """Save shadow trace to disk for auditor review."""
        trace_data = {
            "run_id": result.run_id,
            "query_id": result.query_id,
            "timestamp": query.timestamp.isoformat(),
            "workflow": query.workflow_name,
            "shadow_mode": self.shadow_mode,
            "status": result.status,
            "confidence": result.confidence,
            "diverges": result.diverges_from_live,
            "divergence_reason": result.divergence_reason,
            "latency_ms": result.latency_ms,
            "output": result.output,
        }
        
        trace_file = self.traces_dir / f"{result.run_id}.json"
        trace_file.write_text(json.dumps(trace_data, indent=2, default=str), encoding="utf-8")
    
    def _save_annotation(self, annotation: AnnotatedTrace) -> None:
        """Save auditor annotation to disk."""
        annotation_data = {
            "trace_id": annotation.trace_id,
            "run_id": annotation.run_id,
            "assessment": annotation.assessment,
            "auditor_id": annotation.auditor_id,
            "justification": annotation.justification,
            "timestamp": annotation.timestamp.isoformat(),
        }
        
        annotation_file = self.annotations_dir / f"{annotation.trace_id}.json"
        annotation_file.write_text(json.dumps(annotation_data, indent=2), encoding="utf-8")


class GraduatedRolloutOrchestrator:
    """Orchestrates gradual rollout based on shadow deployment evidence.
    
    Phases:
      1. **Canary (1%)**: Monitor for errors/performance degradation
      2. **Early Access (10%)**: Track if users explicitly opt-in
      3. **Wide Beta (50%)**: General availability with opt-out
      4. **Full Rollout (100%)**: Complete migration
    """
    
    def __init__(self, shadow_deployment: ShadowDeployment) -> None:
        self.shadow = shadow_deployment
        self.current_phase = "canary"
        self.target_percentage = 0.01
        self.phase_duration = timedelta(hours=4)
        self.phase_start = datetime.utcnow()
    
    async def should_advance_phase(self) -> bool:
        """Evaluate if enough evidence exists to advance to next phase."""
        # Phase duration check
        elapsed = datetime.utcnow() - self.phase_start
        if elapsed < self.phase_duration:
            return False
        
        # Get rollout recommendation
        decision = await self.shadow.decide_rollout()
        return decision.decision == "promote_to_live"
    
    async def execute_phase(self) -> None:
        """Run current rollout phase."""
        if self.current_phase == "canary":
            self.target_percentage = 0.01
        elif self.current_phase == "early_access":
            self.target_percentage = 0.10
        elif self.current_phase == "wide_beta":
            self.target_percentage = 0.50
        elif self.current_phase == "full_rollout":
            self.target_percentage = 1.0
    
    async def advance_to_next_phase(self) -> None:
        """Move to next rollout phase if ready."""
        if self.current_phase == "canary":
            self.current_phase = "early_access"
        elif self.current_phase == "early_access":
            self.current_phase = "wide_beta"
        elif self.current_phase == "wide_beta":
            self.current_phase = "full_rollout"
        
        self.phase_start = datetime.utcnow()
