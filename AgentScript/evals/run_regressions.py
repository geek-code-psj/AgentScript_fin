"""Run AgentScript regression cases, with optional DeepEval assertions."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentscript.demo.legal_demo import find_divergence, run_demo

try:  # pragma: no cover - optional external package surface
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase
except Exception:  # pragma: no cover
    BaseMetric = object
    LLMTestCase = None


@dataclass(frozen=True, slots=True)
class RegressionResult:
    name: str
    passed: bool
    confidence: float | None
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class StructuredExpectationMetric(BaseMetric):  # type: ignore[misc]
    def __init__(self, *, rule_name: str, check) -> None:
        self.rule_name = rule_name
        self.check = check
        self.threshold = 1.0
        self.success = False
        self.score = 0.0
        self.reason = ""
        self.evaluation_model = "rules"

    def measure(self, test_case) -> float:  # type: ignore[override]
        passed, reason = self.check(test_case)
        self.success = passed
        self.score = 1.0 if passed else 0.0
        self.reason = reason
        return self.score

    async def a_measure(self, test_case) -> float:  # type: ignore[override]
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success


async def run_regressions() -> list[RegressionResult]:
    cases = json.loads((ROOT / "evals" / "regression_cases.json").read_text(encoding="utf-8"))
    results: list[RegressionResult] = []
    temp_dir = ROOT / "tests" / ".regression-artifacts" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    reference_trace = temp_dir / "reference.sqlite"
    reference = await run_demo(mode="happy", query="BNS theft appeal", trace_path=reference_trace)

    for case in cases:
        name = str(case["name"])
        mode = str(case["mode"])
        query = str(case["query"])
        expected = dict(case["expected"])
        trace_path = temp_dir / f"{name}.sqlite"

        if name == "bad_model_replay_masking":
            candidate = await run_demo(mode="bad-model", query=query, trace_path=trace_path)
            replay_trace = temp_dir / "replayed.sqlite"
            replayed = await run_demo(
                mode="bad-model",
                query=query,
                trace_path=replay_trace,
                replay_from=reference_trace,
            )
            divergence = find_divergence(reference_trace, trace_path)
            passed = (
                divergence is not None
                and divergence["step_id"] == expected["divergence_step_id"]
                and replayed["claim"] == reference["claim"]
            )
            details = {
                "candidate": candidate["claim"],
                "replayed": replayed["claim"],
                "divergence": divergence,
            }
            confidence = float(replayed["claim"]["confidence"])
        else:
            run = await run_demo(mode=mode, query=query, trace_path=trace_path)
            confidence = float(run["claim"]["confidence"])
            passed = _evaluate_expected(run, expected)
            details = {
                "claim": run["claim"],
                "state": run["state"],
                "expected": expected,
            }
            _maybe_run_deepeval(name=name, run=run, expected=expected)

        results.append(
            RegressionResult(
                name=name,
                passed=passed,
                confidence=confidence,
                details=details,
            )
        )

    return results


def _evaluate_expected(run: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Evaluate if a run matches expected outcomes.
    
    Supports validation of:
    - Confidence bounds (min/max)
    - Tool call counts (search_calls, fallback_calls)
    - Retry attempts and circuit breaker states
    - Replay fidelity and model config changes
    - Memory operations and semantic search
    - Error types and messages
    - OTel span attributes
    - PII redaction effectiveness
    """
    confidence = float(run.get("claim", {}).get("confidence", 0.0))
    state = dict(run.get("state", {}))

    # Confidence bounds
    if "min_confidence" in expected and confidence < float(expected["min_confidence"]):
        return False
    if "max_confidence" in expected and confidence > float(expected["max_confidence"]):
        return False
    
    # Tool call counts
    if "fallback_calls" in expected and int(state.get("fallback_calls", 0)) != int(expected["fallback_calls"]):
        return False
    if "search_calls" in expected and int(state.get("search_calls", 0)) != int(expected["search_calls"]):
        return False
    if "retry_attempts" in expected and int(state.get("retry_attempts", 0)) != int(expected["retry_attempts"]):
        return False
    
    # Circuit breaker state
    if "circuit_state" in expected and state.get("circuit_state") != expected["circuit_state"]:
        return False
    if "recovery_attempt" in expected and bool(state.get("recovery_attempt", False)) != bool(expected["recovery_attempt"]):
        return False
    
    # Replay and determinism
    if "replay_matches_reference" in expected and bool(run.get("replay_matches")) != bool(expected["replay_matches_reference"]):
        return False
    if "replay_matches_live" in expected and bool(run.get("replay_matches")) != bool(expected["replay_matches_live"]):
        return False
    if "output_hash_matches" in expected and str(run.get("output_hash", "")) != str(expected.get("expected_hash", run.get("output_hash", ""))):
        return False
    if "degraded" in expected and bool(run.get("degraded", False)) != bool(expected["degraded"]):
        return False
    
    # Error handling
    if "error" in expected:
        error_type = run.get("error_type")
        if error_type != expected["error"]:
            return False
        if "error_message_contains" in expected:
            error_msg = str(run.get("error_message", ""))
            if expected["error_message_contains"] not in error_msg:
                return False
    
    # Model config
    if "model_config" in expected:
        model_config = run.get("model_config", {})
        cfg_expected = expected["model_config"]
        if "model_id_recorded" in cfg_expected and not model_config.get("model_id"):
            return False
        if "temperature_recorded" in cfg_expected and model_config.get("temperature") is None:
            return False
        if "max_tokens_recorded" in cfg_expected and model_config.get("max_tokens") is None:
            return False
    
    # Replay with model config divergence
    if "model_config_differs" in expected and not run.get("model_config_changed"):
        return False
    
    # Memory operations
    if "memory_operations" in expected:
        mem_ops = run.get("memory_operations", {})
        mem_expected = expected["memory_operations"]
        if "semantic_search_called" in mem_expected and not mem_ops.get("semantic_search_called"):
            return False
        if "memory_hit_rate" in mem_expected:
            hit_rate = float(mem_ops.get("memory_hit_rate", 0.0))
            rate_bounds = mem_expected["memory_hit_rate"]
            if "min" in rate_bounds and hit_rate < rate_bounds["min"]:
                return False
            if "max" in rate_bounds and hit_rate > rate_bounds["max"]:
                return False
    
    # Memory state persistence
    if "memory_state" in expected:
        mem_state = run.get("memory_state", {})
        mem_expected = expected["memory_state"]
        if "session_id_persisted" in mem_expected and not mem_state.get("session_id_persisted"):
            return False
        if "context_maintained_across_steps" in mem_expected and not mem_state.get("context_maintained"):
            return False
    
    # PII redaction
    if "pii_redacted" in expected and not run.get("pii_redacted"):
        return False
    if "email_masked" in expected and "email" in run.get("pii_types_redacted", []):
        pass  # Expected and found
    elif "email_masked" in expected and expected["email_masked"]:
        return False
    if "api_key_masked" in expected and "api_key" not in run.get("pii_types_redacted", []):
        return False
    if "ssn_masked" in expected and "ssn" not in run.get("pii_types_redacted", []):
        return False
    
    # OTel observability
    if "otel_spans" in expected:
        otel = run.get("otel_spans", {})
        otel_expected = expected["otel_spans"]
        if "workflow_span_created" in otel_expected and not otel.get("workflow_span_created"):
            return False
        if "tool_call_spans_recorded" in otel_expected and not otel.get("tool_calls"):
            return False
        if "semantic_attributes" in otel_expected:
            attrs = otel.get("semantic_attributes", [])
            for required_attr in otel_expected["semantic_attributes"]:
                if required_attr not in attrs:
                    return False
    
    # LangSmith tracing
    if "langsmith_enabled" in expected and not run.get("langsmith_enabled"):
        return False
    if "trace_posted_to_langsmith" in expected and not run.get("trace_posted"):
        return False
    if "child_spans_recorded" in expected and not run.get("child_spans_count", 0) > 0:
        return False
    
    # Timeout handling
    if "timeout_triggered" in expected and not run.get("timeout_triggered"):
        return False
    if "fallback_invoked" in expected and not run.get("fallback_invoked"):
        return False
    
    # Concurrency and isolation
    if "concurrent_runs" in expected and int(state.get("concurrent_runs", 0)) < expected["concurrent_runs"]:
        return False
    if "isolation_maintained" in expected and not run.get("isolation_maintained"):
        return False
    if "no_cross_contamination" in expected and run.get("cross_contamination"):
        return False
    
    # Trace event ordering
    if "trace_events_ordered" in expected and not run.get("trace_events_ordered"):
        return False
    if "sequence_numbers_monotonic" in expected and not run.get("sequence_numbers_monotonic"):
        return False
    if "causality_preserved" in expected and not run.get("causality_preserved"):
        return False
    
    # Error recovery
    if "recovery_action" in expected and run.get("recovery_action") != expected["recovery_action"]:
        return False
    if "span_attribute_set" in expected:
        attrs = run.get("span_attributes", {})
        if expected["span_attribute_set"] not in attrs:
            return False
    
    return True


def _maybe_run_deepeval(*, name: str, run: dict[str, Any], expected: dict[str, Any]) -> None:
    if LLMTestCase is None:
        return

    test_case = LLMTestCase(
        input=f"Run regression for {name}",
        actual_output=json.dumps(run["claim"], sort_keys=True),
        expected_output=json.dumps(expected, sort_keys=True),
        context=[json.dumps(run["state"], sort_keys=True)],
    )
    metric = StructuredExpectationMetric(
        rule_name=name,
        check=lambda _: (
            _evaluate_expected(run, expected),
            "Structured regression expectations satisfied."
            if _evaluate_expected(run, expected)
            else "Structured regression expectations failed.",
        ),
    )
    metric.measure(test_case)
    if not metric.is_successful():
        raise AssertionError(metric.reason)


def main() -> int:
    results = asyncio.run(run_regressions())
    print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
