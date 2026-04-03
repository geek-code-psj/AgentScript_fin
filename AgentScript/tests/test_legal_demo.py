from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agentscript.demo.legal_demo import find_divergence, run_demo
from agentscript.runtime.tracing import SQLiteTraceReplayer


@pytest.mark.asyncio
async def test_legal_demo_retry_mode_recovers_and_records_retry_event() -> None:
    trace_path = Path("tests") / f"legal-retry-{uuid4().hex}.sqlite"
    result = await run_demo(mode="retry", trace_path=trace_path)

    assert result["claim"]["confidence"] == pytest.approx(0.87)
    assert result["state"]["search_calls"] == 2

    replayer = SQLiteTraceReplayer(trace_path)
    replay = replayer.replay(result["run_id"])
    replayer.close()

    assert any(event.event_type == "retry_scheduled" for event in replay.events)


@pytest.mark.asyncio
async def test_legal_demo_outage_mode_uses_fallback_claim() -> None:
    result = await run_demo(mode="outage")

    assert result["claim"]["confidence"] == pytest.approx(0.61)
    assert result["state"]["summary_calls"] == 1
    assert result["state"]["fallback_calls"] == 1


@pytest.mark.asyncio
async def test_legal_demo_replay_masks_bad_model_divergence() -> None:
    reference_trace = Path("tests") / f"legal-reference-{uuid4().hex}.sqlite"
    bad_trace = Path("tests") / f"legal-bad-{uuid4().hex}.sqlite"
    replay_trace = Path("tests") / f"legal-replayed-{uuid4().hex}.sqlite"

    reference = await run_demo(mode="happy", trace_path=reference_trace)
    bad_model = await run_demo(mode="bad-model", trace_path=bad_trace)
    replayed = await run_demo(
        mode="bad-model",
        trace_path=replay_trace,
        replay_from=reference_trace,
    )

    divergence = find_divergence(reference_trace, bad_trace)

    assert divergence is not None
    assert divergence["step_id"] == "summarize_claim_0"
    assert bad_model["claim"] != reference["claim"]
    assert replayed["claim"] == reference["claim"]
    assert replayed["state"]["search_calls"] == 0
    assert replayed["state"]["summary_calls"] == 0
