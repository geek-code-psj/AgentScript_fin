"""Week 6 legal research demo for AgentScript."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Literal

from agentscript.runtime import (
    AsyncInterpreter,
    SQLiteTraceRecorder,
    SQLiteTraceReplayer,
    ToolRegistry,
    compile_runtime_file,
)


Mode = Literal["happy", "retry", "outage", "bad-model"]

ROOT = Path(__file__).resolve().parents[3]
LEGAL_SCRIPT_PATH = ROOT / "examples" / "legal_research.as"
LEGAL_DATA_PATH = ROOT / "examples" / "data" / "indian_legal_corpus.json"


class DemoServiceError(RuntimeError):
    """Synthetic upstream outage for the legal research demo."""

    status_code = 503


@dataclass(slots=True)
class DemoState:
    mode: str
    search_calls: int = 0
    filter_calls: int = 0
    summary_calls: int = 0
    fallback_calls: int = 0


def load_corpus() -> list[dict[str, object]]:
    """Load the local legal corpus used by the demo tools."""

    return json.loads(LEGAL_DATA_PATH.read_text(encoding="utf-8"))


def build_demo_registry(
    mode: Mode = "happy",
    *,
    state: DemoState | None = None,
) -> tuple[ToolRegistry, DemoState]:
    """Build a local tool registry that simulates legal research behavior."""

    demo_state = state or DemoState(mode=mode)
    corpus = load_corpus()
    registry = ToolRegistry()

    @registry.tool()
    def search_indian_kanoon(query: str) -> list[dict[str, str]]:
        demo_state.search_calls += 1
        if mode == "retry" and demo_state.search_calls == 1:
            raise DemoServiceError("503 upstream timeout from Indian legal search")
        if mode == "outage":
            raise DemoServiceError("search service is offline")
        return [_to_citation(entry) for entry in _rank_entries(corpus, query)[:3]]

    @registry.tool()
    def filter_relevance(
        citations: list[dict[str, str]],
        query: str,
    ) -> list[dict[str, str]]:
        demo_state.filter_calls += 1
        ranked = [citation for citation in citations if _matches(citation, query)]
        return ranked or citations[:1]

    @registry.tool()
    def summarize_claim(
        citations: list[dict[str, str]],
        query: str,
    ) -> dict[str, object]:
        demo_state.summary_calls += 1
        if mode == "bad-model":
            return {
                "text": f"Bad model divergence: '{query}' has no useful theft appeal authority.",
                "confidence": 0.12,
            }
        lead = citations[0] if citations else _empty_citation()
        cached = lead["source"].startswith("Cached /")
        return {
            "text": (
                f"For '{query}', the strongest authority is {lead['source']} discussing "
                f"{lead['span']}."
            ),
            "confidence": 0.61 if cached else 0.87,
        }

    @registry.tool()
    def recall_cached(query: str) -> list[dict[str, str]]:
        demo_state.fallback_calls += 1
        citations = [_to_citation(entry) for entry in _rank_entries(corpus, query)[:2]]
        return [
            {
                "source": f"Cached / {citation['source']}",
                "span": citation["span"],
                "url": citation["url"],
            }
            for citation in citations
        ]

    return registry, demo_state


def _to_citation(entry: dict[str, object]) -> dict[str, str]:
    return {
        "source": str(entry["source"]),
        "span": str(entry["span"]),
        "url": str(entry["url"]),
    }


def _empty_citation() -> dict[str, str]:
    return {
        "source": "Local cache",
        "span": "No authority available",
        "url": "https://example.invalid/cache",
    }


def _rank_entries(
    corpus: list[dict[str, object]],
    query: str,
) -> list[dict[str, object]]:
    query_tokens = set(_tokenize(query))
    ranked = sorted(
        corpus,
        key=lambda entry: (
            -_score_entry(entry, query_tokens),
            str(entry["source"]),
        ),
    )
    return ranked


def _score_entry(entry: dict[str, object], query_tokens: set[str]) -> int:
    haystacks = {
        *_tokenize(str(entry["summary"])),
        *_tokenize(str(entry["span"])),
        *[str(tag).lower() for tag in entry.get("tags", [])],
    }
    return len(query_tokens.intersection(haystacks))


def _matches(citation: dict[str, str], query: str) -> bool:
    query_tokens = set(_tokenize(query))
    citation_tokens = {
        *_tokenize(citation["source"]),
        *_tokenize(citation["span"]),
        *_tokenize(citation["url"]),
    }
    return bool(query_tokens.intersection(citation_tokens))


def _tokenize(text: str) -> list[str]:
    return [token for token in text.lower().replace("/", " ").replace("-", " ").split() if token]


async def run_demo(
    *,
    mode: Mode = "happy",
    query: str = "BNS theft appeal",
    trace_path: Path | None = None,
    replay_from: Path | None = None,
    replay_run_id: str | None = None,
) -> dict[str, object]:
    """Run the Week 6 legal research demo and return structured output."""

    program = compile_runtime_file(LEGAL_SCRIPT_PATH)
    registry, state = build_demo_registry(mode)
    recorder = SQLiteTraceRecorder(trace_path) if trace_path is not None else None
    replay_replayer: SQLiteTraceReplayer | None = None
    replay_source = None
    if replay_from is not None:
        replay_replayer = SQLiteTraceReplayer(replay_from)
        replay_source = replay_replayer.load_source(replay_run_id)

    try:
        interpreter = AsyncInterpreter(
            program,
            tools=registry,
            trace_recorder=recorder,
            replay_source=replay_source,
        )
        claim = await interpreter.run_workflow("legal_brief", arguments={"query": query})
        run_id = interpreter.last_run_id
    finally:
        if replay_replayer is not None:
            replay_replayer.close()
        if recorder is not None:
            recorder.close()

    return {
        "claim": claim,
        "run_id": run_id,
        "state": asdict(state),
        "trace_path": None if trace_path is None else str(trace_path),
        "replayed_from": None if replay_from is None else str(replay_from),
    }


def find_divergence(
    reference_trace: Path,
    candidate_trace: Path,
    *,
    reference_run_id: str | None = None,
    candidate_run_id: str | None = None,
) -> dict[str, object] | None:
    """Compare two traces and return the first divergent tool step, if any."""

    reference_replayer = SQLiteTraceReplayer(reference_trace)
    candidate_replayer = SQLiteTraceReplayer(candidate_trace)
    try:
        reference = reference_replayer.load_source(reference_run_id)
        candidate = candidate_replayer.load_source(candidate_run_id)

        for step_id, reference_result in reference.tool_results.items():
            candidate_result = candidate.tool_results.get(step_id)
            if candidate_result is None:
                return {
                    "step_id": step_id,
                    "reason": "missing-step",
                    "reference": reference_result.payload,
                    "candidate": None,
                }
            if (
                reference_result.ok != candidate_result.ok
                or reference_result.payload != candidate_result.payload
                or reference_result.source != candidate_result.source
            ):
                return {
                    "step_id": step_id,
                    "reason": "payload-mismatch",
                    "reference": reference_result.payload,
                    "candidate": candidate_result.payload,
                }
        return None
    finally:
        reference_replayer.close()
        candidate_replayer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AgentScript legal research demo.")
    parser.add_argument(
        "--mode",
        choices=["happy", "retry", "outage", "bad-model"],
        default="happy",
        help="Demo mode to execute.",
    )
    parser.add_argument(
        "--query",
        default="BNS theft appeal",
        help="Legal query to run through the demo agent.",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=None,
        help="Optional SQLite trace path for recording the run.",
    )
    parser.add_argument(
        "--replay-from",
        type=Path,
        default=None,
        help="Optional SQLite trace file to replay instead of calling live tools.",
    )
    parser.add_argument(
        "--replay-run-id",
        default=None,
        help="Optional run id inside the replay trace.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_demo(
            mode=args.mode,
            query=args.query,
            trace_path=args.trace,
            replay_from=args.replay_from,
            replay_run_id=args.replay_run_id,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
