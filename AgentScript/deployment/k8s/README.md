# AgentScript Kubernetes Deployment Guide

Production deployment of AgentScript to Kubernetes using Helm charts, enabling scalable, observable, and resilient agentic AI systems in enterprise environments.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Quick Start](#quick-start)
4. [Configuration](#configuration)
5. [Storage Backend](#storage-backend)
6. [Observability Integration](#observability-integration)
7. [Advanced Topics](#advanced-topics)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required
- **Kubernetes cluster** (1.24+) with at least 3 worker nodes
  - `minikube` or Docker Desktop K8s for local testing
  - EKS, GKE, AKS, or DigitalOcean K8s for production
- **kubectl** (1.24+) and **helm** (3.10+)
- **Docker** with container registry access (Docker Hub, ECR, GCR, etc.)

### Optional but Recommended
- **OpenTelemetry Collector** (Jaeger, Datadog Agent, New Relic, or Tempo)
- **PostgreSQL 14+** (for trace backend; SQLite for development)
- **Prometheus & Grafana** (for metrics and dashboards)
- **LangSmith account** (for AI-specific observability)

### Installation Checks

```bash
# Verify cluster access
kubectl cluster-info
kubectl get nodes

# Verify Helm
helm version

# Create namespace
kubectl create namespace agentscript
kubectl config set-context --current --namespace=agentscript
```

---

## Architecture Overview

```

┌─────────────────────────────────────────────────────────────────┐
│                      Kubernetes Cluster                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │          AgentScript StatefulSet (3 replicas)            │  │
│  │                                                          │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │  │
│  │  │ Pod 0   │  │ Pod 1   │  │ Pod 2   │                 │  │
│  │  │ Agent   │  │ Agent   │  │ Agent   │                 │  │
│  │  │ Runtime │  │ Runtime │  │ Runtime │                 │  │
│  │  └────┬────┘  └────┬────┘  └────┬────┘                 │  │
│  │       │            │            │                      │  │
│  │  ┌────▼─────────────▼────────────▼────┐                │  │
│  │  │    Persistent Trace Store (PVC)     │                │  │
│  │  │  (SQLite or PostgreSQL)             │                │  │
│  │  └─────────────────────────────────────┘                │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Service (LoadBalancer)                      │  │
│  │  - HTTP: localhost:80                                   │  │
│  │  - Metrics: localhost:8080/metrics                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │       ConfigMaps & Secrets                              │  │
│  │  - Agent definitions (.as files)                        │  │
│  │  - API keys and credentials                             │  │
│  │  - Circuit breaker tuning                               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         │
         │ Exports traces & metrics
         ▼
┌──────────────────────────────────────┐
│   OpenTelemetry Collector            │
│   (Jaeger, Datadog, New Relic, etc.) │
└──────────────────────────────────────┘
         │
         ├─→ Jaeger / Datadog / New Relic / Lightstep
         │   (Long-term trace storage & querying)
         │
         └─→ Prometheus / Grafana
             (Metrics, dashboards, alerting)

```

---

## Quick Start

### 1. Deploy Using Default Values

```bash
# Add the AgentScript Helm repository (when published)
helm repo add agentscript https://charts.agentscript.dev
helm repo update

# Install the Helm chart in the agentscript namespace
helm install my-agentscript agentscript/agentscript \
  --namespace agentscript \
  --create-namespace \
  --values values.yaml

# Verify deployment
kubectl get statefulsets -n agentscript
kubectl get pods -n agentscript
kubectl get pvc -n agentscript
```

### 2. Port-Forward for Local Access

```bash
# Forward the service to localhost
kubectl port-forward -n agentscript svc/my-agentscript 8000:80

# Access the dashboard
open http://localhost:8000
```

### 3. Verify Health

```bash
# Check pod logs
kubectl logs -n agentscript my-agentscript-0

# Check readiness
kubectl get pods my-agentscript-0 -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'

# Test the HTTP endpoint
curl -X GET http://localhost:8000/health
```

### 4. Uninstall

```bash
helm uninstall my-agentscript -n agentscript
```

---

## Configuration

### Common Customizations

#### 1. Use PostgreSQL Instead of SQLite

```bash
helm install my-agentscript agentscript/agentscript \
  --namespace agentscript \
  --set traceStore.backend=postgresql \
  --set traceStore.postgresql.host=postgres.default.svc.cluster.local \
  --set traceStore.postgresql.port=5432 \
  --set traceStore.postgresql.username=agentscript \
  --set traceStore.postgresql.password=<password>
```

#### 2. Enable Datadog Integration

```bash
helm install my-agentscript agentscript/agentscript \
  --namespace agentscript \
  --set otel.exporter=datadog \
  --set env.DD_AGENT_HOST=datadog-agent.default.svc.cluster.local \
  --set env.DD_AGENT_PORT=8126
```

#### 3. Scale to 5 Replicas

```bash
helm install my-agentscript agentscript/agentscript \
  --namespace agentscript \
  --set replicaCount=5 \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=5 \
  --set autoscaling.maxReplicas=20
```

#### 4. Set API Keys via Helm

```bash
helm install my-agentscript agentscript/agentscript \
  --namespace agentscript \
  --set secrets.openai.apiKey=sk_live_xxxx \
  --set secrets.anthropic.apiKey=sk-ant-xxxx \
  --set langsmith.apiKey=ls_dev_xxxx
```

### Custom values.yaml

Create `custom-values.yaml`:

```yaml
# Override only the settings you need
replicaCount: 5

autoscaling:
  enabled: true
  minReplicas: 5
  maxReplicas: 20
  targetCPUUtilizationPercentage: 60

otel:
  exporter: datadog
  
langsmith:
  enabled: true
  # Set via sealed-secrets in production
```

Then deploy:

```bash
helm install my-agentscript agentscript/agentscript \
  -f custom-values.yaml \
  --namespace agentscript
```

---

## Storage Backend

### SQLite (Default, Development)

Best for small deployments (< 100K traces, single-node clusters).

```yaml
traceStore:
  backend: sqlite
  sqlite:
    persistenceSize: 10Gi  # Increase for larger datasets
    backupEnabled: true
    backupSchedule: "0 2 * * *"  # Daily at 2 AM
```

**Pros:**
- Zero external dependencies
- Great for testing and demos
- Easy backup/restore

**Cons:**
- Single-node bottleneck
- Limited query performance
- No multi-replica consistency

### PostgreSQL (Production)

Best for high-volume deployments (> 100K traces, multi-node clusters).

```yaml
traceStore:
  backend: postgresql
  postgresql:
    host: postgres.default.svc.cluster.local
    port: 5432
    username: agentscript
    database: agentscript_traces
    # Store password in sealed-secrets
```

**Pros:**
- Scales to millions of traces
- Advanced querying (indexes, window functions)
- Backup/replication ecosystem
- ACID compliance

**Cons:**
- Requires PostgreSQL cluster
- More complex operations

### Setting Up PostgreSQL

```bash
# Install PostgreSQL via Helm (optional)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install postgres bitnami/postgresql \
  --set auth.username=agentscript \
  --set auth.password=<password> \
  --set auth.database=agentscript_traces \
  --namespace agentscript

# Get the connection string
POSTGRES_HOST=postgres-postgresql.agentscript.svc.cluster.local
POSTGRES_PASSWORD=$(kubectl get secret postgres-postgresql -o jsonpath="{.data.postgres-password}" | base64 -d)
```

---

## Observability Integration

### Connect to Jaeger (All-in-One)

```bash
# Deploy Jaeger all-in-one
kubectl run jaeger \
  --image jaegertracing/all-in-one \
  -p 16686:16686 \
  -p 4317:4317 \
  --namespace agentscript

# Forward Jaeger UI to localhost
kubectl port-forward -n agentscript \
  $(kubectl get pod -n agentscript -l run=jaeger -o name) \
  16686:16686

# Deploy AgentScript with Jaeger endpoint
helm install my-agentscript agentscript/agentscript \
  --set otel.jaegerEndpoint=http://jaeger:4317 \
  --namespace agentscript

# View traces in Jaeger UI
open http://localhost:16686
```

### Connect to Datadog

```bash
# Set Datadog API key and site
helm install my-agentscript agentscript/agentscript \
  --set env.DD_AGENT_HOST=<datadog-agent-ip> \
  --set env.DD_AGENT_PORT=8126 \
  --set env.DD_API_KEY=<api-key> \
  --set otel.exporter=datadog \
  --namespace agentscript
```

### Connect to Prometheus + Grafana

```bash
# Deploy Prometheus
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/prometheus \
  --namespace agentscript

# Deploy Grafana
helm repo add grafana https://grafana.github.io/helm-charts
helm install grafana grafana/grafana \
  --set adminPassword=<password> \
  --namespace agentscript

# Port-forward Grafana
kubectl port-forward -n agentscript svc/grafana 3000:80

# Configure Prometheus datasource in Grafana
# Add dashboard from: deployment/k8s/grafana-dashboard.json
```

---

## Advanced Topics

### Multi-Region Deployment

Deploy AgentScript to multiple regions with cross-region trace aggregation:

```bash
# Region 1 (us-east-1)
helm install my-agentscript agentscript/agentscript \
  --set podAnnotations.region=us-east-1 \
  --namespace agentscript

# Region 2 (eu-west-1)
helm install my-agentscript agentscript/agentscript \
  --set podAnnotations.region=eu-west-1 \
  --namespace agentscript

# All traces flow to central Datadog account
```

### Blue-Green Deployment

```bash
# Deploy v1.0.0 (blue)
helm install agentscript-blue agentscript/agentscript \
  --set image.tag=1.0.0 \
  --namespace agentscript-blue \
  --create-namespace

# Deploy v1.1.0 (green)
helm install agentscript-green agentscript/agentscript \
  --set image.tag=1.1.0 \
  --namespace agentscript-green \
  --create-namespace

# Switch traffic via Ingress Controller
kubectl patch ingress agentscript \
  -p '{"spec":{"rules":[{"http":{"paths":[{"backend":{"serviceName":"agentscript-green"}}]}}]}}'
```

### Custom Agent Definitions

Store agent definitions in a private ConfigMap:

```bash
# Create ConfigMap from local agent files
kubectl create configmap my-agents \
  --from-file=agents/legal_research.as \
  --from-file=agents/document_analyzer.as \
  -n agentscript

# Reference in values
helm install my-agentscript agentscript/agentscript \
  --set agents[0].name=legal_researcher \
  --set agents[0].script=/cfg/legal_research.as \
  --namespace agentscript
```

### Resource Quotas and Limits

```bash
# Set namespace quota (prevent runaway usage)
kubectl create quota agentscript-quota \
  --hard=requests.cpu=20,requests.memory=100Gi,limits.cpu=50,limits.memory=200Gi \
  --namespace agentscript
```

---

## Troubleshooting

### Pod Won't Start

```bash
# Check pod events
kubectl describe pod my-agentscript-0 -n agentscript

# Check logs
kubectl logs my-agentscript-0 -n agentscript

# Check resource availability
kubectl top nodes
kubectl top pod my-agentscript-0 -n agentscript
```

### Traces Not Appearing

```bash
# Verify OTEL endpoint connectivity
kubectl exec -it my-agentscript-0 -n agentscript -- \
  curl -v http://jaeger-collector:4317/status

# Check environment variables
kubectl exec -it my-agentscript-0 -n agentscript -- env | grep OTEL

# Look for trace export errors in logs
kubectl logs my-agentscript-0 -n agentscript | grep -i "export\|trace\|error"
```

### Storage Running Out of Space

```bash
# Check PVC usage
kubectl get pvc -n agentscript
kubectl describe pvc trace-store-my-agentscript-0 -n agentscript

# Expand PVC
kubectl patch pvc trace-store-my-agentscript-0 \
  -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}' \
  -n agentscript

# Archive old traces
kubectl exec -it my-agentscript-0 -n agentscript -- \
  python -m agentscript.cli archive \
  --before-date 2024-01-01 \
  --output /backups/traces-2023.tar.gz
```

### High Latency

```bash
# Check network policies
kubectl get networkpolicies -n agentscript

# Verify inter-pod communication
kubectl exec -it my-agentscript-0 -n agentscript -- \
  curl http://my-agentscript-1:8000/health

# Check resource contention
kubectl top pod -n agentscript
kubectl top nodes

# Monitor circuit breaker state
kubectl logs my-agentscript-0 -n agentscript | grep "circuit.breaker"
```

---

## Reference

- [Helm Chart Repository](https://github.com/agentscript/agentscript)
- [OpenTelemetry Configuration](../docs/ARCHITECTURE.md#observability)
- [Operations Guide](../docs/OPERATIONS.md)
- [Security Checklist](../docs/SECURITY.md)
