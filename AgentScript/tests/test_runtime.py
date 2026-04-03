from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agentscript.runtime import (
    AsyncInterpreter,
    Environment,
    FunctionalClock,
    ToolRegistry,
    compile_runtime_program,
)
from agentscript.runtime.errors import ToolInvocationError
from agentscript.runtime.memory import MemoryEntry, MemoryManager
from agentscript.runtime.tracing import SQLiteTraceRecorder, SQLiteTraceReplayer


class ServiceUnavailableError(RuntimeError):
    status_code = 503


@pytest.mark.asyncio
async def test_runtime_executes_sync_and_async_tools() -> None:
    source = """
    tool search(query: string) -> string
    tool decorate(text: string) -> string

    workflow main(query: string) -> string {
      let result: string = search(query)
      return decorate(result)
    }
    """
    registry = ToolRegistry()

    @registry.tool()
    def search(query: str) -> str:
        return f"law:{query}"

    @registry.tool()
    async def decorate(text: str) -> str:
        return f"[{text}]"

    program = compile_runtime_program(source)
    result = await AsyncInterpreter(program, tools=registry).run_workflow(
        "main",
        arguments={"query": "bns"},
    )
    assert result == "[law:bns]"


@pytest.mark.asyncio
async def test_runtime_retries_transient_tool_failures() -> None:
    source = """
    agent resilient {
      retry(3, backoff=exponential, base_delay_seconds=0.01, max_delay_seconds=0.02)
    }

    tool flaky(query: string) -> string
    workflow main(query: string) -> string {
      return flaky(query)
    }
    """
    attempts: list[str] = []
    delays: list[float] = []
    registry = ToolRegistry()

    @registry.tool()
    def flaky(query: str) -> str:
        attempts.append(query)
        if len(attempts) < 3:
            raise ServiceUnavailableError("transient")
        return f"ok:{query}"

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    program = compile_runtime_program(source)
    interpreter = AsyncInterpreter(program, tools=registry, sleep=fake_sleep)
    result = await interpreter.run_workflow("main", arguments={"query": "case"})

    assert result == "ok:case"
    assert len(attempts) == 3
    assert delays == [0.01, 0.02]


@pytest.mark.asyncio
async def test_runtime_uses_fallback_after_exhausted_retries() -> None:
    source = """
    agent resilient {
      retry(2, backoff=linear, base_delay_seconds=0.01)
      fallback {
        step degraded using fallback_answer(query=query)
      }
    }

    tool flaky(query: string) -> string
    tool fallback_answer(query: string) -> string

    workflow main(query: string) -> string {
      return flaky(query)
    }
    """
    registry = ToolRegistry()
    attempts = 0

    @registry.tool()
    def flaky(query: str) -> str:
        nonlocal attempts
        attempts += 1
        raise ServiceUnavailableError("boom")

    @registry.tool()
    def fallback_answer(query: str) -> str:
        return f"fallback:{query}"

    program = compile_runtime_program(source)
    result = await AsyncInterpreter(program, tools=registry).run_workflow(
        "main",
        arguments={"query": "notice"},
    )

    assert attempts == 2
    assert result == "fallback:notice"


@pytest.mark.asyncio
async def test_runtime_circuit_breaker_recovers_through_half_open_state() -> None:
    source = """
    agent guarded {
      retry(1)
      fallback {
        step degraded using fallback_answer()
      }
      circuit_breaker(threshold=0.50, window=2, cooldown_seconds=5, half_open_max_calls=1, min_calls=2)
    }

    tool flaky() -> string
    tool fallback_answer() -> string

    workflow main() -> string {
      return flaky()
    }
    """
    registry = ToolRegistry()
    calls: list[str] = []
    healthy = {"value": False}
    current_time = {"value": 0.0}

    @registry.tool()
    def flaky() -> str:
        calls.append("flaky")
        if not healthy["value"]:
            raise ServiceUnavailableError("down")
        return "restored"

    @registry.tool()
    def fallback_answer() -> str:
        calls.append("fallback")
        return "degraded"

    async def fake_sleep(delay: float) -> None:
        current_time["value"] += delay

    def now() -> float:
        return current_time["value"]

    program = compile_runtime_program(source)
    interpreter = AsyncInterpreter(
        program,
        tools=registry,
        clock=FunctionalClock(sleep_fn=fake_sleep, now_fn=now),
    )

    first = await interpreter.run_workflow("main")
    second = await interpreter.run_workflow("main")
    third = await interpreter.run_workflow("main")
    current_time["value"] = 6.0
    healthy["value"] = True
    fourth = await interpreter.run_workflow("main")

    assert first == "degraded"
    assert second == "degraded"
    assert third == "degraded"
    assert fourth == "restored"
    assert calls == ["flaky", "fallback", "flaky", "fallback", "fallback", "flaky"]


@pytest.mark.asyncio
async def test_runtime_executes_nested_workflows() -> None:
    source = """
    workflow child(prefix: string) -> string {
      let suffix: string = "-child"
      return prefix + suffix
    }

    workflow parent() -> string {
      let prefix: string = "root"
      return child(prefix)
    }
    """
    program = compile_runtime_program(source)
    result = await AsyncInterpreter(program).run_workflow("parent")
    assert result == "root-child"


def test_environment_chain_resolves_parent_values() -> None:
    parent = Environment()
    parent.define("root", "value")
    child = Environment(parent=parent)
    child.define("leaf", "child")

    assert child.get("leaf") == "child"
    assert child.get("root") == "value"


@pytest.mark.asyncio
async def test_runtime_raises_when_no_fallback_exists() -> None:
    source = """
    agent strict {
      retry(1)
    }

    tool flaky() -> string
    workflow main() -> string {
      return flaky()
    }
    """
    registry = ToolRegistry()

    @registry.tool()
    def flaky() -> str:
        raise ServiceUnavailableError("bad")

    program = compile_runtime_program(source)
    with pytest.raises(ToolInvocationError, match="failed after 1 attempt"):
        await AsyncInterpreter(program, tools=registry).run_workflow("main")


@pytest.mark.asyncio
async def test_runtime_mem_search_reads_session_memory() -> None:
    source = """
    workflow recall(query: string) -> list[MemoryEntry] {
      let note: string = "BNS section 103 theft punishment"
      return mem_search(query)
    }
    """
    program = compile_runtime_program(source)
    result = await AsyncInterpreter(program).run_workflow(
        "recall",
        arguments={"query": "theft punishment"},
    )

    assert isinstance(result, list)
    assert result
    assert isinstance(result[0], MemoryEntry)
    assert result[0].key == "note"
    assert "theft punishment" in result[0].value.lower()


@pytest.mark.asyncio
async def test_runtime_persists_trace_and_replays_from_sqlite() -> None:
    source = """
    tool annotate(query: string) -> string

    workflow recall(query: string) -> list[MemoryEntry] {
      let note: string = annotate(query)
      return mem_search(query)
    }
    """
    registry = ToolRegistry()
    live_calls = {"annotate": 0}

    @registry.tool()
    def annotate(query: str) -> str:
        live_calls["annotate"] += 1
        return f"BNS note about {query} contact advocate@example.com sk-1234567890abcdef"

    trace_path = Path("tests") / f"trace-{uuid4().hex}.sqlite"
    replay_trace_path = Path("tests") / f"replay-{uuid4().hex}.sqlite"
    recorder = SQLiteTraceRecorder(trace_path)
    program = compile_runtime_program(source)
    interpreter = AsyncInterpreter(program, tools=registry, trace_recorder=recorder)
    live_result = await interpreter.run_workflow("recall", arguments={"query": "appeal"})

    assert live_result
    assert interpreter.last_run_id is not None

    replayer = SQLiteTraceReplayer(trace_path)
    replay = replayer.replay(interpreter.last_run_id)
    replay_source = replayer.load_source(interpreter.last_run_id)

    assert replay.status == "completed"
    assert replay.workflow_name == "recall"
    assert isinstance(replay.final_output, list)
    assert replay.final_output[0]["key"] == "note"
    assert any(event.event_type == "mem_search" for event in replay.events)

    replay_registry = ToolRegistry()

    @replay_registry.tool()
    def annotate(query: str) -> str:
        raise AssertionError("Live tool should not be called during replay.")

    replay_recorder = SQLiteTraceRecorder(replay_trace_path)
    replay_result = await AsyncInterpreter(
        program,
        tools=replay_registry,
        trace_recorder=replay_recorder,
        replay_source=replay_source,
    ).run_workflow("recall", arguments={"query": "appeal"})

    replay_run_id = replay_recorder.latest_run_id()
    replay_replayer = SQLiteTraceReplayer(replay_trace_path)
    replay_run = replay_replayer.replay(replay_run_id)
    jsonl_text = trace_path.with_suffix(".jsonl").read_text(encoding="utf-8")

    assert replay_result == live_result
    assert live_calls["annotate"] == 1
    assert any(event.event_type == "tool_replayed" for event in replay_run.events)
    assert "[REDACTED_EMAIL]" in jsonl_text
    assert "[REDACTED_API_KEY]" in jsonl_text

    replay_replayer.close()
    replay_recorder.close()
    recorder.close()
    replayer.close()


def test_memory_manager_session_snapshot_tracks_latest_values() -> None:
    memory = MemoryManager()
    memory.write("alpha", "first")
    memory.write("beta", {"value": 2})

    snapshot = memory.snapshot()
    assert snapshot["alpha"] == "first"
    assert snapshot["beta"] == {"value": 2}
