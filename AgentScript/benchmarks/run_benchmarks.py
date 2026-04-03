"""Benchmark runner for AgentScript Week 8 polish."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentscript.compiler.ir import lower_source
from agentscript.compiler.lexer import lex_source
from agentscript.compiler.parser import parse_source
from agentscript.demo.legal_demo import build_demo_registry
from agentscript.runtime import AsyncInterpreter, MemoryManager, compile_runtime_file


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    lexer_tokens_per_second: float
    parser_ms: float
    ir_lowering_ms: float
    runtime_ms: float
    memory_search_ms: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def run_benchmarks(*, iterations: int = 100) -> BenchmarkReport:
    source = (ROOT / "examples" / "legal_research.as").read_text(encoding="utf-8")
    bulk_source = "\n\n".join([source] * 60)
    token_count = len(lex_source(bulk_source))

    lexer_seconds = _time_many(lambda: lex_source(bulk_source), iterations=max(5, iterations // 5))
    parser_seconds = _time_many(lambda: parse_source(source), iterations=iterations)
    ir_seconds = _time_many(lambda: lower_source(source), iterations=iterations)

    runtime_seconds = _time_async_many(iterations=max(5, iterations // 5))
    memory_seconds = _time_memory_search(iterations=iterations)

    return BenchmarkReport(
        lexer_tokens_per_second=token_count / max(lexer_seconds / max(1, iterations // 5), 1e-9),
        parser_ms=(parser_seconds / iterations) * 1000.0,
        ir_lowering_ms=(ir_seconds / iterations) * 1000.0,
        runtime_ms=(runtime_seconds / max(5, iterations // 5)) * 1000.0,
        memory_search_ms=(memory_seconds / iterations) * 1000.0,
    )


def write_report(path: Path, report: BenchmarkReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def write_markdown(path: Path, report: BenchmarkReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "| Benchmark | Result |",
        "| --- | ---: |",
        f"| Lexer throughput | {report.lexer_tokens_per_second:,.0f} tokens/sec |",
        f"| Parser | {report.parser_ms:.3f} ms |",
        f"| IR lowering | {report.ir_lowering_ms:.3f} ms |",
        f"| Runtime | {report.runtime_ms:.3f} ms |",
        f"| Memory search | {report.memory_search_ms:.3f} ms |",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _time_many(fn, *, iterations: int) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        fn()
    return time.perf_counter() - started


def _time_async_many(*, iterations: int) -> float:
    import asyncio

    async def run_once() -> None:
        program = compile_runtime_file(ROOT / "examples" / "legal_research.as")
        registry, _ = build_demo_registry("happy")
        await AsyncInterpreter(program, tools=registry).run_workflow(
            "legal_brief",
            arguments={"query": "BNS theft appeal"},
        )

    started = time.perf_counter()
    for _ in range(iterations):
        asyncio.run(run_once())
    return time.perf_counter() - started


def _time_memory_search(*, iterations: int) -> float:
    memory = MemoryManager()
    for index in range(40):
        memory.write(
            f"note_{index}",
            f"BNS theft appeal note {index} discussing evidence and appellate review.",
        )

    started = time.perf_counter()
    for _ in range(iterations):
        memory.search("BNS theft appeal")
    return time.perf_counter() - started


def main() -> int:
    report = run_benchmarks()
    write_report(ROOT / "benchmarks" / "latest.json", report)
    write_markdown(ROOT / "benchmarks" / "latest.md", report)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
