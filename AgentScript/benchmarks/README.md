# AgentScript Benchmarks

Run the benchmark harness from the repo root:

```bash
python benchmarks/run_benchmarks.py
```

It writes:

- `benchmarks/latest.json`
- `benchmarks/latest.md`

The harness measures:

- lexer throughput on repeated AgentScript source
- parser latency
- IR lowering latency
- runtime latency for the legal demo workflow
- memory search latency
