from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks.run_benchmarks import run_benchmarks, write_markdown, write_report
from evals.run_regressions import run_regressions


def test_vscode_extension_manifest_and_grammar_exist() -> None:
    extension_root = Path("vscode") / "agentscript"
    package = json.loads((extension_root / "package.json").read_text(encoding="utf-8"))
    grammar = json.loads(
        (extension_root / "syntaxes" / "agentscript.tmLanguage.json").read_text(encoding="utf-8")
    )

    assert package["contributes"]["languages"][0]["id"] == "agentscript"
    assert ".as" in package["contributes"]["languages"][0]["extensions"]
    assert "repository" in grammar
    assert "keywords" in grammar["repository"]


def test_benchmark_runner_emits_positive_metrics() -> None:
    report = run_benchmarks(iterations=3)
    output_dir = Path("tests") / ".benchmark-artifacts" / uuid4().hex
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmarks.json"
    markdown_path = output_dir / "benchmarks.md"

    write_report(json_path, report)
    write_markdown(markdown_path, report)

    assert report.lexer_tokens_per_second > 0
    assert report.parser_ms > 0
    assert report.ir_lowering_ms > 0
    assert report.runtime_ms > 0
    assert report.memory_search_ms > 0
    assert json.loads(json_path.read_text(encoding="utf-8"))["parser_ms"] > 0
    assert "| Benchmark | Result |" in markdown_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_regression_suite_passes_all_cases() -> None:
    results = await run_regressions()

    assert results
    assert all(result.passed for result in results)
