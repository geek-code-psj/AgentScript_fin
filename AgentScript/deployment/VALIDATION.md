# AgentScript Deployment Validation Checklist

Use this checklist to validate AgentScript deployment before and after going live.

---

## Pre-Deployment Validation (1-2 weeks before launch)

### Code & Configuration

- [ ] All unit tests pass
  ```bash
  pytest tests/ -v
  ```

- [ ] All regression tests pass (including new 25+ cases)
  ```bash
  python -m evals.run_regressions 2>&1 | jq '.[] | select(.passed == false)'
  # Should return: (empty)
  ```

- [ ] Circuit breaker thresholds are tuned for your SLOs
  - [ ] `failureRateThreshold` adjusted for tool reliability
  - [ ] `waitDurationInOpenState` tested with actual downtime
  - [ ] `slowCallDurationThreshold` reflects your P95 target

- [ ] Agent definitions (.as files) are valid
  ```bash
  python -m agentscript.cli validate agents/legal_research.as
  python -m agentscript.cli validate agents/document_analyzer.as
  # Each should: "✓ Valid syntax"
  ```

- [ ] Model configuration is locked
  - [ ] Model ID pinned (e.g., "gpt-4-turbo-2024-04-09", not "gpt-4")
  - [ ] Temperature, top_p immutable
  - [ ] System prompt hash recorded for reproducibility

### Secrets & Access Control

- [ ] API keys stored in Kubernetes Secrets (not ConfigMaps)
  ```bash
  kubectl get secrets -n agentscript | grep -E "api-key|langsmith"
  ```

- [ ] Credentials rotated (< 90 days old)
  - [ ] OpenAI API key
  - [ ] Anthropic API key
  - [ ] LangSmith API key
  - [ ] Database password

- [ ] RBAC configured correctly
  ```bash
  kubectl describe clusterrole agentscript
  # Should show minimal permissions (configmaps, secrets, events)
  ```

- [ ] No credentials in environment, code, or logs
  ```bash
  grep -r "sk_live\|sk_test\|Bearer" src/ | wc -l
  # Should be: 0
  ```

### Observability Setup

- [ ] OpenTelemetry exporter endpoint is reachable
  ```bash
  kubectl run curl --image=curlimages/curl -- \
    -c 'curl -v http://jaeger-collector:4317/status' \
    -n agentscript --rm -it
  # Should: 200 OK
  ```

- [ ] Jaeger (or Datadog/New Relic) is configured
  ```bash
  # View sample traces
  curl http://localhost:16686/api/traces?service=agentscript | jq '.data | length'
  # Should be: > 0 (at least some traces)
  ```

- [ ] LangSmith is enabled and accessible
  ```bash
  curl https://api.smith.langchain.com/health \
    -H "Authorization: Bearer $LANGSMITH_API_KEY"
  # Should: 200 OK
  ```

- [ ] Prometheus scrape config includes AgentScript
  ```bash
  kubectl get configmap prometheus-server -o yaml | \
    grep agentscript
  # Should include: job_name: agentscript
  ```

- [ ] Grafana dashboards are imported
  ```bash
  kubectl port-forward -n monitoring svc/grafana 3000:80
  # Import dashboard from: deployment/k8s/grafana-dashboard.json
  ```

- [ ] Log aggregation is working
  - [ ] Logs appear in ELK/Splunk/CloudWatch
  - [ ] Log level is INFO (not DEBUG which is noisy)

### Security & PII

- [ ] PII redaction patterns are comprehensive
  ```bash
  python -m agentscript.test.pii_redaction \
    --dataset test_data/synthetic_pii.jsonl
  # Should pass with 99.9%+ catch rate
  ```

- [ ] Test PII redaction end-to-end
  ```bash
  cat << EOF | kubectl exec -i my-agentscript-0 -n agentscript -- \
    python -c "from agentscript.runtime.tracing import redact_payload; import sys, json; print(json.dumps(redact_payload(json.load(sys.stdin))))"
  {"email": "user@example.com", "text": "SSN 123-45-6789"}
  EOF
  # Should redact both email and SSN
  ```

- [ ] PII is not exported to logs
  ```bash
  kubectl logs -n agentscript my-agentscript-0 | \
    grep -E "[0-9]{3}-[0-9]{2}-[0-9]{4}|[a-z]+@[a-z]+\." | wc -l
  # Should be: 0 (no PII in logs)
  ```

- [ ] Encryption at rest is enabled
  - [ ] Secrets encrypted with etcd encryption
  - [ ] PVC volumes encrypted (CloudStorage encryption, EBS encryption, etc.)

### Database

- [ ] Database backend tested (SQLite or PostgreSQL)
  ```bash
  kubectl exec -it my-agentscript-0 -n agentscript -- \
    sqlite3 /var/lib/agentscript/traces/traces.db \
    "SELECT COUNT(*) FROM traces;"
  # Should return a number (database connected)
  ```

- [ ] Database backup strategy verified
  ```bash
  ls -lah /backups/traces-2024-*.db | wc -l
  # Should be: multiple backups (daily snapshots)
  ```

- [ ] Database restore tested
  ```bash
  # Simulate restore: backup → delete → restore
  kubectl exec my-agentscript-0 -n agentscript -- \
    cp /var/lib/agentscript/traces/traces.db \
    /tmp/backup.db
  kubectl exec my-agentscript-0 -n agentscript -- \
    rm /var/lib/agentscript/traces/traces.db
  kubectl exec my-agentscript-0 -n agentscript -- \
    cp /tmp/backup.db /var/lib/agentscript/traces/traces.db
  # Verify pod restarts without errors
  ```

---

## Deployment Validation (Day 0, during launch)

### Health Checks

- [ ] All pods are running and ready
  ```bash
  kubectl get pods -n agentscript
  # All rows should be: Running, 1/1 Ready
  ```

- [ ] Liveness probes are passing
  ```bash
  kubectl describe pods -n agentscript | grep -A 2 "Liveness"
  # Should show: passing
  ```

- [ ] Service is accessible
  ```bash
  kubectl get svc -n agentscript my-agentscript
  # Should have external IP or LoadBalancer URL
  
  # Test it
  curl http://<EXTERNAL_IP>:80/health
  # Should: 200 OK
  ```

- [ ] Metrics endpoint is accessible
  ```bash
  curl http://<POD_IP>:8080/metrics | head -20
  # Should show: HELP, TYPE, metric definitions
  ```

### Basic Functionality

- [ ] Simple workflow executes successfully
  ```bash
  curl -X POST http://<EXTERNAL_IP>:80/workflows/legal_brief/run \
    -H "Content-Type: application/json" \
    -d '{"query": "theft appeal"}'
  # Should: 200 OK with results
  ```

- [ ] Deterministic execution verified
  ```bash
  # Run same query twice
  curl ... > run1.json
  curl ... > run2.json
  
  # Compare outputs (ignoring timestamps)
  diff <(jq '.result' run1.json) <(jq '.result' run2.json)
  # Should be: empty (identical)
  ```

- [ ] Error handling works
  ```bash
  # Test with invalid agent
  curl -X POST http://<EXTERNAL_IP>:80/workflows/nonexistent/run \
    -d '{"query": "test"}'
  # Should: 404 or 400 (not 500)
  ```

### Observability

- [ ] Traces are being exported
  ```bash
  # Check Jaeger UI
  open http://localhost:16686
  # Navigate to: Services → agentscript → find recent traces
  ```

- [ ] Metrics are being exported
  ```bash
  # Query Prometheus
  curl 'http://prometheus:9090/api/v1/query?query=agentscript_workflow_total'
  # Should return: non-zero values
  ```

- [ ] Grafana dashboard shows live data
  ```bash
  open http://localhost:3000
  # Should show: workflow success rate, latency, tool invocations, etc.
  ```

- [ ] LangSmith shows runs
  ```bash
  open https://smith.langchain.com
  # Should show: recent traces from agentscript
  ```

- [ ] Logs are aggregated
  ```bash
  # Check ELK/Splunk
  # Filter by: service=agentscript, level=INFO
  # Should see: workflow execution logs, trace export logs
  ```

### Load Testing

- [ ] System sustains target load for 5 minutes
  ```bash
  # Generate 100 concurrent requests
  ab -n 500 -c 100 http://<EXTERNAL_IP>:80/health
  
  # Output should show:
  # - Requests per second: > 50
  # - Failed requests: 0
  # - 95% latency: < 5 seconds
  ```

- [ ] Memory/CPU don't spike unexpectedly
  ```bash
  watch -n 1 'kubectl top pod -n agentscript'
  # During load test, should stay relatively stable
  ```

- [ ] No errors under load
  ```bash
  kubectl logs -n agentscript my-agentscript-0 --tail=100 | \
    grep -E "ERROR|FAILED|exception" | wc -l
  # Should be: 0 or very low (< 1%)
  ```

---

## Post-Deployment Validation (After 24 hours live)

### Stability

- [ ] No pod crashes or restarts
  ```bash
  kubectl get events -n agentscript --sort-by='.lastTimestamp'
  # Should show: no "OOMKilled", "BackOff", "CrashLoopBackOff"
  ```

- [ ] Success rate maintained > 99%
  ```bash
  # Prometheus query
  rate(agentscript_workflow_success_total[1h]) / \
  rate(agentscript_workflow_total[1h])
  # Should be: > 0.99
  ```

- [ ] Latency within SLA
  ```bash
  histogram_quantile(0.95, 
    rate(agentscript_workflow_duration_seconds_bucket[1h]))
  # Should be: < 5 seconds
  ```

- [ ] No circuit breakers open
  ```bash
  agentscript_circuit_breaker_state{state="open"}
  # Should be: 0
  ```

### Data Quality

- [ ] Replay of 10 random traces succeeds
  ```bash
  python -m agentscript.cli audit.sample-traces --count 10
  
  for trace in sampled_traces.jsonl; do
    python -m agentscript.cli replay --trace-file $trace
  done
  
  # All should succeed with matches original output
  ```

- [ ] PII redaction verified on real traces
  ```bash
  # Sample 1000 recent traces
  sqlite3 /var/lib/agentscript/traces/traces.db \
    "SELECT payload FROM traces LIMIT 1000" | \
    python -c "
import sys, json, re
for line in sys.stdin:
  if '[EMAIL_REDACTED]' not in line:
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', line)
    if match:
      print(f'FOUND UNREDACTED EMAIL: {match.group()}')"
  
  # Should output: nothing (all PII redacted)
  ```

- [ ] Shadow deployment (if enabled) has zero hallucinations
  ```bash
  # Query LangSmith/Jaeger for "shadow_mode" traces
  # Manually audit 50 random traces for quality
  # All should have: assessment = "acceptable" or better
  ```

### Dashboard & Alerting

- [ ] Custom dashboards show correct data
  - [ ] Success rate matches Prometheus
  - [ ] Latency percentiles visible
  - [ ] Circuit breaker state shown
  - [ ] No gaps in metrics

- [ ] Alerts fire correctly
  ```bash
  # Trigger a test alert
  kubectl set env statefulset/my-agentscript \
    -n agentscript \
    TEST_ALERT=true
  
  # Should receive: Slack/PagerDuty notification within 1 minute
  ```

### Scaling

- [ ] Horizontal Pod Autoscaler is active
  ```bash
  kubectl get hpa -n agentscript
  # Should show: my-agentscript with desired replicas
  ```

- [ ] Scaling up works
  ```bash
  # Trigger high load
  ab -n 10000 -c 100 http://<EXTERNAL_IP>:80/health
  
  # Monitor HPA
  watch kubectl get hpa -n agentscript
  # Should see: replicas increase from 3 to 5-10
  ```

### Backup & Disaster Recovery

- [ ] Daily backup is being created
  ```bash
  ls -lah /backups/traces-*.db | tail -5
  # Should show: today's backup exists and is reasonable size
  ```

- [ ] Backup to cloud storage is working
  ```bash
  aws s3 ls s3://agentscript-backups/ --recursive | tail -10
  # Should show: recent backup files
  ```

- [ ] Backup can be restored
  ```bash
  # Perform test restore to temporary location
  kubectl exec my-agentscript-0 -n agentscript -- \
    sqlite3 /backups/traces-*.db "SELECT COUNT(*) FROM traces;"
  # Should return: some number > 0
  ```

---

## Sign-Off

**Deployment validated by:** _____________________ (name)

**Date:** ________________

**Environment:** ☐ Staging  ☐ Production

**Approval to go live:** ☐ Yes, all checks passed  ☐ No, issues found (detail below)

**Issues found (if any):**

```
_________________________________________________________
_________________________________________________________
_________________________________________________________
```

**Next review date:** ________________ (7 days from deployment)

---

**Links:**
- [Kubernetes Deployment Guide](../deployment/k8s/README.md)
- [Operations Manual](OPERATIONS.md)
- [SLAs & Metrics](SLAS.md)
- [Security Guide](SECURITY.md)
