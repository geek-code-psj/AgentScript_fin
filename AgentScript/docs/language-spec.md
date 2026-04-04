# AgentScript Language Specification (v0.1)

## Overview

AgentScript is a domain-specific language for building agentic workflows with first-class support for:

- **Typed LLM Artifacts** — Claim, Citation, Intent, Embedding are language primitives with compile-time checking
- **Observable Execution** — Every step emits structured events (JSONL traces, OpenTelemetry spans)
- **In-Language Fault Tolerance** — retry, fallback, circuit_breaker declared alongside workflows (not scattered Python boilerplate)
- **Deterministic Replay** — Traces drive byte-identical replays; system clock is virtualized; no external variation

**Status:** Currently implements Week 8 milestone with lexer, parser, semantic analyzer, IR lowering, and async interpreter.

---

## Table of Contents

1. [Lexical Rules](#lexical-rules)
2. [Grammar (BNF)](#grammar-bnf)
3. [Type System](#type-system)
4. [Declarations](#declarations)
5. [Statements](#statements)
6. [Fault Tolerance Policies](#fault-tolerance-policies)
7. [Memory and Semantics](#memory-and-semantics)
8. [Examples](#examples)

---

## Lexical Rules

### Comments

Line comments start with `//` or `#`:

```agentscript
// This is a line comment
agent my_agent { }  # Also a comment

# Full-line comment
workflow main() {
  let x: int = 42  // inline comment
}
```

### Whitespace

Whitespace (space, tab, newline) separates tokens but is otherwise ignored.

### Identifiers

Identifiers start with letter or underscore, followed by letters, digits, or underscores:

```text
[A-Za-z_][A-Za-z0-9_]*
```

Valid: `legal_researcher`, `_private`, `Tool123`
Invalid: `123abc`, `legal-researcher`, `my agent`

### Keywords (Reserved)

```text
agent        // Agent policy declaration
workflow     // Workflow declaration
tool         // Tool signature declaration
type         // Custom type declaration (planned)
let          // Variable binding
step         // Named tool invocation
using        // Tool invocation keyword
if           // Conditional branch
else         // Else branch
return       // Return from workflow
import       // Module import (planned)
retry        // Retry policy
fallback     // Fallback path
circuit_breaker  // Circuit breaker policy
memory       // Memory operation keyword
emits        // Event emission (planned)
true, false  // Boolean literals
null         // Null literal
```

### Literals

**Strings** — Double-quoted, with escape sequences:
```text
"hello"
"escape quote: \" and backslash: \\"
"newline: \n, tab: \t, return: \r"
```

**Numbers** — Integers and floats:
```text
123              // integer
0x1A             // hex integer
0.5, 3.14        // float
1e-10            // scientific notation
```

**Booleans:**
```text
true, false
```

**Null:**
```text
null
```

### Operators and Punctuation

```text
Arithmetic:    + - * / %
Comparison:    == != < <= > >=
Logical:       && || !
Assignment:    =
Type anno:     :
Separators:    , . ; :
Brackets:      ( ) { } [ ]
Arrow:         -> =>
```

---

## Grammar (BNF)

```ebnf
// Top-level program
program             = { declaration }

declaration         = agent_decl
                    | workflow_decl
                    | tool_decl
                    | type_decl
                    | import_stmt

// Agent declaration with fault-tolerance policies
agent_decl          = "agent" IDENT "{" { agent_item } "}"

agent_item          = retry_policy
                    | fallback_policy
                    | circuit_breaker_policy

retry_policy        = "retry" "(" NUMBER [ "," retry_arg { "," retry_arg } ] ")"
retry_arg           = "backoff" "=" backoff_type
                    | "base_delay_seconds" "=" NUMBER
                    | "max_delay_seconds" "=" NUMBER

backoff_type        = "exponential" | "linear"

fallback_policy     = "fallback" "{" { statement } "}"

circuit_breaker_policy = "circuit_breaker" "(" circuit_arg { "," circuit_arg } ")"
circuit_arg         = "threshold" "=" NUMBER
                    | "window" "=" NUMBER
                    | "cooldown_seconds" "=" NUMBER
                    | "half_open_max_calls" "=" NUMBER
                    | "min_calls" "=" NUMBER

// Workflow declaration
workflow_decl       = "workflow" IDENT "(" [ params ] ")" [ "->" type_ref ] "{" { statement } "}"

params              = param { "," param }
param               = IDENT ":" type_ref

// Tool signature
tool_decl           = "tool" IDENT "(" [ params ] ")" "->" type_ref

// Type declaration (planned extension)
type_decl           = "type" IDENT "{" { field } "}"
field               = IDENT ":" type_ref

// Import (planned extension)
import_stmt         = "import" STRING_LITERAL

// Type reference
type_ref            = base_type [ "[" type_ref "]" ]
base_type           = "string" | "int" | "float" | "bool" | "null"
                    | "Claim" | "Citation" | "Intent" | "Embedding" | "MemoryEntry"
                    | IDENT

// Statements
statement           = let_stmt
                    | step_stmt
                    | if_stmt
                    | return_stmt

let_stmt            = "let" IDENT ":" type_ref "=" expression

step_stmt           = "step" IDENT "using" call_expr

if_stmt             = "if" expression "{" { statement } "}" [ "else" "{" { statement } "}" ]

return_stmt         = "return" expression

// Expressions
expression          = comparison

comparison          = additive [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) additive ]

additive            = multiplicative { ( "+" | "-" ) multiplicative }

multiplicative      = unary { ( "*" | "/" | "%" ) unary }

unary               = [ "!" | "-" ] primary

primary             = LITERAL
                    | IDENT
                    | IDENT "(" [ call_args ] ")"
                    | "mem_search" "(" expression ")"
                    | "(" expression ")"
                    | array_literal
                    | dict_literal

call_expr           = IDENT "(" [ call_args ] ")"

call_args           = call_arg { "," call_arg }

call_arg            = [ IDENT "=" ] expression

array_literal       = "[" [ expression { "," expression } ] "]"

dict_literal        = "{" [ dict_pair { "," dict_pair } ] "}"

dict_pair           = STRING_LITERAL ":" expression
                    | IDENT ":" expression
```

---

## Type System

### Scalar Types

| Type | Literal Syntax | Notes |
|------|---|---|
| `string` | `"hello"` | UTF-8 strings, escaped |
| `int` | `123` | 64-bit signed integer |
| `float` | `3.14` | 64-bit IEEE 754 |
| `bool` | `true`, `false` | Boolean values |
| `null` | `null` | Null type (used in optional types) |

### Collection Types

| Type | Syntax | Notes |
|------|--------|-------|
| `list[T]` | `[1, 2, 3]` | Ordered, homogeneous collection |
| `dict[K, V]` | `{"a": 1}` | Key-value pairs (K, V must be comparable) |

### LLM-Native Types (Built-In)

These types represent AI artifacts and are first-class language constructs:

#### `Claim`
Structured representation of an extracted assertion.

```agentscript
type Claim {
  confidence: float      // 0.0 to 1.0
  text: string          // The claim itself
}
```

**Example:**
```agentscript
let claim: Claim = Claim(
  confidence=0.92,
  text="Section 103 of the BNS criminalizes theft"
)
```

#### `Citation`
Structured reference to source material.

```agentscript
type Citation {
  source: string        // Document name or URL
  span: string          // Quoted text from source
  url: string?          // Optional URL
}
```

**Example:**
```agentscript
let cite: Citation = Citation(
  source="Indian Kanoon",
  span="A person is guilty of theft if...",
  url="https://indiankanoon.org/..."
)
```

#### `Intent`
User intent classification.

```agentscript
type Intent {
  name: string          // Intent class (e.g., "ask_legal_question")
  score: float          // Confidence 0.0 to 1.0
}
```

#### `Embedding`
Dense vector representation (typically 768 or 1536 dimensions).

```agentscript
type Embedding {
  dim: int              // Vector dimension
  vector: list[float]   // Dense vector
}
```

#### `MemoryEntry`
Entry returned from semantic memory search.

```agentscript
type MemoryEntry {
  key: string           // Memory key
  value: string         // Stored value
  score: float          // Cosine similarity (0.0 to 1.0)
}
```

### Type Safety

AgentScript enforces types at:
1. **Compile time** — Semantic analyzer validates type correctness
2. **Runtime** — Tool invocations validate argument types; LLM-native types validated at schema boundary

Example type error:

```agentscript
workflow bad() -> string {
  let claim: Claim = "not a claim"  // ❌ Type error: expected Claim, got string
  return claim
}
```

### Optional Types (Planned)

Future syntax for nullable types:

```agentscript
let maybe_url: string? = null
```

---

## Declarations

### Agent Declaration

Declares a named agent with fault-tolerance policies:

```agentscript
agent resilient {
  retry(3, backoff=exponential, base_delay_seconds=0.2, max_delay_seconds=1.0)
  fallback {
    step cached using recall_cached(query=query)
  }
  circuit_breaker(threshold=0.5, window=10, cooldown_seconds=30)
}
```

**Policies** (applied in order):
1. `retry` — Bounded retries with backoff
2. `fallback` — Graceful degradation path
3. `circuit_breaker` — Prevent cascading failures

### Workflow Declaration

Declares a named, typed workflow:

```agentscript
workflow legal_brief(query: string) -> Claim {
  step sources using search_law(query=query)
  let relevant: list[Citation] = filter_citations(...)
  let summary: Claim = summarize_claim(citations=relevant, query=query)
  return summary
}
```

**Parameters:**
- Input parameters with types (e.g., `query: string`)

**Return type:**
- Single return type (can be scalar or LLM-native type)

**Body:**
- Sequence of statements (let, step, if, return)

### Tool Declaration

Declares an external tool interface:

```agentscript
tool search_law(query: string) -> list[Citation]
tool filter_citations(citations: list[Citation]) -> list[Citation]
tool summarize_claim(citations: list[Citation], query: string) -> Claim
```

Tool implementations are registered at runtime in Python via the `ToolRegistry`.

---

## Statements

### Let Statement (Variable Binding)

Bind an expression to a typed variable:

```agentscript
let sources: list[Citation] = search_law(query)
let count: int = 42
let claim: Claim = Claim(confidence=0.9, text="...")
```

**Type checking:**
- Right-hand side must be assignable to declared type
- Variable is immutable after binding (no reassignment)

### Step Statement (Named Tool Invocation)

Invoke a tool and bind result to a named step:

```agentscript
step sources using search_law(query=query)
step relevant using filter_citations(citations=sources)
```

**Equivalent to:**
```agentscript
let sources = search_law(query=query)
let relevant = filter_citations(citations=sources)
```

**Tracing:**
- Each `step` is a discrete trace event (TOOL_CALL + TOOL_RESULT)
- Enables replay at step granularity

### If Statement (Conditional Branch)

Conditional execution:

```agentscript
if count > 0 {
  step result using summarize_claim(citations=sources)
} else {
  step result using get_default_claim()
}
```

### Return Statement

Return from workflow:

```agentscript
return claim
```

Must match declared return type.

---

## Fault Tolerance Policies

Fault tolerance is declared at the agent level and applies to all tool invocations within workflows using that agent.

### Retry Policy

**Syntax:**
```agentscript
agent policy_name {
  retry(max_attempts, backoff=exponential|linear, base_delay_seconds=..., max_delay_seconds=...)
}
```

**Example:**
```agentscript
agent resilient {
  retry(3, backoff=exponential, base_delay_seconds=0.1, max_delay_seconds=2.0)
}
```

**Behavior:**
1. Tool call fails (HTTP error, timeout, exception)
2. Wait `delay = min(base_delay * 2^attempt, max_delay_seconds)`
3. Retry up to `max_attempts` times
4. If all retries exhausted, proceed to fallback or error

**Trace Capture:**
- Each attempt recorded in `TOOL_RESULT` with `retry_count` field
- Latency breakdown shows cumulative wait time

### Fallback Policy

**Syntax:**
```agentscript
agent policy_name {
  fallback {
    step result using fallback_tool(...)
  }
}
```

**Example:**
```agentscript
agent resilient {
  fallback {
    step result using cached_search(query=query)
  }
}
```

**Triggered when:**
- Retry counter exhausted
- Circuit breaker in Open state
- Tool not registered (optional behavior)

### Circuit Breaker Policy

**Syntax:**
```agentscript
agent policy_name {
  circuit_breaker(
    threshold=0.5,           // Failure rate threshold (0.0-1.0)
    window=10,               // Evaluate last N calls
    cooldown_seconds=30,     // Wait before half-open probing
    half_open_max_calls=1,   // Probe requests in half-open state
    min_calls=2              // Min calls before evaluating threshold
  )
}
```

**States:**

| State | Condition | Requests |
|-------|-----------|----------|
| CLOSED | Normal ops; failure rate < threshold | Pass through |
| OPEN | Failure rate > threshold | Immediately rejected; fallback executed |
| HALF_OPEN | Cooldown expired | Limited probes (1-3 requests) |

**Trace Capture:**
- Circuit state transitions logged
- Transition timestamp and reason recorded

---

## Memory and Semantics

### Session Memory

Simple key-value store, persisted per-run:

```agentscript
workflow store_note(fact: string, key: string) {
  store_memory(key, fact)
}
```

**Implementation:** SQLite `session_memory` table

### Semantic Memory Search

Embeddings-based retrieval (requires `pip install -e .[memory]`):

```agentscript
workflow find_precedents(query: string) -> list[MemoryEntry] {
  return mem_search(query)  // Returns semantically similar entries
}
```

**Implementation:**
- Uses Chroma vector database
- Embeddings from OpenAI (1536-dim) or custom
- Cosine similarity ranking
- Optional threshold filtering

**Type:**
```agentscript
type MemoryEntry {
  key: string
  value: string
  score: float  // Cosine similarity [0.0, 1.0]
}
```

---

## Examples

### Example 1: Simple Workflow

```agentscript
agent fast {
  retry(2, backoff=linear, base_delay_seconds=0.1, max_delay_seconds=0.5)
}

tool getUser(id: int) -> string
tool sendEmail(email: string, message: string) -> bool

workflow notify_user(user_id: int, message: string) -> bool {
  step email using getUser(id=user_id)
  step sent using sendEmail(email=email, message=message)
  return sent
}
```

### Example 2: Fallback Degradation

```agentscript
agent resilient {
  retry(3, backoff=exponential, base_delay_seconds=0.5)
  fallback {
    step result using get_cached_answer(query=query)
  }
}

tool search_external(query: string) -> string
tool get_cached_answer(query: string) -> string

workflow answer_question(query: string) -> string {
  step result using search_external(query=query)
  return result
}
```

When `search_external` fails after 3 retries, automatically falls back to `get_cached_answer`.

### Example 3: Circuit Breaker

```agentscript
agent protected {
  circuit_breaker(threshold=0.5, window=10, cooldown_seconds=60)
}

tool flaky_api(input: string) -> list[Citation]

workflow research(query: string) -> list[Citation] {
  step results using flaky_api(input=query)
  return results
}
```

When >50% of the last 10 calls fail:
1. Circuit enters OPEN state
2. All subsequent requests immediately fail
3. After 60 seconds, probes with limited requests
4. If successful, transitions back to CLOSED

### Example 4: LLM-Native Types

```agentscript
tool extract_claim(text: string) -> Claim
tool find_citations(claim: Claim) -> list[Citation]
tool validate_claim(claim: Claim, citations: list[Citation]) -> Intent

workflow analyze_document(text: string) -> Intent {
  step claim using extract_claim(text=text)
  step citations using find_citations(claim=claim)
  step validation using validate_claim(claim=claim, citations=citations)
  return validation
}
```

All steps are type-safe; compiler verifies Claim flows to find_citations, etc.

### Example 5: Memory Search

```agentscript
tool search_corpus(query: string) -> list[Citation]

workflow ask_with_memory(question: string) -> string {
  step memories using mem_search(question)
  step external using search_corpus(query=question)
  let all_sources: list[Citation] = combine(memories, external)
  return answer_with_sources(all_sources)
}
```

---

## Compiler Stages

1. **Lexer** ([src/agentscript/compiler/lexer.py](../src/agentscript/compiler/lexer.py))
   - Tokenize source → token stream
   - Performance: 263K tokens/sec

2. **Parser** ([src/agentscript/compiler/parser.py](../src/agentscript/compiler/parser.py))
   - Token stream → AST (recursive-descent)
   - Performance: ~1 ms for typical workflows

3. **Semantic Analyzer** ([src/agentscript/compiler/semantic_analyzer.py](../src/agentscript/compiler/semantic_analyzer.py))
   - Type checking, scope resolution, callable validation
   - Outputs symbol table

4. **IR Lowering** ([src/agentscript/compiler/ir.py](../src/agentscript/compiler/ir.py))
   - AST → flat IR with explicit TOOL_CALL/TOOL_RESULT units
   - Dead-code elimination
   - Performance: ~1 ms

5. **Runtime Execution** ([src/agentscript/runtime/interpreter.py](../src/agentscript/runtime/interpreter.py))
   - Async interpreter over lowered IR
   - Applies agent policies (retry, fallback, circuit_breaker)
   - Emits traces

---

## Future Extensions (Planned)

- **Custom types:** User-defined struct types
- **Pattern matching:** Match expressions for Intent discrimination
- **Async composition:** Explicit parallel tool invocations
- **Module system:** Import external workflow libraries
- **Generics:** Parameterized types (e.g., `list[T]`)
- **Error handling:** Try-catch for explicit error recovery
- **Macros:** Code generation helpers

---

## References

- [ARCHITECTURE.md](./ARCHITECTURE.md) — Compiler pipeline and runtime engine details
- [GETTING_STARTED.md](./GETTING_STARTED.md) — Tutorial with working examples
- [src/agentscript/compiler/](../src/agentscript/compiler/) — Implementation source code
