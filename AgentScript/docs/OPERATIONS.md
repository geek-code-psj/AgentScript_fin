# AgentScript Production Operations Manual

Operational guidelines for deploying, monitoring, and maintaining AgentScript in production environments.

## Table of Contents

1. [Day-2 Operations](#day-2-operations)
2. [Monitoring & Alerting](#monitoring--alerting)
3. [Circuit Breaker Tuning](#circuit-breaker-tuning)
4. [Trace Management](#trace-management)
5. [Incident Response](#incident-response)
6. [Disaster Recovery](#disaster-recovery)
7. [Performance Tuning](#performance-tuning)

---

## Day-2 Operations

### Health Checks

Perform these daily:

```bash
# Check all pods are running
kubectl get pods -n agentscript --field-selector=status.phase=Running

# Verify metrics are flowing
kubectl exec -it my-agentscript-0 -n agentscript -- \
  curl http://localhost:8080/metrics | grep agentscript_workflow | head

# Check trace store connectivity
kubectl exec -it my-agentscript-0 -n agentscript -- \
  sqlite3 /var/lib/agentscript/traces/traces.db "SELECT COUNT(*) FROM traces;"

# Verify OTel collector is receiving data
# (Check Jaeger UI or Datadog for recent traces)
```

### Capacity Planning

Monitor these metrics weekly:

| Metric | Target | Action if Exceeded |
|--------|--------|-------------------|
| PVC utilization | < 80% | Expand PVC or archive traces |
| Pod memory | < 75% of request | Adjust requests or scale horizontally |
| CPU utilization | < 70% | Scale horizontally (HPA) |
| P95 latency | < 5 seconds | Tune circuit breaker or add replicas |

### Backup Strategy

```bash
# Daily snapshot of trace database
kubectl exec my-agentscript-0 -n agentscript -- \
  cp /var/lib/agentscript/traces/traces.db \
  /backups/traces-$(date +%Y-%m-%d).db

# Weekly full backup to cloud storage
kubectl exec my-agentscript-0 -n agentscript -- \
  tar czf - /var/lib/agentscript/traces | \
  aws s3 cp - s3://agentscript-backups/traces-$(date +%Y-week-%V).tar.gz
```

---

## Monitoring & Alerting

### Key Metrics to Monitor

#### 1. Workflow Success Rate

**Prometheus query:**
```promql
rate(agentscript_workflow_success_total[5m]) / 
  rate(agentscript_workflow_total[5m])
```

**Target SLO:** ≥ 99.5% (excluding customer errors)

**Alert if:** < 99%

```yaml
# Prometheus alert rule
- alert: LowWorkflowSuccessRate
  expr: |
    rate(agentscript_workflow_success_total[5m]) / 
    rate(agentscript_workflow_total[5m]) < 0.99
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "Workflow success rate {{ $value | humanizePercentage }}"
    action: "Check logs for common failure patterns"
```

#### 2. End-to-End Latency

**Prometheus query:**
```promql
histogram_quantile(0.95, rate(agentscript_workflow_duration_seconds_bucket[5m]))
```

**Target SLO:** p95 < 5 seconds (happy path)

**Alert if:** p95 > 7 seconds for 5 minutes

#### 3. Circuit Breaker State

**Prometheus query:**
```promql
agentscript_circuit_breaker_state{state="open"}
```

**Target SLO:** 0 circuits open during normal operations

**Alert if:** Any circuit open > 2 minutes

#### 4. Tool Invocation Success

**Prometheus query:**
```promql
rate(agentscript_tool_success_total[5m]) / 
rate(agentscript_tool_invocations_total[5m])
```

**Target SLO:** ≥ 99.5% (with retries)

**Alert if:** < 98%

#### 5. Trace Export Success

**Prometheus query:**
```promql
rate(agentscript_otel_export_success[1m])
```

**Target SLO:** 100% (traces should always be exported)

**Alert if:** < 99% for 2 minutes (indicates observability loss)

### Datadog Integration

Configure these monitors in Datadog:

```yaml
# Monitor: High Error Rate
name: "AgentScript High Error Rate"
type: metric alert
query: |
  avg(last_5m):avg:agentscript.workflow.errors{*} / 
  avg:agentscript.workflow.total{*} > 0.05
notify_no_data: true
no_data_timeframe: 15

# Monitor: High Latency
name: "AgentScript High P95 Latency"
type: metric alert
query: |
  avg(last_5m):percentile:agentscript.workflow.duration{*} > 5000
threshold: 5000
unit: milliseconds

# Monitor: Circuit Breaker Open
name: "AgentScript Circuit Breaker Open"
type: metric alert
query: |
  max(last_2m):agentscript.circuit_breaker.state{state:open} > 0
```

### Custom Dashboards

Create dashboards showing:
1. **Workflow success/failure rates** (by workflow, by tool)
2. **Latency percentiles** (p50, p95, p99, p999)
3. **Circuit breaker state** (open/half-open/closed per tool)
4. **Resource utilization** (CPU, memory, disk)
5. **Trace export pipeline** (latency, success rate)
6. **Error breakdown** (by error type, by recoverable/permanent)

---

## Circuit Breaker Tuning

AgentScript uses adaptive circuit breakers for each tool to prevent cascading failures.

### Configuration

Edit `values.yaml`:

```yaml
circuitBreaker:
  # Failure rate threshold (%)
  failureRateThreshold: 50  # Open if 50% of recent calls failed

  # Slow call rate threshold (%)
  slowCallRateThreshold: 50  # Open if 50% of calls are slow

  # What counts as "slow"
  slowCallDurationThreshold: 2000  # ms

  # Minimum calls before opening
  minimumNumberOfCalls: 10

  # Calls allowed in HALF_OPEN state (testing recovery)
  permittedNumberOfCallsInHalfOpenState: 3

  # How long to wait before trying again
  waitDurationInOpenState: 30000  # ms (30 seconds)
```

### State Transitions

```
       minimumNumberOfCalls threshold reached
       AND (failureRate > threshold OR slowCallRate > threshold)
                        │
                        ▼
    ┌─────────────────────────────────────┐
    │ OPEN (fail fast, no calls allowed) │
    └────────────┬────────────────────────┘
                 │
                 │ waitDurationInOpenState elapsed
                 ▼
    ┌──────────────────────────────────────────┐
    │ HALF_OPEN (test recovery, limited calls) │
    └────────────┬─────────────────────────────┘
                 │
          ┌──────┴──────────────┐
          │                     │
    All permitted calls failed  All permitted calls succeeded
          │                     │
          ▼                     ▼
       OPEN               CLOSED (normal operation)
```

### Tuning Strategies

**Strategy 1: Conservative (Safety First)**
- Used for critical tools that must not fail
- Lower `failureRateThreshold` (25%)
- Longer `waitDurationInOpenState` (60 seconds)
- Example: Payment processor, data validation

```yaml
circuitBreaker:
  failureRateThreshold: 25
  minimumNumberOfCalls: 5
  waitDurationInOpenState: 60000
```

**Strategy 2: Aggressive (Throughput First)**
- Used for non-critical tools (search, enrichment)
- Higher `failureRateThreshold` (75%)
- Shorter `waitDurationInOpenState` (5 seconds)
- Fast recovery attempts

```yaml
circuitBreaker:
  failureRateThreshold: 75
  slowCallDurationThreshold: 5000  # tolerates slower calls
  waitDurationInOpenState: 5000
```

**Strategy 3: Balanced (Default)**
- Most tools should use this
- Good balance of safety and availability

### Monitoring Circuit Breakers

```bash
# Check current state of all circuits
kubectl logs my-agentscript-0 -n agentscript | \
  grep "circuit.breaker" | tail -20

# Export metrics for analysis
kubectl exec my-agentscript-0 -n agentscript -- \
  python -c "
import json
from agentscript.runtime.circuit_breaker import CircuitBreakerState
# Export current state
print(json.dumps({
    'tool': 'search_law',
    'state': 'CLOSED',
    'consecutive_failures': 0,
    'last_failure_time': None
}, indent=2))
"
```

### Recovering from Circuit Open State

If a circuit breaker is stuck open:

```bash
# 1. Identify the affected tool
kubectl logs my-agentscript-0 -n agentscript | grep "OPEN" | head -5

# 2. Check if the downstream service is back online
kubectl exec my-agentscript-0 -n agentscript -- \
  curl -v http://external-tool:8080/health

# 3. If service is healthy, wait for waitDurationInOpenState to pass
# The circuit will automatically transition to HALF_OPEN

# 4. Monitor recovery attempts
kubectl logs my-agentscript-0 -n agentscript -f | \
  grep "circuit.breaker.*HALF_OPEN\|circuit.breaker.*CLOSED"

# 5. If service is still down, manual recovery:
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli circuit-breaker reset search_law
```

---

## Trace Management

### Trace Retention Policy

Define your retention based on regulations and costs:

```yaml
# Short-term (hot): 30 days in SQLite/PostgreSQL
# Medium-term (warm): 90 days in cloud blob storage (S3/GCS)
# Long-term (cold): 1 year in archive (Glacier, Cloud Archive)

traceStore:
  sqlite:
    retentionDays: 30
    # Automatically delete traces older than 30 days
    
  backupSchedule: "0 2 * * *"  # Daily at 2 AM
```

### Archival

```bash
# Archive traces older than 60 days to S3
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli archive \
  --before-date $(date -d "60 days ago" +%Y-%m-%d) \
  --output-s3 s3://agentscript-archive/traces/

# Verify archive
aws s3 ls s3://agentscript-archive/traces/
```

### Compliance & Audit

For GDPR/CCPA, implement trace deletion on user request:

```bash
# Delete all traces for a specific user
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli delete-user-traces \
  --user-id user_12345

# Generate GDPR data export
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli export-user-traces \
  --user-id user_12345 \
  --output traces-user-12345.jsonl
```

---

## Incident Response

### Incident Severity Levels

| Level | SLO Impact | Response Time | Examples |
|-------|-----------|---------------|----------|
| P1 (Critical) | > 25% failure rate | 15 minutes | Complete outage, data loss |
| P2 (High) | 5-25% failure rate | 1 hour | Degraded performance, partial feature |
| P3 (Medium) | 1-5% failure rate | 4 hours | Specific tool failing, slow latency |
| P4 (Low) | < 1% failure rate | Next business day | Minor observability issues |

### Runbook: High Error Rate (P1)

```bash
# 1. Check current status
kubectl get pods -n agentscript
kubectl top pod -n agentscript

# 2. Identify failing workflows
kubectl logs my-agentscript-0 -n agentscript | \
  grep -E "ERROR|FAILED|exception" | tail -50

# 3. Check if external service is to blame
curl -v http://external-tool:8080/health
# If downstream is down, that's the root cause

# 4. Use deterministic replay to debug
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli replay \
  --run-id run_abc123def \
  --mode debug
# Step through execution to find divergence

# 5. Mitigation
# Option A: Scale to more replicas (faster processing)
kubectl scale statefulset my-agentscript --replicas=5 -n agentscript

# Option B: Adjust circuit breaker thresholds (if false positives)
kubectl set env statefulset/my-agentscript \
  -n agentscript \
  CIRCUIT_BREAKER_FAILURE_THRESHOLD=75

# Option C: Switch to fallback model
kubectl set env statefulset/my-agentscript \
  -n agentscript \
  FALLBACK_MODEL_ENABLED=true
```

### Runbook: High Latency (P2)

```bash
# 1. Check resource contention
kubectl top nodes
kubectl top pod -n agentscript

# 2. Identify slow steps
kubectl logs my-agentscript-0 -n agentscript | \
  grep "duration_ms" | awk -F'duration_ms=' '{print $2}' | sort -rn | head

# 3. Check external service latencies
# (Look at trace export timing)

# 4. Mitigation
# Option A: Scale horizontally
kubectl scale statefulset my-agentscript --replicas=5 -n agentscript

# Option B: Increase timeouts temporarily
kubectl set env statefulset/my-agentscript \
  -n agentscript \
  TOOL_TIMEOUT_MS=5000

# Option C: Skip non-critical tools
kubectl set env statefulset/my-agentscript \
  -n agentscript \
  SKIP_ENRICHMENT=true
```

### Runbook: Circuit Breaker Triggered (P2)

```bash
# 1. Identify the broken tool
kubectl describe statefulset my-agentscript -n agentscript | \
  grep -E "circuit.breaker.*OPEN"

# 2. Check why it's failing
# Assume tool_name is "search_law"
curl -v http://search-law-service:8080/health

# 3. If service is down, wait or escalate
# Circuit will auto-recover in waitDurationInOpenState (30s default)

# 4. Manual recovery if needed
kubectl exec my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli circuit-breaker reset search_law

# 5. Switch to fallback if recovery takes too long
kubectl set env statefulset/my-agentscript \
  -n agentscript \
  FALLBACK_STRATEGY=heuristic_rules
```

---

## Disaster Recovery

### RTO (Recovery Time Objective): < 5 minutes
### RPO (Recovery Point Objective): < 1 minute

### Backup Strategy

**Daily snapshots:**
```bash
# Run at 2 AM via cron
0 2 * * * kubectl exec my-agentscript-0 -n agentscript -- \
  cp /var/lib/agentscript/traces/traces.db \
  /backups/traces-$(date +\%Y-\%m-\%d).db
```

**Weekly full backups to cloud:**
```bash
0 3 * * 0 kubectl exec my-agentscript-0 -n agentscript -- \
  sh -c 'tar czf - /var/lib/agentscript/traces | \
  aws s3 cp - s3://agentscript-dr/traces-$(date +%Y-week-%V).tar.gz'
```

### Restore Procedure

**Scenario: Trace database is corrupted**

```bash
# 1. Identify the backup to restore
aws s3 ls s3://agentscript-dr/

# 2. Stop the current pods
kubectl scale statefulset my-agentscript --replicas=0 -n agentscript

# 3. Download and restore backup
kubectl exec -it my-agentscript-0 -n agentscript -- sh << EOF
aws s3 cp s3://agentscript-dr/traces-2024-week-10.tar.gz - | \
  tar xzf - -C /var/lib/agentscript/
EOF

# 4. Verify database integrity
kubectl exec my-agentscript-0 -n agentscript -- \
  sqlite3 /var/lib/agentscript/traces/traces.db "PRAGMA integrity_check;"

# 5. Restore replicas
kubectl scale statefulset my-agentscript --replicas=3 -n agentscript

# 6. Verify health
kubectl wait --for=condition=ready pod \
  -l app=agentscript \
  -n agentscript \
  --timeout=300s
```

### Multi-Region Failover

For HA across regions:

```bash
# Primary region (us-east-1) is down
# Switch to secondary region (eu-west-1)

# 1. Update DNS/load balancer to point to secondary
kubectl get svc -n agentscript-eu-west-1

# 2. Verify secondary can handle traffic
kubectl top pod -n agentscript-eu-west-1

# 3. Scale secondary if needed
kubectl scale statefulset my-agentscript --replicas=10 \
  -n agentscript-eu-west-1

# 4. Investigate primary outage
# (in parallel)
```

---

## Performance Tuning

### Compiler Optimization

Reduce parse time for large workflows:

```bash
# Cache compiled bytecode
export AGENTSCRIPT_CACHE_DIR=/var/cache/agentscript/

# Precompile common workflows on startup
python -m agentscript.cli precompile \
  /etc/agentscript/agents/legal_research.as
```

### Memory Optimization

```yaml
# Reduce memory footprint
env:
  TRACE_SAMPLING_RATE: 0.5  # Sample 50% of traces
  CACHE_MAX_ENTRIES: 1000   # Limit memory cache size
  GC_COLLECTION_INTERVAL: 300  # Garbage collect every 5m
```

### Latency Optimization

| Optimization | Impact | Trade-off |
|--------------|--------|-----------|
| Reduce trace sampling | -20% latency | Less observability |
| Disable PII redaction | -5% latency | Security risk |
| Use heuristic fallback | -2s latency | Accuracy loss |
| Parallelize tool calls | -30% latency | Resource usage |

### Reference Architecture

For **500 concurrent workflows/sec**:

```
┌─────────────────────────────────────────┐
│ 10 AgentScript pods (3 CPU, 4 GB RAM)   │
│ - 5 replicas base + 5 HPA headroom      │
├─────────────────────────────────────────┤
│ PostgreSQL 14 cluster (3 nodes)         │
│ - 16 CPU, 64 GB RAM per node            │
│ - Replication for HA                    │
├─────────────────────────────────────────┤
│ Jaeger OpenTelemetry backend            │
│ - Storage: 10 TB (30-day retention)     │
└─────────────────────────────────────────┘
```

---

**Last updated:** 2024-04-05
**Maintained by:** Platform Engineering Team
