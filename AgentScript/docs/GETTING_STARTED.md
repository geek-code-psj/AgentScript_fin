# Getting Started with AgentScript

This guide walks you through installing AgentScript, writing your first workflow, and observing its execution.

**Time to complete:** ~10 minutes

## Prerequisites

- Python 3.11+
- git
- (Optional) Docker and Docker Compose for observability stack

## Step 1: Clone and Install (2 min)

```bash
# Clone the repository
git clone https://github.com/yourusername/AgentScript.git
cd AgentScript

# Create virtual environment
python -m venv .venv

# Activate
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# Install in development mode with all extras
pip install -e .[dev,memory,dashboard,otel]

# Verify installation
agentscript --help
```

You should see:
```
usage: agentscript [-h] {lex,parse,check,compile,run,dashboard,replay} ...

AgentScript CLI
...
```

## Step 2: Explore the Example Workflows (2 min)

AgentScript comes with a pre-built legal research agent. Let's examine it:

```bash
# View the workflow definition
cat examples/legal_research.as
```

You'll see:

```agentscript
agent legal_researcher {
  retry(3, backoff=exponential, base_delay_seconds=0.2, max_delay_seconds=1.0)
  fallback {
    step cached_sources using recall_cached(query=query)
  }
  circuit_breaker(threshold=0.50, window=2, cooldown_seconds=5, ...)
}

tool search_indian_kanoon(query: string) -> list[Citation]
tool filter_relevance(citations: list[Citation], query: string) -> list[Citation]
tool summarize_claim(citations: list[Citation], query: string) -> Claim

workflow legal_brief(query: string) -> Claim {
  step sources using search_indian_kanoon(query)
  step relevant using filter_relevance(citations=sources, query=query)
  let brief: Claim = summarize_claim(citations=relevant, query=query)
  return brief
}
```

This workflow demonstrates:
- **Type safety**: `list[Citation]`, `Claim` are strongly typed
- **Fault tolerance**: `retry`, `fallback`, `circuit_breaker` policies
- **Structured steps**: `step sources using...` (named execution with tracing)
- **Memory**: `recall_cached` provides fallback if primary search fails

## Step 3: Run the Workflow (2 min)

```bash
# Execute the legal_brief workflow with built-in demo tools
agentscript run examples/legal_research.as \
  --demo legal \
  --workflow legal_brief \
  --arg 'query="BNS theft appeal"' \
  --mode retry \
  --trace tests/my_first_run.sqlite

# Output:
# ✓ Execution complete
# ✓ Trace saved to tests/my_first_run.sqlite
# ✓ Result: Claim(confidence=0.95, text="...")
```

**What just happened?**
1. AgentScript lexed the `.as` file (tokenization)
2. Parsed it (syntax validation)
3. Ran semantic analysis (type checking, scope validation)
4. Lowered to IR (intermediate representation)
5. Executed the workflow using the async runtime
6. Recorded every tool call in JSONL + SQLite formats
7. Applied redaction to sensitive data (API keys, PII)
8. Returned the typed result

## Step 4: Inspect the Execution Trace (2 min)

### Option A: CLI Dashboard (lightweight)

```bash
# Dump the trace as pretty-printed JSON
agentscript dashboard tests/my_first_run.sqlite --dump-json

# Output:
# {
#   "run_id": "legal_brief_20260405_120000",
#   "workflow_name": "legal_brief",
#   "status": "success",
#   "events": [
#     {
#       "type": "TOOL_CALL",
#       "tool_name": "search_indian_kanoon",
#       "arguments": {"query": "BNS theft appeal"},
#       "timestamp": "2026-04-05T12:00:01.234Z"
#     },
#     {
#       "type": "TOOL_RESULT",
#       "tool_name": "search_indian_kanoon",
#       "ok": true,
#       "status_code": 200,
#       "response": [...],
#       "latency_ms": 1523,
#       "retry_count": 0
#     },
#     ...
#   ]
# }
```

### Option B: Interactive Dashboard (full-featured)

```bash
# Start FastAPI dashboard
agentscript dashboard tests/my_first_run.sqlite

# Opens http://localhost:8000
# Shows:
# - Timeline of tool calls
# - Circuit breaker state transitions
# - Memory operations
# - Latency breakdown
# - Comparison against replayed execution
```

**What to look for:**
- **Latency**: How long did each tool invocation take?
- **Retries**: Were any calls retried due to transient failure?
- **Circuit breaker**: Did it enter Open/Half-Open states?
- **Memory**: What was stored/retrieved from session or semantic memory?

## Step 5: Write Your First Custom Workflow (2 min)

Create a new file `my_workflow.as`:

```agentscript
// A simple workflow with typed outputs
agent fast {
  retry(2, backoff=exponential, base_delay_seconds=0.1, max_delay_seconds=0.5)
}

tool translate(text: string, language: string) -> string
tool detect_language(text: string) -> string

workflow translate_and_detect(text: string, target_language: string) -> Intent {
  step detected using detect_language(text)
  step translated using translate(text, target_language)
  let intent: Intent = Intent(name=translated, score=0.9)
  return intent
}
```

Compile it:

```bash
agentscript check my_workflow.as

# Output:
# ✓ Lexical analysis passed
# ✓ Syntax analysis passed
# ✓ Semantic analysis passed (all types valid)
```

## Step 6: Register Custom Tools in Python (2 min)

Create `my_tools.py`:

```python
import asyncio
from agentscript.runtime import AsyncInterpreter, ToolRegistry, compile_runtime_program

# Read the workflow DSL
with open("my_workflow.as") as f:
    source = f.read()

# Create tool registry
registry = ToolRegistry()

# Implement translate tool
@registry.tool()
async def translate(text: str, language: str) -> str:
    # In real use: call translation API (Google Translate, DeepL, etc.)
    return f"[Translated to {language}] {text}"

# Implement detect_language tool
@registry.tool()
async def detect_language(text: str) -> str:
    # In real use: call language detection API
    return "en"  # English

# Compile and execute
program = compile_runtime_program(source)
result = asyncio.run(
    AsyncInterpreter(program, tools=registry).run_workflow(
        "translate_and_detect",
        arguments={"text": "Hello", "target_language": "Spanish"},
    )
)

print(f"Result: {result}")  # Intent(name="[Translated to Spanish] Hello", score=0.9)
```

Run it:

```bash
python my_tools.py
# Result: Intent(name="[Translated to Spanish] Hello", score=0.9)
```

## Step 7: Enable Observability (1 min)

### OpenTelemetry Spans

Set the environment variable and run again:

```bash
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
agentscript run examples/legal_research.as \
  --demo legal \
  --workflow legal_brief \
  --arg 'query="BNS theft appeal"' \
  --trace tests/otel_run.sqlite
```

If you have an OTel collector running (Jaeger, Tempo, Datadog agent), traces are exported automatically.

**Verify:** Export to Jaeger UI (if running locally on port 16686):
```
http://localhost:16686/search
```

### LangSmith Integration (Optional)

Set your LangSmith API key:

```bash
export LANGSMITH_API_KEY=ls_...
export LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Traces are now sent to LangSmith for semantic debugging. View them at:
```
https://smith.langchain.com/hub
```

## Step 8: Deterministic Replay (1 min)

Demonstrate the power of deterministic replay:

```bash
# Replay the execution from step 3
agentscript replay tests/my_first_run.sqlite

# Output:
# ✓ Loaded trace: legal_brief_20260405_120000
# ✓ Virtualized clock (timestamps from trace)
# ✓ Disabled live tool execution
# ✓ Stepped through IR instructions
# ✓ Byte-identical output: Claim(confidence=0.95, text="...")
# ✓ Replay fidelity: 100%
```

**Key insight:** The replay uses the same execution trace, so:
- No API calls (faster, no rate limits)
- No model inference (same outputs: deterministic)
- No timestamp surprises (time is frozen to trace values)
- Can step through frame-by-frame to debug exactly where reasoning diverged

## Step 9: Docker Observability Stack (Optional, 2 min)

For the full experience with React dashboard, trace store, and optional OTel backend:

```bash
# Start the full stack
docker compose up --build

# Waits for:
# - Python backend with FastAPI (http://localhost:8000)
# - React dashboard frontend
# - SQLite trace store
# - Optional: Jaeger, Prometheus collectors
```

Open http://localhost:8000 and:
1. Run a workflow
2. Watch traces populate in real-time
3. Inspect timeline, memory, and circuit breaker state
4. Trigger deterministic replay from the UI

## Next Steps

1. **[ARCHITECTURE.md](./ARCHITECTURE.md)** — Deep dive into compiler pipeline, runtime engine, fault tolerance patterns
2. **[language-spec.md](./language-spec.md)** — Complete DSL reference (syntax, types, operators)
3. **[API_REFERENCE.md](./API_REFERENCE.md)** — FastAPI dashboard endpoints
4. **[OPERATIONS.md](./OPERATIONS.md)** — Production deployment and monitoring

## Common Patterns

### Pattern 1: Retry with Fallback

```agentscript
agent resilient {
  retry(3, backoff=exponential)
  fallback {
    step result using cached_answer(query)
  }
}
```

### Pattern 2: Circuit Breaker Protection

```agentscript
agent protected {
  circuit_breaker(threshold=0.5, window=10, cooldown_seconds=30)
}

workflow main() -> string {
  step result using flaky_external_service()
  return result
}
```

When flaky_external_service fails >50% of the time over 10 calls:
- Circuit opens (all requests fail immediately)
- After 30s cooldown, probes with 1 request to check recovery
- If probe succeeds, circuit closes and normal operation resumes

### Pattern 3: Semantic Memory + Vector Search

```agentscript
workflow remember(fact: string, key: string) {
  store_memory(key, fact)
}

workflow recall_similar(query: string) -> list[MemoryEntry] {
  return mem_search(query)  // Embeddings-based retrieval
}
```

(Requires `pip install -e .[memory]` for Chroma embeddings)

### Pattern 4: Type-Safe LLM Outputs

```agentscript
type UserIntent {
  action: string
  confidence: float
  entities: list[string]
}

tool classify_intent(user_input: string) -> UserIntent

workflow process_request(user_input: string) -> UserIntent {
  step intent using classify_intent(user_input)
  // intent is type-safe; compiler verifies all reads
  return intent
}
```

## Troubleshooting

### "Tool not registered" error

```
Error: ToolNotRegisteredError: 'my_tool' not found in registry
```

**Solution:** Ensure your `@registry.tool()` decorator matches the tool name in the DSL:

```python
@registry.tool()
def my_tool(arg: str) -> str:
    return arg
```

```agentscript
tool my_tool(arg: string) -> string
```

### "Type mismatch" error

```
Error: SemanticAnalysisError: Expected Citation but got string
```

**Solution:** Check your type annotations in both DSL and Python tool implementations:

```agentscript
tool search() -> list[Citation]  // Returns list of Citation objects
```

```python
@registry.tool()
def search() -> list[dict]:  # Should match: list[Citation]
    return [{"source": "...", "text": "...", "url": None}]
```

### Circuit breaker never opens

Check your thresholds:

```agentscript
circuit_breaker(threshold=0.5, min_calls=10)
// Requires at least 10 calls before evaluating threshold
```

If tool only called 5 times, circuit stays in CLOSED state.

## Questions?

- **Documentation:** See [docs/](./docs/) directory
- **Issues:** GitHub Issues
- **Community:** Discussion forum (TBD)
