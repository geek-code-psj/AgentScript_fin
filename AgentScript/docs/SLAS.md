# AgentScript SLAs & Performance Metrics

Production Service Level Objectives (SLOs) and Key Performance Indicators (KPIs) for AgentScript deployments.

## Executive Summary

AgentScript commits to these measurable SLOs across four dimensions:

| Category | Metric | Target SLO | Measurement |
|----------|--------|-----------|------------|
| **Availability** | Workflow success rate | 99.5% | Monthly uptime |
| **Latency** | End-to-end (p95) | < 5 seconds | Happy path execution |
| **Quality** | Tool accuracy | 99.5% | Successful invocations with retries |
| **Security** | PII redaction catch | 99.9% | Sensitive data masking |
| **Observability** | Trace fidelity | 100% | Deterministic replay success |

---

## 1. Availability SLO

### Core Commitment

**99.5% workflow success rate** (monthly uptime equivalent: 99.5%, or ~3.6 hours downtime/month)

### Definition

"Successful" means:
- Request accepted by AgentScript (HTTP 200-299)
- Workflow execution completes (success or controlled fallback)
- Output returned within SLA timeout

"Failed" means:
- Unrecoverable errors (HTTP 500, timeout after retries, circuit breaker open > 5 minutes)
- Data corruption or loss
- Service unavailable (all pods down)

### Factors Affecting Availability

| Factor | Impact | Mitigation |
|--------|--------|-----------|
| Downstream service outage | ✓ counted as failure | Circuit breaker + fallback path |
| LLM API rate limits | ✓ counted if no retry | Exponential backoff + queuing |
| Database unavailable | ✗ NOT counted | Always keep SQLite copy locally |
| Network flake (< 5s) | ✗ NOT counted | Automatic retries absorb it |
| PII redaction failure | ✓ counted as failure | Fail-safe: never expose raw PII |

### How It's Measured

```promql
# Prometheus query
(
  rate(agentscript_workflow_success_total[30d])
  /
  rate(agentscript_workflow_total[30d])
) * 100
```

**Alert if:** < 99%

### Excluded from SLO

The following do NOT count against SLO:

- **Customer errors** (invalid input, malformed agent definition)
  - HTTP 400-404 responses
  - Validation failures on user workflow
  
- **Scheduled maintenance** (with advance notice)
  - Maximum 4 hours/month during off-hours
  - Coordinated with customers

- **Force majeure** (external infrastructure failure)
  - Cloud provider outage (declared by provider)
  - DDoS attack (ISP level)

---

## 2. Latency SLO

### Core Commitment

**p95 end-to-end latency < 5 seconds** (happy path: single tool call)

Percentile breakdown:
- **p50 (median):** < 2 seconds
- **p95:** < 5 seconds
- **p99:** < 10 seconds
- **p999:** < 30 seconds

### Definition

"End-to-end latency" = time from request received to response sent (includes):
- Request parsing
- Workflow compilation (if needed)
- All tool invocations
- Response serialization
- Network transmission

"Happy path" = single tool call with no retries, no circuit breaker

### Factors Affecting Latency

| Factor | Typical Impact | Best Case | Worst Case |
|--------|---|---|---|
| Lexer + Parser | 10-50 ms | 5 ms | 100 ms |
| Tool call (1x) | 500-2000 ms | 200 ms | 30 s (timeout) |
| Tool call (retry) | +500 ms per attempt | | |
| Trace export (async) | 0 ms (async) | | |
| PII redaction | 5-20 ms per trace | | |
| **Total (p95)** | **1.5-2.5 sec** | **500 ms** | **5 sec** |

### Latency Budget

Where your 5 seconds goes:

```
Request → Parse & Compile     50 ms (1%)
         → Load agent memory   50 ms (1%)
         → Tool call 1      1500 ms (30%)
         → Tool call 2      1500 ms (30%)
         → Synthesize       500 ms (10%)
         → Trace export     300 ms (6%)
         → PII redaction     20 ms (0.4%)
         → Response send    80 ms (1.6%)
         ─────────────────────────────
         TOTAL            ~4500 ms (90%)
              Headroom:     500 ms (10%)
```

### How It's Measured

```promql
histogram_quantile(0.95, 
  rate(agentscript_workflow_duration_seconds_bucket[5m])
)
```

Alerts:
- **P95 > 5s for 5 minutes:** Alert, investigate
- **P95 > 7s for 10 minutes:** Critical, page on-call
- **P99 > 30s for 5 minutes:** Critical, consider circuit breaker

### Optimization Strategies

| Strategy | Expected Improvement | Trade-off |
|----------|-----|---------|
| Reduce trace sampling (50%) | -5% latency | Less observability |
| Parallelize tool calls | -30% latency | More resource usage |
| Skip non-critical tools | -10% latency | Reduced accuracy |
| Use cached results | 90% faster (cache hits) | Staleness risk |
| Heuristic fallback | 2s faster | Lower confidence |

---

## 3. Quality SLO

### Core Commitment

**99.5% tool invocation success rate** (after all retries)

### Definition

"Successful" tool invocation = tool returns:
- Any response code (200, 201, 202, etc.)
- Within timeout threshold
- Parseable response

"Failed" =:
- Timeout after retries
- Unparseable response
- Persistent 5xx errors

### Retry Policy

Retries are AUTOMATIC and TRANSPARENT:

```
Attempt 1: --[call]--> [timeout/5xx]
           wait 100ms
Attempt 2: --[call]--> [timeout/5xx]
           wait 200ms
Attempt 3: --[call]--> [timeout/5xx]
           wait 400ms
Attempt 4: --[call]--> Failed
           Circuit breaker: OPEN
```

**Retryable errors:** 500, 502, 503, 504, timeout, connection refused  
**Non-retryable:** 400, 401, 403, 404, 422

### How It's Measured

```promql
(
  rate(agentscript_tool_success_total[30d])
  /
  rate(agentscript_tool_invocations_total[30d])
) * 100
```

**Alert if:** < 98% (1% margin before SLO breach)

### Tools Covered by SLO

This SLO applies to:
- ✅ All registered tools in the tool registry
- ✅ External API calls (search, enrichment, etc.)
- ❌ User-provided tools (customers responsible for their reliability)
- ❌ Demo/example tools (for testing only)

---

## 4. PII Redaction SLO

### Core Commitment

**99.9% PII redaction catch rate** (< 0.1% of sensitive data exposed)

### Protected Data Types

AgentScript redacts these patterns:

| Type | Pattern | Example | Redacted |
|------|---------|---------|----------|
| Email | `[\w\.-]+@[\w\.-]+\.\w+` | john@example.com | [EMAIL_REDACTED] |
| Phone | `\d{3}-\d{3}-\d{4}` | 555-867-5309 | [PHONE_REDACTED] |
| SSN | `\d{3}-\d{2}-\d{4}` | 123-45-6789 | [SSN_REDACTED] |
| API Key | `sk_[a-zA-Z0-9]{32}` | sk_live_12345... | [API_KEY_REDACTED] |
| Bearer Token | `Bearer [a-zA-Z0-9\._\-]{20,}` | Bearer eyJ0XYZ...| [TOKEN_REDACTED] |
| Credit Card | `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` | 4532-1111-2222-3333 | [CC_REDACTED] |
| URL Creds | `https://([^:]+):([^@]+)@` | https://user:pass@host | https://[CREDS_REDACTED]@ |
| IP Address | `\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}` | 192.168.1.1 | [IP_REDACTED] |

### Verification

Monthly:

```bash
# Run regression test with synthetic PII
python -m agentscript.test.pii_redaction \
  --dataset synthetic_pii_dataset.jsonl \
  --output redaction_report.json

# Check report
cat redaction_report.json
# Expected: {
#   "total_pii_instances": 10000,
#   "redacted_count": 9991,
#   "missed_count": 9,
#   "catch_rate": 0.9991,
#   "slo_met": true
# }
```

### How It's Measured

```
catch_rate = (redacted_count / total_pii_instances) * 100
SLO breach if: catch_rate < 99.9% for any 7-day period
```

---

## 5. Observability & Replay Fidelity SLO

### Core Commitment

**100% deterministic trace replay success** (byte-identical outputs)

### Definition

"Deterministic replay" = re-executing a workflow with:
- Same input arguments
- Same recorded tool results (injected)
- Same model configuration snapshot

**Success** = output matches the original execution byte-for-byte

**Failure** = output differs (usually due to model change)

### Why This Matters

Replay enables:
- **Debugging:** Step through execution to find bugs
- **Forensics:** Audit exactly what happened
- **Testing:** Validate model/config changes without calling tools
- **Cost reduction:** Skip expensive tool calls during iteration

### How It's Measured

Monthly replay audit:

```bash
# Sample 100 random historical traces
python -m agentscript.cli audit.sample-traces \
  --count 100 \
  --output sampled_traces.jsonl

# Replay each with original configuration
for trace in sampled_traces.jsonl; do
  python -m agentscript.cli replay \
    --trace-file $trace \
    --mode deterministic
done

# Verify byte-identity
# Expected: 100% success (all replays match original outputs)
```

---

## 6. Tradeoffs Reference

### Latency vs Observability

| Setting | Latency Impact | Trace Quality | Recommendation |
|---------|---|---|---|
| 100% trace sampling | Baseline | Complete | Production |
| 50% trace sampling | -5% | Good | High-traffic environments |
| 10% trace sampling | -8% | Fair | Very high volume |
| 0% (disabled) | -15% | None | Only for emergency |

### Throughput vs Accuracy

| Config | Throughput | Accuracy | Use Case |
|--------|---|---|---|
| Full pipeline | 100 req/s | 95%+ | Balanced |
| Skip enrichment | 150 req/s | 85% | High volume |
| Fast heuristics | 500 req/s | 70% | Real-time needs |

### Cost vs Resilience

| Strategy | Cost | Resilience | Recovery Time |
|----------|---|---|---|
| Single replica | Baseline | Low | N/A (no HA) |
| 3 replicas | 3x | High | < 30 sec |
| Multi-region | 6x | Very high | < 5 sec |

---

## 7. SLO Dashboard

Sample Grafana queries:

```
## Availability
rate(agentscript_workflow_success_total[30d]) / rate(agentscript_workflow_total[30d])

## Latency (p95)
histogram_quantile(0.95, rate(agentscript_workflow_duration_seconds_bucket[5m]))

## Tool success rate
rate(agentscript_tool_success_total[30d]) / rate(agentscript_tool_invocations_total[30d])

## Circuit breaker state
agentscript_circuit_breaker_state{state="open"}

## Trace export latency
histogram_quantile(0.95, rate(agentscript_otel_export_duration_seconds_bucket[5m]))
```

---

## 8. SLO Error Budget

Your monthly "error budget" (how many failures you can afford):

**99.5% SLO = 3.6 hours/month downtime allowed**

If you've used 2 hours already and there's a 1-hour incident, you're now **in breach**.

Plan accordingly:

- Reserve < 1 hour for planned maintenance
- Reserve < 1 hour for unexpected outages
- Try to stay under your budget to maintain goodwill

---

## 9. Reporting & Reviews

### Weekly Status

- Success rate (target: > 99.9%)
- P95 latency (target: < 3.5 sec, headroom before 5s limit)
- Circuit breaker trips (target: 0)
- Any SLO breaches?

### Monthly Review

- Full SLO recap (all metrics)
- Error budget consumption
- Top failure modes and fixes
- Capacity planning for next quarter

### Incident Postmortems

For any SLO breach:

```markdown
# Incident: Site Unavailable (2024-04-05)

## Timeline
- 14:23 UTC: Downstream search_law service degraded
- 14:27 UTC: Circuit breaker opened (4 failures)
- 14:32 UTC: 45% of workflows failing (15 min duration)
- 14:45 UTC: search_law service recovered, circuit auto-recovered
- 14:50 UTC: All traffic back to baseline

## Impact
- Duration: 23 minutes
- Affected workflows: search, enrichment, synthesis
- Success rate dropped to 55% (45% failure rate)
- SLO breach: Yes (99.5% target violated)

## Root Cause
Search service deployment had a memory leak. After restart, recovered.

## Actions
- Implement memory monitoring alerts for external services
- Add fallback heuristic for search failures
- Increase circuit breaker test frequency
```

---

## Reference

- See [OPERATIONS.md](OPERATIONS.md) for how to maintain these SLOs
- See [ARCHITECTURE.md](ARCHITECTURE.md) for system design that enables these SLOs
- See [benchmarks/latest.md](../benchmarks/latest.md) for actual measured performance

---

**Last updated:** 2024-04-05  
**Next review:** 2024-05-05
