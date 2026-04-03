# AgentScript Language Spec (Draft v0.1)

## Goals

AgentScript is a domain-specific language for building agentic workflows with first-class support for:

- typed LLM artifacts
- observable execution
- in-language fault tolerance
- deterministic replay from runtime traces

## Design Principles

### 1. LLM-native types

The language treats AI artifacts as values with structure, not raw strings.

Built-in conceptual types include:

- `Claim(confidence: float, text: string)`
- `Citation(source: string, span: string, url: string?)`
- `Intent(name: string, score: float)`
- `Embedding(dim: int, vector: list[float])`
- `MemoryEntry(key: string, value: string, score: float)`

These types are part of the language story, so future compiler passes can validate and visualize type flow.

### 2. Fault tolerance in the language

Runtime resilience is expressed declaratively, not wrapped around calls in host-language glue code.

Examples:

```agentscript
agent legal_researcher {
  retry(3, backoff=exponential, base_delay_seconds=0.2, max_delay_seconds=1.0)
  circuit_breaker(threshold=0.50, window=2, cooldown_seconds=5, half_open_max_calls=1, min_calls=2)
  fallback {
    step cached_sources using recall_cached(query=query)
  }
}
```

### 3. Replay-first runtime

Every execution is intended to emit structured traces that support deterministic replay. Replay is a core runtime contract, not a debugging afterthought.

## Core Lexical Rules

### Comments

- `//` starts a line comment
- `#` starts a line comment

### Identifiers

Identifiers follow this pattern:

```text
[A-Za-z_][A-Za-z0-9_]*
```

### Literals

- strings: double-quoted, escaped with `\"`, `\\`, `\n`, `\t`, `\r`
- integers: `123`
- floats: `0.50`, `42.0`
- booleans: `true`, `false`
- null: `null`

## Reserved Keywords

The initial keyword set is:

```text
agent workflow tool type let step using if else return import
retry fallback circuit_breaker memory emits true false null
```

## Punctuation and Operators

```text
( ) { } [ ] : , . ;
= == != < <= > >= -> =>
+ - * /
```

## Draft Syntax

### Tool Signature

```agentscript
tool search_law(query: string) -> list[Citation]
```

### Workflow

```agentscript
workflow legal_brief(query: string) -> Claim {
  let sources: list[Citation] = search_law(query)
  let summary: Claim = summarize_claim(sources)
  return summary
}
```

### Built-In Memory Search

```agentscript
workflow recall(query: string) -> list[MemoryEntry] {
  let note: string = "BNS section 103 theft punishment"
  return mem_search(query)
}
```

### Fault Policy

```agentscript
agent legal_researcher {
  retry(3, backoff=exponential)
  fallback {
    step degraded_answer using summarize_minimally
  }
  circuit_breaker(threshold=0.50)
}
```

## Planned Compiler Pipeline

1. Lexer
2. Recursive-descent parser
3. AST validation
4. Semantic analysis
5. IR lowering
6. Runtime execution
7. Memory search and trace emission
8. Replay from persisted traces

The current IR uses explicit `TOOL_CALL` and `TOOL_RESULT` instructions so replay can capture tool requests and outcomes as first-class runtime units.

## Visible Demo Requirements

The demo must visibly prove:

1. Type errors are real and user-facing.
2. Retry, fallback, and circuit breaker behavior actually run.
3. Replay reproduces the same execution path from trace data.
4. Observability feels like DevTools, not plain logs.
