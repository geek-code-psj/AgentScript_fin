# AgentScript Architecture Guide

This document provides a deep technical overview of AgentScript's architecture, from language design through runtime execution, observability, and deterministic replay.

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Compiler Pipeline](#compiler-pipeline)
3. [Runtime Engine](#runtime-engine)
4. [Fault Tolerance](#fault-tolerance)
5. [Observability System](#observability-system)
6. [Deterministic Replay](#deterministic-replay)
7. [Memory Management](#memory-management)
8. [Tool Gateway](#tool-gateway)

---

## Design Philosophy

### The Problem with Imperative Agent Loops

Traditional AI agent implementations wrap LLM reasoning in imperative Python while-loops:

```python
# ❌ Anti-pattern: messy, fragile, unobservable
while not done:
    response = llm.invoke(prompt)
    tool = parse_tool_from_response(response)  # Fragile parsing
    result = tool.call()
    prompt += f"\n{result}"
    if some_error_condition:
        # What retry logic? What fallback? What tracing?
        retry_count += 1
```

**Problems:**
- No observability (where did execution go wrong?)
- No fault tolerance (all-or-nothing: succeed or restart from scratch)
- No determinism (temperature, API state, timestamps all vary on replay)
- No schema validation (LLM outputs are raw text, not typed structures)
- Token waste (excessive prompting about JSON formatting)

### The AgentScript Solution

AgentScript separates concerns rigorously:

```
┌──────────────────────────┐
│  Intelligent Reasoning   │
│  (LLM via tools)         │
│  (Probabilistic)         │
└──────────────────────────┘
           ↓
┌──────────────────────────┐
│  Orchestration Layer     │
│  (AgentScript Runtime)   │
│  (Deterministic)         │
└──────────────────────────┘
```

**Key Design Decisions:**

1. **Language-First Orchestration** — Workflows are declared in a DSL, not imperative Python. This enables:
   - Static analysis (compile-time type checking, scope resolution)
   - Optimizations (dead-code elimination, inlining)
   - Observability hooks (every step is instrumentable)
   - Determinism (control flow is predictable, not data-dependent)

2. **Type-Safe LLM Interfaces** — LLM-native types (`Claim`, `Citation`, `Intent`) are first-class language constructs:
   - Compiler validates type flow
   - Runtime enforces schema at execution boundary
   - Reduces hallucination surface (LLM can't invent random fields)

3. **Fault Tolerance in the Language** — `retry`, `fallback`, `circuit_breaker` are language primitives:
   - Declared together with workflows (single source of truth)
   - Not scattered across Python boilerplate
   - Composable (retry + circuit_breaker + fallback work together)

4. **Replay-First Design** — Every execution captures immutable, redacted traces:
   - Tool results recorded in JSONL (event sourcing)
   - Timestamps virtualized (can replay at any time)
   - Deterministic: same trace input = same output
   - Enables forensic debugging (find exact divergence point)

---

## Compiler Pipeline

The AgentScript compiler transforms DSL source → executable IR. See [src/agentscript/compiler/](../src/agentscript/compiler/) for implementation.

### Stage 1: Lexical Analysis

**File:** [src/agentscript/compiler/lexer.py](../src/agentscript/compiler/lexer.py)

Tokenizes source text into meaningful units:

```agentscript
agent legal_researcher {
  retry(3, backoff=exponential)
}
```

Produces tokens: `AGENT`, `IDENTIFIER("legal_researcher")`, `LBRACE`, `RETRY`, `LPAREN`, `NUMBER(3)`, ...

**Performance:** 263,662 tokens/sec (can handle large corpora)

**Core Concepts:**
- Keywords: `agent`, `workflow`, `tool`, `retry`, `fallback`, `circuit_breaker`, `let`, `step`, `using`, `return`, `if`, `else`, `type`, `import`
- Literals: strings (double-quoted), integers, floats, booleans, null
- Operators: arithmetic (`+`, `-`, `*`, `/`), comparison (`==`, `!=`, `<`, `>`, `<=`, `>=`)
- Punctuation: parentheses, braces, brackets, colons, commas, semicolons

### Stage 2: Syntax Analysis

**File:** [src/agentscript/compiler/parser.py](../src/agentscript/compiler/parser.py)

Parses token stream into Abstract Syntax Tree (AST). Uses recursive-descent parsing.

**Performance:** 0.992 ms for typical workflows

**Key AST Nodes:**
- `Program` (top-level declarations)
- `AgentDeclaration` (fault-tolerance policies: retry, fallback, circuit_breaker)
- `ToolDeclaration` (tool signature: name, parameters, return type)
- `WorkflowDeclaration` (workflow name, parameters, body statements)
- `StepStatement` (invoke a tool: `step result using tool(...)`)
- `LetStatement` (bind variable: `let x: Type = expression`)
- `IfStatement` (conditional branch)
- `ReturnStatement`
- `CallExpression` (function/tool invocation)
- `MemorySearch` (semantic search: `mem_search(query)`)

**AST Pretty Printer:** [src/agentscript/compiler/pretty_printer.py](../src/agentscript/compiler/pretty_printer.py)

### Stage 3: Semantic Analysis

**File:** [src/agentscript/compiler/semantic_analyzer.py](../src/agentscript/compiler/semantic_analyzer.py)

Validates type correctness, scope, and callable signatures. Builds a symbol table.

**Built-in LLM-Native Types:**
- `Claim` — (confidence: float, text: string) — extracted assertion
- `Citation` — (source: string, span: string, url: string?) — source reference
- `Intent` — (name: string, score: float) — user intent classification
- `Embedding` — (dim: int, vector: list[float]) — dense vector
- `MemoryEntry` — (key: string, value: string, score: float) — semantic memory
- Standard types: `string`, `int`, `float`, `bool`, `null`, `list[T]`, `dict[K, V]`

**Validations:**
1. **Type Checking** — All variables and expressions type-check
2. **Scope Resolution** — All references are defined before use
3. **Callable Verification** — Tool/workflow calls match signatures
4. **Reachability** — Unreachable code is flagged (dead-code detection)

### Stage 4: Intermediate Representation (IR) Lowering

**File:** [src/agentscript/compiler/ir.py](../src/agentscript/compiler/ir.py)

Lowers AST → flattened IR representation designed for runtime interpretation.

**Performance:** 1.109 ms

**IR Instruction Types:**
- `VAR_INIT` — Initialize variable
- `VAR_SET` — Update variable
- `TOOL_CALL` — Invoke external tool (entry point for retry/circuit_breaker/replay)
- `TOOL_RESULT` — Receive tool result (exit point for tracing)
- `MEMORY_SEARCH` — Query semantic memory (lowers to dedicated opcode)
- `CONDITIONAL_JUMP` — Branch on condition
- `JUMP` — Unconditional branch
- `RETURN` — Exit workflow

**Optimizations:**
- Dead-code elimination (unreachable instructions removed)
- Constant folding (compile-time expression evaluation)
- Scope-based variable allocation

### Stage 5: Program Synthesis

**File:** [src/agentscript/runtime/program.py](../src/agentscript/runtime/program.py)

Constructs a `RuntimeProgram` object containing:
- Compiled workflows and agents
- Agent policies (retry/fallback/circuit_breaker configuration)
- Tool registry reference

---

## Runtime Engine

The async interpreter executes compiled IR. See [src/agentscript/runtime/interpreter.py](../src/agentscript/runtime/interpreter.py).

### Execution Model

```
┌─────────────────────────┐
│   Workflow Invocation   │
│  (arguments provided)   │
└───────────┬─────────────┘
            │
            ↓
┌─────────────────────────┐
│  Load IR Instructions   │
│  Lookup Agent Policies  │
└───────────┬─────────────┘
            │
            ↓
┌─────────────────────────────────────────────────┐
│  STEP-BY-STEP EXECUTION                         │
│  1. Load instruction                            │
│  2. Execute (may call Tool Gateway)             │
│  3. Update state (variables, memory)            │
│  4. Emit trace event                            │
│  5. Next instruction                            │
└───────────┬───────────────────────────────────┘
            │
            ↓
┌─────────────────────────┐
│   Return Result         │
│  (type-validated)       │
└─────────────────────────┘
```

### Key Components

1. **Program State** ([src/agentscript/runtime/environment.py](../src/agentscript/runtime/environment.py))
   - Variable bindings (local scope)
   - Execution context (current workflow, step index)
   - Memory state (session + semantic memory)

2. **Memory Manager** ([src/agentscript/runtime/memory.py](../src/agentscript/runtime/memory.py))
   - Session memory (simple key-value store, persisted in SQLite)
   - Semantic memory (Chroma embeddings, by default disabled; enable with `pip install -e .[memory]`)
   - `mem_search(query: string) -> list[MemoryEntry]` — retrieve semantically similar memories

3. **Tool Gateway** ([src/agentscript/runtime/gateway.py](../src/agentscript/runtime/gateway.py))
   - Single choke point for all external tool invocations
   - Applies retry logic, circuit breaker, fallback
   - Records tool calls and results in trace
   - In replay mode: returns recorded results instead of invoking live tools

4. **Tracing System** ([src/agentscript/runtime/tracing.py](../src/agentscript/runtime/tracing.py))
   - Records every TOOL_CALL and TOOL_RESULT as structured JSON
   - Writes to JSONL file (append-only, streaming-friendly)
   - Maintains SQLite index for fast replay lookups
   - Redacts sensitive data (API keys, PII) before persisting

### Async Execution

All workflow execution is async-aware:

```python
result = asyncio.run(
    AsyncInterpreter(program, tools=registry).run_workflow(
        "legal_brief",
        arguments={"query": "BNS section 103"},
    )
)
```

Enables:
- Non-blocking tool invocations (parallel requests if allowed)
- Integration with async HTTP clients
- Proper resource cleanup

---

## Fault Tolerance

AgentScript implements three complementary fault-tolerance patterns.

### 1. Bounded Retries with Exponential Backoff

**Declaration:**
```agentscript
agent resilient {
  retry(max_attempts: int, backoff: exponential, base_delay_seconds: float, max_delay_seconds: float)
}
```

**Behavior:**
1. Tool call fails (HTTP 500, timeout, etc.)
2. Wait `delay = min(base_delay * 2^attempt, max_delay_seconds)`
3. Increment retry counter
4. If counter < max_attempts, retry
5. If counter >= max_attempts, move to fallback

**Implementation:** [src/agentscript/runtime/gateway.py](../src/agentscript/runtime/gateway.py) lines ~100-150

**Trace Captures:**
- Each retry attempt recorded with `TOOL_CALL` + `TOOL_RESULT`
- `retry_count` field in result object indicates how many attempts were made
- Latency breakdown shows cumulative wait time

### 2. Circuit Breaker Pattern

**Declaration:**
```agentscript
agent resilient {
  circuit_breaker(threshold: float, window: int, cooldown_seconds: int, half_open_max_calls: int, min_calls: int)
}
```

**States:**

| State | Condition | Action |
|-------|-----------|--------|
| **Closed** | Normal operation (<50% failure rate over window) | Requests pass through; latency/success counters updated |
| **Open** | Failure rate exceeds threshold (>50% over last N calls) | All requests immediately rejected; routed to fallback; circuit remains open for `cooldown_seconds` |
| **Half-Open** | Cooldown period expired | Limited requests allowed (1-3) to probe service health; if successful, transition to Closed; if failed, back to Open |

**Implementation:** [src/agentscript/runtime/gateway.py](../src/agentscript/runtime/gateway.py) lines ~200-280

**Example:**
```
Time    Event                          Circuit State   Action
T=0s    Normal requests passing         CLOSED          ✓ pass through
T=5s    3 failures in last 10 calls     OPEN            ✗ all requests fail over to fallback
T=60s   Cooldown expired                HALF_OPEN       Send 1 probe request
T=60.5s Probe succeeds                  CLOSED          Resume normal operations
```

### 3. Fallback Paths

**Declaration:**
```agentscript
agent resilient {
  fallback {
    step result using cheaper_model(query=query)
    // OR: use cached result
    // OR: use heuristic rule
    // OR: escalate to human
  }
}
```

**When Triggered:**
- Retry counter exhausted
- Circuit breaker in Open state
- Tool not registered (optional: degrade gracefully)

**Fallback Strategies:**
- **Model degradation** — switch from GPT-4 to cheaper, faster model
- **Cached execution** — return last known good result with staleness marker
- **Rules-based** — apply simple decision tree or heuristic function
- **Human escalation** — pause workflow, alert human for manual review

---

## Observability System

Every execution is observable via multiple channels. See [src/agentscript/observability/](../src/agentscript/observability/).

### 1. JSONL Trace Format

**Location:** `traces/*.jsonl` (append-only event log)

**Event Structure:**

```json
{
  "run_id": "legal_brief_20260405_143022",
  "step_id": 1,
  "timestamp": "2026-04-05T14:30:22.123Z",
  "event_type": "TOOL_CALL",
  "tool_name": "search_indian_kanoon",
  "tool_arguments": {"query": "BNS theft appeal"},
  "model_id": "gpt-4-turbo",
  "model_config": {"temperature": 0.7, "top_p": 0.9}
}
```

```json
{
  "run_id": "legal_brief_20260405_143022",
  "step_id": 1,
  "timestamp": "2026-04-05T14:30:23.456Z",
  "event_type": "TOOL_RESULT",
  "tool_name": "search_indian_kanoon",
  "ok": true,
  "status_code": 200,
  "response_payload": [{"source": "Indian Kanoon", "text": "..."}],
  "latency_ms": 1333,
  "retry_count": 0
}
```

**Security:** Redacted on write using regex patterns:
- API keys (Bearer, AWS, OpenAI patterns)
- PII (SSN, email, phone, IP)
- Authentication headers
- See [src/agentscript/runtime/records.py](../src/agentscript/runtime/records.py)

### 2. SQLite Trace Index

**Location:** `traces/*.sqlite`

**Purpose:** Fast replay lookup (O(1) TOOL_RESULT by run_id + step_id)

**Schema:**
```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  workflow_name TEXT,
  status TEXT,
  created_at TIMESTAMP
);

CREATE TABLE tool_results (
  run_id TEXT,
  step_id INTEGER,
  tool_name TEXT,
  response JSONBLOB,
  PRIMARY KEY (run_id, step_id)
);
```

### 3. OpenTelemetry Spans (Optional)

**Enable:** Set `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`

**Exported to:** Datadog, New Relic, Jaeger, Tempo, or local OTLP collector

**Semantic Attributes:**

| Attribute | Level | Value | Example |
|-----------|-------|-------|---------|
| `gen_ai.agent.name` | Conditional | String | `legal_researcher` |
| `gen_ai.operation.name` | Required | Enum | `tool_call`, `agent_run`, `inference`, `retrievals` |
| `gen_ai.provider.name` | Required | String | `OpenAI`, `Anthropic` |
| `gen_ai.conversation.id` | Conditional | UUID | User session ID |
| `error.type` | Conditional | String | On failure: exception class name |

**Implementation:** [src/agentscript/observability/otel.py](../src/agentscript/observability/otel.py)

### 4. FastAPI Dashboard

**Run:** `agentscript dashboard traces/demo.sqlite` or `docker compose up`

**Endpoints:**
- `GET /api/runs` — List all execution runs
- `GET /api/runs/{run_id}` — Get run details (timeline of TOOL_CALL/TOOL_RESULT events)
- `GET /api/runs/{run_id}/timeline` — Event timeline with latency breakdown
- `GET /api/memory/{run_id}` — Inspect session and semantic memory state
- `POST /api/replay/{run_id}` — Trigger deterministic replay

**Frontend:** React dashboard ([dashboard/src/](../dashboard/src/)) shows:
- Timeline of tool calls with latency
- Circuit breaker state transitions
- Memory search results
- Trace comparison (live vs. replay)

---

## Deterministic Replay

Replay enables forensic debugging by re-executing historical runs exactly. See [src/agentscript/runtime/replay_engine.py](../src/agentscript/runtime/).

### High-Level Architecture

```
LIVE MODE                           REPLAY MODE
───────────────────────────────────────────────────────
Tool Gateway                        Tool Gateway
  ↓ (execute)                         ↓ (look up)
Live Tool (HTTP call)      →  Trace Index (JSONL lookup)
  ↓ (record result)                   ↓ (return recorded)
JSONL + SQLite             ←  TOOL_RESULT (byte-identical)
```

### Mechanism

1. **Load Historical Trace**
   ```bash
   agentscript replay tests/legal-demo.sqlite --run-id legal_brief_20260405_143022
   ```

2. **Inject Trace Index**
   - Parse SQLite index or JSONL file
   - Build in-memory map: (run_id, step_id) → TOOL_RESULT

3. **Virtualize System Clock**
   - Intercept `time.time()`, `datetime.now()`, `time.sleep()` calls
   - Return timestamps from trace (ensures time-dependent logic produces same results)
   - See [src/agentscript/runtime/clock.py](../src/agentscript/runtime/clock.py)

4. **Disable LLM Calls**
   - No inference during replay (model unavailable, behavior would differ)
   - Reasoning paths use recorded outputs

5. **Execute Interpreter in Replay Mode**
   - Step through IR instructions
   - When hitting TOOL_CALL, look up result in trace index
   - Return exact recorded payload
   - If trace entry missing: error (indicates data corruption or bug)

6. **Compare Live vs. Replay**
   - Both produce byte-identical outputs if implementation is correct
   - Highlight divergence points (where reasoning went wrong)

**Output fidelity guarantee:** 100% deterministic (same trace input → identical execution flow + outputs)

---

## Memory Management

AgentScript supports two levels of memory persistence: session and semantic.

### Session Memory

Simple key-value store, persisted in SQLite.

```agentscript
workflow remember_fact(fact: string, key: string) {
  // Store in session memory
  store_memory(key, fact)
}

workflow recall_fact(key: string) -> string {
  // Retrieve from session memory (blocks if not found)
  return get_memory(key)
}
```

**Implementation:** [src/agentscript/runtime/memory.py](../src/agentscript/runtime/memory.py)

### Semantic Memory (Optional)

Embeddings-based vector search, for retrieving semantically similar stored facts.

**Requires:** `pip install -e .[memory]` (installs Chroma)

```agentscript
workflow find_similar_precedents(query: string) -> list[MemoryEntry] {
  // Search semantic memory (retrieves entries with high cosine similarity)
  return mem_search(query)
}
```

**Integrated into dashboard:**
- View stored memories
- Test semantic search queries
- Inspect embedding similarity scores

**Performance:** 0.206 ms average lookup time

---

## Tool Gateway

The Tool Gateway is the heart of AgentScript's fault tolerance and observability. It's the single choke point for all external tool invocations.

**Responsibilities:**
1. **Retry logic** — bounded attempts with exponential backoff
2. **Circuit breaker** — prevent cascading failures
3. **Fallback routing** — degrade gracefully
4. **Trace capture** — record TOOL_CALL + TOOL_RESULT
5. **Replay stubbing** — in replay mode, return recorded results instead of live calls
6. **Performance measurement** — latency, retry counts
7. **Error categorization** — transient vs. permanent failures

**Source:** [src/agentscript/runtime/gateway.py](../src/agentscript/runtime/gateway.py)

**Call Stack (Live Mode):**
```
Interpreter.execute_tool_call()
  ↓
ToolGateway.invoke(tool, args, agent_policy)
  ↓
  ┌─────────────────────────────────┐
  │ Retry Loop (max_attempts)       │
  │   ↓ attempt 1                   │
  │   ├─ CircuitBreaker.allow()?    │
  │   │   NO → Fallback             │
  │   │   YES → Live tool call      │
  │   │   ├─ SUCCESS → Return       │
  │   │   └─ FAILURE → Retry        │
  │   ↓ attempt 2 (exponential wait)│
  │   ... (same logic)              │
  └─────────────────────────────────┘

TraceWriter.record_tool_call()
TraceWriter.record_tool_result()
```

---

## Summary

AgentScript's architecture enforces:

✅ **Separation of Concerns** — DSL orchestration decoupled from probabilistic LLM reasoning  
✅ **Type Safety** — Structured outputs validated at compilation and runtime  
✅ **Observability** — JSONL traces, OTel spans, FastAPI dashboard  
✅ **Fault Tolerance** — Retries, circuit breakers, fallback paths (language primitive)  
✅ **Determinism** — Replay-first design ensures reproducible execution  
✅ **Auditability** — Immutable trace logs with aggressive redaction for compliance  

This combination enables production-grade AI agents that survive real-world failure modes.

---

## Further Reading

- [GETTING_STARTED.md](./GETTING_STARTED.md) — Hands-on tutorial
- [API_REFERENCE.md](./API_REFERENCE.md) — FastAPI endpoints and OpenAPI spec
- [language-spec.md](./language-spec.md) — DSL syntax and semantics
- [OPERATIONS.md](./OPERATIONS.md) — Production monitoring and incident response
