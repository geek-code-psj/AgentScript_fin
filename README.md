# AgentScript

AI agents are powerful — but impossible to debug.

**AgentScript fixes that.**

[![PyPI version](https://img.shields.io/pypi/v/agentscript-lang)](https://pypi.org/project/agentscript-lang/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![VS Code Extension](https://img.shields.io/badge/VS%20Code-Extension-blue)](https://marketplace.visualstudio.com/items?itemName=agentscript)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

---

## The problem

AI workflows break silently.

A tool call fails at step 4. An LLM hallucinates at step 7. A memory write gets skipped at step 12. You get a wrong answer — and no idea where it came from.

Python gives you the rope to build agents. It gives you nothing to understand them.

---

## What AgentScript looks like

```
agent LegalResearcher {
  memory: persistent
  tools: [web_search, filter_relevance, summarize, cite, save_note]
  retry(3, backoff=exponential)

  task research(query: string) -> list[Citation] {
    let results = web_search(query, source="indiankanoon.org")
    let relevant = filter_relevance(results, threshold=0.72)

    loop case in relevant {
      let summary: Claim = summarize(case.content)
      let citation: Citation = cite(case.url, summary)
      save_note(citation, tags=["BNS", query])
    }

    return memory.search(query, top_k=5)
  }
}
```

Run it:

```bash
agentscript run legal_researcher.as --task research --input '{"query": "BNS section 302"}'
```

---

## Now replay it.

```bash
agentscript replay --run-id a3f9b2 --step
```

```
→ Step 1  CALL_TOOL  web_search("BNS section 302")        312ms   ✓
→ Step 2  CALL_TOOL  filter_relevance([...], 0.72)         44ms   ✓
→ Step 3  MEM_SET    citation_0 = "SC held that..."         2ms   ✓
→ Step 4  CALL_TOOL  web_search("BNS section 302")           0ms  ↻ retry 2
→ Step 5  CALL_TOOL  summarize(case.content)               190ms   ✓
```

Every decision the agent made. Every memory write. Every retry.
Step by step. Inspectable. Replayable. Forever.

That's what debugging AI looks like when you actually build the tools for it.

---

## Why not just Python?

| | Python + LangChain | AgentScript |
|---|---|---|
| Tool call tracing | Manual, library-dependent | Automatic, every call |
| Retry on failure | Wrapped with `tenacity` | Declared in the agent |
| Execution replay | Not possible | Built into the runtime |
| Structured AI outputs | Pydantic + prompt tricks | Validated types in the language |
| Observability dashboard | Third-party (Arize, LangSmith) | Ships with the runtime |

---

## Three guarantees

**1. Every step is traced.**
Tool calls, memory ops, conditionals — all recorded to a structured SQLite log as they happen. Nothing is hidden.

**2. Any run can be replayed.**
Tool outputs are cached at execution time. Step through any previous run and inspect state at each instruction.

**3. Failures are handled in the language.**
Retry policies, fallbacks, and circuit breakers live inside the agent definition — not scattered across Python glue code.

---

## For non-technical readers — what did we actually build?

Imagine you hire someone to research legal cases. They search documents, filter out the irrelevant ones, summarize the useful ones, and hand you a cited report.

Now imagine that person is an AI — and you have a recording of every decision they made. You can pause it, rewind it, and inspect exactly what they were thinking at each step.

That's AgentScript.

### What level is this?

| Level | Example |
|---|---|
| Beginner | To-do app, weather app, basic website |
| Intermediate | REST API with auth, ML model, chat app |
| Advanced | Multi-agent platform, on-device AI, RAG pipeline |
| **AgentScript** | **A programming language with its own compiler, typed IR, async runtime, and observability layer** |

Building a programming language from scratch is what senior engineers at JetBrains, Apple, and Mozilla do as their full-time job. The closest real-world comparisons:

- **Temporal** — workflow orchestration language at Stripe, Airbnb, Netflix
- **BAML** — DSL for structured LLM outputs, Y Combinator backed
- **Pkl** — Apple's configuration language

AgentScript is in that category. Built by one person, from scratch, in college.

---

## Architecture

```
Source (.as file)
      │
      ▼
  [1] Lexer              Source text → flat token stream
      │
      ▼
  [2] Parser             Tokens → Abstract Syntax Tree (recursive descent)
      │
      ▼
  [3] Semantic Analyzer  Type checking, scope resolution, tool validation
      │                  Produces: symbol table + annotated AST
      ▼
  [4] IR Lowering        AST → flat bytecode instruction list
      + Optimizer         Dead-code elimination pass
      │
      ▼
  [5] Async Interpreter  Executes IR instructions
                          ├── Tool registry (decorator-based)
                          ├── Scoped environment chain
                          ├── Two-tier memory (RAM + ChromaDB)
                          ├── Fault model (retry / fallback / circuit-breaker)
                          └── Observability tracer → SQLite
```

---

## Formal semantics

### Type system

AgentScript has structured types with compile-time validation rules — not just named structs.

| Type | Shape | Compile-time rules |
|---|---|---|
| `string` | UTF-8 text | none |
| `number` | IEEE 754 float | none |
| `bool` | true / false | none |
| `Claim` | `{ text: string, confidence: float }` | `confidence` must be in `[0.0, 1.0]`. Cannot be cast to `string`. |
| `Citation` | `{ url: string, span: string, summary: Claim }` | Requires a valid `Claim` in `summary`. Cannot exist without one. |
| `Intent` | `{ action: string, entity: string }` | Produced only by intent-classifier tools. Cannot be constructed inline. |
| `Embedding` | `float[]` | Cannot be assigned to any other type. Only valid as input to `memory.search()`. |

**Type errors caught at compile time:**

```
let x: Claim = "some text"            ← ERROR: cannot assign string to Claim
let c: Citation = cite(url, "text")   ← ERROR: summary must be Claim, got string
let e: Embedding = [0.1, 0.2]         ← ERROR: Embedding cannot be constructed inline
```

No implicit subtyping. No silent coercions.

---

### Execution order

- Statements run top to bottom, sequentially.
- `parallel loop` dispatches iterations via `asyncio.gather()` — no ordering guarantee. Memory writes from parallel branches are serialized with an async lock (last write wins).
- A task calling another task is a synchronous blocking call.

---

### Retry semantics

`retry(n, backoff=B)` wraps the entire task body.

- **Failure** = any tool call that raises, or returns a value that fails its type check.
- **Backoff:** `none` | `linear` (1s, 2s, 3s…) | `exponential` (1s, 2s, 4s…). Capped at 30s.
- If a `fallback` block is present and the primary branch fails, the fallback runs immediately — no retry within the fallback. If the fallback also fails, the retry counter increments.
- After `n` retries: raises `MaxRetriesExceeded`.

---

### Circuit breaker semantics

`circuit_breaker(threshold=T, window=W)` tracks tool failure rate over a rolling `W`-second window (default 60s).

- When failure rate exceeds `T`: circuit opens. All tool calls immediately raise `CircuitOpenError`.
- Resets after one successful tool call following a 10-second cooldown.
- State is **per task instance** — two concurrent runs have independent circuit state.

---

### Replay guarantees

Replay is **trace-based** — tool outputs cached during the original run are returned directly. Tools are not re-executed.

- LLM responses are frozen: same response as the original run.
- Memory state reconstructed step-by-step from the `memory_evolution` log.
- External side effects (DB writes, emails) are **not** replayed — only return values are.
- Replay is **read-only**. You cannot modify state during a replay session.

---

### Memory model

**Session memory** (RAM dict) — scoped to a single task execution. Cleared on task completion or error. Last write wins. No versioning.

**Persistent memory** (ChromaDB) — scoped to agent lifetime, survives across runs.
- `memory.set(key, value)` — embeds and stores. Synchronous. Overwrites existing key without history.
- `memory.get(key)` — exact key lookup.
- `memory.search(query, top_k=N)` — cosine similarity search, returns top N by score.

---

### IR instruction set

> You don't need to understand this to use AgentScript — it's here for transparency and for anyone who wants to extend the runtime.

| Instruction | Operands | Behavior |
|---|---|---|
| `LOAD` | `name` | Push variable onto stack |
| `STORE` | `name` | Pop stack top → current scope |
| `PUSH` | `literal` | Push literal onto stack |
| `CALL_TOOL` | `name, argc` | Pop args, call tool, push result |
| `CALL_TASK` | `name, argc` | Pop args, call task (blocking), push result |
| `JUMP_IF_FALSE` | `offset` | Pop bool; jump if false |
| `JUMP` | `offset` | Unconditional jump |
| `LOOP_START` | `var, iterable` | Initialize loop iterator |
| `LOOP_NEXT` | `offset` | Advance; jump to offset if exhausted |
| `MEM_GET` | `key` | Push `session_memory[key]` |
| `MEM_SET` | `key` | Pop value → `session_memory[key]` |
| `MEM_SEARCH` | `query, top_k` | Cosine search → push result list |
| `SCOPE_PUSH` | — | Push new scope frame |
| `SCOPE_POP` | — | Pop scope frame |
| `RETURN` | — | Pop return value, exit task frame |
| `RAISE` | `message` | Raise runtime error |

**Optimizer:** removes `STORE` instructions whose variable is never subsequently `LOAD`ed or passed to `CALL_TOOL`. No inter-procedural passes in the current version.

---

## Observability

Every run writes a full trace to SQLite:

```json
{
  "run_id": "a3f9b2",
  "agent": "LegalResearcher",
  "task": "research",
  "steps": [
    { "op": "CALL_TOOL", "tool": "web_search",      "duration_ms": 312, "status": "ok"    },
    { "op": "CALL_TOOL", "tool": "filter_relevance", "duration_ms": 44,  "status": "ok"    },
    { "op": "MEM_SET",   "key": "citation_0",        "duration_ms": 2,   "status": "ok"    },
    { "op": "CALL_TOOL", "tool": "web_search",       "duration_ms": 0,   "status": "retry", "attempt": 2 }
  ],
  "memory_evolution": [
    { "step": 3, "op": "SET", "key": "citation_0", "value_preview": "BNS 302 — SC held..." }
  ],
  "total_duration_ms": 1847,
  "final_status": "ok"
}
```

The dashboard (`agentscript dashboard → http://localhost:8000`) shows:

- **Tool call timeline** — horizontal bars, duration and status per tool
- **Memory evolution** — every write, in execution order
- **Replay viewer** — step through any past run, inspect state at each instruction
- **Retry log** — which tools failed, how many attempts, what recovered

---

## 📁 Folder structure

```
agentscript/
│
├── README.md
├── LICENSE
├── pyproject.toml
│
├── agentscript/
│   ├── lexer/
│   │   ├── lexer.py               ← Source text → token stream
│   │   ├── token_types.py         ← All token types (TT enum)
│   │   └── token.py               ← Token dataclass (type, value, line, col)
│   │
│   ├── parser/
│   │   ├── parser.py              ← Recursive descent parser
│   │   └── ast_nodes.py           ← Every AST node as a dataclass
│   │
│   ├── analyzer/
│   │   ├── semantic.py            ← Type checker + scope resolver
│   │   ├── symbol_table.py        ← Symbol table
│   │   └── type_system.py         ← Claim, Citation, Intent, Embedding
│   │
│   ├── ir/
│   │   ├── lowering.py            ← AST → bytecode IR
│   │   ├── instructions.py        ← Full instruction set
│   │   └── optimizer.py           ← Dead-code elimination
│   │
│   ├── runtime/
│   │   ├── interpreter.py         ← Async IR interpreter
│   │   ├── environment.py         ← Scoped variable store
│   │   ├── tool_registry.py       ← Tool decorator + registry
│   │   ├── fault.py               ← Retry / fallback / circuit-breaker
│   │   └── scheduler.py           ← Multi-agent concurrent scheduler
│   │
│   ├── memory/
│   │   ├── session.py             ← In-RAM session memory
│   │   ├── persistent.py          ← ChromaDB semantic store
│   │   └── memory_manager.py      ← Unified get / set / search API
│   │
│   ├── observability/
│   │   ├── tracer.py              ← Trace emitter → SQLite
│   │   ├── replay.py              ← Trace-based replay engine
│   │   └── schema.sql             ← SQLite schema
│   │
│   └── cli.py                     ← agentscript CLI
│
├── dashboard/
│   ├── backend/
│   │   ├── main.py                ← FastAPI app
│   │   └── routes/
│   │       ├── traces.py
│   │       ├── memory.py
│   │       └── replay.py
│   └── frontend/
│       └── src/
│           ├── components/
│           │   ├── Timeline.tsx       ← Tool call timeline
│           │   ├── MemoryGraph.tsx    ← Memory evolution view
│           │   ├── ReplayViewer.tsx   ← Step-through replay
│           │   └── TraceTable.tsx     ← Raw trace log
│           └── App.tsx
│
├── vscode-extension/
│   ├── syntaxes/
│   │   └── agentscript.tmLanguage.json  ← Syntax highlighting for .as files
│   └── package.json
│
├── examples/
│   ├── legal_researcher.as        ← Indian legal case research agent
│   ├── financial_analyst.as       ← Multi-source financial research
│   ├── medical_summarizer.as      ← Clinical note summarizer
│   └── hello_agent.as             ← Getting started
│
├── spec/
│   └── LANGUAGE_SPEC.md           ← Full grammar + language reference
│
├── benchmarks/
│   ├── bench_lexer.py
│   ├── bench_parser.py
│   ├── bench_ir.py
│   ├── bench_runtime.py
│   └── RESULTS.md
│
└── tests/
    ├── unit/
    │   ├── test_lexer.py
    │   ├── test_parser.py         ← includes hypothesis fuzz tests
    │   ├── test_analyzer.py
    │   ├── test_ir.py
    │   └── test_runtime.py
    ├── integration/
    │   ├── test_legal_agent.py
    │   └── test_fault_model.py
    └── conftest.py
```

---

## Installation

```bash
pip install agentscript-lang
```

```bash
# From source
git clone https://github.com/yourusername/agentscript
cd agentscript
pip install -e ".[dev]"
```

VS Code: search **AgentScript** in the Extensions marketplace.

---

## Quick start

```bash
# Run an agent
agentscript run examples/hello_agent.as --task main

# Run with live trace output
agentscript run examples/legal_researcher.as --task research \
  --input '{"query": "BNS section 302"}' --trace

# Open the observability dashboard
agentscript dashboard
# → http://localhost:8000

# Replay any previous run
agentscript replay --run-id a3f9b2 --step
```

---

## Benchmarks

Measured on M2 MacBook Pro, Python 3.11, 100-line agent file:

| Operation | Result |
|---|---|
| Lexer throughput | ~2.1M tokens/sec |
| Parse time | ~1.2ms |
| IR lowering | ~0.3ms |
| Interpreter overhead vs raw Python | ~8% |
| Memory search (ChromaDB, 10k docs) | ~18ms |
| Trace write per step | ~0.4ms |

---

## Roadmap

- [ ] Formal BNF grammar + parser generator target
- [ ] Multi-agent messaging bus (agent-to-agent calls)
- [ ] LSP server — VS Code autocomplete + inline type hints
- [ ] WASM compilation target
- [ ] Rust port of the lexer and parser
- [ ] AgentScript Hub — public shareable agent registry

---

## Contributing

```bash
pytest tests/ -v
pytest tests/ --hypothesis-seed=0    # fuzz the parser
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE)

---

## Author

**Prabal Pratap Singh Jadon**  
Final-year CS student. Founding engineer. Built [VakilDoot](https://github.com/geek-code-psj/Vakildoot) — fully offline on-device legal AI (Phi-4-mini + ExecuTorch on Android). [Hedge Fund AI v3](https://github.com/geek-code-psj/Hedge-fund-ai) — multi-agent LangGraph platform with 93 production deployments. SwasthyaAI — DPDP-compliant medical AI, 17/17 compliance tests passing.

[GitHub](https://github.com/geek-code-psj) · [Email](mailto:email.prabalsingh1@gmail.com)
