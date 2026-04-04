# AgentScript Security Hardening Guide

Production security checklist and best practices for deploying AgentScript in enterprise environments.

## Table of Contents

1. [Encryption](#encryption)
2. [Authentication & Authorization](#authentication--authorization)
3. [Secrets Management](#secrets-management)
4. [Audit Logging](#audit-logging)
5. [Network Security](#network-security)
6. [Data Protection](#data-protection)
7. [Compliance](#compliance)

---

## Encryption

### At-Rest Encryption

#### Kubernetes etcd (where secrets are stored)

```bash
# Enable etcd encryption at rest
# In Kubernetes API server flags:
--encryption-provider-config=/etc/kubernetes/encryption-config.yaml
```

**encryption-config.yaml:**
```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key1
              secret: <base64-encoded-32-byte-key>
      - identity: {}
```

Verify:
```bash
# Spin up a new secret and check if it's encrypted in etcd
kubectl create secret generic test --from-literal=key=value -n agentscript

# SSH to etcd and verify (if accessible)
etcdctl get /kubernetes.io/secrets/agentscript/test \
  --command-timeout=30s | grep -q "test"
# Output should be: binary/unreadable (encrypted)
```

#### Persistent Volumes (PVC)

Enable encryption for trace storage:

**AWS EBS:**
```bash
# Create encrypted storage class
cat << EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ebs-encrypted
provisioner: ebs.csi.aws.com
parameters:
  encrypted: "true"
  kmsKeyId: arn:aws:kms:us-east-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab
EOF
```

**Google Persistent Disk:**
```bash
# GKE uses automatic encryption at rest by default
# For additional security, use Cloud KMS
gcloud container clusters update my-cluster \
  --database-encryption-key projects/my-project/locations/us-central1/keyRings/keyring/cryptoKeys/key
```

#### PostgreSQL (if using as trace backend)

Enable native encryption:

```sql
-- Create database with encryption
CREATE DATABASE agentscript_traces 
  TEMPLATE template0 
  ENCRYPTION 'scram-sha-256';

-- Enable SSL/TLS for connections
-- In postgresql.conf:
-- ssl = on
-- ssl_cert_file = '/etc/ssl/certs/server.crt'
-- ssl_key_file = '/etc/ssl/private/server.key'
```

Verify:
```bash
psql -h postgres.agentscript.svc.cluster.local \
  -U agentscript \
  -d agentscript_traces \
  -c "SELECT datname, datacl FROM pg_database WHERE datname='agentscript_traces';"
```

### In-Transit Encryption

#### TLS for All Communication

**API Server:**
```yaml
# values.yaml for Helm chart
ingress:
  enabled: true
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  tls:
    - secretName: agentscript-tls
      hosts:
        - agentscript.example.com
```

**mTLS (Mutual TLS) Between Pods:**
```bash
# Install Istio for mTLS
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm install istio-base istio/base -n istio-system
helm install istio istio/istiod -n istio-system

# Enable mTLS for AgentScript namespace
kubectl label namespace agentscript istio-injection=enabled

# Create PeerAuthentication policy
cat << EOF | kubectl apply -f -
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: agentscript
spec:
  mtls:
    mode: STRICT  # Require mTLS for all traffic
EOF
```

Verify mTLS:
```bash
# Check if traffic is encrypted
kubectl logs -n agentscript my-agentscript-0 | \
  grep -i "tls\|encrypted\|certificate" | head -10
```

#### OpenTelemetry Exporter

Encrypt OTel traces in transit:

```bash
# Use OTLP over TLS
helm install my-agentscript agentscript/agentscript \
  --set otel.jaegerEndpoint=https://jaeger-collector:4317 \
  --set env.OTEL_EXPORTER_OTLP_CERTIFICATE=/etc/ssl/certs/ca.crt \
  -n agentscript
```

---

## Authentication & Authorization

### API Authentication

#### ServiceAccount RBAC

AgentScript runs with minimal RBAC:

```yaml
rules:
  # Read-only access to ConfigMaps (agent definitions)
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "list", "watch"]
  
  # Read-only access to Secrets (API keys)
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list"]
  
  # Query pod logs (for debugging)
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
  
  # Create Events (audit trail)
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch"]
```

Verify RBAC:
```bash
# Test that AgentScript cannot delete pods
kubectl auth can-i delete pods --as=system:serviceaccount:agentscript:agentscript
# Output should be: no
```

#### OpenID Connect (OIDC) for API

Require OIDC tokens for HTTP API calls:

```bash
# Install a reverse proxy (e.g., oauth2-proxy)
helm repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
helm install oauth2-proxy oauth2-proxy/oauth2-proxy \
  --set config.clientID=my-client-id \
  --set config.clientSecret=my-client-secret \
  --set config.oidcIssuerURL=https://auth.example.com \
  -n agentscript
```

Configure Ingress to use OAuth2:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentscript
  annotations:
    oauth2-proxy.sign-in-url: https://oauth2-proxy:4180/oauth2/start
spec:
  rules:
    - host: agentscript.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: oauth2-proxy  # oauth2-proxy in front of agentscript
                port:
                  number: 4180
```

---

## Secrets Management

### Option 1: Sealed Secrets

Use sealed-secrets for GitOps-friendly secret management:

```bash
# Install sealed-secrets
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets -n kube-system

# Create a secret
kubectl create secret generic agentscript-api-keys \
  --from-literal=openai-key=sk_live_xxxxx \
  --from-literal=anthropic-key=sk-ant-xxxxx \
  -n agentscript --dry-run=client -o yaml > secret.yaml

# Seal it
kubeseal -f secret.yaml -w sealed-secret.yaml

# Commit sealed-secret.yaml to git (NOT secret.yaml)
# It can only be decrypted in this cluster
```

### Option 2: Hashicorp Vault

Use Vault for dynamic secret management:

```bash
# Install Vault
helm repo add hashicorp https://helm.releases.hashicorp.com
helm install vault hashicorp/vault \
  --set server.dataStorage.size=10Gi \
  -n hashicorp

# Configure Kubernetes auth method
vault auth enable kubernetes
vault write auth/kubernetes/config \
  token_reviewer_jwt="$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
  kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT_HTTPS" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

# Create policy
vault policy write agentscript - << EOF
path "secret/agentscript/*" {
  capabilities = ["read"]
}
EOF

# Bind to ServiceAccount
vault write auth/kubernetes/role/agentscript \
  bound_service_account_names=agentscript \
  bound_service_account_namespaces=agentscript \
  policies=agentscript \
  ttl=24h
```

### Option 3: AWS Secrets Manager

For AWS deployments:

```bash
# Store secrets in Secrets Manager
aws secretsmanager create-secret \
  --name agentscript/openai-api-key \
  --secret-string sk_live_xxxxx

# Grant pods access via IAM role
# 1. Create IAM role for pods
# 2. Attach policy to read secrets
# 3. Configure Kubernetes IRSA (IAM Roles for Service Accounts)

eksctl create iamserviceaccount \
  --cluster my-cluster \
  --name agentscript \
  --namespace agentscript \
  --attach-policy-arn arn:aws:iam::ACCOUNT:policy/SecretsManagerRead

# Pods now automatically get AWS credentials
```

### Secrets Rotation

Implement automatic secret rotation:

```bash
# Rotate OpenAI key every 90 days
cronjob:
  schedule: "0 2 * * *"  # Daily check
  command: |
    # 1. Check key age
    AGE=$(curl -s https://api.openai.com/api/key-info -H "Authorization: Bearer $OLD_KEY" | jq '.created_at')
    DAYS=$(($(date +%s) - $AGE) / 86400))
    
    if [ $DAYS -gt 90 ]; then
      # 2. Generate new key (manual step or API call)
      # 3. Update Kubernetes secret
      kubectl set env statefulset/my-agentscript \
        -n agentscript \
        OPENAI_API_KEY=$(aws secretsmanager get-secret-value \
          --secret-id agentscript/openai-api-key | jq -r '.SecretString')
      
      # 4. Restart pods to pick up new key
      kubectl rollout restart statefulset/my-agentscript -n agentscript
    fi
```

Verify secrets are not exposed:

```bash
# Check no secrets in environment
kubectl exec -it my-agentscript-0 -n agentscript -- env | \
  grep -E "api.?key|secret|token" | wc -l
# Should be: 0 (or very few, only non-sensitive keys)

# Check no secrets in logs
kubectl logs -n agentscript my-agentscript-0 | \
  grep -E "sk_live|sk_test|Bearer" | wc -l
# Should be: 0
```

---

## Audit Logging

### Kubernetes Audit Logs

Enable audit logging to track all API calls:

```yaml
# audit-policy.yaml
apiVersion: audit.k8s.io/v1
kind: Policy
rules:
  # Log secret access
  - level: RequestResponse
    resources: ["secrets"]
    namespaces: ["agentscript"]
  
  # Log RBAC changes
  - level: RequestResponse
    verbs: ["create", "patch", "delete"]
    resources: ["clusterroles", "clusterrolebindings"]
  
  # Log ServiceAccount activity
  - level: Metadata
    resources: ["serviceaccounts"]
    namespaces: ["agentscript"]
  
  # Default: log everything at Metadata level
  - level: Metadata
```

Enable in kube-apiserver:
```bash
--audit-policy-file=/etc/kubernetes/audit-policy.yaml \
--audit-log-path=/var/log/kubernetes/audit.log \
--audit-log-maxage=30 \
--audit-log-maxbackup=10 \
--audit-log-maxsize=100
```

### AgentScript Application Audit

Log sensitive operations:

```python
# In agentscript/runtime/engine.py
def _log_audit(event_type: str, details: dict) -> None:
    """Log security-relevant events."""
    audit_log = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "user_id": os.environ.get("USER_ID"),
        "workflow": details.get("workflow"),
        "status": details.get("status"),
        "source_ip": os.environ.get("REMOTE_ADDR"),
    }
    
    # Write to syslog for centralization
    import syslog
    syslog.syslog(syslog.LOG_SECURITY, json.dumps(audit_log))
    
    # Also write to structured log
    logger.warning("AUDIT", extra=audit_log)
```

Log these events:
- API authentication (success/failure)
- Secret access
- Workflow execution (success/failure)
- PII redaction (coverage metrics)
- Error conditions

### Centralized Logging

Send all logs to a central store:

```bash
# Using ELK Stack
helm install elasticsearch elastic/elasticsearch \
  --set replicas=3 \
  -n logging

helm install logstash elastic/logstash \
  --set pipelines.main.config.input='input { beats { port => 5000 } }' \
  -n logging

helm install filebeat elastic/filebeat \
  --set hostPathRoot=/var/lib/agentscript/logs \
  -n agentscript
```

Query audit logs:
```bash
# In Kibana UI
GET /kubernetes-audit-*/_search
{
  "query": {
    "term": {
      "objectRef.namespace": "agentscript"
    }
  }
}
```

---

## Network Security

### Network Policies

Restrict traffic between pods:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentscript-default-deny
  namespace: agentscript
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
  # Now nothing is allowed by default
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentscript-allow-internal
  namespace: agentscript
spec:
  podSelector:
    matchLabels:
      app: agentscript
  ingress:
    # Allow from Ingress controller
    - from:
        - namespaceSelector:
            matchLabels:
              name: ingress-nginx
      ports:
        - port: 8000
    
    # Allow from Prometheus scraper
    - from:
        - namespaceSelector:
            matchLabels:
              name: monitoring
      ports:
        - port: 8080
  
  egress:
    # Allow DNS
    - to:
        - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
    
    # Allow to external services (tool endpoints)
    - to:
        - podSelector:
            matchLabels:
              external-service: "true"
      ports:
        - port: 443
    
    # Allow to trace backend
    - to:
        - podSelector:
            matchLabels:
              app: postgres
      ports:
        - port: 5432
```

Verify network policy:
```bash
# Test that pods can't talk to unspecified services
kubectl exec -it my-agentscript-0 -n agentscript -- \
  curl http://blocked-service:8080
# Should timeout (connection refused)
```

### Pod Security Policies

Restrict what pods can do:

```yaml
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: agentscript
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - "configMap"
    - "emptyDir"
    - "downwardAPI"
    - "secret"
    - "projected"
    - "persistentVolumeClaim"
  runAsUser:
    rule: "MustRunAsNonRoot"
  runAsGroup:
    rule: "MustRunAs"
    ranges:
      - min: 1000
        max: 65535
  fsGroup:
    rule: "MustRunAs"
    ranges:
      - min: 1000
        max: 65535
  readOnlyRootFilesystem: true
  seLinux:
    rule: "MustRunAs"
    seLinuxOptions:
      type: spc_t
```

---

## Data Protection

### PII Handling

Implement strong PII redaction:

```yaml
piiRedaction:
  enabled: true
  patterns:
    - name: email
      regex: '[\w\.-]+@[\w\.-]+\.\w+'
      replacement: '[EMAIL_REDACTED]'
    - name: ssn
      regex: '\d{3}-\d{2}-\d{4}'
      replacement: '[SSN_REDACTED]'
    - name: phone
      regex: '\d{3}-\d{3}-\d{4}'
      replacement: '[PHONE_REDACTED]'
    - name: api_key
      regex: 'sk_[a-zA-Z0-9]{32}'
      replacement: '[API_KEY_REDACTED]'
    - name: bearer_token
      regex: 'Bearer [a-zA-Z0-9\._\-]{20,}'
      replacement: '[TOKEN_REDACTED]'
```

Test annually:
```bash
python -m agentscript.test.pii_redaction \
  --dataset synthetic_pii_dataset.jsonl \
  --expected-catch-rate 0.999
```

### Data Retention & Deletion

Comply with GDPR/CCPA:

```bash
# Delete user data on request
curl -X DELETE https://agentscript.example.com/api/users/user_12345 \
  -H "Authorization: Bearer $TOKEN"

# Implement hard delete (not soft delete)
# Use database triggers to prevent accidental recovery
CREATE TRIGGER prevent_user_recovery BEFORE UPDATE ON traces
  FOR EACH ROW
  WHEN NEW.user_id IS NOT NULL AND OLD.user_id IS NULL
    RAISE(ABORT, 'Cannot restore deleted user data');
```

---

## Compliance

### SOC 2 Type II

Document controls:

- [ ] **CC6.1:** Access Control
  - [ ] RBAC policies in place
  - [ ] API authentication enabled
  - [ ] Audit logs retained 90 days

- [ ] **CC7.1:** Logical & Physical Access Controls
  - [ ] Network policies restrict pod-to-pod
  - [ ] Kubernetes RBAC enforced
  - [ ] Secrets encrypted at rest

- [ ] **CC7.2:** System Monitoring
  - [ ] All API calls audited
  - [ ] Metrics and traces exported
  - [ ] Alerts configured for anomalies

- [ ] **CCM1.1:** Encryption
  - [ ] Data at rest encrypted
  - [ ] Data in transit encrypted (TLS)
  - [ ] Keys rotated annually

### GDPR Compliance

- [ ] **Data Subject Rights**
  - [ ] Implement right to access (data export)
  - [ ] Implement right to erasure (delete-user-traces)
  - [ ] Implement right to rectification (update traces)

- [ ] **Data Protection**
  - [ ] Encrypt PII at rest
  - [ ] PII not logged
  - [ ] Redaction catch > 99.9%

- [ ] **Data Processing Agreement**
  - [ ] DPA signed with all LLM providers
  - [ ] Sub-processor list maintained
  - [ ] Data processors documented

### HIPAA Compliance (Healthcare)

- [ ] BAA (Business Associate Agreement) signed
- [ ] Encryption for PHI (Protected Health Information)
- [ ] Access controls and audit logs
- [ ] De-identification and anonymization
- [ ] Breach notification procedures

---

## Security Checklist

### Pre-Production

- [ ] Secrets not committed to git
  ```bash
  git log --all -p -S "sk_live\|Bearer" | wc -l
  # Should be: 0
  ```

- [ ] TLS enabled for all API endpoints
- [ ] RBAC policies reviewed and tested
- [ ] Network policies deployed and tested
- [ ] Secrets rotation schedule established
- [ ] Audit logging enabled and retained

### Ongoing

- [ ] Weekly: Review audit logs for suspicious activity
- [ ] Monthly: Rotate API keys
- [ ] Quarterly: Pen testing
- [ ] Annually: SOC 2 audit, key rotation

### Incident Response

In case of security incident:

```bash
# 1. Isolate affected resources
kubectl delete statefulset my-agentscript -n agentscript

# 2. Preserve evidence
kubectl logs my-agentscript-0 -n agentscript > /evidence/logs.txt
kubectl get events -n agentscript > /evidence/events.txt

# 3. Rotate compromised credentials
kubectl delete secret agentscript-api-keys -n agentscript
# (Re-create with new API keys)

# 4. Review audit logs
grep -r "agentscript" /var/log/kubernetes/audit.log | jq '.' > /evidence/audit.jsonl

# 5. Notify stakeholders
# ... according to incident response plan
```

---

**References:**
- [Kubernetes Security Best Practices](https://kubernetes.io/docs/concepts/security/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [CIS Kubernetes Benchmark](https://www.cisecurity.org/benchmark/kubernetes)

**Last updated:** 2024-04-05  
**Next review:** 2024-10-05 (6-month audit)
