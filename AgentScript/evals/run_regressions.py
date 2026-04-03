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
    confidence = float(run["claim"]["confidence"])
    state = dict(run["state"])

    if "min_confidence" in expected and confidence < float(expected["min_confidence"]):
        return False
    if "max_confidence" in expected and confidence > float(expected["max_confidence"]):
        return False
    if "fallback_calls" in expected and int(state["fallback_calls"]) != int(expected["fallback_calls"]):
        return False
    if "search_calls" in expected and int(state["search_calls"]) != int(expected["search_calls"]):
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
